"""The three agents. Each one reads retrieved knowledge (RAG) before it decides anything.

1. TriageAgent             - how sick is the pet, and what must be cancelled to keep it safe?
2. ScheduleOptimizerAgent  - how can a missed medication be given safely, without doubling?
3. NotificationAgent       - turns both decisions into plan adjustments and owner-facing alerts.

Safety design: the knowledge-base rules always run. Gemini (if available) can only ADD
to the rules, never weaken them, and every Gemini answer is validated before use.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta

from models import HealthReport, Owner, Pet, Priority, Task
from retriever import KnowledgeBase
from scheduler import Adjustments, Alert

log = logging.getLogger(__name__)

SEVERITIES = ["routine", "monitor", "see_vet", "urgent"]  # lowest to highest
_NEVER_CANCEL = {"medication", "feeding"}  # the AI may not cancel these (rules or Gemini)


def _worse(a: str, b: str) -> str:
    return max(a, b, key=SEVERITIES.index)


# ---------------------------------------------------------------- Agent 1: Triage
@dataclass
class TriageResult:
    severity: str = "routine"
    cancelled: dict[int, str] = field(default_factory=dict)  # id(task) -> reason
    cancelled_titles: list[str] = field(default_factory=list)
    alerts: list[Alert] = field(default_factory=list)
    rationale: str = ""
    sources: list[str] = field(default_factory=list)  # knowledge-base ids that drove the decision
    used_llm: bool = False
    # Evidence for the confidence score (see confidence.py):
    symptom_match: dict[str, str] = field(default_factory=dict)  # symptom -> "exact" | "keyword" | "none"
    llm_status: str = "off"  # off | unavailable | invalid | agreed | differs
    unknown_pet: bool = False


class TriageAgent:
    def __init__(self, kb: KnowledgeBase, llm=None) -> None:
        self.kb = kb
        self.llm = llm

    def run(self, owner: Owner, report: HealthReport | None, on_date) -> TriageResult:
        result = TriageResult()
        self._scan_toxic_tasks(owner, on_date, result)
        if report is None:
            return result
        pet = owner.find_pet(report.pet_name)
        if pet is None:
            log.warning("Triage: no pet named %r; skipping symptom triage.", report.pet_name)
            result.unknown_pet = True
            result.alerts.append(Alert("warning", f"Health report names unknown pet '{report.pet_name}'."))
            return result

        due = [t for t in pet.tasks if t.is_due(on_date)]
        stressful: set[str] = set()
        for symptom, count in report.symptoms.items():
            docs = self.kb.retrieve(symptom.replace("_", " "), species=pet.species, topic_prefix="symptom", k=1)
            if not docs:
                result.symptom_match[symptom] = "none"
                log.warning("Triage: no guideline found for symptom %r; advising a vet call.", symptom)
                result.severity = _worse(result.severity, "see_vet")
                result.alerts.append(Alert("warning", f"No guidance for '{symptom}'. Contact your vet."))
                continue
            doc = docs[0]
            result.symptom_match[symptom] = "exact" if doc.get("symptom") == symptom else "keyword"
            result.sources.append(doc["id"])
            level = "routine"
            if count >= doc["urgent_at"]:
                level = "urgent"
            elif count >= doc["vet_contact_at"]:
                level = "see_vet"
            elif count >= doc["monitor_at"]:
                level = "monitor"
            result.severity = _worse(result.severity, level)
            if count >= doc["stress_cancel_at"]:
                stressful |= set(doc["stressful_categories"])
            log.info("Triage rule: %s x%d -> %s (%s)", symptom, count, level, doc["id"])
            if level in ("see_vet", "urgent"):
                word = "URGENT: go to the vet now" if level == "urgent" else "Contact the vet"
                result.alerts.append(Alert(level if level == "urgent" else "warning",
                                           f"{word} about {pet.name}'s {symptom.replace('_', ' ')} ({count}x)."))
        if result.severity == "urgent":
            stressful.add("walk")

        for task in due:
            if task.category in stressful and task.category not in _NEVER_CANCEL:
                result.cancelled[id(task)] = f"Postponed: {pet.name} is unwell ({result.severity.replace('_', ' ')})"
                result.cancelled_titles.append(task.title)

        self._ask_llm(pet, report, due, result)
        return result

    def _scan_toxic_tasks(self, owner: Owner, on_date, result: TriageResult) -> None:
        """Cancel any due task whose title mentions something toxic for that pet's species."""
        for pet, task in owner.all_tasks():
            if not task.is_due(on_date):
                continue
            for doc in self.kb.retrieve(task.title, species=pet.species, topic_prefix="toxic", k=1):
                if not any(term in task.title.lower() for term in doc["food"]):
                    continue
                result.cancelled[id(task)] = f"Unsafe for {pet.species}s: {doc['text'].split('.')[0]}."
                result.cancelled_titles.append(task.title)
                result.sources.append(doc["id"])
                result.alerts.append(Alert("urgent", f"Task '{task.title}' removed: {doc['text']}"))
                log.warning("Toxic task cancelled: %r for %s (%s)", task.title, pet.name, doc["id"])

    def _ask_llm(self, pet: Pet, report: HealthReport, due: list[Task], result: TriageResult) -> None:
        if self.llm is None or not getattr(self.llm, "available", False):
            return
        context = [d["text"] for d in self.kb.retrieve(
            " ".join(report.symptoms) + " " + report.notes, species=pet.species, topic_prefix="symptom", k=3)]
        prompt = (
            "You are a pet-care triage assistant. Use ONLY the veterinary guidance below.\n"
            f"GUIDANCE: {json.dumps(context)}\n"
            f"PET: {pet.name} ({pet.species}, {pet.breed or 'unknown breed'})\n"
            f"SYMPTOMS TODAY (counts): {json.dumps(report.symptoms)}\nOWNER NOTES: {report.notes!r}\n"
            f"TODAY'S TASKS: {json.dumps([{'title': t.title, 'category': t.category} for t in due])}\n"
            f"Rule-based severity so far: {result.severity}.\n"
            'Reply as JSON: {"severity": "routine|monitor|see_vet|urgent", '
            '"cancel_titles": [exact task titles to postpone for the pet\'s wellbeing], '
            '"rationale": "one or two sentences for the owner"}'
        )
        rules_severity, rules_cancelled = result.severity, set(result.cancelled)
        answer = self.llm.generate_json(prompt)
        if answer is None:
            result.llm_status = "unavailable"
            return
        # Guardrail: validate everything Gemini says before it can change the plan.
        severity = answer.get("severity")
        rationale = answer.get("rationale")
        titles = answer.get("cancel_titles", [])
        if severity not in SEVERITIES or not isinstance(rationale, str) or not isinstance(titles, list):
            log.warning("Gemini triage answer failed validation; ignored: %r", answer)
            result.llm_status = "invalid"
            return
        result.used_llm = True
        result.rationale = rationale.strip()[:500]
        result.severity = _worse(result.severity, severity)  # can raise severity, never lower it
        by_title = {t.title: t for t in due}
        for title in titles:
            task = by_title.get(title)
            if task is None or task.category in _NEVER_CANCEL:
                log.warning("Gemini asked to cancel %r; rejected by guardrail.", title)
                continue
            if id(task) not in result.cancelled:
                result.cancelled[id(task)] = f"Postponed on AI advice: {pet.name} is unwell"
                result.cancelled_titles.append(title)
        agrees = severity == rules_severity and set(result.cancelled) == rules_cancelled
        result.llm_status = "agreed" if agrees else "differs"
        log.info("Gemini vs rules: %s (rules=%s, gemini=%s)", result.llm_status, rules_severity, severity)


