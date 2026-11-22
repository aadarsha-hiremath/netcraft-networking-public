# Phase 4.4 final data safety and integrity acceptance

Verified on November 21, 2026, on Windows 11 build 26300 (AMD64), Python 3.12.14,
SQLite 3.53.1 and PySide6 Essentials 6.12.0. The Phase 4.4 branch starts at
`7ae9804942ba0079bcaf0b6511e098559983da99`. Git showed that the verified
Phase 4.3 deletion commit has the Phase 4.2 tip
`8d748ebcb4732ad89303d3ad0afc3f4df196287e` as its parent.

The desktop and automated tests used generated SQLite databases, separate INI
files, loopback servers and disposable destinations. The owner's history,
preferences and Windows network configuration were outside the test scope.
Desktop interactions used Windows pointer input on the actual app windows and
save picker. Qt Test drove the automated native integration cases separately.

## Verification results

| Command or check | Actual result |
| --- | --- |
| Phase 4.3 baseline, offscreen pytest | PASS. 233 passed in 102.52 s before source or test edits. |
| Final full suite, offscreen pytest | PASS. 239 passed in 71.19 s. |
| Final full suite, Windows Qt pytest | PASS. 239 passed in 76.57 s, exit code 0. See the Windows diagnostic below. |
| Ruff lint and formatting | PASS. 81 files already formatted. |
| Strict MyPy with the repository's full file scope | PASS. All 72 files, including sources, tests and benchmarks. |
| `pip check` | PASS. No broken requirements. |
| `git diff --check` | PASS. |

```powershell
$python = '.\.venv\Scripts\python.exe'
$env:QT_QPA_PLATFORM = 'offscreen'
& $python -m pytest -q
$env:QT_QPA_PLATFORM = 'windows'
& $python -m pytest -q
& $python -m ruff check .
& $python -m ruff format --check .
& $python -m mypy --strict
& $python -m pip check
git diff --check
```

The six added cases are
`test_retention_preview_cancel_apply_refresh_and_repeat`,
`test_changed_retention_plan_is_rejected_even_with_same_counts`,
`test_diagnostic_json_cannot_overwrite_workspace_or_expose_redirect_credentials`,
`test_filtered_samples_summary_and_trend_match_sqlite`,
`test_export_confirmation_decline_and_accept`, and
`test_cancelling_large_export_preserves_destination`.
The existing `test_committed_run_leaves_history_when_another_connection_is_writing`
now also holds a separate read transaction and checks Export against the survivor.

## History deletion

The native Live Monitor generated two successful loopback runs using two TCP
slots and three UDP slots. Windows pointer input selected the TCP row, opened the
actual confirmation and chose No. Both rows remained visible before reopening
the prompt. Yes removed the TCP row and the status bar showed `Run deleted.`.
Manual refresh, Run ID sort, TCP filtering and page navigation did not restore
the row. Overview plotted only UDP and Analytics showed three timed successes.
A separate process reopened the database with only the UDP run. An independent
read-only SQLite query found the three successful UDP samples under that run and
no TCP rows. [Delete prompt](phase-4-4-evidence/delete-prompt.png) ·
[After deletion](phase-4-4-evidence/delete-result.png) ·
[After restart](phase-4-4-evidence/delete-reopened.png)

| Stage | Runs | Samples |
| --- | ---: | ---: |
| Generated TCP and UDP runs | 2 | 5 |
| Decline deletion | 2 | 5 |
| Confirm deletion and reopen | 1 | 3 |

The automated Windows test compared the surviving run and sample records in
full. A read transaction opened before the commit held its original snapshot.
A second connection obtained a write lock after the commit and kept it held
during refresh, navigation and export. Fresh History, Overview, Analytics and
Export saw the survivor, the old reader saw the new state after ending its
transaction, and both connections released their locks. SQLite
`integrity_check` returned `ok` and `foreign_key_check` returned zero rows.
The Phase 4.3 rollback, retry and stale-read regressions also passed.

## Retention

