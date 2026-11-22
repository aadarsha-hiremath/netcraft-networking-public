"""Collect notices named by the corresponding QtBase source attribution records."""

import argparse
import json
import posixpath
from pathlib import Path, PurePosixPath
from tarfile import TarFile

MODULES = {"qtcore", "qtgui", "qtwidgets", "qtopengl", "qtopenglwidgets"}


def collect(source: Path, output: Path) -> None:
    index = {}
    with TarFile.open(source, mode="r:gz") as archive:
        members = {entry.name: entry for entry in archive if entry.isfile()}
        for name in sorted(members):
            if not name.endswith("/qt_attribution.json"):
                continue
            stream = archive.extractfile(members[name])
            assert stream is not None
            # Some vendor records contain literal tabs inside JSON strings.
            records = json.load(stream, strict=False)
            for record in records if isinstance(records, list) else [records]:
                if record.get("QDocModule") not in MODULES:
                    continue
                component = record["Id"]
                if component in index or not component.replace("-", "").replace("_", "").isalnum():
                    raise ValueError(f"Invalid or duplicate Qt attribution {component}")
                files = {}
                for key in ("LicenseFile", "CopyrightFile"):
                    references = record.get(key, [])
                    for reference in [references] if isinstance(references, str) else references:
                        member = posixpath.normpath(
                            posixpath.join(posixpath.dirname(name), reference)
                        )
                        source_text = archive.extractfile(members[member])
                        assert source_text is not None
                        target = (
                            PurePosixPath("upstream") / component / PurePosixPath(reference).name
                        )
                        destination = output / target
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        destination.write_bytes(source_text.read())
                        files[reference] = target.as_posix()
                index[component] = {**record, "ArchivedNoticeFiles": files}

    if not index or not {"zlib", "freetype"} <= index.keys():
        raise ValueError("Expected Qt Core and Qt Gui attribution records")
    (output / "qtbase-attributions.json").write_text(
        json.dumps(index, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    print(f"Collected {len(index)} vendor attributions for the retained Qt modules")


def copy_notices(source: Path, names: dict[str, str], output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    with TarFile.open(source, mode="r:gz") as archive:
        for original, filename in names.items():
            stream = archive.extractfile(original)
            assert stream is not None
            (output / filename).write_bytes(stream.read())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--pyside", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    args = parser.parse_args()
    collect(args.source, args.output)
    copy_notices(
        args.pyside,
        {
            f"pyside-setup-6.12.0/LICENSES/{name}": name
            for name in ("Apache-2.0.txt", "BSD-3-Clause.txt", "LicenseRef-Qt-Commercial.txt")
        },
        args.output / "pyside-shiboken",
    )
    copy_notices(
        args.python,
        {
            "cpython-3.14.10/Modules/expat/COPYING": "Expat-COPYING.txt",
            "cpython-3.14.10/PC/crtlicense.txt": "Windows-CRT-conditions.txt",
        },
        args.output.parent / "cpython",
    )
