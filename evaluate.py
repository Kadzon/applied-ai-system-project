"""Reliability check: runs labelled scenarios several times and reports accuracy + consistency.

    python evaluate.py --offline     # rules only (always reproducible)
    python evaluate.py               # also uses Gemini if GEMINI_API_KEY is set
    python evaluate.py --runs 5

Each scenario has an expected severity and expected cancelled tasks. Consistency means
every repeat of a scenario produced the identical outcome.
"""
from __future__ import annotations

import sys
from datetime import date, datetime, time

from app_logging import setup_logging
from llm import GeminiClient
from main import build_sample_owner
from models import HealthReport, MissedDose
from retriever import KnowledgeBase
from workflow import run_care_workflow

TODAY = date(2026, 11, 16)
NOW = datetime.combine(TODAY, time(10, 30))
MISSED_8AM = MissedDose("heartworm pill", datetime.combine(TODAY, time(8, 0)))

# (name, report, expected severity, expected skipped task titles)
SCENARIOS = [
    ("healthy day", None, "routine", set()),
    ("one vomit", HealthReport("Rex", {"vomiting": 1}), "monitor", set()),
    ("two vomits + missed pill", HealthReport("Rex", {"vomiting": 2}, [MISSED_8AM]), "see_vet",
     {"Grooming appointment"}),
    ("four vomits", HealthReport("Rex", {"vomiting": 4}), "urgent", {"Grooming appointment", "Morning walk"}),
    ("lethargy", HealthReport("Rex", {"lethargy": 1}), "monitor", {"Grooming appointment"}),
    ("cat not eating twice", HealthReport("Mochi", {"not_eating": 2}), "see_vet", set()),
    ("unknown pet", HealthReport("Ghost", {"vomiting": 5}), "routine", set()),
]


def outcome(result) -> tuple:
    return (result.triage.severity, frozenset(s.task.title for s in result.plan.skipped))


def main() -> None:
    setup_logging()
    runs = int(sys.argv[sys.argv.index("--runs") + 1]) if "--runs" in sys.argv else 3
    llm = None if "--offline" in sys.argv else GeminiClient()
    kb = KnowledgeBase()
    print(f"Mode: {'Gemini + rules' if llm and llm.available else 'offline rules'}   runs per scenario: {runs}\n")

    correct = consistent = 0
    for name, report, exp_sev, exp_skipped in SCENARIOS:
        outcomes = [outcome(run_care_workflow(build_sample_owner(), report, TODAY, NOW, 180, kb=kb, llm=llm))
                    for _ in range(runs)]
        is_consistent = len(set(outcomes)) == 1
        sev, skipped = outcomes[0]
        # Gemini may only add cancellations, so expected tasks must be a subset; severity must be >= expected.
        is_correct = all(
            o[0] == exp_sev and set(o[1]) == exp_skipped if not (llm and llm.available)
            else o[0] in (exp_sev, "see_vet", "urgent") and exp_skipped <= set(o[1])
            for o in outcomes
        )
        correct += is_correct
        consistent += is_consistent
        print(f"{'PASS' if is_correct else 'FAIL'}  {'stable' if is_consistent else 'UNSTABLE':8} {name:28} "
              f"-> {sev}, skipped={sorted(skipped)}")

    total = len(SCENARIOS)
    print(f"\nAccuracy: {correct}/{total}   Consistency: {consistent}/{total}")
    sys.exit(0 if correct == total and consistent == total else 1)


if __name__ == "__main__":
    main()
