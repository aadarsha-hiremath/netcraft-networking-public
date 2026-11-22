"""Local GUI preferences. Diagnostic inputs and target credentials are never written."""

from dataclasses import asdict, dataclass

from PySide6.QtCore import QSettings

WINDOWS = (5, 15, 60, 1440, 10080)


@dataclass(frozen=True, slots=True)
class Preferences:
    dark: bool = True
    window_minutes: int = 15
    unit: str = "ms"
    timeout: float = 3.0
    interval: float = 5.0
    workers: int = 4
    export_format: str = "csv"
    retention_days: int = 30
    retention_count: int = 10_000


class PreferenceStore:
    def __init__(self, settings: QSettings) -> None:
        self.settings = settings

    def load(self) -> Preferences:
        def value[Entry](key: str, default: Entry, kind: type[Entry]) -> Entry:
            try:
                entry = self.settings.value(key, default, type=kind)
            except (ValueError, TypeError):
                return default
            return entry if isinstance(entry, kind) else default

        minutes = value("window_minutes", 15, int)
        unit = value("unit", "ms", str)
        format_name = value("export_format", "csv", str)
        return Preferences(
            dark=value("dark", True, bool),
            window_minutes=minutes if minutes in WINDOWS else 15,
            unit=unit if unit in {"ms", "µs"} else "ms",
            timeout=max(0.1, min(60.0, value("timeout", 3.0, float))),
            interval=max(0.1, min(3600.0, value("interval", 5.0, float))),
            workers=max(1, min(16, value("workers", 4, int))),
            export_format=format_name if format_name in {"csv", "json"} else "csv",
            retention_days=max(1, min(3650, value("retention_days", 30, int))),
            retention_count=max(1, min(1_000_000, value("retention_count", 10_000, int))),
        )

    def save(self, preferences: Preferences) -> None:
        for key, value in asdict(preferences).items():
            self.settings.setValue(key, value)
        self.settings.sync()
