# NetCraft 0.5.0 release readiness

## Phase 5.1 distribution decision

**Public GitHub source release — GO for owner approval.** The source
distribution and wheel contain the MIT project and dependency references.
The Windows runtime and its bundled third-party binaries have a separate
decision. Approve the scope of the public source publication first.

**Public Windows portable ZIP — NO-GO.** The rebuilt ZIP is for local
verification. It removes Qt Network, QML, Quick, SQL and unused optional
plugins. NetCraft retains its Python network stack and Widgets desktop.
Qt Core 6.12.0 and OpenSSL 3.5.9 remain subject to the reviewed advisories.
Matching fixed vendor binaries, verified Qt corresponding-source delivery,
Microsoft redistribution review and a clean Windows 11 x64 run remain open.

[Runtime component, CVE and licensing evidence](phase-5-1-runtime-audit.md) ·
[Clean Windows 11 test procedure](clean-windows-11-verification.md) ·
[Publication checklist](github-publication-checklist.md)

Verification ran on November 21–22, 2026 on the Windows 11 x64 development
host, build 26300. The dedicated branch
`codex/phase-5-1-windows-distribution` starts at
`d0e3f621ffe10e6b8b02ed14e33a55826b0fac69`. App source and telemetry
schema are unchanged. The branch adds a packaging regression, stabilizes
the scheduler test's virtual clock and updates the portable QA harness.
The runtime is 64-bit CPython 3.14.10, SQLite 3.54.1, OpenSSL 3.5.9 and
Qt 6.12.0. `NetCraft.exe` targets .NET Framework 4.8 and is unsigned.

### Exact artifacts

| Local artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `release-51-source/netcraft_networking-0.5.0.tar.gz` | 371,939 | `01ef28c2f87725af60bb821f4a69ad2d02999acd86b7042d9f377024e4b58c6c` |
| `release-51-source/netcraft_networking-0.5.0-py3-none-any.whl` | 102,787 | `f31a9176b60f54f837fb42f28362afb4f2435d8d75862b53963d651a39ac27e8` |
| `release-51-portable/NetCraft-0.5.0-win-x64.zip` | 44,854,696 | `f7336fa1527d632bde20c5e8e4b95bbd89eb8651dfec0f06fd93b6c1f1bae04e` |

Both offline portable builds used the same wheel and pinned vendor inputs.
The second used a different output path with a space. Their ZIP digests
match. The build and independent audit verified CRCs, member SHA-256,
extracted bytes, the runtime manifest's wheel digest and the ZIP sidecar
checksum. The ZIP has 865 members and 58 supplemental attribution and
license files. The PE inventory covers 77 native files. The source wheel
contains no DLL, PYD or EXE payload. The audit checked for personal
worktree paths, database files, settings, credentials, temporary files and
development payloads. Color-map CSV files are PyQtGraph assets.

### Reproduction commands

```powershell
$python = '.\.venv\Scripts\python.exe'
$base = 'dist/phase5-1'
$env:PIP_NO_INDEX = '1'
$env:PIP_FIND_LINKS = (Resolve-Path 'dist/phase4-wheels').Path
$env:SOURCE_DATE_EPOCH = '1795219200'
& $python -m build --outdir "$base/release-51-source" --env-dir "$base/release-51-build-env" --sdist-extract-dir "$base/release-51-sdist"
$wheel = "$base/release-51-source/netcraft_networking-0.5.0-py3-none-any.whl"
$builder = 'dist/phase4-5/wheel-env/Scripts/python.exe'
foreach ($folder in 'release-51-portable', 'release-51-repeat space') {
    & ./packaging/windows/build-portable.ps1 -Wheel $wheel -BuildPython $builder -OutputDirectory "$base/$folder" -CacheDirectory "$base/cache" -Offline
    Get-FileHash -LiteralPath "$base/$folder/NetCraft-0.5.0-win-x64.zip"
}
$zip = "$base/release-51-portable/NetCraft-0.5.0-win-x64.zip"
& $python -I -B packaging/windows/bundle_contents.py verify $zip
Expand-Archive -LiteralPath $zip -DestinationPath "$base/Release 51 Relocation"
$env:QT_QPA_PLATFORM = 'offscreen'
& $python -m pytest -q
$env:QT_QPA_PLATFORM = 'windows'
& $python -m pytest -q
& $python -m ruff check .
& $python -m ruff format --check .
& $python -m mypy --strict src tests benchmarks packaging/windows
& $python -m pip check
git diff --check
gitleaks git --log-opts='--all' --redact --no-banner .
```

