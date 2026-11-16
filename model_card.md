# Model Card: PawPal+ AI

## Model Details

- **System:** PawPal+ AI, a pet-care scheduler with an AI layer. It is not a single trained model. It is a pipeline of retrieval, rule-based agents and an optional LLM.
- **Components:**
  - Keyword retriever (`retriever.py`) over a hand-written JSON knowledge base.
  - Three agents: Triage, Schedule Optimizer and Notification (`agents.py`).
  - A plan verifier (`workflow.py`).
  - Optional Gemini second opinion (`llm.py`, default `gemini-3.5-flash-lite`).
- **Date of this card:** 2026-11-16.
- **Author:** Nirel Kadzo

## Intended Use

- **For:** A pet owner who reports symptoms and missed medication and wants a daily care plan adjusted. It is a coursework demo of RAG plus an agentic workflow.
- **Not for:** Diagnosing animals, replacing a vet, or dosing decisions beyond the sample missed-dose rules. The knowledge base is a small illustrative sample, **not veterinary advice**.

## How the AI Decides

- **Rules set the floor.** Symptom thresholds and missed-dose limits come from retrieved knowledge-base entries and always run.
- **Gemini can only add.** It may postpone extra tasks and write a rationale. It can never lower the severity, and it can never cancel medication or feeding tasks.
- **Everything Gemini returns is validated.** Invalid severity, wrong types or invented task names are discarded.
- **Fallback.** With no key or on any API failure, the system uses the offline rules.

## Testing Summary

### Evidence we have

| Method | Status | Result |
|---|---|---|
| Automated unit and integration tests | Done | `python -m pytest`: **32 passed**, all offline (23 for the pipeline, 9 for confidence scoring). |
| Reliability script | Done | `python evaluate.py`: 7 labelled scenarios, 3 runs each. Offline: **7/7 correct, 7/7 consistent**. With Gemini: **7/7 correct, 6/7 consistent**. |
| Logging and error handling | Done | `logs/pawpal_ai.log` records every decision, retrieval, rejected AI answer and API error. |
| Confidence scoring | Done | Every result carries a 0 to 1 score with a high, medium or low label and a list of reasons. See the section below. |
| Human evaluation | **Not done** | No person has reviewed the outputs yet. |

### What the automated tests cover

- **Retrieval:** Returns the right entry (for example "raisins" finds the grapes entry), respects the species filter, and returns nothing for unrelated queries.
- **Triage thresholds:** 1 vomit gives "monitor" with nothing cancelled. 2 vomits gives "see_vet" and grooming is cancelled. 4 vomits gives "urgent" and the walk is cancelled too.
- **Hard safety rules:** Feeding and medication tasks are never cancelled, even at 6 vomits.
- **Medication:** A missed pill is rescheduled once and never doubled. A pill that is too late is cancelled with a "contact the vet" alert. An unknown medication changes nothing and tells the owner to ask a vet. A missed dose with no matching task adds exactly one dose.
- **Toxic food:** A task titled "Grapes as a treat" is removed with an urgent alert.
- **Gemini guardrails** (fake client): an invalid severity, wrong types, a missing field and `None` all fall back to the rules. Gemini cannot cancel medication or invented tasks, and cannot lower the severity.
- **Verifier and failures:** The verifier catches a deliberately created double dose. An unknown pet gets an alert and no crash. A forced agent crash returns the normal plan plus a warning.

### Confidence scoring

The score comes from evidence the system already has, not from asking a model to rate itself. It starts at 0.95 and every change is listed in the result's `reasons` (`confidence.py`).

| Evidence | Effect |
|---|---|
| A symptom has no matching guideline | -0.40 each |
| A symptom matched a guideline by keyword only | -0.10 each |
| No missed-dose policy for a reported medication | -0.40 |
| Gemini agreed with the rules | +0.05 |
| Gemini differed from the rules (different severity or extra cancellations) | -0.20 |
| Gemini's answer failed validation | -0.10 |
| The plan's own safety check found a problem | -0.30 |
| Unknown pet | fixed 0.20 |

Labels: **high** at 0.80 or above, **medium** at 0.55 or above, **low** below that. A low score adds a warning telling the owner to check with a vet. The owner sees the score in the app ("How sure am I?") and the reasons in the "How was this decided?" expander.

**Does it track real uncertainty?** In the live Gemini run it did where we expected it to:

| Scenario | Offline | With Gemini |
|---|---|---|
| Two vomits + missed pill (the unstable one) | 0.95 | **0.75 to 1.00**, depending on whether Gemini agreed |
| Lethargy (Gemini added a walk cancellation) | 0.95 | 0.75 |
| Unknown pet | 0.20 | 0.20 |
| Healthy day | 1.00 | 1.00 |

Nine tests check each rule above (`tests/test_confidence.py`).

**Limits of the score:** The weights are hand-picked, not calibrated against real outcomes. A high score means "the system found its guidelines and nothing disagreed", not "this is medically correct". The score can't detect a wrong guideline.

### Live Gemini results

Run on 2026-11-16 with `gemini-3.5-flash-lite`:

- **Headline scenario** (2 vomits, missed heartworm pill, 2 PM grooming): Gemini added its own advice. It postponed the morning walk and wrote a plain-language rationale. The pill was still moved once, and breakfast and medication were untouched.
- **Consistency:** The "two vomits + missed pill" scenario was **unstable**. Across repeated runs Gemini sometimes cancelled the walk and sometimes did not. The other 6 scenarios were identical on every run.
- **Guardrails:** Severity never dropped below the rules' value, and no medication or feeding task was cancelled.

### Failures we hit and how the system responded

| Problem | Cause | System behaviour |
|---|---|---|
| 401 "API key not valid" | The key in `.env` was the wrong type (`AQ.` format, not an AI Studio `AIza` key). Vertex AI rejected it too. | Logged the error, used offline rules, and the app kept working. Fixed by creating a proper AI Studio key. |
| 404 model not found | `gemini-2.5-flash` was retired for new users. | Same fallback. Fixed by changing the default model. |
| 503 and 504 under load | `gemini-3.7-flash` and `gemini-3.8-flash` were overloaded. | Added one retry on temporary errors, then fallback. Switched the default to `gemini-3.5-flash-lite`, which answered in about 2 seconds. |

The app never crashed in any of these cases. The offline fallback and the log let us diagnose each problem from the log file.

## Limitations and Biases

**Coverage bias**
- **Species:** The system only knows dogs and cats. A rabbit, bird or reptile owner would get a plan with no health guidance, and that gap is easy to miss.
- **Content:** The knowledge base has four symptoms, four medication types, five toxic items and three breeds. It was written by me, not a vet, so its thresholds are illustrative and have had no clinical review.
- **Language:** Retrieval matches English keywords. "Threw up" works. "Puked" and misspellings don't, and then the system finds no guideline. Owners who describe symptoms in other words or other languages get weaker help.

**Behavioural bias**
- **Cautious by design:** Once a symptom count crosses a threshold, the system postpones tasks, even if the pet is fine. I chose over-caution because under-triage is the more dangerous error. The cost is that owners may skip tasks they didn't need to skip.
- **Owner input is trusted:** The system uses the symptom counts and missed-dose times the owner enters. An owner who didn't notice vomiting, or who guesses a time, gets advice based on wrong facts.
- **Gemini's biases are unknown:** Gemini's training data is outside my control, and its answers vary between runs. The rules limit what it can change, but they don't remove its biases from the rationale text.

**Technical limits**
- Medication is matched by name, so unusual names may not match.
- Multi-dose-per-day medication and interactions (for example vomiting after an NSAID) aren't modelled.
- The confidence weights are hand-picked, not calibrated.
- Pets and tasks aren't saved between sessions.
- My 7 evaluation scenarios use the same rules that produce the answers. They prove consistency, not medical accuracy.

## Ethical Considerations and Misuse

**Harm from wrong advice (the main risk).** A false "all clear" could delay vet care. Mitigations built in:
- The rules set a minimum severity, and Gemini can't lower it.
- `see_vet` and `urgent` results tell the owner to contact a vet and say the app can't diagnose.
- Low confidence adds a "check with your vet" warning.
- The system never schedules a second dose to catch up, and tells the owner not to give a dose that is past its limit.

**Ways it could be misused, and what limits them**

