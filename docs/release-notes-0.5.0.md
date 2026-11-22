# NetCraft v0.5.0

NetCraft v0.5.0 is a source-only release. The portable Windows ZIP and installer
are unavailable for this release. Use the GitHub source archive to install
the CLI or the optional Windows desktop app with Python.

## Highlights

- TCP, UDP, DNS, HTTP and HTTPS diagnostics, with timing and TLS verification.
- Finite endpoint monitoring, local SQLite history, and reliability and latency
  summaries.
- Seven desktop pages for overview, diagnostics, monitoring, history, analytics,
  exports and settings.
- Windows system DNS support for a blank resolver field, alongside explicit IPv4
  and IPv6 DNS diagnostics.
- History deletion stays reflected across refresh and restart. Retention shows a
  preview and asks for a new preview if the affected rows change.
- CSV and JSON export use atomic replacement and protect active workspace files.
- MIT license for the NetCraft project. The source archive includes `LICENSE`.

## Install and compatibility

Extract the GitHub source ZIP or tarball and open its root folder. Use Python
3.12 or newer and run `python -m pip install .` for the CLI. Use
`python -m pip install '.[gui]'` for the desktop app, then run `netcraft-gui`
from that environment. Existing history and preferences keep their current
locations. For disposable testing, pass both `--history` and `--settings`
with separate paths. See `README.md` in the source archive for PowerShell steps.

Windows 11 x64 is the verified desktop environment. Older Windows versions,
ARM64, Linux and macOS desktop operation, live VPN and split DNS behavior, and
long-duration stability remain outside this verification. The tested display's
work area does not accommodate a 1920×1080 logical window at 150% scale.
