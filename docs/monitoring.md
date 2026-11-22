# Monitoring, telemetry, and persistent history

## Targets and scheduling

Monitoring starts only on an explicit command. Supported targets are TCP handshakes, UDP echo
exchanges, DNS record queries, and HTTP or verified HTTPS HEAD requests. Each UDP probe sends
one fixed echo message to the chosen peer. Monitoring reuses the Phase 1 and 2 diagnostics.
HTTP status codes such as 404 and 500 remain observed responses, and a DNS NOERROR empty answer
remains a completed DNS diagnostic.

```powershell
netcraft monitor --tcp 127.0.0.1:8000 --http http://127.0.0.1:8000/status --samples 5 --interval 2
netcraft monitor --dns A=lab.example.test --resolver 127.0.0.1 --resolver-port 5353 --duration 15 --interval 2
netcraft monitor --udp '[::1]:9001' --samples 3 --timeout 1 --json
```

These examples expect existing loopback listeners. Target flags are repeatable. A target
identity is scoped by protocol. TCP and UDP use the normalized host and port. DNS uses the
record type and canonical query name. HTTP and HTTPS use normalized origins, including port.
Each origin may have one HTTP or HTTPS target per run, and origin-based history intentionally
groups different paths from separate runs. A supplied HTTP query is used for the request and
kept out of persisted telemetry and progress. Credentials in URL authority are rejected.

The first slot is due immediately. Later slots use monotonic deadlines anchored to the first
slot, so probe duration does not move the schedule. Sample count is the number of scheduled
slots per target, including skipped slots. Duration is an alternative, measured from scheduling
start. It stops new probes and requests cancellation of active ones at expiry. The coordinator
rotates target priority per slot when targets outnumber workers. It creates an observation
for due slots it skips and never accumulates a backlog of network work.

The defaults are a 5-second interval, 10 slots, 4 workers, and a 3-second per-probe timeout.
Limits are 32 explicit targets, 16 workers, 100,000 total slots, intervals from 0.1 seconds to
1 hour, and duration from 0.1 seconds to 24 hours. One probe may run for a given target at a
time within the run. A second active monitor for the same history file is rejected. Independent
history files have independent schedulers.

The coordinator waits for a monotonic deadline, worker completion, or external cancellation
using socket readiness. On the Windows main thread the wait uses at most 250 ms per call so
Python can service Ctrl+C. There is no spin loop. Ctrl+C and the cancellation token stop
scheduling and join workers. A blocking OS hostname lookup can delay the join.
[Phase 2 timing and cancellation boundaries](timing-methodology.md)

## Telemetry schema

Rows and exports include `schema_version` 1. Samples contain a run ID, sample ID, zero-based
slot, protocol, safe target identity, scheduled UTC time, and observed UTC time. Scheduled UTC
is the schedule anchor plus the monotonic slot offset. Observed UTC is the actual probe start
or skip observation time. Elapsed measurements use monotonic clocks. Unavailable metrics
are JSON null and blank CSV cells.

| Field family | Captured data |
| --- | --- |
| Outcomes | `status` is pending, success, failure, timeout, cancelled, skipped, or interrupted. `error_category` carries the protocol's stable classification. `reason` records why the scheduler skipped or stopped a sample. |
| End-to-end | `latency_ns` is the diagnostic's measured duration, including time spent in failed or timed-out probes. Cancelled, skipped, pending, and interrupted samples have no end-to-end latency. |
| Stages | TCP connect, HTTP hostname lookup, TLS handshake, request send, first byte, headers, body read, and UDP exchange when measured. Standalone DNS records its full diagnostic time in `latency_ns`. Monitoring uses HEAD, so the HTTP body timing is normally unavailable. |
| Response | DNS status, response code and record count, HTTP status, TLS version and verification, or UDP response byte count. |

Skipped slots have one of `target_busy`, `worker_limit`, or `scheduler_late`. A slot a
full interval overdue is recorded as late without launching a catch-up probe. Cancellation,
duration expiry, interrupted processes, and failed runs have separate reasons. No future
slots are invented after shutdown.

History retains an allowlist of metadata and error categories. It excludes HTTP paths and
query strings, credentials, bodies, headers, DNS record contents, and free-form exception
messages. The CLI is intended for a trusted local operator who supplies targets.

