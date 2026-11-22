# Phase 4.1 acceptance report

Windows acceptance was performed on November 20, 2026 using Windows 11 build 26300
(AMD64), Python 3.12.14, SQLite 3.53.1, Qt and PySide6 Essentials 6.12.0, dnspython
2.9.2, pyqtgraph 0.14.0, and NumPy 2.5.5. Phase 4 commit
`682b1b204fcbcdd17a11d28086bf8cffef4f8508` was the baseline. All test histories,
preferences, fixture certificates, and report exports used separate disposable paths.

## DNS correction

The GUI already passed `None` for a blank resolver. The engine called
`dns.resolver.Resolver()`, took its flat nameserver list, and sent direct DNS to the
first candidate. On this host dnspython used its Windows registry discovery backend.
The registry still contained an enabled Wi-Fi adapter's DNS address while the adapter
was disconnected. The engine's list and direct sockets lost the interface
eligibility and routing information Windows ordinarily uses.

The corrected engine delegates automatic Windows queries to `DnsQueryRaw` with no
custom DNS servers and automatic interface selection. Windows applies configured
interface selection, caching, retries, and applicable policy. NetCraft validates
and parses the returned wire answer, bounds the wait by the per-query timeout and
overall lifetime, and requests native cancellation if the wait ends early. The
native adapter bounds outstanding request handles at 32 and frees API-owned results
in the completion callback. The explicit-server and non-Windows paths remain direct
DNS. See [resolver behavior and result fields](dns-diagnostics.md).

The live host had active Ethernet and disconnected Wi-Fi throughout these
observations. All queries in this comparison requested the A record for
`example.com`. Values are individual observations.

| Path | Observed result | Elapsed |
| --- | --- | ---: |
| Phase 4 CLI, resolver blank | First UDP attempt went to the disconnected-interface-only resolver and timed out. The next configured resolver answered. | 1,021.085 ms |
| Phase 4.1 CLI, resolver blank | NOERROR, two records, `windows_system`, upstream endpoint unavailable | 27.158 ms |
| Phase 4.1 desktop, resolver blank | NOERROR, two records, **System resolver** | 16.669 ms |
| Phase 4.1 CLI, explicit `1.1.1.1` | NOERROR, two records, UDP to the requested port 53 | 11.431 ms |
| Phase 4.1 desktop, explicit `1.1.1.1` | NOERROR, two records, resolved server displayed as `1.1.1.1 port 53` | 9.476 ms |

The baseline used a one-second attempt timeout and a three-second overall lifetime.
The successful Windows system observations exposed no source IP or per-upstream
attempt history. In system mode NetCraft sent no direct resolver requests and
reported fallback as unknown. The system results therefore demonstrate that the app
no longer selects the inactive adapter directly. They do not identify which server
Windows used or whether Windows answered from a cache.

```powershell
netcraft dns example.com --type A --json
netcraft dns example.com --type A --resolver 1.1.1.1 --json

New-Item -ItemType Directory -Force .\dist\acceptance
netcraft-gui --history .\dist\acceptance\history.sqlite3 --settings .\dist\acceptance\preferences.ini
```

## Desktop acceptance

The normal Windows launcher was driven with OS mouse and keyboard input. The clean
launch displayed zero targets, stopped monitoring, no runs and no latency points.
The installed wheel reopened the same disposable database and restored the saved
light theme and the previously captured TCP run. The sidebar collapsed and expanded,
and the Diagnostics, Live Monitor, and Analytics keyboard navigation and top-bar
actions were exercised.

