# PawPal+ AI: Smart Pet Care Assistant

## Original Project

This project extends **PawPal+**, my Module 2 Streamlit scheduler. PawPal+ let an owner enter their pets and care tasks (feeding, walks, medication, grooming), each with a duration, a priority and a daily or weekly recurrence. Its `Scheduler` fit the day's due tasks into the owner's time budget, highest priority first, and explained which tasks were scheduled or skipped. It had no knowledge of pet health, so it treated "walk the dog" the same whether or not the dog was sick.

## Title and Summary

**PawPal+ AI** adds an AI layer on top of that scheduler. The owner reports what happened today, for example "Rex vomited twice and missed his heartworm pill, and has grooming at 2 PM". Three cooperating agents look up veterinary guidance, then reshape the day: they cancel the grooming, flag "contact the vet", and move the pill to a safe time as a single dose.

**Why it matters:** A schedule that ignores a pet's health can cause real harm. It could stress a sick animal, double a medication dose, or schedule a toxic treat. Here the AI changes the plan itself, not just a message printed beside it, and it is built so that the AI cannot make things less safe than the written rules.

## Architecture Overview

The full diagram is in [`diagrams/architecture.mmd`](diagrams/architecture.mmd). Data flows in three stages:

1. **Input.** The owner supplies pets and tasks (`models.py`) and a `HealthReport` of symptoms, missed doses and notes.
2. **Process** (`workflow.py`):
   - **Retriever** (`retriever.py`) searches `knowledge_base/*.json` for symptom thresholds, missed-dose policies and toxic foods. This is the RAG step.
   - **Agent 1, Triage:** counts symptoms against the retrieved guideline, sets a severity (routine, monitor, see_vet, urgent), and cancels stressful or toxic tasks.
   - **Agent 2, Schedule Optimizer:** applies the retrieved missed-dose policy. It reschedules today's dose once, or says "too late, call the vet".
   - **Agent 3, Notification:** merges both decisions into one `Adjustments` object plus owner-facing alerts.
   - **Scheduler** (`scheduler.py`) applies the adjustments and builds the plan.
   - **Evaluator** (`verify_plan`) checks the finished plan. It looks for cancelled tasks still scheduled, double doses, and medication dropped for lack of time. Problems become warning alerts.
   - **Gemini** (`llm.py`, optional) gives Triage a second opinion, but only through guardrails (see Design Decisions).
3. **Output.** The final plan, the alerts, and a baseline plan without the AI, so the owner can see exactly what changed.

**Human and test checkpoints:**
- The owner reviews the plan and alerts and decides what to do.
- A vet makes the real call for `see_vet` and `urgent` cases.
- The developer audits `logs/pawpal_ai.log` and the `evaluate.py` report.
- The pytest suite and `evaluate.py` check the AI automatically.

## Setup Instructions

Requires Python 3.10 or newer.

1. Open a terminal in this folder (`applied-ai-system-project`).
2. Create and activate a virtual environment.
   - Windows: `python -m venv .venv` then `.venv\Scripts\activate`
   - macOS/Linux: `python -m venv .venv` then `source .venv/bin/activate`
3. Install dependencies: `pip install -r requirements.txt`
4. *(Optional)* Enable Gemini. Copy `.env.example` to `.env` and set `GEMINI_API_KEY=` to a key from https://aistudio.google.com/apikey. The default model is `gemini-2.5-flash`, and you can change it with `GEMINI_MODEL` in `.env`. **Skip this step to run fully offline.**
5. Run the demo: `python main.py` (add `--offline` to force offline rules).
6. Run the tests: `python -m pytest`
7. Run the reliability check: `python evaluate.py --offline` (add `--runs 5` for more repeats).

All decisions are logged to `logs/pawpal_ai.log`, which is created on first run.

## Sample Interactions

These are real outputs from offline mode. The owner has two pets, Rex (a dog) and Mochi (a cat). It's Monday 2026-11-16, and the time is 10:30.

### 1. The headline scenario: vomiting, missed pill, grooming at 2 PM

**Input:** Rex vomited twice and seems tired. The 08:00 heartworm pill was missed.

```
Plan WITHOUT the AI agents
  08:00-08:05  Rex: Heartworm pill
  ...
  14:00-15:00  Rex: Grooming appointment

Plan WITH the AI agents
  08:00-08:10  Rex: Breakfast
  08:10-08:20  Mochi: Feed Mochi
  08:20-08:50  Rex: Morning walk
  08:50-09:05  Mochi: Play session
  10:45-10:50  Rex: Heartworm pill  (rescheduled by the AI schedule optimizer)
  SKIPPED  Rex: Grooming appointment  (Postponed: Rex is unwell (see vet))

[WARNING] Contact the vet about Rex's vomiting (2x).
[WARNING] Cancel/postpone 'Grooming appointment' for Rex: Postponed: Rex is unwell (see vet)
[INFO] Give Rex's heartworm pill at 10:45 - ONE dose only, do not double up.
Severity: see_vet   Knowledge used: med-heartworm, sym-vomiting
```

The retrieved vomiting guideline says two episodes means "contact the vet and postpone stressful activities". The retrieved heartworm policy allows a catch-up dose within 72 hours and never a double dose. Breakfast and the pill stay scheduled because the AI is not allowed to cancel feeding or medication.