Settings used Keep days **1** and Keep samples **3**. The fixture froze its
anchor at `2026-11-21T09:00:00Z`, giving the exact cutoff
`2026-11-20T09:00:00Z`. The preview reads a snapshot. Applying the
policy rechecks the affected record IDs in the write transaction. A change
with identical counts still requires a new preview.

1. Remove `aged-0`, `aged-1` and `aged-2`, which are strictly before the
   cutoff. `boundary` is exactly at the cutoff and passes this step.
2. Of the six age-eligible samples, keep the three newest across all runs by
   observation time, with descending sample ID breaking ties. The order kept
   is `newest`, `tie-c`, `tie-b`. The count cap removes `boundary`,
   `recent-low` and `tie-a`.
3. Count pruning removes every run left without samples. In this fixture that
   is `age-expired` and `count-expired`. `mixed` keeps `tie-c`
   and `survivor` keeps both of its rows. Every retained run column matches
   the before snapshot.

| Stage | Runs | Samples |
| --- | ---: | ---: |
| Before | 4 | 9 |
| Decline retention | 4 | 9 |
| Confirm retention | 2 | 3 |
| Repeat with the same frozen cutoff and reopen | 2 | 3 |

The desktop preview showed six samples and two empty runs. No cancelled
the operation and reopening the prompt showed the same counts. Yes applied
the policy after fixture-only approval. Overview and Analytics showed a
2.000 ms median and Analytics showed three timed successes. History showed
`mixed` and `survivor`. A fresh process displayed both rows and retained
the 4.25-second timeout and retention settings. A read-only comparison against
the initial snapshot matched every retained column and the INI SHA-256.
The repeat confirmation was exercised by Qt Test and removed zero samples and
zero runs. SQLite returned `ok` and zero foreign-key violations.
[Retention preview](phase-4-4-evidence/retention-preview.png)

The retention result and saved Settings captures remain in the local acceptance
record. The source archive omits both captures because they show a Windows account path.

## Export safety

The desktop Windows picker created an 886-byte CSV from the deletion workspace.
All three exported sample IDs, run IDs, protocols, targets, UTC timestamps,
statuses and latency values matched an independent SQLite query. The CSV
SHA-256 was
`60fa646a25ef3216e130b38fc54ab596792a3e7f3b5d9adb18cc6313e5312290`.
The export worker also wrote a JSON fixture from the same database outside
the desktop workflow. All three JSON rows matched their full SQLite records.
Its SHA-256 was
`75b23f515823fc3e97f9e1b279bc80cf469ebb501223d71c8bd3ad5a4d0320b3`.
Declining the actual replacement prompt preserved the generated sentinel CSV
with SHA-256
`4c269046a78d5a18f90d1bb222a402219bf9a1f4a0fde339246c0a0d97fd71ee`.
[Overwrite prompt](phase-4-4-evidence/overwrite-prompt.png) ·
[New CSV saved](phase-4-4-evidence/export-result.png)

Accepting replacement passed in the Windows Qt Test with a real Qt
confirmation, a fixture path supplied in place of Save As, and a verified
nine-row replacement. A subsequent decline preserved the replacement bytes
and displayed `Export cancelled.`. The desktop pass covered the actual
Windows Save As picker and declined the overwrite. Windows desktop input
stopped when the owner pressed Escape before the affirmative replacement.

The automated Windows case also held the disposable destination open with a
real Windows sharing lock. The Export worker displayed `Permission denied`,
preserved the original bytes and removed its temporary file. An active export
of 30,009 samples was cancelled at the 4,096th stream write. The existing
destination bytes, the absence of temporary exports and the drained Qt pool
were checked. The automation supplied the disposable path directly for these
failure paths.

Filtered CSV samples and JSON summary and trend reports matched direct SQL.
The HTTP-filtered fixture contained two successful samples with a 1 ms minimum,
2 ms maximum and 1.5 ms median. A real local HTTP redirect response verified
that diagnostic JSON excludes response cookies and bodies, URL paths and
queries, and a redirect that contains userinfo. Export writes a same-directory
temporary file, flushes and synchronizes it, and then publishes atomically.
The no-overwrite path uses a non-replacing rename on Windows and a hard link
on other supported platforms so an existing destination fails the publish.
Confirmed overwrite uses `os.replace`. Cancellation and failed publish
clean up the temporary file.

