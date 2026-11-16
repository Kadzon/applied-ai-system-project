"""Domain models for PawPal+ AI: Owner, Pet, Task, plus the health report the AI agents read.

No scheduling logic and no AI logic lives here, only plain data.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
from enum import Enum


class Priority(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Frequency(Enum):
    DAILY = "daily"
    WEEKLY = "weekly"


# The categories a task can have. The AI agents use these to decide what is safe to cancel.
CATEGORIES = {"feeding", "walk", "medication", "grooming", "appointment", "play", "general"}


@dataclass
class Task:
    title: str
    duration_minutes: int
    priority: Priority
    frequency: Frequency = Frequency.DAILY
    weekdays: set[int] = field(default_factory=set)  # 0=Monday..6=Sunday; only used when WEEKLY
    completed_on: date | None = None
    category: str = "general"
    fixed_time: time | None = None  # appointments / timed meds. None = scheduler picks the time
    medication: str | None = None  # name of the medicine, if category == "medication"

    def __post_init__(self) -> None:
        if self.frequency == Frequency.WEEKLY and not self.weekdays:
            raise ValueError("A weekly task needs at least one weekday selected.")
        if self.duration_minutes <= 0:
            raise ValueError("A task needs a duration greater than zero.")
        if self.category not in CATEGORIES:
            raise ValueError(f"Unknown category '{self.category}'. Use one of {sorted(CATEGORIES)}.")
        if self.category == "medication" and not self.medication:
            raise ValueError("A medication task needs a medication name.")

    def is_due(self, on_date: date) -> bool:
        """Return whether this task needs doing on the given date."""
        if self.completed_on == on_date:
            return False
        if self.frequency == Frequency.DAILY:
            return True
        return on_date.weekday() in self.weekdays

    def mark_completed(self, on_date: date) -> None:
        self.completed_on = on_date

    def mark_incomplete(self) -> None:
        self.completed_on = None


@dataclass
class Pet:
    name: str
    species: str  # "dog", "cat", ...
    breed: str = ""
    tasks: list[Task] = field(default_factory=list)

    def add_task(self, task: Task) -> None:
        self.tasks.append(task)


@dataclass
class Owner:
    name: str
    pets: list[Pet] = field(default_factory=list)

    def add_pet(self, pet: Pet) -> None:
        self.pets.append(pet)

    def all_tasks(self) -> list[tuple[Pet, Task]]:
        """Every task across every pet, each paired with the pet it belongs to."""
        return [(pet, task) for pet in self.pets for task in pet.tasks]

    def find_pet(self, name: str) -> Pet | None:
        for pet in self.pets:
            if pet.name.lower() == name.strip().lower():
                return pet
        return None


@dataclass
class MissedDose:
    medication: str
    scheduled_at: datetime  # when the dose should have been given


@dataclass
class HealthReport:
    """What the owner tells the app happened today."""

    pet_name: str
    symptoms: dict[str, int] = field(default_factory=dict)  # e.g. {"vomiting": 2}
    missed_doses: list[MissedDose] = field(default_factory=list)
    notes: str = ""
