"""Scheduling logic: turns an Owner's due tasks into a daily Plan.

The AI agents never edit tasks directly. They describe changes in an `Adjustments`
object, and `Scheduler.build_plan` applies it. That keeps every AI decision visible
in the plan (cancelled tasks show up as skipped, with the reason).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from models import Owner, Pet, Priority, Task

_PRIORITY_ORDER = {Priority.HIGH: 0, Priority.MEDIUM: 1, Priority.LOW: 2}


@dataclass
class Alert:
    level: str  # "urgent" | "warning" | "info"
    message: str


@dataclass
class Adjustments:
    """Changes the AI agents want made to today's plan. Tasks are keyed by id(task)."""

    cancelled: dict[int, str] = field(default_factory=dict)  # id(task) -> reason
    rescheduled: dict[int, time] = field(default_factory=dict)  # id(task) -> new fixed time
    added: list[tuple[Pet, Task]] = field(default_factory=list)  # brand-new tasks for today
    alerts: list[Alert] = field(default_factory=list)


@dataclass
class ScheduledTask:
    task: Task
    pet: Pet
    start_time: time
    end_time: time
    reason: str


@dataclass
class SkippedTask:
    task: Task
    pet: Pet
    reason: str


@dataclass
class Plan:
    date: date
    scheduled: list[ScheduledTask] = field(default_factory=list)
    skipped: list[SkippedTask] = field(default_factory=list)
    alerts: list[Alert] = field(default_factory=list)
    total_minutes_used: int = 0


class Scheduler:
    def __init__(self, day_start: time = time(8, 0)) -> None:
        self.day_start = day_start

    def build_plan(
        self,
        owner: Owner,
        on_date: date,
        time_budget_minutes: int,
        adjustments: Adjustments | None = None,
    ) -> Plan:
        adj = adjustments or Adjustments()
        plan = Plan(date=on_date, alerts=list(adj.alerts))

        candidates = [pair for pair in owner.all_tasks() if pair[1].is_due(on_date)]
        candidates += adj.added
        candidates.sort(key=lambda p: (_PRIORITY_ORDER[p[1].priority], p[1].duration_minutes))

        # Pass 1: decide who is in (AI cancellations first, then the time budget).
        accepted: list[tuple[Pet, Task, str]] = []
        used = 0
        for pet, task in candidates:
            if id(task) in adj.cancelled:
                plan.skipped.append(SkippedTask(task, pet, adj.cancelled[id(task)]))
            elif used + task.duration_minutes > time_budget_minutes:
                plan.skipped.append(SkippedTask(task, pet, "Not enough time left in today's budget"))
            else:
                used += task.duration_minutes
                reason = f"{task.priority.value} priority, fit within remaining time"
                if id(task) in adj.rescheduled:
                    reason = "rescheduled by the AI schedule optimizer"
                elif id(task) in {id(t) for _, t in adj.added}:
                    reason = "added by the AI schedule optimizer"
                accepted.append((pet, task, reason))
        plan.total_minutes_used = used

        # Pass 2: place them. Timed tasks keep their time; the rest fill gaps around them.
        def fixed_start(task: Task) -> time | None:
            return adj.rescheduled.get(id(task), task.fixed_time)

        fixed_blocks = []  # (start datetime, end datetime)
        for pet, task, reason in accepted:
            start_t = fixed_start(task)
            if start_t is not None:
                start = datetime.combine(on_date, start_t)
                end = start + timedelta(minutes=task.duration_minutes)
                fixed_blocks.append((start, end))
                plan.scheduled.append(ScheduledTask(task, pet, start.time(), end.time(), reason))

        clock = datetime.combine(on_date, self.day_start)
        for pet, task, reason in accepted:
            if fixed_start(task) is not None:
                continue
            end = clock + timedelta(minutes=task.duration_minutes)
            for f_start, f_end in sorted(fixed_blocks):  # jump past any timed task we'd overlap
                if clock < f_end and end > f_start:
                    clock = f_end
                    end = clock + timedelta(minutes=task.duration_minutes)
            plan.scheduled.append(ScheduledTask(task, pet, clock.time(), end.time(), reason))
            clock = end

        plan.scheduled.sort(key=lambda s: s.start_time)
        return plan
