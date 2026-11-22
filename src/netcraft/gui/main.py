"""Optional native entry point. Importing or launching the GUI performs no network probes."""

import argparse
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="netcraft-gui", description="NetCraft Network Diagnostics Studio"
    )
    parser.add_argument(
        "--history", type=Path, help="Use a specific local Phase 3 history database."
    )
    parser.add_argument("--settings", type=Path, help="Use a separate local INI preferences file.")
    args = parser.parse_args(argv)
    try:
        from PySide6.QtCore import QSettings
        from PySide6.QtWidgets import QApplication

        from netcraft.gui.window import StudioWindow
    except ModuleNotFoundError as exc:
        if (exc.name or "").split(".")[0] not in {"PySide6", "pyqtgraph", "numpy", "shiboken6"}:
            raise
        print(
            'Desktop dependencies are missing. Install "netcraft-networking[gui]".',
            file=sys.stderr,
        )
        return 2

    app = QApplication([parser.prog])
    app.setApplicationName("NetCraft")
    app.setOrganizationName("NetCraft")
    history = args.history or Path.home() / ".netcraft" / "history.sqlite3"
    settings = (
        QSettings(str(args.settings), QSettings.Format.IniFormat)
        if args.settings is not None
        else QSettings("NetCraft", "Network Diagnostics Studio")
    )
    window = StudioWindow(history, settings)
    screen = app.primaryScreen()
    if screen is not None:
        available = screen.availableGeometry().size()
        window.resize(min(1400, available.width()), min(810, available.height()))
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
