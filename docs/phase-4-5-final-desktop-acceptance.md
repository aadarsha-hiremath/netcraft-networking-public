# Phase 4.5 final desktop acceptance and Phase 5 readiness

## Owner-confirmed desktop acceptance

The project owner manually confirmed that all seven pages open and function,
and that navigation, controls, resizing and normal layouts are usable on the
tested Windows desktop. The owner also used the actual Windows Save As flow.
Declining replacement preserved the original file. Confirming replacement
produced a valid file that reopened successfully. These are owner
observations. The protocol, storage and stress results below came from
separate verification runs.

The earlier Windows automation session closed with `Transport closed`
during target entry. The owner's walkthrough closes the outstanding desktop
gate from that session. The owner did not identify additional display
configurations or per-protocol test details for the manual session.

## Scope and Git baseline

Verification took place on November 21, 2026 on Windows 11 build 26300
(AMD64), Python 3.12.14, Windows Qt 6.12.0, SQLite 3.53.1 and OpenSSL 3.5.8.
All application probes used loopback fixtures or the Windows system resolver
for `localhost`. NetCraft's history, INI files, local CA and export targets
were disposable. Windows DNS and adapter settings were outside the audit scope.

Phase 4.5 is on `codex/phase-4-5-final-desktop-acceptance`, starting at
`4b7e8a45f9ac31ce71c9185282124b1f7843f959`. The Phase 4.4 commit contains
the retention preview and stale-plan check, workspace-file export protection,
export cancellation status, and all six new safety cases. The Phase 4.3 fix
`7ae9804942ba0079bcaf0b6511e098559983da99` is its parent. Its report and
233-test logs established that deletion committed while the old History view
kept a stale row when refresh tried to take a write lock.

I reviewed the [Phase 4.1 report](phase-4-1-acceptance.md), the local
Phase 4.2 report at `docs/phase-4-2-acceptance.md`, the Phase 4.3 verification in
the task record and its local logs, and the
[Phase 4.4 report](phase-4-4-final-safety-acceptance.md). The Phase 4.2 report
and its evidence were already untracked. The diff from the Phase 4.1 commit
`8d748ebcb4732ad89303d3ad0afc3f4df196287e` through Phase 4.5 is empty for
`src/netcraft/protocols/dns.py` and `src/netcraft/protocols/dns_windows.py`.

## Evidence by page

Desktop input means Windows pointer or keyboard actions on the actual
window. Windows Qt automation means a native Qt window and test-driven
widgets. The two evidence sources are recorded separately.

| Page | Phase 4.5 findings | Desktop acceptance |
| --- | --- | --- |
| Overview | Fresh wheel showed 0 targets, Stopped, no runs, and no latency points. Success rate and median showed dashes. Qt automation checked populated summaries and refresh. A new installed-wheel process reopened one run and three latency points. | Fresh desktop verified. Earlier desktop restart and survivor-only Overview are in Phase 4.4. |
| Diagnostics | Native Qt covered TCP and UDP success, refusal or silence, DNS success and negative outcomes, HTTP statuses, verification failures and cancellation. Offscreen copy and JSON output matched the sanitized snapshot without path, query, cookie or body values. | Owner confirmed the page functions. The protocol matrix is from automated fixtures. Phase 4.1 and 4.2 provide earlier desktop DNS and sanitized output evidence. |
| Live Monitor | Native Qt opened the target editor, added and edited a TCP target, completed three samples and removed the target. Mixed fixtures also verified counters, charts, scheduled skips and shutdown with an active probe. | Owner confirmed the page functions. The earlier desktop session reached the Add monitor target dialog. |
| History | Empty History had no rows. Native regression covered filter, sorting, page and run inspection, absence after deletion, and subsequent reads under SQLite contention. | Empty state observed in Phase 4.5. Phase 4.4 separately confirmed actual deletion, refresh, sorting, filtering, and restart. |
| Analytics | Blank fresh metrics and timeline. Independently rechecked successful-only timings and completed-attempt rates against native mixed-protocol SQLite results. | Fresh no-data view verified through the keyboard. Phase 4.4 showed the refreshed populated views after deletion and retention. |
| Export | Fresh Export had an empty Run ID, no saved result, and inactive cancellation. The full suite verified CSV/JSON output, filters, summary and trend, accepted replacement, denied write, and interrupted export cleanup. | Owner confirmed both Save As choices and reopened the replacement file. Phase 4.4 also verified desktop save and decline. |
| Settings | Native Qt changed and saved all preference values, reopened them, reset defaults, and tested retention preview and confirmation with the disposable database. | Owner confirmed the page functions. Phase 4.4 physically accepted retention and verified persisted settings. |