## Reliability and latency

- Attempts are success, failure, and timeout combined. Success rate is successes divided by
  attempts. Failure rate includes timeouts, and timeout rate uses the same denominator.
  Cancelled, skipped, pending, and interrupted counts are reported separately. Rates are null
  when there are no attempts.
- Latency statistics use only successful rows with observed end-to-end timings. Mean, minimum,
  maximum, and median are computed over that population. The median averages the two middle
  values for an even count. p95 and p99 use nearest rank, `ceil(percentile × count)`.
- Jitter is the arithmetic mean of absolute changes between consecutive successful timings
  for the same protocol and target, ordered by observed time and sample ID. Overall and
  protocol jitter pool the eligible within-target pairs. With no pair, jitter is null.
- A failure streak is a sequence of failed or timed-out attempts for one target. A success
  resets it. Samples outside the selected time window and non-attempt statuses are ignored.
  Broader summaries report the maximum current and longest streak across included targets.
- Error distributions count errors in failed and timed-out attempts. The latency trend export
  streams timestamped successful timings in ascending order.

Use `summary --group all|protocol|target` and filter by run ID, protocol, exact stored target,
or an ISO 8601 time window using inclusive `--since` and exclusive `--until`. Every supplied
timestamp must include an offset. [SQLite window function semantics](https://www.sqlite.org/windowfunctions.html)

## SQLite, retention, and recovery

The default path is `~/.netcraft/history.sqlite3`. Use `--db` to choose a local path. SQLite
stores runs and samples with foreign keys and time, run, and target indexes. The file uses WAL
so readers can query history while the monitor appends. The scheduler writes to SQLite only
from the coordinator thread. A pending sample is committed before dispatch, then finalized
atomically. The database schema is checked using SQLite's application ID and user version.
[SQLite write-ahead log](https://www.sqlite.org/wal.html)

A sidecar interprocess lock protects the active run and maintenance. If the process exits
unexpectedly, the OS releases the lock. The next monitor or history command that can acquire
it marks orphaned running runs and pending samples interrupted. Completed observations are
preserved. The interrupted run's end timestamp reflects recovery time. Local filesystems
with working SQLite WAL and OS locks are the supported deployment.

```powershell
netcraft history --runs --limit 20
netcraft history --prune-days 30 --keep-samples 10000
$runId = 'paste-the-exact-run-id'
netcraft history --delete-run $runId
```

Pruning is explicit. The UTC cutoff removes samples whose observation time is strictly
earlier than the cutoff. The count cap applies across the database and keeps the newest
remaining samples by observation time, with descending sample ID breaking ties. Age-only
pruning removes empty runs that ended before the cutoff, and count pruning removes all
empty runs. The desktop previews the sample and run counts and asks for a new preview
if the selected records change before it can apply the policy. Deletion removes one exact
run ID and cascades to its samples. A live monitor blocks maintenance. Pruning removes
records logically and does not request an immediate shrink of the SQLite file.

## Exports and verification

```powershell
$runId = 'paste-the-exact-run-id'
netcraft export --report run --run $runId --format json
netcraft export --report samples --protocol tcp --format csv
netcraft export --report summary --group target --since 2026-11-01T00:00:00Z --format json
netcraft export --report latency --group protocol --format csv
netcraft export --report trend --run $runId --format csv
```

Exports stream to standard output within a read snapshot. JSON has a schema version and a
`rows` array. A full JSON run export also includes run metadata. A full CSV run starts with a
`run` record and follows it with sample records. All times and durations use UTC and
nanoseconds. CSV preserves commas, quotes, and line breaks. Formula-like text gets a leading
apostrophe for spreadsheet safety. SQLite performs historical sorting and ranks, with
temporary work allowed on disk, while Python streams output row by row.

Run the full suite with `python -m pytest` in the development environment. All protocol tests
use loopback fixtures or controlled outcomes. The history tests reopen SQLite files and
simulate process termination in a child process. Run
`python -m benchmarks.monitoring --samples 40 --interval 0.1` for a repeatable loopback
measurement. The benchmark counts observations, reports median and p95 per protocol, checks
worker shutdown, and measures Python allocations in a separate run. Allocation figures
exclude native SQLite and kernel memory. The database size is measured after all three runs
and checkpointing on close.
