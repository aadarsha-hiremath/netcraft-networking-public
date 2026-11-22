# Build and verify NetCraft portable for Windows x64

The ZIP contains a small Windows launcher, an isolated CPython runtime, the
NetCraft wheel, PySide6 Essentials, Qt shared libraries and the Windows platform
plugin. Verify the SHA-256 from the approved release record before extracting
the whole archive. Keep `NetCraft.exe`, `portable_entry.py`, `app` and `runtime`
together in a user-writable folder. The launcher targets .NET Framework 4.8
and depends on Windows system libraries. CPython, Qt and vendor copies of the
Visual C++ runtime are bundled. A separate Python installation is unnecessary.
The development host is Windows 11 x64. [Clean Windows 11 procedure](clean-windows-11-verification.md)

NetCraft stores normal user history and preferences outside the extracted
folder. Existing users keep their history. New profiles start with an empty
workspace. For testing on an existing profile, pass both `--history` and
`--settings` with disposable paths outside the extracted folder.

## Build from the repository

Use PowerShell 7 on Windows x64 with .NET Framework 4.8, Python 3.12 or newer,
the `build` package and an updated pip. Roslyn 4.14.0 and .NET Framework 4.8
reference assemblies are fetched and hash-checked as build inputs. The script
uses fresh output directories and leaves existing builds intact.

```powershell
$build = '.\.venv\Scripts\python.exe'
& $build -m pip install 'pip==26.3' build setuptools
& $build -m build --outdir dist/release/source --env-dir dist/release/build-env --sdist-extract-dir dist/release/sdist-tree
.\packaging\windows\build-portable.ps1 `
    -Wheel dist/release/source/netcraft_networking-0.5.0-py3-none-any.whl `
    -BuildPython $build -OutputDirectory dist/release/portable
Get-FileHash -Algorithm SHA256 -LiteralPath dist/release/portable/NetCraft-0.5.0-win-x64.zip
```

The build pins upstream archives, source snapshots for notices, and the Windows
x64 runtime wheels. It stages the Widgets Qt subset, omitting QML, Qt Network,
Qt SQL, optional decoders, development tools, headers and wheel caches. The
launcher is compiled deterministically. The ZIP has fixed entry timestamps
and modes, sorted paths, a per-file SHA-256 inventory and a runtime DLL list.
The builder verifies each member before writing the ZIP's SHA-256 sidecar.
Keep the same NetCraft input wheel bytes and use the same cache to reproduce
the archive in another fresh output directory. Use `-CacheDirectory` for a
custom cache and `-Offline` to require pre-populated inputs.

```powershell
$wheel = 'dist/release/source/netcraft_networking-0.5.0-py3-none-any.whl'
./packaging/windows/build-portable.ps1 -Wheel $wheel -BuildPython $build -OutputDirectory dist/release/repeat -Offline
Get-FileHash -Algorithm SHA256 -LiteralPath dist/release/portable/NetCraft-0.5.0-win-x64.zip,dist/release/repeat/NetCraft-0.5.0-win-x64.zip
```

The runtime includes CPython 3.14.10, OpenSSL 3.5.9 and the Windows x64
SQLite 3.54.1 DLL. The SQLite DLL supersedes the one in the official CPython
archive. The embeddable Python `._pth` confines imports to the runtime and
the application folder. The launcher also clears inherited Qt plugin and
platform overrides before it starts the GUI. The build-time interpreter
does not ship in the ZIP.
[Python embedding guide](https://docs.python.org/3.14/using/windows.html#the-embeddable-package) ·
[SQLite Windows binaries](https://www.sqlite.org/download.html)

## Exercise the extracted ZIP

The launcher test checks process identity, window responsiveness, empty SQLite
state and normal close. It sends a window-close message only to the child
started for the disposable test. The widget test starts a native Qt process
with the extracted runtime. It keeps all test history, CA files and export
files outside the ZIP. It supplies the export destination in place of the
native picker and exercises the same write worker.

```powershell
$release = 'dist/release'
Expand-Archive -LiteralPath "$release/portable/NetCraft-0.5.0-win-x64.zip" -DestinationPath "$release/Relocation Test"
$bundle = "$release/Relocation Test/NetCraft-0.5.0-win-x64"
.\packaging\windows\verify-launcher.ps1 -Bundle $bundle -Workspace "$release/Launcher Workspace"
```

In a separate terminal, start the local fixture with the development environment's
test extras installed. The script creates a new fixture directory and waits
up to ten minutes.

```powershell
$env:PYTHONPATH = (Get-Location).Path
.\.venv\Scripts\python.exe packaging/windows/verify_fixtures.py dist/release/fixtures
```

With the fixture ready, use the bundled Python from the first terminal.

```powershell
$portablePython = Join-Path $bundle 'runtime/python.exe'
$env:QT_QPA_PLATFORM = 'windows'
& $portablePython -I -B packaging/windows/verify_portable.py exercise "$release/Widget Workspace" "$release/fixtures"
& $portablePython -I -B packaging/windows/verify_portable.py reopen "$release/Widget Workspace" "$release/fixtures"
& $portablePython -I -B packaging/windows/verify_portable.py stress "$release/Stress Workspace" "$release/fixtures"
New-Item -ItemType File -Path "$release/fixtures/finished"
```

For the portable CLI, use `runtime\python.exe -I -B -m netcraft --help`.
To surface a GUI startup exception in a terminal, run
`runtime\python.exe -I -B portable_entry.py` from the bundle folder.

## Distribution conditions

The current launcher is unsigned. SmartScreen or administrator policy may
warn or block execution, especially for a downloaded ZIP. A SHA-256 match
authenticates the bytes only when compared with the checksum from an approved
channel. Keep Windows protections enabled and record blocked launches during
acceptance. Refresh the pinned inputs and reverify the exact distribution
after security or signing changes. Windows ARM64, older Windows versions,
Linux and macOS desktop builds are outside the verified platform scope.
[Third-party notices](../THIRD_PARTY_NOTICES.md)
