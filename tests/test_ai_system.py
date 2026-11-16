"""Tests for retrieval, the three agents, the guardrails, and the full workflow (all offline)."""
from datetime import date, datetime, time

import pytest

from agents import SEVERITIES
from main import build_sample_owner
from models import HealthReport, MissedDose, Priority, Task
from retriever import KnowledgeBase
from workflow import run_care_workflow, verify_plan

TODAY = date(2026, 11, 16)  # a Monday


@pytest.fixture(scope="module")
def kb():
    return KnowledgeBase()


def report(vomiting=0, late_hours=2.5, med="heartworm pill"):
    missed = datetime.combine(TODAY, time(8, 0))
    return HealthReport(
        pet_name="Rex",
        symptoms={"vomiting": vomiting} if vomiting else {},
        missed_doses=[MissedDose(med, missed)] if late_hours is not None else [],
    )


def run(rep, kb, llm=None, owner=None, now=None):
    return run_care_workflow(owner or build_sample_owner(), rep, TODAY,
                             now or datetime.combine(TODAY, time(10, 30)), 180, kb=kb, llm=llm)


def scheduled_titles(plan):
    return [s.task.title for s in plan.scheduled]


def skipped_titles(plan):
    return [s.task.title for s in plan.skipped]


# ------------------------------------------------------------ retrieval
def test_retrieval_finds_relevant_entry(kb):
    assert kb.retrieve("my dog ate raisins", species="dog")[0]["id"] == "toxic-grapes"


def test_retrieval_respects_species_filter(kb):
    ids = [d["id"] for d in kb.retrieve("lily", species="dog")]
    assert "toxic-lily" not in ids


def test_retrieval_returns_nothing_for_unrelated_query(kb):
    assert kb.retrieve("quantum physics lecture") == []


# ------------------------------------------------------------ the retrieved data changes behavior
def test_one_vomit_does_not_cancel_grooming(kb):
    result = run(report(vomiting=1, late_hours=None), kb)
    assert "Grooming appointment" in scheduled_titles(result.plan)
    assert result.triage.severity == "monitor"


def test_two_vomits_cancel_grooming_and_alert_vet(kb):
    result = run(report(vomiting=2, late_hours=None), kb)
    assert "Grooming appointment" in skipped_titles(result.plan)
    assert result.triage.severity == "see_vet"
    assert any("Contact the vet" in a.message for a in result.plan.alerts)
    assert "sym-vomiting" in result.sources


def test_four_vomits_is_urgent_and_cancels_walk(kb):
    result = run(report(vomiting=4, late_hours=None), kb)
    assert result.triage.severity == "urgent"
    assert "Morning walk" in skipped_titles(result.plan)


def test_feeding_and_medication_are_never_cancelled_by_triage(kb):
    result = run(report(vomiting=6, late_hours=None), kb)
    assert "Breakfast" in scheduled_titles(result.plan)
    assert "Heartworm pill" in scheduled_titles(result.plan)


# ------------------------------------------------------------ medication optimizer
def test_missed_pill_is_rescheduled_not_doubled(kb):
    result = run(report(vomiting=0), kb)
    assert scheduled_titles(result.plan).count("Heartworm pill") == 1
    pill = next(s for s in result.plan.scheduled if s.task.title == "Heartworm pill")
    assert pill.start_time == time(10, 45)
    assert result.issues == []


def test_pill_too_late_is_cancelled_with_vet_alert(kb):
    far_future = datetime(2026, 11, 20, 10, 0)  # ~4 days after the missed dose, limit is 72h
    result = run(report(vomiting=0), kb, now=far_future)
    assert "Heartworm pill" in skipped_titles(result.plan)
    assert any("Do NOT give" in a.message for a in result.plan.alerts)


def test_unknown_medication_makes_no_change_and_asks_vet(kb):
    result = run(report(med="mystery elixir"), kb)
    assert any("Ask your vet" in a.message for a in result.plan.alerts)
    assert scheduled_titles(result.plan).count("Heartworm pill") == 1


