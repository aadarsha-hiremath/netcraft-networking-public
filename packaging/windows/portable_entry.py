"""Start the installed GUI from an isolated portable Windows folder."""

import sys
from ctypes import WinDLL


def main() -> int:
    try:
        from netcraft.gui.main import main as launch

        status = launch()
    except Exception:
        if sys.stderr is not None:
            raise
        status = 1
    if status and sys.stderr is None:
        WinDLL("user32").MessageBoxW(
            None,
            "NetCraft could not start. From this folder, run "
            "runtime\\python.exe -I -B portable_entry.py in a terminal to see the error.",
            "NetCraft",
            0x10,
        )
    return status


if __name__ == "__main__":
    raise SystemExit(main())
