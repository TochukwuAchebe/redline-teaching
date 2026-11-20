# Sustained-load soak

[`soak.py`](soak.py) keeps a steady stream of reviews in flight for a fixed time against one server and records how long each takes, any failures, API latency while the queue is busy, and the server's memory (its process group, including LibreOffice) every 2 seconds. Decks are fictional and randomised (8 to 40 slides, a third of them with tables).

## 30 minutes, two reviews always in flight (23 October 2026, local)

The release code with the production capacity settings: durable queue on PostgreSQL, Blob Storage through the Azurite emulator, one queue worker (`MAX_CONCURRENT_JOBS=1`), four database connections. The agent was off, so this measures Redline's own pipeline (upload, extraction, rules, LibreOffice rendering, storage, queue), not the model provider. Machine: Apple M4 Pro, 12 cores, 24 GB; LibreOffice 26.8. Raw result: [2026-10-23-local/result.json](2026-10-23-local/result.json).

| Measure | Result |
|---|---|
| Reviews finished | **1,112** (22,176 slides), all `done` |
| Failures, errors, unfinished reviews | **0** |
| Time per review (upload to done) | median 3.2 s, 95th percentile 4.4 s |
| Throughput over time | median 0.19 s per slide in the first, middle and last thirds of the run (0.194, 0.194, 0.188): no slowdown |
| Server memory | median 227 MB in the first third, 161 MB in the last third, peak 229 MB: no growth |
| API while busy (852 probes each) | `/api/health` median 2 ms, 95th percentile 84 ms; `/api/decks` (the review list, which grew to 1,100+ reviews) median 34 ms, 95th percentile 402 ms, max 855 ms; no errors |

## What this does and doesn't show

- It shows the queue, storage and rendering stay stable under continuous use for 30 minutes: nothing failed, nothing got slower, memory didn't climb.
- It ran on a laptop, not the 1 vCPU Azure container, so absolute times are faster than production. [`../azure-capacity`](../azure-capacity/README.md) has the measured Azure burst and restart recovery.
- Model calls were off. With the agent on, review time is dominated by the provider (one call per slide with text) and its rate limits.
- One server and one worker. Multiple replicas and a separate worker pool are not tested here.

## Reproduce

Start a server with the settings above (for example `REDLINE_DURABLE_JOBS=1`, `DATABASE_URL`, `AZURE_STORAGE_CONNECTION_STRING`, `MAX_CONCURRENT_JOBS=1`, `SESSION_SECRET`, no `ANTHROPIC_API_KEY`), then:

```bash
SOAK_BASE=http://127.0.0.1:8050 SOAK_SECONDS=1800 SOAK_IN_FLIGHT=2 SOAK_PID=<server pid> \
  SESSION_SECRET=<server's secret> PYTHONPATH=backend python eval/soak/soak.py > result.json
```
