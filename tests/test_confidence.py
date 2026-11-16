"""Tests for confidence scoring: the score must move for the reasons we say it moves."""
from datetime import date, datetime, time

import pytest

from main import build_sample_owner
from models import HealthReport, MissedDose, Priority, Task
from retriever import KnowledgeBase
from workflow import run_care_workflow

TODAY = date(2026, 11, 16)
NOW = datetime.combine(TODAY, time(10, 30))


@pytest.fixture(scope="module")
def kb():
    return KnowledgeBase()


class FakeLLM:
    available = True

    def __init__(self, answer):
        self.answer = answer

    def generate_json(self, prompt):
        return self.answer


def run(report, kb, llm=None, owner=None):
    return run_care_workflow(owner or build_sample_owner(), report, TODAY, NOW, 180, kb=kb, llm=llm).confidence


def two_vomits_and_pill(med="heartworm pill"):
    return HealthReport("Rex", {"vomiting": 2}, [MissedDose(med, datetime.combine(TODAY, time(8, 0)))])


def test_clean_match_offline_is_high(kb):
    c = run(two_vomits_and_pill(), kb)
    assert (c.score, c.label) == (0.95, "high")
    assert any("matched a guideline exactly" in r for r in c.reasons)


def test_unknown_symptom_lowers_confidence(kb):
    c = run(HealthReport("Rex", {"hiccups": 3}), kb)
    assert c.score == 0.55 and c.label == "medium"
    assert any("No guideline found for 'hiccups'" in r for r in c.reasons)


def test_unknown_symptom_and_medication_is_low_and_warns_owner(kb):
    rep = HealthReport("Rex", {"hiccups": 3}, [MissedDose("mystery elixir", datetime.combine(TODAY, time(8, 0)))])
    result = run_care_workflow(build_sample_owner(), rep, TODAY, NOW, 180, kb=kb)
    assert result.confidence.label == "low"
    assert any("not confident" in a.message for a in result.plan.alerts)


def test_unknown_pet_is_low(kb):
    assert run(HealthReport("Ghost", {"vomiting": 5}), kb).label == "low"


def test_gemini_agreeing_raises_confidence(kb):
    llm = FakeLLM({"severity": "see_vet", "cancel_titles": [], "rationale": "Agree."})
    assert run(two_vomits_and_pill(), kb, llm).score == 1.0


def test_gemini_differing_lowers_confidence(kb):
    llm = FakeLLM({"severity": "see_vet", "cancel_titles": ["Morning walk"], "rationale": "Rest."})
    c = run(two_vomits_and_pill(), kb, llm)
    assert c.score == 0.75 and c.label == "medium"
    assert any("differed" in r for r in c.reasons)


def test_invalid_gemini_answer_lowers_confidence(kb):
    c = run(two_vomits_and_pill(), kb, FakeLLM({"severity": "catastrophic"}))
    assert c.score == 0.85


def test_failed_safety_check_lowers_confidence(kb):
    owner = build_sample_owner()
    owner.find_pet("Rex").add_task(Task("Second pill", 5, Priority.HIGH, category="medication", medication="Heartworm"))
    c = run(None, kb, owner=owner)
    assert c.score == 0.7
    assert any("safety check found a problem" in r for r in c.reasons)


def test_score_always_between_zero_and_one(kb):
    reports = [None, HealthReport("Rex", {"a": 1, "b": 1, "c": 1}),
               HealthReport("Rex", {"vomiting": 9}, [MissedDose("x", datetime.combine(TODAY, time(8, 0)))])]
    for rep in reports:
        assert 0.0 <= run(rep, kb).score <= 1.0
