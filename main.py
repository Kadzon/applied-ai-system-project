"""Demo: a dog threw up twice, missed the morning heartworm pill, and has grooming at 2:00 PM.

Run:  python main.py            (uses Gemini if GEMINI_API_KEY is set, otherwise offline rules)
      python main.py --offline  (force offline rules)
"""
from __future__ import annotations

import sys
from datetime import date, datetime, time

from app_logging import setup_logging
from llm import GeminiClient
from models import Frequency, HealthReport, MissedDose, Owner, Pet, Priority, Task
from scheduler import Plan
from workflow import run_care_workflow


def build_sample_owner() -> Owner:
    owner = Owner(name="Jordan")
    rex = Pet(name="Rex", species="dog", breed="golden retriever")
    rex.add_task(Task("Breakfast", 10, Priority.HIGH, category="feeding"))
    rex.add_task(Task("Heartworm pill", 5, Priority.HIGH, category="medication", medication="heartworm",
                      fixed_time=time(8, 0)))
    rex.add_task(Task("Morning walk", 30, Priority.MEDIUM, category="walk"))
    rex.add_task(Task("Grooming appointment", 60, Priority.MEDIUM, category="grooming", fixed_time=time(14, 0)))
    mochi = Pet(name="Mochi", species="cat", breed="siamese")
    mochi.add_task(Task("Feed Mochi", 10, Priority.HIGH, category="feeding"))
    mochi.add_task(Task("Play session", 15, Priority.LOW, category="play",
                        frequency=Frequency.WEEKLY, weekdays={0, 2, 4}))
    owner.add_pet(rex)
    owner.add_pet(mochi)
    return owner


def print_plan(title: str, plan: Plan) -> None:
    print(f"\n=== {title} ===")
    for s in plan.scheduled:
        print(f"  {s.start_time:%H:%M}-{s.end_time:%H:%M}  {s.pet.name}: {s.task.title}  ({s.reason})")
    for s in plan.skipped:
        print(f"  SKIPPED  {s.pet.name}: {s.task.title}  ({s.reason})")


def main() -> None:
    setup_logging()
    owner = build_sample_owner()
    today = date(2026, 11, 16)
    now = datetime.combine(today, time(10, 30))
    report = HealthReport(
        pet_name="Rex",
        symptoms={"vomiting": 2},
        missed_doses=[MissedDose("heartworm pill", datetime.combine(today, time(8, 0)))],
        notes="Threw up twice this morning, seems a bit tired.",
    )
    llm = None if "--offline" in sys.argv else GeminiClient()
    print(f"AI mode: {'Gemini + rules' if llm and llm.available else 'offline rules'}")

    result = run_care_workflow(owner, report, today, now, time_budget_minutes=180, llm=llm)

    print_plan("Plan WITHOUT the AI agents", result.baseline_plan)
    print_plan("Plan WITH the AI agents", result.plan)
    print("\n=== Alerts ===")
    for alert in result.plan.alerts:
        print(f"  [{alert.level.upper()}] {alert.message}")
    print(f"\nSeverity: {result.triage.severity}   Knowledge used: {', '.join(result.sources) or 'none'}")
    print(f"Safety check issues: {result.issues or 'none'}")


if __name__ == "__main__":
    main()
