"""Exercise the packaging path without installing the Windows vendor runtimes."""

import os
import runpy
import subprocess
import sys
from pathlib import Path
from zipfile import ZipFile


def test_portable_archive_is_reproducible_and_checks_contents(tmp_path: Path) -> None:
    script = Path(__file__).resolve().parents[1] / "packaging" / "windows" / "bundle_contents.py"
    roles = runpy.run_path(str(script))
    fixtures = tmp_path / "inputs"
    for name in roles["QT_FILES"] | {"Qt6Network.dll", "support/generate_pyi.py"}:
        path = fixtures / "PySide6" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name, encoding="utf-8")
    denied = fixtures / "package" / "tests" / "history.sqlite3"
    denied.parent.mkdir(parents=True)
    denied.write_text("disposable data", encoding="utf-8")
    dev = fixtures / "numpy" / "f2py" / "setup.cfg"
    dev.parent.mkdir(parents=True)
    dev.write_text("disposable build settings", encoding="utf-8")
    root = tmp_path / "NetCraft-test"
    subprocess.run(
        [sys.executable, str(script), "stage", str(root / "app"), str(fixtures)], check=True
    )
    assert not (root / "app" / "PySide6" / "Qt6Network.dll").exists()
    assert not (root / "app" / "PySide6" / "support" / "generate_pyi.py").exists()
    assert not (root / "app" / denied.relative_to(fixtures)).exists()
    assert not (root / "app" / dev.relative_to(fixtures)).exists()

    for name in (
        "NetCraft.exe",
        "runtime/pythonw.exe",
        "runtime/sqlite3.dll",
        "LICENSE",
        "THIRD_PARTY_NOTICES.md",
        "runtime-manifest.json",
        "native-files.json",
        "licenses/qt/LGPL-3.0-only.txt",
        "licenses/qt/GPL-3.0-only.txt",
        "licenses/OpenSSL-3.5.9-LICENSE.txt",
        "licenses/qt/qtbase-attributions.json",
        "licenses/cpython/Windows-CRT-conditions.txt",
        "licenses/cpython/Expat-COPYING.txt",
        "licenses/cpython/mpdecimal-4.0.0/COPYRIGHT.txt",
        "licenses/cpython/xz-5.2.5/COPYING",
        "licenses/cpython/zlib-ng-2.2.4/LICENSE.md",
        "licenses/cpython/zstd-1.5.7/LICENSE",
    ):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name, encoding="utf-8")

    candidate = tmp_path / "NetCraft-test.zip"
    repeated = tmp_path / "repeated" / candidate.name
    repeated.parent.mkdir()
    for archive in (candidate, repeated):
        subprocess.run([sys.executable, str(script), "pack", str(root), str(archive)], check=True)
        subprocess.run([sys.executable, str(script), "verify", str(archive)], check=True)
        os.utime(root / "LICENSE", (1_700_000_000, 1_700_000_000))
    assert candidate.read_bytes() == repeated.read_bytes()

    with ZipFile(repeated, "a") as tampered:
        tampered.writestr("NetCraft-test/history.sqlite3", "unexpected")
    invalid = subprocess.run(
        [sys.executable, str(script), "verify", str(repeated)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert invalid.returncode != 0
    assert "Archive inventory and required files differ" in invalid.stderr