The release checks use two fresh offline Python 3.12 environments. Each
receives the locally verified pip 26.3 wheel, then installs exactly one
candidate artifact with the `[gui]` extra. The sdist installation uses
setuptools 84.2.1 with `--no-build-isolation`. The smoke commands are the
installed `netcraft tcp-echo` and `netcraft udp-echo` with `--json`,
`python -m pip check` and an offscreen empty-workspace launch and close.

The packaged workflow uses the existing disposable peer service script,
`verify_fixtures.py`. `verify-launcher.ps1` starts the extracted executable
with disposable history and preferences. The embedded Python then runs
`verify_portable.py exercise`, `reopen` and `stress` against the exact
archive as documented in the [packaging guide](windows-portable.md#exercise-the-extracted-zip).
These are native Qt widget tests with programmatic controls and a supplied
file picker result. The owner's manual Save As acceptance is recorded in
the Phase 4.5 report.

### Final verification results

| Check | Observed result |
| --- | --- |
| Offscreen regression suite | 240 passed in 88.15 s. |
| Native Windows Qt regression suite | 240 passed in 87.89 s. |
| Ruff and strict MyPy | Ruff lint and format passed with 96 files formatted. Strict MyPy passed on 78 source files. |
| Dependency integrity | `pip check` passed in the development, fresh source and fresh wheel environments. |
| Fresh source and wheel installs | Both installed 0.5.0 from the exact local artifacts and passed TCP and UDP CLI echo and an offscreen desktop launch with zero runs and points. |
| Native executable lifecycle | `NetCraft.exe` opened a responsive native window using bundled `pythonw.exe`. It closed with exit 0 and left a valid, empty isolated history. This host reported .NET Framework release value 534296 and Authenticode status `NotSigned`. |
| Qt deployment | The loader resolved Qt Core, Gui, Widgets, `qwindows.dll` and `qmodernwindowsstyle.dll` from the extracted ZIP. The native application instantiated the Windows 11 style. All 77 PE files had dependencies accounted for on this host, with zero normal or delay imports of the removed Qt modules. |
| Native workflow and diagnostics | All seven pages opened. Five loopback protocol checks succeeded, including a verified TLS chain and hostname and a DNS reply from the explicit fixture resolver. First launch had no data, queued monitoring or resolver calls. |
| Monitoring and cancellation | The finite TCP run stored three successes. Cancelling an in-flight UDP probe produced a second run and one cancelled sample. Worker shutdown completed. |
| CSV, JSON and persistence | The CSV and JSON files were 1,089 and 2,789 bytes, and their rows matched the harness's SQLite checks. A separate packaged process restored two runs, four samples and the 4.25-second setting. SQLite integrity and foreign keys passed, and the database accepted a write transaction after shutdown. |
| Packaged stress test | 180 of 180 samples succeeded and all 180 points rendered in 29.56 s. The 20 ms Qt heartbeat had 1,474 observations, with 18.38 ms median and 52.41 ms maximum spacing. Process working set was 153,763,840 bytes initially and 165,216,256 bytes at the sampled peak. |
| Vulnerability lookup | OSV returned no records for the eight exact shipping and build packages queried. The older development-environment pip finding and the native vendor advisories are recorded separately in the [runtime audit](phase-5-1-runtime-audit.md). |
| Secrets and Git checks | Gitleaks found no secrets in the 10 reachable baseline commits or on the verified index. The exact extracted ZIP returned six generic API-key flags. Review showed all six were Python key type annotations and class references in unmodified dnspython vendor files, with no key values. Working and staged whitespace checks passed. |

Four packaged widget screenshots and raw command logs remain local under
`dist/phase5-1`. The source and the ZIP contain no test history or logs.
The independent archive audit repeated after shutdown and confirmed the
extracted files remained byte-identical to the ZIP.

The initial packaging trial surfaced two omissions in the packaging QA
path. The Qt source attribution files contained literal control characters
in some JSON strings, so the extractor accepts the pinned input format and
writes normalized JSON. The portable harness imported QtTest after QtTest
was removed. It now drives the same controls through Qt Widgets.

An earlier native full run timed out waiting for Overview during the
persistence test. The isolated persistence test passed on retry. A second
native run exposed the scheduler fairness test's mix of fake monotonic time
and real wall-clock time. Equal Windows observation timestamps fell back to
random sample ID ordering. The test now uses a virtual UTC clock tied to
the simulated slot times. Production code, test assertions, and timeouts
are unchanged.

### Clean environment and redistribution gates

No clean guest was available. Windows Sandbox's executable was absent,
and no Hyper-V management, VM service or installed third-party hypervisor
was found. The developer-host test used the exact ZIP extracted to a new
folder and disposable state. Clean Windows 11 loading, DLL prerequisites
and the actual downloaded unsigned-binary trust experience remain
unverified. The manual procedure records those outcomes under normal
Windows security settings. [Clean Windows 11 acceptance](clean-windows-11-verification.md)

The ZIP includes the pinned Qt and CPython source-based notices and a
binary inventory. It keeps Qt DLLs, plugins and bindings separately
replaceable. The audit documents the remaining work to match the installed
wheel to its exact corresponding source and build inputs, approve the
recipient delivery method and verify the CRT distribution terms.

## Phase 5 baseline evidence

The remainder of this report records the earlier Phase 5 build and
verification. Those artifacts and digests are distinct from the Phase 5.1
candidate described above.

**PARTIAL for public release.** Fresh source and wheel installations passed,
and the exact portable ZIP passed native Windows verification. Public binary
distribution remains gated by the bundled Qt and OpenSSL advisories, the
corresponding-source review and owner approval.

## Baseline and version

Phase 4 closed locally at `5f9fe3592469124ecf59dddcc6ae1890e6540736`.
The owner confirmed all seven desktop pages, normal layouts and both paths
through Windows Save As overwrite. The integrated closure rerun passed
239 tests offscreen and 239 using Windows Qt. The Phase 4 branch retains
the Windows DNS correction, History stale-row fix, retention preview and
export safeguards. [Phase 4 closure](phase-4-5-final-desktop-acceptance.md)

Phase 5 is on `codex/phase-5-release-engineering` from that closure commit.
The candidate version is **0.5.0** for the new portable distribution path.
The owner selected MIT for the project. Application networking, monitor
scheduling, telemetry, the SQLite schema and GUI source are unchanged.

## Environment

Windows 11 build 26300 x64 and PowerShell 7.6.9 were used on November 21, 2026.
Source and wheel checks used Python 3.12.14 and pip 26.3 in fresh environments.
The portable runtime uses CPython 3.14.10, OpenSSL 3.5.9, SQLite 3.54.1 and
Qt 6.12.0. The runtime manifest records the Python wheel dependency versions.
The build used upstream vendor hashes and an exact Windows x64 wheel lock.

All application checks used disposable datasets and loopback fixtures. The
launcher and widget tests passed both history and preferences paths explicitly.
Windows networking settings were outside the verification scope.

## Build and verification commands

```powershell
$python = '.\.venv\Scripts\python.exe'
$env:PIP_NO_INDEX = '1'
$env:PIP_FIND_LINKS = (Resolve-Path 'dist/phase4-wheels').Path
& $python -m build --outdir dist/phase5/release-source --env-dir dist/phase5/release-build-env --sdist-extract-dir dist/phase5/release-sdist-tree
& ./packaging/windows/build-portable.ps1 -Wheel dist/phase5/release-source/netcraft_networking-0.5.0-py3-none-any.whl -BuildPython dist/phase4-5/wheel-env/Scripts/python.exe -OutputDirectory dist/phase5/release-portable -Offline
& $python dist/phase5/audit_artifacts.py
Expand-Archive -LiteralPath dist/phase5/release-portable/NetCraft-0.5.0-win-x64.zip -DestinationPath 'dist/phase5/Verified Relocation'
& ./dist/phase5/verify-release-installs.ps1
```

The local installation harness created two new Python virtual environments,
installed pip 26.3 from the offline cache, and installed each artifact with
the `[gui]` extra. For the sdist it installed setuptools 84.2.1 first and
used `pip install --no-build-isolation`. It ran `python -m pip check`, both
`netcraft tcp-echo` and `netcraft udp-echo` with `--json`, then launched and
closed an empty Qt workspace using `QT_QPA_PLATFORM=offscreen`.

The extracted-bundle commands used the repository paths below. The launcher
and widget checks were run with the current directory set outside the bundle.
The fixture was started in a separate terminal and stopped with `finished`.

```powershell
$env:PYTHONPATH = (Get-Location).Path
& $python packaging/windows/verify_fixtures.py dist/phase5/verified-fixtures
```

```powershell
$root = (Resolve-Path dist/phase5).Path
$bundle = Join-Path $root 'Verified Relocation/NetCraft-0.5.0-win-x64'
$fixture = Join-Path $root 'verified-fixtures'
$portable = Join-Path $bundle 'runtime/python.exe'
$qa = (Resolve-Path packaging/windows/verify_portable.py).Path
$launcher = (Resolve-Path packaging/windows/verify-launcher.ps1).Path
$env:QT_QPA_PLATFORM = 'windows'
Push-Location -LiteralPath $root
try {
    & $launcher -Bundle $bundle -Workspace (Join-Path $root 'Verified Launcher Workspace')
    & $portable -I -B $qa exercise (Join-Path $root 'Verified Widget Workspace') $fixture
    & $portable -I -B $qa reopen (Join-Path $root 'Verified Widget Workspace') $fixture
    & $portable -I -B $qa stress (Join-Path $root 'Verified Stress Workspace') $fixture
} finally {
    New-Item -ItemType File -Path (Join-Path $fixture 'finished') | Out-Null
    Pop-Location
}
```

[Portable build and verification instructions](windows-portable.md) ·
[Release notes draft](release-notes-0.5.0.md) ·
[GitHub publication checklist](github-publication-checklist.md)

## Candidate artifacts

All paths are under the ignored `dist/phase5` directory. The archive audit
checked file names, required runtime and license files, wheel SPDX metadata,
ZIP checksum and absolute build-path leakage in text files. It found no
history files, preferences, fixture certificates or keys, logs, `.pyc` files,
or local pip origin metadata in the final artifacts. The initial audit caught
two bytecode files already present in the PySide6 wheel. The build now removes
them and the final ZIP was rebuilt, extracted and tested. An initial source
build also needed an explicit writable sdist extraction directory on this
Windows host. The verified build specifies it.
All 4,232 ZIP file CRCs passed a separate `ZipFile.testzip()` check. The
relocated directory contained the exact archived file list after the native
tests, including after shutdown and relaunch.

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `release-source/netcraft_networking-0.5.0.tar.gz` | 363,511 | `7f4859dd4ca44eb3787c99ae095091dd21ecfaa3366956c6d41baf7ff16c53b2` |
| `release-source/netcraft_networking-0.5.0-py3-none-any.whl` | 102,787 | `bdd6710c96c2c6fc92f76f59e4e3ac0a3f48c5ecafe9c124d774ab3e08b8afa3` |
| `release-portable/NetCraft-0.5.0-win-x64.zip` | 106,232,490 | `cb914eba9364c3b27678ffabd1b8802b360abc38f822a93c073a3c5e3fb1fb6b` |

## Engineering audit

The source audit followed the diagnostic worker from input parsing through the
socket deadline, cancellation token, completion dispatch and cleanup. The
tests cover successful and refused TCP, UDP echo and silence, DNS negative
and timeout paths, verified TLS, HTTP parser and response boundaries, and
shutdown while work is active. Monitoring tests check bounded concurrency,
cancelled or skipped observations and recovery after interruption.

History and Export tests cover transactional deletion, contention, retention
preview and confirmation, interrupted writes, replacement and reserved paths.
The product uses its own SQLite schema and local files. Exports keep workspace
history and preferences reserved, and sanitized HTTP observations omit
credentials, queries and bodies. Launch remains idle until targets are
configured and an operation is requested.

### Regression and static checks

Each pytest run exercised the same 239 tests, including the Phase 4.5
regressions. The Windows Qt run exited cleanly. Static checks used the
development virtual environment and included the packaging Python scripts.

```powershell
$python = '.\.venv\Scripts\python.exe'
$env:QT_QPA_PLATFORM = 'offscreen'
& $python -m pytest -q
$env:QT_QPA_PLATFORM = 'windows'
& $python -m pytest -q
& $python -m ruff check .
& $python -m ruff format --check .
& $python -m mypy --strict src tests benchmarks packaging/windows
& $python -m pip check
git diff --check
git diff --cached --check
```

| Check | Result |
| --- | --- |
| Pytest, offscreen Qt | 239 passed in 93.08 s |
| Pytest, native Windows Qt | 239 passed in 72.20 s |
| Ruff lint and format | Passed. 91 files already formatted. |
| Strict MyPy | Passed. 75 source files checked. |
| pip dependency consistency | Passed in development, source and wheel environments. |
| Git whitespace validation | Passed for the working diff and staged changes. |

## Installed and packaged smoke tests

| Check | Observed result |
| --- | --- |
| Fresh source install | PASS. Installed `0.5.0` from the source archive with GUI dependencies. Both CLI echo tests succeeded, `pip check` passed, and the empty GUI showed zero runs and points. |
| Fresh wheel install | PASS. Repeated the install, dependency, CLI and empty workspace checks from the exact wheel in a distinct environment. |
| Normal Windows launcher | PASS. `NetCraft.exe` spawned the bundled `pythonw.exe`. The OS reported a responsive native window. Closing the window returned exit `0` and left a valid, empty SQLite database outside the bundle. |
| First launch and navigation | PASS. The native widget harness traversed Overview, Diagnostics, Live Monitor, History, Analytics, Export and Settings. Empty state had zero runs and points and no active jobs or resolver calls. |
| Diagnostics | PASS. TCP, UDP, DNS, HTTP and HTTPS each completed against a loopback fixture. HTTPS verified against the disposable CA. |
| Monitoring and cancellation | PASS. One finite TCP run stored three samples. A second run was cancelled after the silent UDP fixture received a datagram. It stored one cancelled sample, and the Qt pool joined at shutdown. |
| History, analytics and exports | PASS. History and charts showed the stored observations. Native CSV and JSON workers produced 1,091 and 2,791 bytes respectively, with all four sample identifiers matching SQLite. The harness supplied the picker result. The actual Windows overwrite dialog had already been exercised by the owner in Phase 4. |
| Restart and integrity | PASS. A second packaged process restored the 4.25-second timeout, two runs and four samples. SQLite integrity and foreign-key checks passed, and the database was writable after shutdown. |

The packaged diagnostic results reported 0.737 ms for TCP, 0.729 ms for UDP,
13.115 ms for custom-resolver DNS, 2.669 ms for HTTP and 73.382 ms for
verified HTTPS. These were individual loopback observations.

The screenshots were captured from actual Windows Qt widgets running in the
extracted bundle with synthetic targets and workspace data.
[Empty Overview](phase-5-evidence/portable-overview.png) ·
[Verified HTTPS](phase-5-evidence/portable-https.png) ·
[Finite monitoring](phase-5-evidence/portable-monitor.png) ·
[Analytics after cancellation](phase-5-evidence/portable-analytics.png)

### Packaged responsiveness

Sixty half-second slots across TCP, UDP and HTTP produced **180 successes
and 180 rendered points in 29.58 seconds**, with zero skips. The 20 ms Qt
timer delivered 1,473 observations with median spacing of 20.08 ms and a
maximum of 43.80 ms. The observed process working set was 147.24 MiB before
monitoring, 158.17 MiB at the sampled peak, and 157.94 MiB at the end. The
window closed and its workers joined. These are sampled process-residency
figures for one loopback run. The Phase 4 baseline separately exercised 900
monitoring slots and matched two exports of 150,000 rows to SQLite.
[Phase 4 benchmark](phase-4-5-final-desktop-acceptance.md#performance-and-visual-verification)

## Security and privacy

Gitleaks reached all Git refs with redaction enabled. The incoming baseline
had nine reachable commits and no detected secrets. The index scan and final
reachable scan used the commands below. All reachable author and committer
addresses used GitHub noreply. Tracked path checks found no environment files,
temporary databases, raw logs or key files.

```powershell
gitleaks git --staged --redact --no-banner .
gitleaks git --log-opts='--all' --redact --no-banner .
git ls-files -- '*.sqlite' '*.sqlite3' '*.db' '*.log' '*.pem' '*.key' '.env' '.env.*'
```

The OSV batch check returned no package advisory records for the exact
portable wheels and pip 26.3. The separate scan of all 32 public development
packages found 12 advisory records for the older bootstrap pip 25.0.1.
The fresh installs and portable wheel handling used pip 26.3. The test
environment's older pip does not ship in any candidate artifact.
[OSV batch lookup](https://google.github.io/osv.dev/post-v1-querybatch/) ·
[pip change history](https://pip.pypa.io/en/stable/news/)

The official CPython 3.14.10 archive contained SQLite 3.50.4. The local build
uses the hash-verified upstream 3.54.1 DLL instead, and exercised history,
integrity, foreign keys and exports with the replaced DLL.
[CPython security release](https://www.python.org/downloads/release/python-31410/) ·
[SQLite download and checksums](https://www.sqlite.org/download.html)

The native-library review found advisories outside the Python package scan.
The latest observed public PyPI PySide6 Essentials wheel was 6.12.0.
Qt lists issues affecting that version, including CVE-2026-116337 in Qt
Network and CVE-2026-105924 in Qt Core. NetCraft's probes use Python sockets
and TLS. The application has no Qt HTTP or CBOR diagnostic path, though
the vendor libraries are present. OpenSSL also lists advisories affecting
3.5.9, including CVE-2026-107493 for a particular PEM API use case. The
binary has not been cleared for public distribution on these runtime versions.
[Qt HTTP advisory](https://www.qt.io/blog/security-issue-cve-2026-116337) ·
[Qt Core advisory](https://www.qt.io/blog/security-advisory-cve-2026-105924) ·
[OpenSSL 3.5 advisories](https://openssl-library.org/news/vulnerabilities-3.5/index.html)

The PyPI Qt for Python wheel metadata identifies open-source licensing
alternatives. The wheels' license-file declarations included the commercial
notice. The build keeps that notice, adds the upstream GNU texts and Qt
exception, preserves vendored notices and includes a source and component
inventory. Replaceable DLLs and plugins remain separate. The exact Qt
corresponding-source delivery and redistribution review are publication gates.
[Third-party notices](../THIRD_PARTY_NOTICES.md) ·
[Qt distribution guidance](https://www.qt.io/development/open-source-lgpl-obligations) ·
[Qt community distribution documentation](https://doc.qt.io/qtforpython-6/commercial/index.html)

## Remaining gates

| Type | Status |
| --- | --- |
| Binary security | Review patched Qt and OpenSSL inputs when available. Rebuild and retest the exact ZIP before publishing it. |
| Binary redistribution | Confirm exact Qt and other component terms and arrange corresponding-source delivery for the published artifact. |
| Owner approval | Review the local branch, version, release notes and the source and binary publication decisions separately. |
| Compatibility | Validated on one Windows 11 x64 host. Clean Windows VM, older Windows, ARM64 and non-Windows desktop verification remain open. The ZIP is unsigned. |
| Display and duration | The prior 150% scaling limit still applies. The finite local workloads establish the observed responsiveness and cleanup, with multi-hour stability outside scope. |
