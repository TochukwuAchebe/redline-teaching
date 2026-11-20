# Local queue, recovery and responsiveness measurement

Executed against code `bfee38f`, using the real HTTP API, dedicated local PostgreSQL and LibreOffice. AI was disabled and files used the local object adapter. All decks and database records were newly generated test data. No Azure resources, existing reviews or provider APIs were targeted.

## Result

| Measure | Observed result |
|---|---|
| Completed baseline | 12-slide synthetic deck in 2.94 seconds |
| Queue bound | Four reviews admitted with workers paused; a fifth returned HTTP 409 with the queue-full explanation |
| Waiting cancellation | Cancelled review remained cancelled with no result throughout the run |
| API restart | Stable signed session, completed review and queued work survived a real API-process restart |
| Worker interruption | Process interrupted during rendering; replacement recovered the review through the unchanged 60-second lease |
| Recovery time | 62.34 seconds from interruption to observing that review complete; database confirms two claims |
| Concurrent decisions | 32 distinct real findings accepted concurrently while the replacement worker served the queue; all retained after recovery |
| Remaining work | All three non-cancelled queued reviews completed, each with all 12 slides rendered |
| Recovered previews | All 12 preview endpoints for the interrupted review returned PNG content |
| HTTP responsiveness | 508 successful measured responses; median 8.41 ms, lower-order p95 44.98 ms, maximum 53.40 ms |
| Expected rejection | One HTTP 409, for deliberately exceeding the configured queue bound |
| Total elapsed | 67.87 seconds, including setup, restarts, lease recovery and cleanup |

The queue limit was deliberately set to **4** and worker concurrency to **1** for this test; these are test settings, not measurements of a universal maximum. The HTTP figures include small uploads, polls, decisions and preview reads over loopback. They exclude readiness probes and the intentionally rejected request. They do not include client network latency or represent per-deck processing latency. The 60-second lease dominates recovery time by design.

## Evidence and reproducibility

`result.json` is the complete passing summary, including the source commit, test-harness hash, generated deck hash, platform and every completed verification stage. The machine was Darwin/arm64 with 12 logical CPUs. `initial-harness-failure.json` preserves the initial run: its final assertion incorrectly used an async generator with `all`, so it was marked failed. That harness expression was corrected and the entire scenario rerun. It is not counted as a passing run or as an application failure.

With the project's Python dependencies and LibreOffice installed, and the dedicated local PostgreSQL integration database running at `127.0.0.1:55432/redline`:

```bash
PGPASSFILE=/path/to/ignored/local-test.pgpass \
  python scripts/benchmark_local.py --output /tmp/redline-capacity-new-run.json
```

Use a fresh output path and wait for the command to finish before reading its summary. The script generates local-only credentials without printing them, creates a random test schema and temporary deck directory, starts only its own API and worker child processes, and cleans up those processes and its own schema. It cannot be pointed at a remote database or Azure deployment. It raises on failed verification and writes `passed: false` on failure.

## What this does not establish

This is **one local recovery scenario with small text-heavy synthetic decks**, not a production load or soak test. It does not establish Azure CPU/memory sizing, large-image or chart-heavy throughput, Blob latency, distributed replicas, AI latency/cost/rate-limit capacity, or sustained concurrent-user capacity. Rendering time varies with fonts and deck complexity. Review the full workload before raising worker count or Azure replicas.

Production capacity should be measured on Azure with a representative workload before adding workers or replicas.
