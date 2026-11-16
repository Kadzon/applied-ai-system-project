"""The agentic loop: Triage -> Optimize -> Notify -> build plan -> verify the plan.

The verify step is the "check its own work" part. If the finished plan breaks a safety
rule, the workflow fixes it (or alerts the owner) before returning.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime

from agents import NotificationAgent, OptimizerResult, ScheduleOptimizerAgent, TriageAgent, TriageResult
from confidence import Confidence, label_for, score_confidence
from models import HealthReport, Owner
from retriever import KnowledgeBase
from scheduler import Adjustments, Alert, Plan, Scheduler

log = logging.getLogger(__name__)


@dataclass
class WorkflowResult:
    baseline_plan: Plan  # what the plan would be with no AI involvement
    plan: Plan  # the final, AI-adjusted plan
    triage: TriageResult
    optimizer: OptimizerResult
    issues: list[str] = field(default_factory=list)  # problems the verify step caught
    confidence: Confidence = field(default_factory=lambda: Confidence(0.0, "low", ["AI did not run."]))

    @property
    def sources(self) -> list[str]:
        return sorted(set(self.triage.sources + self.optimizer.sources))


def verify_plan(plan: Plan, adj: Adjustments) -> list[str]:
    """Return a list of safety problems in the plan (empty list = plan is safe)."""
    problems = []
    cancelled_ids = set(adj.cancelled)
    doses: dict[tuple[str, str], int] = {}
    for item in plan.scheduled:
        if id(item.task) in cancelled_ids:
            problems.append(f"Cancelled task '{item.task.title}' is still scheduled.")
        if item.task.category == "medication":
            key = (item.pet.name, (item.task.medication or "").lower())
            doses[key] = doses.get(key, 0) + 1
    for (pet_name, med), count in doses.items():
        if count > 1:
            problems.append(f"{pet_name} is scheduled for {count} doses of {med} (possible double dose).")
    for item in plan.skipped:
        if item.task.category == "medication" and "budget" in item.reason:
            problems.append(f"Medication '{item.task.title}' was dropped for lack of time; make time for it.")
    return problems


def run_care_workflow(
    owner: Owner,
    report: HealthReport | None,
    on_date: date,
    now: datetime,
    time_budget_minutes: int,
    kb: KnowledgeBase | None = None,
    llm=None,
    scheduler: Scheduler | None = None,
) -> WorkflowResult:
    kb = kb or KnowledgeBase()
    scheduler = scheduler or Scheduler()
    log.info("Workflow start: owner=%s date=%s report=%s", owner.name, on_date, report)

    baseline = scheduler.build_plan(owner, on_date, time_budget_minutes)

    try:
        triage = TriageAgent(kb, llm).run(owner, report, on_date)
        optimizer = ScheduleOptimizerAgent(kb).run(owner, report, on_date, now)
        adjustments = NotificationAgent().run(owner, report, triage, optimizer)
    except Exception:  # noqa: BLE001 - guardrail: an agent bug must never take the schedule down
        log.exception("Agent pipeline failed; returning the unmodified plan.")
        failed = Alert("warning", "The AI assistant hit an error. Showing your normal plan; "
                                  "please call your vet if your pet seems unwell.")
        baseline.alerts.append(failed)
        return WorkflowResult(baseline, baseline, TriageResult(), OptimizerResult(), ["agent pipeline error"])

    plan = scheduler.build_plan(owner, on_date, time_budget_minutes, adjustments)
    issues = verify_plan(plan, adjustments)
    for issue in issues:
        log.error("Plan verification failed: %s", issue)
        plan.alerts.append(Alert("warning", f"Check needed: {issue}"))
    confidence = score_confidence(report, triage, optimizer)
    if issues:  # a failed safety check always lowers trust
        confidence.score = round(max(0.0, confidence.score - 0.30), 2)
        confidence.label = label_for(confidence.score)
        confidence.reasons.append("The plan's own safety check found a problem (-0.30).")
    if confidence.label == "low":
        plan.alerts.append(Alert("warning", f"I'm not confident about this advice ({confidence.score:.2f}). "
                                            "Please check with your vet."))
    log.info("Confidence: %.2f (%s) - %s", confidence.score, confidence.label, "; ".join(confidence.reasons))
    log.info("Workflow done: severity=%s scheduled=%d skipped=%d issues=%d",
             triage.severity, len(plan.scheduled), len(plan.skipped), len(issues))
    return WorkflowResult(baseline, plan, triage, optimizer, issues, confidence)