def test_missed_dose_with_no_matching_task_adds_one_dose(kb):
    owner = build_sample_owner()
    owner.find_pet("Rex").tasks = [t for t in owner.find_pet("Rex").tasks if t.category != "medication"]
    result = run(report(), kb, owner=owner)
    assert scheduled_titles(result.plan).count("Give missed heartworm pill dose") == 1


# ------------------------------------------------------------ toxic-food guardrail via RAG
def test_toxic_treat_task_is_removed(kb):
    owner = build_sample_owner()
    owner.find_pet("Rex").add_task(Task("Grapes as a treat", 5, Priority.LOW, category="feeding"))
    result = run(None, kb, owner=owner)
    assert "Grapes as a treat" in skipped_titles(result.plan)
    assert result.plan.alerts[0].level == "urgent"


# ------------------------------------------------------------ Gemini guardrails (fake LLM)
class FakeLLM:
    available = True

    def __init__(self, answer):
        self.answer = answer

    def generate_json(self, prompt):
        return self.answer


def test_llm_can_add_valid_cancellation_and_rationale(kb):
    llm = FakeLLM({"severity": "monitor", "cancel_titles": ["Morning walk"], "rationale": "Rest today."})
    result = run(report(vomiting=2, late_hours=None), kb, llm=llm)
    assert "Morning walk" in skipped_titles(result.plan)
    assert result.triage.used_llm and "Rest today." in [a.message[5:] for a in result.plan.alerts if a.message.startswith("Why:")]


def test_llm_cannot_lower_severity(kb):
    llm = FakeLLM({"severity": "routine", "cancel_titles": [], "rationale": "All fine."})
    result = run(report(vomiting=4, late_hours=None), kb, llm=llm)
    assert result.triage.severity == "urgent"


def test_llm_cannot_cancel_medication_or_invent_tasks(kb):
    llm = FakeLLM({"severity": "urgent", "cancel_titles": ["Heartworm pill", "Breakfast", "Nonexistent"],
                   "rationale": "x"})
    result = run(report(vomiting=1, late_hours=None), kb, llm=llm)
    assert "Heartworm pill" in scheduled_titles(result.plan)
    assert "Breakfast" in scheduled_titles(result.plan)


@pytest.mark.parametrize("bad", [{"severity": "catastrophic", "cancel_titles": [], "rationale": "x"},
                                 {"severity": "urgent", "cancel_titles": "walk", "rationale": "x"},
                                 {"severity": "urgent"}, None])
def test_invalid_llm_answer_falls_back_to_rules(kb, bad):
    result = run(report(vomiting=2, late_hours=None), kb, llm=FakeLLM(bad))
    assert result.triage.severity == "see_vet"
    assert "Grooming appointment" in skipped_titles(result.plan)
    assert not result.triage.used_llm


# ------------------------------------------------------------ verification + error handling
def test_verify_plan_catches_double_dose(kb):
    owner = build_sample_owner()
    rex = owner.find_pet("Rex")
    rex.add_task(Task("Second pill", 5, Priority.HIGH, category="medication", medication="Heartworm"))
    result = run(None, kb, owner=owner)
    problems = verify_plan(result.plan, adj=type("A", (), {"cancelled": {}})())
    assert any("double dose" in p for p in problems)


def test_unknown_pet_is_reported_not_crashed(kb):
    result = run(HealthReport(pet_name="Ghost", symptoms={"vomiting": 5}), kb)
    assert any("unknown pet" in a.message for a in result.plan.alerts)


def test_agent_crash_returns_baseline_plan_with_warning(kb, monkeypatch):
    import workflow

    def boom(*a, **k):
        raise RuntimeError("bug")

    monkeypatch.setattr(workflow.TriageAgent, "run", boom)
    result = run(report(vomiting=2), kb)
    assert result.plan is result.baseline_plan
    assert any("hit an error" in a.message for a in result.plan.alerts)


def test_severity_scale_is_ordered():
    assert SEVERITIES == ["routine", "monitor", "see_vet", "urgent"]