## Persistence and failure recovery

The desktop restarts above verified retained history and preferences in fresh
processes. The full Windows and offscreen regression suites also exercised
initializing missing history, unavailable paths, busy history, invalid input,
DNS and HTTP failure categories, cancellation of pending probes, Qt worker
shutdown, and an event-loop heartbeat while work ran. These failure cases
used Qt integration tests with offline fixtures.

## Defects corrected

- Settings formerly asked about the limits without reporting which counts
  they affected. It now computes the preview off the GUI thread, confirms the
  selected plan within the transaction and refreshes dependent pages.
- Diagnostic JSON saves bypassed the workspace file guard, and report exports
  did not reserve file-backed preferences. Both save paths now protect those
  destinations, the history database and its sidecar files.
- Declining export replacement after a successful save could leave the old
  success label visible. Cancellation now shows `Export cancelled.`.
- Strict MyPy exposed an untyped DNS fixture argument and annotations that
  referenced names through the wrong GUI module. The correction is confined
  to test typing and preserves the assertions and Windows DNS implementation.

## Reproduction

For independent, deterministic reproduction from the repository root, run
the focused tests on Windows with the native backend.

```powershell
$env:QT_QPA_PLATFORM = 'windows'
& .\.venv\Scripts\python.exe -m pytest -q tests/gui/test_retention_safety.py tests/gui/test_export_safety.py tests/gui/test_studio.py
```

For a desktop pass, always give the GUI fresh explicit paths with
`netcraft-gui --history <disposable-history.sqlite3> --settings <disposable-preferences.ini>`.
Use short finite TCP and UDP runs against loopback echo servers. Select the
TCP row, first decline deletion and then confirm it. Refresh, sort by Run ID,
filter for TCP, navigate through Overview and Analytics, and close and relaunch
with the same paths. Export the remaining samples to a new file. For overwrite,
create a distinct sentinel in the same disposable folder, choose it in Save As,
decline, hash it, repeat and confirm, and compare the result with the new export.

The checked-in `seed_retention` fixture can also launch the deterministic
Settings workflow on a fresh disposable path. The override below is scoped
to that verification process.

```powershell
$env:QT_QPA_PLATFORM = 'windows'
$env:NETCRAFT_QA = Join-Path (Get-Location) ('dist/retention-' + [guid]::NewGuid().ToString('N'))
@'
import os
from datetime import timedelta
from pathlib import Path
from netcraft.gui import settings_page
from netcraft.gui.main import main
from tests.gui.test_retention_safety import ANCHOR, seed_retention

root = Path(os.environ["NETCRAFT_QA"])
path = root / "history.sqlite3"
seed_retention(path)
settings_page.recent_cutoff = lambda days: ANCHOR - timedelta(days=days)
raise SystemExit(main(["--history", str(path), "--settings", str(root / "preferences.ini")]))
'@ | & .\.venv\Scripts\python.exe -
```

In Settings save 1 day and 3 samples, decline the first prompt, then reopen
and confirm. Verify counts with `SELECT count(*) FROM runs` and
`SELECT count(*) FROM samples`. Check the retained IDs and run
`PRAGMA integrity_check` and `PRAGMA foreign_key_check` through a separate
read-only connection. Relaunch with the same fixture paths.

## Limitations

The affirmative replacement was verified by native Qt automation. Its
end-to-end Windows desktop Save As confirmation remains outstanding because
desktop input stopped before that action. The denied-write, active export
cancellation and repeat retention checks were also performed through Qt
automation.

Native pytest printed the previously observed Windows COM diagnostic
`0x8001010d` and still exited successfully with all 239 cases passing.
The native call site remains unidentified. This pass covers one Windows
desktop configuration, local SQLite and local filesystem replacement.
Network shares, real VPN or split DNS, and other Windows versions were outside
the Phase 4.4 test scope.

## Verdict

**PARTIAL.** All automated checks passed. Desktop deletion, retention,
restart, initial CSV save and declined overwrite passed. Affirmative
replacement through the live Windows Save As desktop workflow remains
unverified.