| Misuse | Prevention | Gap |
|---|---|---|
| Treating the app as a replacement for a vet | Disclaimers in the app and docs, vet-first alerts, low-confidence warnings | An owner who ignores the warnings can't be stopped. |
| Entering false symptoms or times to get a "give the pill now" answer | The dose policy has fixed limits and always says one dose only | The system can't verify what the owner types. |
| Editing the knowledge base to unsafe values | It's a plain JSON file that anyone can read and review | Anyone with file access can change it. There is no automatic clinical check. |
| Hostile text in the free-text notes field, since notes are sent to Gemini | Gemini's output is validated, it can't cancel medication or feeding, and the rules set the floor | **I did not test prompt injection.** The validation limits the damage, but the rationale text itself could still be manipulated. |
| Using it for someone else's pet or for livestock or exotic animals | Species filter returns no guidance for unsupported species | It just gives less help. It doesn't refuse. |

**Privacy.** The Gemini toggle sends the pet's symptoms, task names and the owner's notes to Google. The API key is kept in `.env`, which is gitignored, and the app works fully offline without it. The log file (`logs/pawpal_ai.log`) stores symptom counts and decisions locally, so anyone who shares that file shares the pet's health history.

## Reflection

### What surprised me while testing reliability

- **The plain rules were more reliable than the AI.** Offline, all 7 scenarios were identical on every run. With Gemini, one scenario changed between runs: sometimes it also postponed the morning walk, sometimes not. I expected the AI to be the strong part of the system. It turned out to be the least predictable part, which is why the rules set the floor.
- **Most of the "AI" failures weren't about intelligence.** What broke Gemini during testing was a wrong key type (401), a retired model name (404), and overloaded models (503 and 504). Handling failures well mattered more than getting clever answers.
- **The confidence score caught the instability on its own.** I hadn't tuned it to the unstable scenario, but it dropped to 0.75 exactly when Gemini disagreed with the rules.
- **Passing tests proved less than I thought.** 7/7 sounded good until I noticed I wrote the expected answers using the same rules that produce them.

### What this project taught me about AI and problem-solving

- Treat the model as a helper inside guardrails, not the decision-maker. The useful pattern was: rules decide the minimum, the model may add, and every model answer is validated before it can touch the plan.
- Make the AI's effect visible. Having agents return an `Adjustments` object, instead of editing tasks directly, meant every decision showed up in the plan with a reason, which made it easy to check.
- Test the failure paths. Bad AI answers, crashes and missing data raised more design questions than the happy path.
- "It works" needs a claim and evidence. Saying exactly what each test proves, and what it doesn't, is part of the work.

### Collaboration with AI

I built this with Claude Code, and I made the design calls. Examples:

**A suggestion I accepted: RAG plus the agentic loop.** The AI recommended combining a knowledge base with a three-agent chain (Triage, Schedule Optimizer, Notification), and I chose that option. It fit my brief's own example, and it put the AI inside the scheduling logic instead of printing advice beside it. It also gave me clear places to test, like the thresholds and the "no double dose" rule.

**A suggestion I did not accept as written: the backend and the scope.**
- **Backend:** The AI recommended the Claude API with an offline fallback. I chose the Gemini API with an offline fallback instead. The fallback part of its suggestion stayed, because it lets anyone run and test the project without a key.
- **Scope:** After I asked how to prove the AI works, the AI offered to build confidence scoring and a human-evaluation sheet together. I accepted the confidence scoring and held back human evaluation for now. Confidence scoring was code that the AI could build and test immediately. Human evaluation needs a real person's judgment, so it can't be faked, and I list it under Next Steps instead.

**A time the AI was wrong and I caught it.** The AI set the default model to `gemini-2.5-flash`, which Google had retired for new users. I read the error in the log, spotted the replacement name, and we updated it. Testing then showed that the model I named (`gemini-3.8-flash`) was overloaded, so we changed the default again to `gemini-3.5-flash-lite` because it answered in about 2 seconds. The lesson: check model names against the live API instead of trusting a default.

**What I'd do differently.** I'd write the evaluation scenarios from an outside source before building the rules, so the tests could actually disagree with me.

## Next Steps

1. **Human evaluation:** Have the owner, a peer or a vet rate 8 to 10 scenario outputs for safety and usefulness, and record the scores here.
2. **Independent test set:** Build scenarios from an outside source (a vet site or checklist) with expected answers written before running, including paraphrases like "puked".
3. **Retrieval:** Add synonyms or embeddings so unlisted wording is recognised.
4. **Consistency:** Investigate why Gemini varies between runs and decide whether to constrain its allowed cancellations further.