| Page | Native Windows result | Evidence |
| --- | --- | --- |
| Overview | Pass for empty startup, navigation, and restored history and theme after restart. | Zero targets and no measurements on first launch. The restarted launcher displayed the stopped state and three stored samples. |
| Diagnostics | Pass for automatic and explicit DNS, sanitized result copy, JSON save and cancellation of the save dialog. Native Qt automation also passed the local protocol matrix below. | [System DNS](phase-4-1-evidence/native-system-dns.jpg), [explicit DNS](phase-4-1-evidence/native-explicit-dns.jpg) |
| Live Monitor | Pass for adding a loopback TCP target, starting a finite run, observing counters and chart updates, and stopping the run. Native Qt automation checked all five protocols and cancelled a monitor after a silent fixture confirmed receipt of the pending datagram. | Three of three TCP observations succeeded and remained available after stopping. [Five-protocol run](phase-4-1-evidence/five-protocol-monitor.png) |
| History | Pass for run details, UTC timestamps, sample inspection, pagination, declining deletion, and restart persistence. | Opened the selected TCP run, inspected telemetry, then opened page two of the larger disposable fixture. |
| Analytics | Pass for timeline and target, protocol, and error comparisons with real TCP data. | [Analytics](phase-4-1-evidence/native-analytics.jpg) |
| Export | Pass for native save, cancel, overwrite decline, full CSV and JSON, filtered samples, reliability summary, and latency trend. | [Saved report](phase-4-1-evidence/native-export.jpg) |
| Settings | Pass for changing the theme, restarting, and declining destructive retention. Native Qt automation also changed, saved, reopened, and reset every supported preference. | [Light theme](phase-4-1-evidence/native-settings.jpg) |

The entire-run CSV contains one run row and three sample rows. The sample IDs, run
IDs, UTC observed timestamps, statuses and integer nanosecond latencies matched
the database. Reopening the diagnostic JSON confirmed an A answer, NOERROR, the
system resolver label, two records, and elapsed nanoseconds. The successful TCP
latencies were 633,500, 327,900, and 559,600 ns in time order. Independently computed
median was 559,600 ns, p95 and p99 were 633,500 ns using nearest rank, and jitter
was 268,650 ns using the mean absolute change between consecutive timings for the
same target. The three attempts gave a 100% success rate, 0% failure and timeout
rates, and no failure streak. The GUI values agreed at its display precision.

The final wheel's native Export page saved a separate 10,000-sample synthetic
fixture as a 2,158,387-byte CSV and a 6,918,149-byte JSON file. Both matched
SQLite sample IDs, timestamps, statuses, integer latency, null timing, and a
target containing Unicode, a comma, and quotes. The summary and trend also
matched SQLite, and a DNS-only filter against the TCP fixture returned zero
rows. Declining the native replacement prompt preserved the JSON file hash.

Additional tests used Qt Test input and real loopback traffic with the Windows platform
plugin. The desktop handled TCP success, refusal, timeout, and invalid host and port.
UDP echoed a valid response and handled a silent endpoint and active cancellation.
DNS returned A, AAAA, CNAME, MX, NS, TXT, SOA, CAA, SRV, and PTR answers and handled
NXDOMAIN, an empty answer, a silent resolver, and invalid input. HTTP GET and HEAD
returned 200, 302 preserved the redirect, and 404 and 500 remained valid HTTP responses.
A malformed response and a response timeout produced separate errors. HTTPS showed
verified TLS timings with an ephemeral CA, rejected an untrusted certificate and a
trusted certificate for the wrong hostname, and reported premature closure during
negotiation. The CA was scoped to each diagnostic or monitor target.

The mixed fixture run scheduled 35 slots and produced 14 successes across all
five protocols, three NXDOMAIN failures, one timeout, and 17 skips. Recalculation
from successful latencies matched the 19.069 ms median, 170.723 ms p95 and p99,
and 18.010 ms jitter. There were 18 completed attempts. Success, failure, and
timeout rates were 77.78%, 22.22%, and 5.56%. The failure rate includes timeouts.
The run also exercised navigation and resizing while monitoring was active.
A separate check closed the window while a UDP diagnostic was awaiting a
response and confirmed the worker pool drained.

Native client-area checks used effective Qt scaling on this one display. All
seven routes were opened and primary navigation was visible at 1920×1080,
1600×900, 1366×768, and 1280×720 with device pixel ratios 1.0 and 1.25. At the
host's 1.5 device pixel ratio, the 1920×1080 logical client exceeded the available
1707×960 logical work area. The seven routes were exercised at the other three
client sizes at 1.5. Qt scaling overrides were process-local. Physical display
mode changes and a full control-by-control visual review at each size remain untested.

## Verification