[Fresh wheel Overview](phase-4-5-evidence/fresh-overview.png) ·
[Restored wheel Overview](phase-4-5-evidence/restored-overview.png) ·
[Five-protocol Qt run](phase-4-5-evidence/mixed-monitor.png) ·
[Prior desktop deletion and retention](phase-4-4-final-safety-acceptance.md#history-deletion) ·
[Prior desktop overwrite decline](phase-4-4-final-safety-acceptance.md#export-safety)

The fresh wheel loaded the installed package from `site-packages` with a
new SQLite file and explicit INI path. The initial Overview contained zero
history or latency observations, and neither the diagnostics nor monitor lane
had a job. The full suite asserts no call to the resolver on first launch.
The fresh window showed no startup exception. These checks provide startup
evidence without making a packet-capture claim.
The separate wheel restart restored one real three-sample TCP run, the light
theme, a 4.25-second timeout and a 60-minute chart window.

## Protocols and independent analytics

Native CLI with a blank resolver queried A for `localhost`. Windows returned
`127.0.0.1`, `NOERROR`, `windows_system`, and `local` transport in
12.334 ms. The recorded endpoint was unknown, as expected for the Windows
system path. The Windows resolver returned the local answer. There was no
test request to a public resolver. Windows Qt also submitted the blank field
to the installed system policy and displayed **System resolver**, then
queried a fixture on `::1` and returned its `192.0.2.55` A record.
[Windows System DNS](phase-4-5-evidence/windows-system-dns.png) ·
[Explicit IPv6 DNS](phase-4-5-evidence/explicit-ipv6-dns.png).

The repeated native Qt protocol fixture used explicit IPv4 loopback DNS,
including successful A, AAAA, CNAME, MX, NS, TXT, SOA, CAA, SRV and PTR
answers, plus NXDOMAIN, empty answer and DNS timeout. The full regression
also includes a real `::1` resolver fixture. TCP loopback exercised
success, refusal and a client deadline. UDP exercised echo, timeout and
active cancellation. HTTP GET and HEAD, 302, 404 and 500, malformed response,
and timeout had distinct observed outcomes. Trusted TLS succeeded in the
mixed run. Untrusted CA, hostname mismatch and early close returned
`tls_certificate`, `tls_hostname`, and `remote_closed`. The first
standalone trusted TLS run carried the short timeout from the preceding
timeout scenario. That observation was a timeout and is kept separate from
the verified mixed-run TLS success. A separate native Qt run set a
three-second timeout and returned HTTP 200 over verified HTTPS.
Both certificate chain and hostname verification were true, and that
observation recorded 76.426 ms in the TLS stage.
[Verified HTTPS](phase-4-5-evidence/verified-https.png).

The mixed workload scheduled 35 slots and recorded 5 successes, one DNS
failure, one timeout, and 28 skipped slots. From the five successful
observations, the independently computed median was 141.502 ms and p95 and
p99 were 276.117 ms using nearest rank. No target had two successful
observations, so jitter was unavailable. Across seven attempts the success,
failure and timeout rates were 71.43%, 28.57% and 14.29%. The failure rate
includes timeouts. The native cancellation pass cancelled a diagnostic and a
monitor while a silent loopback DNS fixture had received the request, then
verified the Qt pool and persisted cancellation.

## Regression, packaging, and checks

The first native baseline run passed all 239 tests. The initial offscreen
run failed the cadence test when the loaded host missed a slot. After
controlling that test's clock, a separate offscreen run failed because a
pre-existing Qt thread exited during an unrelated DNS cleanup test. The
same kind of thread exit then appeared during a Windows DNS callback
cleanup test. The
cadence regression now advances at a fixed 100 ms after an actual probe
completion, still asserts all three round-robin successes, and asserts the
other six slots were skipped for the worker limit. The DNS regression now
asserts that both its actual worker and fixture thread stopped, and still
rebinds the released UDP port. Cleanup checks for TCP, HTTP, Windows DNS
and monitoring still reject surviving new threads. They permit threads
that were already active before the test to finish. Product scheduling,
DNS, and UI source are unchanged.

```powershell
$python = '.\.venv\Scripts\python.exe'
$env:QT_QPA_PLATFORM = 'offscreen'
& $python -m pytest -q
& $python -m pytest -q tests/integration/test_monitor_scheduler.py tests/integration/test_dns_diagnostics.py
$env:QT_QPA_PLATFORM = 'windows'
& $python -m pytest -q
& $python -m ruff check .
& $python -m ruff format --check .
& $python -m mypy --strict
& $python -m pip check
git diff --check
```

| Final check | Observed result |
| --- | --- |
| Focused cadence and DNS tests | 43 passed in 1.74 s. |
| Full offscreen pytest | 239 passed in 69.17 s after the cleanup assertion changes. Exit code 0. |
| Full Windows Qt pytest | 239 passed in 111.85 s after the cleanup assertion changes. Exit code 0. |
| Ruff | Lint passed. 83 files already formatted. |
| Strict MyPy | 72 files passed. |
| `pip check` and `git diff --check` | Passed. |

The offline local wheelhouse supplied the project dependencies. The isolated
build created the 85,965-byte sdist and the 102,867-byte pure-Python wheel
(`netcraft_networking-0.4.1-py3-none-any.whl`, SHA-256
`4adedd9668efaec8453e4969be3d56d8e9a415758b233effff882302fe1f442f`).
The core wheel, GUI wheel, and editable GUI installed into three separate
environments. Each passed `pip check` and both CLI loopback echoes.
The core wheel did not install Qt. Its console entry function returned 2
with instructions to install the GUI extra. The Windows windowed launcher
has no console, so the desktop guide now points to `python -m netcraft.gui.main`
to see startup messages. The GUI wheel opened in native Qt. The first build
and editable attempt exposed a setuptools issue with braces in the sandbox
temporary path. Explicit workspace-local build paths and an approved retry
of the offline editable install resolved it.

```powershell
$env:PIP_NO_INDEX = '1'
$env:PIP_FIND_LINKS = (Resolve-Path 'dist/phase4-wheels').Path
& $python -m build --outdir dist/phase4-5/rebuild --env-dir dist/phase4-5/build-env --sdist-extract-dir dist/phase4-5/sdist-tree
& dist/phase4-5/editable-env/Scripts/python.exe -m pip install --no-build-isolation -e '.[gui]'
& dist/phase4-5/wheel-env/Scripts/python.exe -m pip install 'dist/phase4-5/rebuild/netcraft_networking-0.4.1-py3-none-any.whl[gui]'
& dist/phase4-5/core-env/Scripts/python.exe -m pip install 'dist/phase4-5/rebuild/netcraft_networking-0.4.1-py3-none-any.whl'
& dist/phase4-5/wheel-env/Scripts/netcraft.exe tcp-echo 'phase-4.5 wheel' --json
& dist/phase4-5/wheel-env/Scripts/netcraft.exe udp-echo 'phase-4.5 wheel' --json
& .\.venv\Scripts\netcraft.exe dns localhost --type A --json
```

## Performance and visual verification

The one-minute benchmark used three real TCP, UDP and HTTP loopback targets
and 300 slots per target at 200 ms. It completed in **59.864 s**, accounted
for **900 slots**, and recorded **801 successes and 99 skips**. The final
chart reported 900 rendered points. A 20 ms Qt timer measured 2,977
callbacks, a **20.051 ms median** and a **46.836 ms maximum** gap. The
separate 30-slot Python allocation pass peaked **1,236,133 bytes** above
baseline and retained **1,067,572 bytes** above baseline at the post-run
sample. It excludes native Qt and OS allocations. The pool joined and
there were zero additional non-daemon Python threads after cleanup.
[Finite monitoring chart](phase-4-5-evidence/finite-monitor.png).

The first long attempt used the historical polling helper and timed out
while opening a history-dependent page. Its early process teardown printed
a Windows file-sharing error and a Qt signal-source exception. The repeat
used the same workload with a live Qt event loop for each wait and completed
through all seven pages and normal close. This distinction is also recorded
in the Phase 4.2 performance notes. The benchmark provides a one-minute
stress observation at a short cadence and a separate short memory sample.

The synthetic bulk test loaded 40 runs and 150,000 samples in a
64,626,688-byte SQLite file. It navigated the seven routes three times,
sorted History by Run ID, opened the five-row second run page and
paginated the 60-row sample view. The longest of 21 navigation actions
was an Analytics load at **6.583 s**. The window continued pumping events.
The heartbeat median was **20.049 ms**, its maximum was **47.105 ms**,
and 2,254 callbacks were recorded. Analytics rendered up to 1,200 points.

| Bulk operation | Elapsed | Verified result |
| --- | ---: | --- |
| History run page 2 | 13.953 ms | Five remaining runs |
| Run-details sample page 2 | 65.570 ms | 60 sample rows per full page |
| History target filter | 160.383 ms | One matching run. The UDP filter returned an empty state. |
| CSV samples | 6.640 s | 150,000 rows, 21,855,898 bytes |
| JSON samples | 5.401 s | 150,000 rows, 95,205,654 bytes |

The export timings start at dispatch and end at the worker's completion
message. The Qt test supplied the local paths directly. A separate SQLite
query verified sample ID, run ID, timestamp, status and latency for all rows
in both files. An independent calculation on the same database matched
median **1,001,874.5 ns**, p95 **1,003,562 ns**, p99 **1,003,712 ns**, and
jitter **1 ns** over 149,960 within-target pairs. All 150,000 attempts were
successes. The process working set was 155,947,008 bytes at measurement
start, 180,236,288 bytes at the sampled peak and 165,216,256 bytes before
close. Verification of the file contents ran after measurement. The
pool joined, a new SQLite writer acquired the lock, and integrity and
foreign-key checks passed.

The physical desktop was **2560 × 1440 at 150%**. The fresh installed
window displayed normally at a 1400 × 810 logical client. The native Qt
layout pass exercised normal and maximized states, keyboard navigation and
scrolling through all seven routes at fitting sizes. It used process-local
Qt scale overrides for 100% and 125%. Windows display settings remained
unchanged. At 100%, the initial keyboard check missed an expected route.
A retry explicitly focused the test window and waited for each requested
page to open.

| Effective scale | Native work area | Verified layout and overflow |
| --- | --- | --- |
| 100% process-local | 2560 × 1440 | All seven routes at 1920 × 1080, 1600 × 900, 1366 × 768, 1280 × 720 and 760 × 440. |
| 125% process-local | 2048 × 1152 | All seven routes at 1920 × 1080, 1600 × 900, 1366 × 768, 1280 × 720 and 760 × 440. |
| 150% host scale | 1707 × 960 | All seven routes at 1600 × 900, 1366 × 768, 1280 × 720 and 760 × 440. The 1920 × 1080 request was constrained to 1709 × 940, still wider than the reported work area. Full-fit coverage at this size remains open. Maximized client was 1707 × 937. |

[Native Qt maximized capture at 150%](phase-4-5-evidence/maximized-150.png).

```powershell
$env:QT_QPA_PLATFORM = 'windows'
$env:PYTHONPATH = (Get-Location).Path
& $python dist/phase4-5/native-protocols-verified.py
& $python dist/phase4-5/native-startup-verified.py
& $python dist/phase4-5/ui-checks.py native dist/phase4-5/ui-native
& dist/phase4-5/wheel-env/Scripts/python.exe -I dist/phase4-5/desktop.py restore dist/phase4-5/ui-native
& $python dist/phase4-5/long-benchmark.py dist/phase4-5/long-screens
& $python dist/phase4-5/bulk.py dist/phase4-5/bulk-verified
$env:QT_SCALE_FACTOR = '0.6666666666666666'
& $python dist/phase4-5/display.py 100 dist/phase4-5/display-retry
$env:QT_SCALE_FACTOR = '0.8333333333333334'
& $python dist/phase4-5/display.py 125 dist/phase4-5/display
$env:QT_SCALE_FACTOR = '1'
& $python dist/phase4-5/display.py 150 dist/phase4-5/display
Remove-Item Env:QT_SCALE_FACTOR
$env:QT_QPA_PLATFORM = 'offscreen'
& $python dist/phase4-5/ui-checks.py offscreen dist/phase4-5/sanitized
```

## Security and release inputs

- Gitleaks scanned eight Git commits and reported zero findings. A
  separate path audit found no tracked environment files, private keys,
  SQLite histories or SQLite sidecars. Git author and committer addresses
  were GitHub noreply throughout, and the origin URL contained no userinfo.
  Existing ignore rules cover fixture databases, logs, certificates,
  and `dist/`. Use an ignored directory for custom INI files. Pattern
  scans and file checks have finite coverage.
- OSV batch lookup returned zero matches for the resolved GUI runtime
  Python packages, including dnspython 2.9.2, PySide6 Essentials and
  Shiboken 6.12.0, pyqtgraph 0.14.0, NumPy 2.5.5 and colorama 0.4.6.
  The bootstrap pip 25.0.1 returned 12 advisory records, with aliases
  in separate records. The three fresh install environments used a
  pip 26.3 wheel whose SHA-256 matched PyPI. A repeat OSV lookup for
  pip 26.3 returned zero matches. [OSV batch API](https://google.github.io/osv.dev/post-v1-querybatch/) ·
  [pip 26.3 provenance](https://pypi.org/project/pip/26.3/).
- The Python wheel uses the host's CPython, SQLite, OpenSSL, and trust
  store. The audit interpreter predates Python 3.12.16 security fixes
  and SQLite 3.54.0 security fixes. OpenSSL 3.5.8 also predates vendor
  fixes. A release that supplies a runtime needs a patched build and a
  fresh security and smoke check against the actual bytes chosen for
  distribution. NetCraft opens local history paths explicitly and has
  no upload or remote SQLite import. SQLite's advisory scope includes
  untrusted database files and features outside the NetCraft schema.
  [Python 3.12.16 security notes](https://www.python.org/downloads/release/python-31216/) ·
  [SQLite advisories](https://www.sqlite.org/cves.html) ·
  [OpenSSL 3.5 advisories](https://openssl-library.org/news/vulnerabilities-3.5/index.html).
- Source review found no process spawning or shell command construction in
  the application. Loopback echo fixtures bind explicit addresses and
  ephemeral ports. Engine tests cover input validation, socket deadlines,
  cancellation, and bounded worker cleanup. The export regression verifies
  workspace-file protection, explicit replacement, atomic publish, cleanup
  and redaction. Source review verified that the destination guard resolves
  the selected path and reserves the active database, sidecars and file-based
  preferences.

```powershell
gitleaks git --no-banner --no-color --redact=100 --log-opts='--all' --report-format json --report-path dist/phase4-5/gitleaks-history.json .
git ls-files -- '*.sqlite*' '*.db' '*.env' '*.pem' '*.key'
git check-ignore .env dist/phase4-5/fixture.sqlite3 fixture.sqlite3 fixture.sqlite3-wal fixture.sqlite3.monitor.lock
```

## Release prerequisites and limitations

| Severity | Finding |
| --- | --- |
| Release prerequisite | Confirm the patch levels of the Python, SQLite and OpenSSL runtime eventually selected for distribution. These local wheel tests did not select or bundle a release runtime. |
| Display limitation | At the host's 150% scale, the 1920 × 1080 logical case still exceeds the available work area. Native Qt verified normal and maximized windows within the available sizes. |
| Known test-host diagnostic | Windows COM code `0x8001010d` appeared in Phase 4.4. It did not appear in the final Phase 4.5 native pytest run. Its earlier native call site remains unidentified. |
| Coverage limitation | Older Windows builds, live VPN or split DNS policy, Linux native GUI, multi-hour or multi-day stability, network shares and other physical displays are unverified. Reaching an actual local Windows resolver and IPv6 loopback does not establish coverage of every network configuration. |

## Phase 4 closure

The owner-confirmed desktop checks and integrated closure runs passed on
November 21, 2026. The full offscreen suite passed 239 tests in 64.69 s.
The full Windows Qt suite passed 239 tests in 75.54 s. Both exited 0.
Ruff lint and format, strict MyPy, `pip check` and `git diff --check`
also passed.

**PASS for Phase 4.** The integrated baseline is ready for local Phase 5
release engineering. The display limits and runtime release prerequisites
above still apply.
