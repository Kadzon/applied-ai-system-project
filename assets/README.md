# Execution Evidence

Real, unedited command output (apart from trimming the log excerpt and replacing the local project path with `<project>`). Each file starts with the command that produced it, the date, and the exit code.

| File | Command | What it shows |
|---|---|---|
| [pytest_output.txt](pytest_output.txt) | `python -m pytest -v` | 32 tests, all passing, offline |
| [evaluate_offline.txt](evaluate_offline.txt) | `python evaluate.py --offline` | 7 labelled scenarios x 3 runs: 7/7 correct, 7/7 consistent, with confidence scores |
| [evaluate_gemini.txt](evaluate_gemini.txt) | `python evaluate.py` | Same scenarios with Gemini: 7/7 correct, **6/7 consistent**. Exit code 1 is expected: one scenario varied between runs. The log lines show the guardrail rejecting Gemini's attempts to cancel feeding and medication. |
| [demo_main_offline.txt](demo_main_offline.txt) | `python main.py --offline` | The headline scenario: plan without and with the AI, alerts, confidence reasons |
| [demo_main_gemini.txt](demo_main_gemini.txt) | `python main.py` | The same scenario with Gemini, including its rationale |
| [decision_log_excerpt.txt](decision_log_excerpt.txt) | (excerpt of `logs/pawpal_ai.log`) | Real Gemini API failures (401, 404, 429) and one full workflow run, step by step |

## Reproduce

From the project folder, with the virtual environment active and dependencies installed (see the main README):

```powershell
python -m pytest -v
python evaluate.py --offline
python main.py --offline
```

These three give the same results every time. The two Gemini files need `GEMINI_API_KEY` in `.env`, and **their output can differ between runs** because the model's answers vary, which is the finding the model card describes. The log excerpt is trimmed from `logs/pawpal_ai.log`, which is created on first run and not committed.
