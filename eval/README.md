# Evaluation

How to measure whether Redline is right, using only fictional decks you can rebuild yourself.

## What's here

| Evaluation | What it measures | Run it |
|---|---|---|
| **Demo decks** (`samples/`, `backend/tests/test_demo_decks.py`) | Every planted issue in `demo-with-issues.pptx` is caught by code; `demo-clean.pptx` raises nothing; applied fixes disappear on re-check | `PYTHONPATH=backend pytest backend/tests/test_demo_decks.py` |
| **AI writing review** (`ai/`) | A generated deck with 10 planted writing problems and 8 clean slides: what the model finds, what it wrongly flags, and how many of its answers were dropped by verification | `PYTHONPATH=backend python eval/ai/run_ai_eval.py` (needs `ANTHROPIC_API_KEY`) |
| **Sustained load** (`soak/`) | Reviews always in flight for 30 minutes: failures, time per review, memory | see [soak/README.md](soak/README.md) |
| **Queue and recovery** (`local-capacity/`) | Queue limits, cancellation and restart recovery against a local PostgreSQL | see [local-capacity/README.md](local-capacity/README.md) |

## Current results

| Measure | Result |
|---|---|
| Demo deck, planted issues caught by code | **13 of 13** code-checkable issues (the 14th, bullet parallelism, is for the AI review) |
| Demo clean deck | **0** findings |
| Seeded AI deck ([ai/report.md](ai/report.md)) | 10 of 10 planted problems found; no flags on the 8 clean slides. Measured with prompt version `2026-10-22.5`; the prompt's examples have since been reworded for this public version, so re-run it before relying on the figure |
| 30-minute soak ([soak/README.md](soak/README.md)) | 1,112 reviews, 0 failures, no slowdown or memory growth |

## How to think about accuracy

- **Test the clean case as hard as the broken one.** A rule that fires on a compliant slide is a false flag, and false flags are what make reviewers stop trusting a tool.
- **Plant one issue per rule, and write the answer key before you score.** `samples/demo-answer-key.yaml` was written from what the generator planted, not from what Redline found.
- **AI output varies between runs.** Treat one live run as an observation. Keep planted problems and clean controls as regression checks, and re-run after any prompt change (and bump `PROMPT_VERSION`, which also clears cached results).
- **Your own adjudication is not independent.** The strongest evidence is reviewers scoring the tool on their own decks.