The offline suite includes the original 212 cases and 17 added cases. It covers the
Windows policy boundary with simulated interface and split-DNS selections, native
completion and cancellation, IPv4 and scoped IPv6 source decoding, explicit
resolver isolation, negative answers, deadlines, portable resolver fallback,
socket and worker cleanup, and GUI propagation of the blank field. The mandatory
suite uses loopback fixtures and mocks for the Windows DNS Client boundary.

| Check | Result |
| --- | --- |
| Full offline pytest, Windows with Qt offscreen | 229 passed, 0 failed, 0 skipped |
| Full offline pytest, native Windows Qt | 229 passed, 0 failed, 0 skipped. The test host printed Windows COM code `0x8001010d` during startup. Running the first four GUI cases on the Phase 4 baseline reproduced the same diagnostic and all four passed. |
| Ruff lint and format check | Passed |
| Strict MyPy | Passed for Windows and simulated Linux typeshed targets |
| Editable install with development and desktop extras | Passed using Python 3.12 |
| Dependency integrity | `pip check` passed in editable and fresh-wheel environments |
| Isolated sdist and wheel builds | `netcraft_networking-0.4.1.tar.gz` and `netcraft_networking-0.4.1-py3-none-any.whl` built, with the wheel built from the sdist |
| Fresh wheel install and CLI smoke | Installed offline with the GUI extra. TCP and UDP loopback echo both returned the original bytes. The installed desktop app opened normally. |
| History schema | Existing schema identity and compatibility tests passed. A Phase 4.1 fresh-wheel restart opened the version 1 history created by the editable launch. No schema change or migration was needed. |

## Performance

One repeat of the Phase 4 native loopback TCP, UDP and HTTP workload used 75
slots per target and a 0.2-second interval. It accounted for 225 slots over
15.131 seconds, including 223 successful probes and two skips. A 20 ms Qt
heartbeat measured a 17.487 ms median and a 33.813 ms maximum across 756
callbacks. A separate 30-slot allocation pass measured a 318,524-byte peak
above baseline and 151,382 retained bytes in Python-tracked memory. The Qt pool
joined and zero engine or fixture threads remained. A shorter concurrent
verification run accounted for 112 successes and eight skips in 120 slots.
Its maximum heartbeat gap was 38.931 ms. Python allocation tracking omits
native Qt and OS/DNS Client allocations. These short runs cannot establish
long-term resource behavior. [Historical Phase 4 benchmark](desktop-studio.md#native-captures-and-measurements)

```powershell
$env:QT_QPA_PLATFORM = 'windows'
python -m benchmarks.desktop_studio --samples 75 --interval 0.2
```

## Security and remaining coverage

Live diagnostics were limited to the selected test domain and explicit resolver.
Other verification used loopback fixtures or local SQLite. No Windows network
settings were changed. Startup displayed no probe or monitoring activity. The
suite asserts no probe dispatch on startup, and no packet capture was taken.
Local HTTPS tests use certificate and hostname verification with an ephemeral CA.
History and exports retain the existing metadata allowlist and exclude sensitive
HTTP fields. Evidence images contain only the disposable workspace and its
synthetic or public test values.

- **Medium, older Windows builds.** To verify automatic resolution on another
  Windows version, launch NetCraft and run a blank-resolver A diagnostic. If
  `DnsQueryRaw` is unavailable, the expected result is `dns_configuration`.
  Explicit DNS remains available. A live VPN, encrypted resolver or NRPT setup
  was not exercised. The tests cover policy delegation and source reporting.
- **Medium, remaining native workflows.** Complete the combinations in the
  [owner acceptance checklist](desktop-studio.md#owner-acceptance-checklist),
  especially confirming deletion and retention against disposable data, a
  denied-write destination through the native dialog, and extended repeated-use
  testing. The automated suite covered export permission errors, history
  deletion and pruning. The native checks declined destructive confirmations.
- **Low, native test-host diagnostic.** Run the GUI export safety module and
  `test_first_launch_and_routes_do_not_probe` with `QT_QPA_PLATFORM=windows` to
  reproduce the Windows COM diagnostic. The native launcher smoke tests and
  monitored sessions completed.
- **Low, platform coverage.** Linux runtime and different physical Windows
  display configurations still need owner verification.

**NOT READY for the next phase.** The DNS correction and
regression suite passed. The full native acceptance matrix still has unverified
items.
