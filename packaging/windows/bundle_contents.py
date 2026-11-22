"""Stage the desktop runtime and write or verify its reproducible ZIP."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import stat
from pathlib import Path, PurePosixPath
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

# NetCraft uses Qt Widgets. PyQtGraph also imports Qt OpenGL bindings.
QT_FILES = {
    "__init__.py",
    "_config.py",
    "_git_pyside_version.py",
    "pyside6.abi3.dll",
    "QtCore.pyd",
    "QtGui.pyd",
    "QtWidgets.pyd",
    "QtOpenGL.pyd",
    "QtOpenGLWidgets.pyd",
    "Qt6Core.dll",
    "Qt6Gui.dll",
    "Qt6Widgets.dll",
    "Qt6OpenGL.dll",
    "Qt6OpenGLWidgets.dll",
    "plugins/platforms/qwindows.dll",
    "plugins/styles/qmodernwindowsstyle.dll",
    "support/__init__.py",
    "support/auto_property_helper.py",
    "support/deprecated.py",
}
VC_RUNTIME = {
    "msvcp140.dll",
    "msvcp140_1.dll",
    "msvcp140_2.dll",
    "msvcp140_codecvt_ids.dll",
    "vcruntime140.dll",
    "vcruntime140_1.dll",
    "concrt140.dll",
}
METADATA = "bundle-files.json"
FORBIDDEN_DIRS = {
    "__pycache__",
    ".git",
    ".netcraft",
    "tests",
    "test",
    "scripts",
    "examples",
    "_examples",
    "include",
}
FORBIDDEN_SUFFIXES = {
    ".db",
    ".sqlite",
    ".sqlite3",
    ".pyc",
    ".pdb",
    ".obj",
    ".lib",
    ".pyi",
    ".pxd",
    ".log",
    ".key",
    ".pem",
    ".tmp",
    ".ini",
    ".cmake",
    ".pc",
}
NUMPY_DEV_DIRS = {"_pyinstaller", "doc", "f2py", "testing"}


def excluded(relative: PurePosixPath) -> bool:
    parts = {part.lower() for part in relative.parts}
    name = relative.name.lower()
    return (
        bool(parts & FORBIDDEN_DIRS)
        or relative.suffix.lower() in FORBIDDEN_SUFFIXES
        or name
        in {
            "direct_url.json",
            "record",
            "installer",
            "requested",
            "desktop.ini",
            "thumbs.db",
            "_remote_debugging.pyd",
            ".pydistutils.cfg",
        }
        or name.startswith((".env", "history.sqlite", "history.db"))
    )


def excluded_runtime_file(relative: PurePosixPath) -> bool:
    if excluded(relative):
        return True
    parts = relative.parts
    if parts[:1] == ("numpy",):
        if len(parts) > 1 and parts[1] in NUMPY_DEV_DIRS:
            return True
        return relative.suffix == ".pyd" and relative.name.split(".")[0].endswith("_tests")
    return parts[:2] == ("shiboken6", "lib")


def stage(destination: Path, inputs: list[Path]) -> None:
    for source in inputs:
        for path in source.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(source)
            if (
                excluded_runtime_file(PurePosixPath(relative.as_posix()))
                or path.suffix.lower() == ".exe"
            ):
                continue
            if relative.parts[0] == "PySide6":
                qt_path = relative.relative_to("PySide6").as_posix()
                if qt_path not in QT_FILES | VC_RUNTIME:
                    continue
            if (
                relative.parts[0] == "shiboken6"
                and path.suffix == ".dll"
                and path.name not in VC_RUNTIME | {"shiboken6.abi3.dll"}
            ):
                continue
            output = destination / relative
            if output.exists():
                raise ValueError(f"Conflicting staged file {relative}")
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, output)

    missing = sorted(name for name in QT_FILES if not (destination / "PySide6" / name).is_file())
    if missing:
        raise ValueError(f"Missing Qt files: {', '.join(missing)}")


def write_archive(root: Path, destination: Path) -> None:
    files = sorted(path for path in root.rglob("*") if path.is_file())
    inventory = {}
    with ZipFile(destination, "x", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for path in files:
            relative = path.relative_to(root).as_posix()
            data = path.read_bytes()
            inventory[relative] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            if relative == METADATA or excluded(PurePosixPath(relative)):
                raise ValueError(f"Unexpected archive file {relative}")
            write_file(archive, f"{root.name}/{relative}", data)
        manifest = json.dumps(inventory, indent=2, sort_keys=True) + "\n"
        write_file(archive, f"{root.name}/{METADATA}", manifest.encode("utf-8"))


def write_file(archive: ZipFile, name: str, data: bytes) -> None:
    entry = ZipInfo(name, (1980, 1, 1, 0, 0, 0))
    entry.create_system = 3
    entry.external_attr = (stat.S_IFREG | 0o644) << 16
    archive.writestr(entry, data, compress_type=ZIP_DEFLATED, compresslevel=9)


def verify_archive(path: Path) -> None:
    with ZipFile(path) as archive:
        entries = archive.namelist()
        root = path.stem
        manifest = f"{root}/{METADATA}"
        inventory = json.loads(archive.read(manifest))
        required = {
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
        }
        required.update(f"app/PySide6/{name}" for name in QT_FILES)
        expected = {f"{root}/{name}" for name in inventory} | {manifest}
        if (
            required - inventory.keys()
            or expected != set(entries)
            or len(expected) != len(entries)
            or len(expected) != len({entry.casefold() for entry in entries})
        ):
            raise ValueError("Archive inventory and required files differ")
        for relative, record in inventory.items():
            fragment = PurePosixPath(relative)
            if (
                fragment.is_absolute()
                or fragment.as_posix() != relative
                or ".." in fragment.parts
                or "\\" in relative
                or ":" in relative
                or excluded(fragment)
            ):
                raise ValueError(f"Unexpected archive path {relative}")
            if fragment.parts[:1] == ("app",) and excluded_runtime_file(
                fragment.relative_to("app")
            ):
                raise ValueError(f"Unexpected development file {relative}")
            if fragment.parts[:2] == ("app", "PySide6"):
                scoped = fragment.relative_to("app/PySide6").as_posix()
                if scoped not in QT_FILES | VC_RUNTIME:
                    raise ValueError(f"Unexpected Qt runtime file {relative}")
            data = archive.read(f"{root}/{relative}")  # ZIP CRC is checked on read.
            if len(data) != record["bytes"] or hashlib.sha256(data).hexdigest() != record["sha256"]:
                raise ValueError(f"Archive integrity check failed for {relative}")
    print(f"Verified {len(entries)} ZIP members against the archive inventory")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="operation", required=True)
    staging = sub.add_parser("stage")
    staging.add_argument("destination", type=Path)
    staging.add_argument("inputs", type=Path, nargs="+")
    packing = sub.add_parser("pack")
    packing.add_argument("root", type=Path)
    packing.add_argument("destination", type=Path)
    verifier = sub.add_parser("verify")
    verifier.add_argument("archive", type=Path)
    args = parser.parse_args()
    match args.operation:
        case "stage":
            stage(args.destination, args.inputs)
        case "pack":
            write_archive(args.root, args.destination)
        case "verify":
            verify_archive(args.archive)


if __name__ == "__main__":
    main()