### 2. Same pet, only one vomit: no overreaction

**Input:** Rex vomited once.

```
Severity: monitor   Knowledge used: sym-vomiting
14:00 Rex: Grooming appointment      <- still scheduled, no alerts
```

The same guideline has a higher threshold for cancelling, so one episode only raises severity to "monitor". This shows the output depends on the retrieved data and not on keywords alone.

### 3. A toxic treat is added to the task list

**Input:** The owner adds the task "Grapes as a treat" for Rex. No health report.

```
SKIPPED Grapes as a treat (Unsafe for dogs: Grapes, raisins and currants can cause sudden kidney failure in dogs and cats.)
[URGENT] Task 'Grapes as a treat' removed: Grapes, raisins and currants can cause sudden kidney failure ... Do not feed. Call a vet or poison hotline immediately if eaten.
Knowledge used: toxic-grapes
```

### 4. The pill is too late to give safely

**Input:** The same missed 08:00 pill, but reported on 2026-11-20 at 10:00 (98 hours late).

```
SKIPPED Heartworm pill (Too late to give safely; contact the vet first)
[WARNING] Rex's heartworm pill is 98h late (limit 72h). Do NOT give it; contact the vet.
```

## Design Decisions

| Decision | Why | Trade-off |
|---|---|---|
| **Rules always run; Gemini can only add** | Medical safety should not depend on a model's mood. Gemini may add cancellations and a plain-language rationale, but it can never lower the severity. It also can't cancel medication or feeding. | The AI is more conservative and less "creative" than a free-running model. |
| **Offline fallback for everything** | Anyone can run, grade and test the project without an API key, and a network failure can't break the app. | There are two code paths to maintain. |
| **Agents return `Adjustments`; they never edit tasks** | Every AI decision shows up in the plan with a reason (cancelled tasks appear as skipped). The scheduler is the only code that builds a plan. | It needs one extra data class and a two-pass scheduler. |
| **Schedule Optimizer uses no LLM** | Dosing should be predictable and testable. The retrieved policy decides, so the same input gives the same answer. | It can't handle a medication that has no entry in the knowledge base. It says "ask your vet" instead. |
| **Reuse today's dose instead of adding one** | If the 08:00 pill is missed, the "Heartworm pill" task already exists. Moving it can't double the dose. A new task is only added when none exists. | Matching tasks to medications is by name, so odd names could fail to match. |
| **Verify step after planning** | A safety check on the finished plan catches bugs in the agents themselves. | It adds a little code, but nothing slows down. |
| **Small keyword retriever, not vector embeddings** | There are no extra dependencies, it runs offline, and every retrieval is explainable and deterministic. | It can miss a paraphrase ("puked" doesn't match "vomiting" unless it's in the keywords). |
| **Small hand-written knowledge base** | The thresholds are easy to read and change. Edit a JSON number and the plan changes. | The data is illustrative, **not veterinary advice**. |

## Testing Summary

**What worked**
- `python -m pytest` runs 23 offline tests, all passing. They cover retrieval and species filtering, each severity threshold, the "never cancel feeding or medication" rule, no double dosing, the too-late-pill path, toxic-food removal, and the verify step catching a deliberately created double dose.
- Gemini's guardrails are tested with a fake client. It returns an invalid severity, wrong types, a missing field, `None`, an attempt to cancel medication, and an attempt to lower severity. In every case the rules win.
- A test forces an agent to crash and confirms the user still gets the normal plan plus a warning.
- `python evaluate.py --offline` runs 7 labelled scenarios 3 times each. The result is **7/7 correct and 7/7 consistent** (identical output every run).

**What didn't, or isn't covered**
- **Gemini has not been tested live.** The Gemini path (`llm.py`) is only exercised through a fake client. Real calls may need prompt tuning. Any real failure falls back to the rules by design.
- Retrieval only matches the words in the keyword lists, so it won't recognise unlisted phrasing or misspellings.
- The evaluation scenarios were written by me, using the same rules that produce the answers. They show consistency and regression safety, but they are not independent proof of medical accuracy.
- Medication matching is by name. It also doesn't yet handle multi-dose-per-day medications or interactions between symptoms and medications (for example, vomiting after an NSAID).

**What I learned**
- Put the guardrails around the model, not inside the prompt. Validating the model's output and keeping deterministic rules as the floor made the system testable.
- Letting agents describe changes (`Adjustments`) and having one component apply them made the AI's effect visible and easy to verify.
- Testing the failure paths (bad AI answer, agent crash, unknown medication) found more design questions than testing the happy path.

## Project Layout

```
models.py        Owner, Pet, Task, HealthReport (plain data)
scheduler.py     builds the daily plan and applies Adjustments
retriever.py     knowledge-base search (RAG)
agents.py        Triage, Schedule Optimizer, Notification
workflow.py      the agent loop and verify_plan
llm.py           Gemini client with safe fallback
app_logging.py   logging setup
main.py          demo scenario
evaluate.py      reliability and consistency check
knowledge_base/  JSON reference data
tests/           pytest suite
diagrams/        architecture.mmd
```

*Not veterinary advice. Always contact a vet when in doubt. The Streamlit UI from PawPal+ is not yet connected to this version.*