# ---------------------------------------------------------------- Agent 2: Schedule optimizer
@dataclass
class OptimizerResult:
    rescheduled: dict[int, time] = field(default_factory=dict)
    cancelled: dict[int, str] = field(default_factory=dict)
    added: list[tuple[Pet, Task]] = field(default_factory=list)
    alerts: list[Alert] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    policies_found: int = 0  # missed doses we had a policy for (evidence for confidence)
    policies_missing: int = 0


class ScheduleOptimizerAgent:
    """Decides what to do with missed medication using the retrieved missed-dose policy.

    This agent is deliberately rule-only: dosing decisions should be predictable.
    """

    def __init__(self, kb: KnowledgeBase) -> None:
        self.kb = kb

    def run(self, owner: Owner, report: HealthReport | None, on_date, now: datetime) -> OptimizerResult:
        result = OptimizerResult()
        if report is None or not report.missed_doses:
            return result
        pet = owner.find_pet(report.pet_name)
        if pet is None:
            return result

        for missed in report.missed_doses:
            docs = self.kb.retrieve(missed.medication, species=pet.species, topic_prefix="medication", k=1)
            if not docs:
                result.policies_missing += 1
                log.warning("Optimizer: no policy for %r; leaving schedule alone.", missed.medication)
                result.alerts.append(Alert("warning",
                    f"No missed-dose guidance for '{missed.medication}'. Ask your vet before giving it."))
                continue
            doc = docs[0]
            result.policies_found += 1
            result.sources.append(doc["id"])
            hours_late = (now - missed.scheduled_at).total_seconds() / 3600
            task = self._find_task(pet, doc["medication"], missed.medication)

            if task is not None and not task.is_due(on_date) and task.completed_on == on_date:
                result.alerts.append(Alert("info",
                    f"{missed.medication} is already marked given today. Not giving it again."))
                continue

            if hours_late > doc["give_now_within_hours"]:
                if task is not None:
                    result.cancelled[id(task)] = "Too late to give safely; contact the vet first"
                result.alerts.append(Alert("warning",
                    f"{pet.name}'s {missed.medication} is {hours_late:.0f}h late (limit {doc['give_now_within_hours']}h). "
                    "Do NOT give it; contact the vet."))
                log.info("Optimizer: %s %.1fh late > limit -> contact vet (%s)", missed.medication, hours_late, doc["id"])
                continue

            slot = (now + timedelta(minutes=15)).replace(second=0, microsecond=0)
            slot_time = slot.time()
            if task is not None:
                result.rescheduled[id(task)] = slot_time  # reuse today's dose: no double dose
            else:
                new_task = Task(title=f"Give missed {missed.medication} dose", duration_minutes=5,
                                priority=Priority.HIGH, category="medication",
                                medication=missed.medication, fixed_time=slot_time)
                result.added.append((pet, new_task))
            result.alerts.append(Alert("info",
                f"Give {pet.name}'s {missed.medication} at {slot_time.strftime('%H:%M')} - ONE dose only, "
                "do not double up."))
            log.info("Optimizer: %s %.1fh late -> give at %s (%s)", missed.medication, hours_late,
                     slot_time.strftime("%H:%M"), doc["id"])
        return result

    @staticmethod
    def _find_task(pet: Pet, doc_medication: str, reported_name: str) -> Task | None:
        for task in pet.tasks:
            if task.category == "medication" and task.medication:
                name = task.medication.lower()
                if doc_medication in name or name in reported_name.lower() or reported_name.lower() in name:
                    return task
        return None


# ---------------------------------------------------------------- Agent 3: Notification
class NotificationAgent:
    """Merges the other agents' decisions into one `Adjustments` object for the scheduler."""

    def run(self, owner: Owner, report: HealthReport | None, triage: TriageResult,
            optimizer: OptimizerResult) -> Adjustments:
        adj = Adjustments()
        adj.cancelled = {**triage.cancelled, **optimizer.cancelled}
        adj.rescheduled = dict(optimizer.rescheduled)
        adj.added = list(optimizer.added)

        alerts = list(triage.alerts)
        by_id = {id(t): (p, t) for p, t in owner.all_tasks()}
        for task_id, reason in triage.cancelled.items():
            if task_id in by_id and not any(by_id[task_id][1].title in a.message for a in alerts):
                pet, task = by_id[task_id]
                alerts.append(Alert("warning", f"Cancel/postpone '{task.title}' for {pet.name}: {reason}"))
        alerts += optimizer.alerts
        if triage.rationale:
            alerts.append(Alert("info", f"Why: {triage.rationale}"))
        order = {"urgent": 0, "warning": 1, "info": 2}
        adj.alerts = sorted(alerts, key=lambda a: order[a.level])
        return adj
