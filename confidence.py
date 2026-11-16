"""Confidence scoring: how sure is the system about today's advice, and why?

The score is built from evidence the system already has, not from asking a model to guess.
It starts at 0.95, subtracts for each weakness, and adds a little when Gemini independently
agrees. Every change is recorded in `reasons`, so the owner (and a reviewer) can see why.

    high    >= 0.80   guideline found and nothing disagreed
    medium  >= 0.55   some weakness, such as a keyword-only match or Gemini differing from the rules
    low      < 0.55   missing guidance or unknown pet, so the owner should check with a vet
"""
from __future__ import annotations

from dataclasses import dataclass, field

from agents import OptimizerResult, TriageResult
from models import HealthReport


@dataclass
class Confidence:
    score: float
    label: str  # "high" | "medium" | "low"
    reasons: list[str] = field(default_factory=list)


def label_for(score: float) -> str:
    return "high" if score >= 0.80 else "medium" if score >= 0.55 else "low"


def score_confidence(report: HealthReport | None, triage: TriageResult, optimizer: OptimizerResult) -> Confidence:
    if report is None:
        return Confidence(1.0, "high", ["No health report, so the normal plan only had a toxic-food safety scan."])
    if triage.unknown_pet:
        return Confidence(0.2, "low", [f"Pet '{report.pet_name}' is not in your list, so nothing could be assessed."])

    score, reasons = 0.95, []

    for symptom, match in triage.symptom_match.items():
        if match == "none":
            score -= 0.40
            reasons.append(f"No guideline found for '{symptom}' (-0.40).")
        elif match == "keyword":
            score -= 0.10
            reasons.append(f"'{symptom}' matched a guideline by keyword only (-0.10).")
    if triage.symptom_match and all(m == "exact" for m in triage.symptom_match.values()):
        reasons.append("Every symptom matched a guideline exactly.")

    if optimizer.policies_missing:
        score -= 0.40
        reasons.append(f"No missed-dose policy for {optimizer.policies_missing} medication(s) (-0.40).")
    elif optimizer.policies_found:
        reasons.append("Missed-dose policy found for every reported medication.")

    status = triage.llm_status
    if status == "agreed":
        score += 0.05
        reasons.append("Gemini agreed with the safety rules (+0.05).")
    elif status == "differs":
        score -= 0.20
        reasons.append("Gemini differed from the safety rules; the stricter result was used (-0.20).")
    elif status == "invalid":
        score -= 0.10
        reasons.append("Gemini's answer failed validation and was ignored (-0.10).")
    elif status == "unavailable":
        reasons.append("Gemini was unavailable; the safety rules alone were used.")
    else:
        reasons.append("Gemini was off; the safety rules alone were used.")

    score = round(max(0.0, min(1.0, score)), 2)
    return Confidence(score, label_for(score), reasons)
