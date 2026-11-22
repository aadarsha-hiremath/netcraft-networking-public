# NetCraft Network Diagnostics Studio

Phase 4 combines a native Qt Widgets workspace with bounded live analytics. The desktop app
uses the existing CLI engine and history schema. Qt loads only for the optional desktop
entry point. The application starts on Overview with no network work and no synthetic data.

## Install and launch on Windows

From the repository root in PowerShell with Python 3.12 available:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[gui]'
.\.venv\Scripts\netcraft-gui.exe
```

To work in an isolated local history file, use:

```powershell
$history = Join-Path $env:LOCALAPPDATA 'NetCraft\acceptance.sqlite3'
.\.venv\Scripts\netcraft-gui.exe --history $history
```

With no path argument, NetCraft uses `~/.netcraft/history.sqlite3` so the Phase 3 GUI and CLI
can share observations. NetCraft validates the file's application ID and schema. A damaged
or incompatible database produces an error, and the application does not replace it.

## Workflows

| Page | Interactions |
| --- | --- |
| Overview | Review actual recent runs, warnings, attempt-based success rate and successful latency for the selected window. First run shows zero configured targets and unavailable measurements. |
| Diagnostics | Choose TCP, UDP echo, DNS, HTTP, or HTTPS. Run and cancel one explicit request. Expand technical details. Copy or save a sanitized snapshot of the submitted target and result. |
| Live Monitor | Add, edit, and remove targets. Set the sample count, interval, and worker limit. Start or stop a finite run. View live counts, latency and outcomes, then open run details. |
| History | Search a partial target identity and filter by protocol or time window. Browse runs in pages of 35, inspect samples in pages of 60, and confirm deletion of a selected run. |
| Analytics | Filter the stored population by window, protocol and exact target. Inspect percentiles, jitter, rates and streaks. Compare target and protocol summaries and examine error categories. |
| Export | Choose a full run, filtered samples, reliability summary, latency summary or trend. Export to CSV or JSON with destination and overwrite confirmation. |
| Settings | Choose theme, time window, latency units, timeout, cadence, concurrency and export format. Save or reset preferences. Apply retention explicitly to local history. |

Use Ctrl+1 through Ctrl+7 for page navigation. Tab and Shift+Tab move between labeled
controls. The sidebar automatically collapses below 1320 logical pixels, and the Menu
button lets you choose its state. Tables scroll in their own panels. The dark and light
palettes have visible focus styles. Charts support mouse hover, wheel zoom, drag, and a
keyboard-accessible Reset zoom button. The time axis and all timestamps are UTC.

Monitor targets are session-only. QSettings persists presentation and default values, not
target text, payloads, trust paths, or request URLs. Each target can have its own diagnostic
timeout, resolver, or additional HTTPS trust anchor. Default monitoring uses 5 seconds, 10
slots and 4 workers. Closing while active prompts before requesting cancellation.
[Phase 3 scheduler limits and retention semantics](monitoring.md)

## Architecture

`gui/window.py` owns the workspace and shutdown. Page modules bind Qt input widgets and
table models to immutable result payloads. `gui/diagnostic_data.py` calls the tested Phase 1
and 2 functions and projects only allowed fields. `gui/live_page.py` uses the Phase 3
scheduler and its cancellation token. `gui/data.py` opens a separate SQLite connection
inside each storage job and calls Phase 3 analytics and export.

`gui/workers.py` runs diagnostic, monitor, history, and file jobs in a four-lane Qt thread
pool. The history lane keeps only the newest pending read as filters or routes change.
Worker outcomes reach the GUI thread via queued signals. A monitor worker owns the bounded
Phase 3 scheduler pool. Its progress callback updates counters and a ring buffer, and the
GUI reads that buffer at most every 180 ms. The plotted history is bounded to 1200 samples
and eight lines. The full selected history still feeds the persisted analytics. Outcomes
without valid latency appear in a separate categorical strip. They never become zero-latency
points. [Qt thread affinity](https://doc.qt.io/qtforpython-6/PySide6/QtCore/QObject.html)

Desktop history uses the unchanged Phase 3 database format. Run and sample views page
through indexed queries. Sorting applies to the displayed page. Exports stream through
the Phase 3 writer into a temporary sibling file, then publish it after success. Cancellation
or a write error leaves any previous export intact. CSV retains the existing Phase 3
formula-like text protection. JSON and SQLite use the underlying sanitized values unchanged.

## Safety, limits, and troubleshooting

- Targets are supplied explicitly. Closing waits for worker cleanup. A blocking system
  hostname lookup may delay cancellation. The window remains responsive during that wait.
- HTTP/HTTPS uses Phase 2 TLS verification and bounded reads. Sanitized diagnostic and
  history output excludes HTTP paths, query strings, headers, credentials and response
  bodies. An HTTP error status is reported independently of network or TLS failure.
- If history is busy, stop any other monitor using that file. If it is unavailable or
  corrupt, select a different local path with `--history` and keep the original for recovery.
- The desktop uses the Qt Widgets bindings supplied by PySide6 Essentials. Install the
  GUI extra in the same environment as NetCraft. If the Windows launcher closes without
  opening a window, run `python -m netcraft.gui.main` from that environment to see any
  startup message in the terminal.
- History, settings, and exports are local. Retention is explicit. High-DPI scaling depends
  on the Windows display configuration. Inspect the app on the monitor you use day to day.

## Development, dependency notices, and acceptance

The Windows Python 3.12 environment was verified with PySide6 Essentials 6.12.0,
PyQtGraph 0.14.0, and pytest-qt 4.5.0. PySide6 Essentials supplies Qt Core, Gui and Widgets,
including Shiboken6. The client does not require Qt WebEngine. PySide6 is available under
LGPL/GPL or commercial terms. PyQtGraph and pytest-qt use the MIT license. The line icons
are drawn by NetCraft code. The screenshots are of the running app. The NetCraft wheel
contains project Python code and dependency metadata. Qt libraries are installed separately
by pip. Redistribution as a bundled application requires a separate license review.
[PySide6 package notice](https://pypi.org/project/PySide6/) ·
[Qt licensing obligations](https://www.qt.io/development/open-source-lgpl-obligations) ·
[PyQtGraph license](https://github.com/pyqtgraph/pyqtgraph/blob/master/LICENSE.txt) ·
[pytest-qt license](https://github.com/pytest-dev/pytest-qt/blob/main/LICENSE)

```powershell
.\.venv\Scripts\python.exe -m pip install -e '.[dev,gui,gui-test]'
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m mypy
.\.venv\Scripts\python.exe -m pip check
$env:QT_QPA_PLATFORM = 'windows'
.\.venv\Scripts\python.exe -m benchmarks.desktop_studio --samples 40 --interval .2
```

Pytest-qt defaults to Qt's offscreen platform and uses local fixtures. Set
`QT_QPA_PLATFORM=windows` explicitly for the native benchmark and actual captures. The
benchmark measures a 20 ms event-loop heartbeat during TCP, UDP, and HTTP loopback monitoring
and measures Python allocations independently. Python allocation figures exclude Qt and
other native memory. Capture with `--captures dist/desktop-captures`.

### Native captures and measurements

The captures show the Windows Qt platform at 150% scaling. Sizes below are logical client
dimensions. The loopback plots show actual measurements from the bundled local fixtures.

| Capture | Client size |
| --- | ---: |
| [Fresh Overview](screenshots/overview.png) | 1600×900 |
| [Completed TCP diagnostic](screenshots/diagnostics.png) | 1366×768 |
| [Live Monitor after a run](screenshots/live-monitor.png) | 1280×720 |
| [Analytics timeline and target comparison](screenshots/analytics.png) | 1600×900 |

One 15.617-second native benchmark scheduled 225 slots at a 0.2-second interval per target.
It recorded 220 successes and five skips. The event-loop heartbeat had a 16.070 ms median
and an 86.641 ms maximum. The separate allocation pass measured a 1,367,106-byte peak above
baseline in Python-tracked memory. The Qt pool joined, and no engine or fixture workers
remained. The results describe the test host while the desktop app and fixtures were running.

### Owner acceptance checklist

1. Launch with a new local `--history` path. Confirm the empty Overview, then navigate every
   page at 1600×900, 1366×768, and 1280×720. Repeat at your usual Windows scale and check focus
   indicators, scrollbars and the collapsed sidebar.
2. Run valid and invalid diagnostics against local fixtures. Check cancellation, result
   identity after editing the form, and sanitized copy or save.
3. Start a short loopback monitor, switch pages, zoom the chart, then stop a second run
   during a pending probe. Confirm the app stays responsive and reports its final state.
4. Relaunch against the same history and preferences. Inspect run details, compare analytics
   to the CLI, and open CSV and JSON exports. Confirm overwrite, retention, and deletion
   prompts only against this disposable history.
