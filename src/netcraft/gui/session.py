"""Window-owned local state and signals shared by desktop pages."""

from pathlib import Path

from PySide6.QtCore import QObject, Signal

from netcraft.gui.preferences import Preferences, PreferenceStore
from netcraft.gui.theme import DARK, LIGHT, Theme
from netcraft.gui.workers import JobHub


class Session(QObject):
    preferences_changed = Signal()
    status_changed = Signal(str, str)
    monitor_changed = Signal()
    history_changed = Signal()
    history_invalidated = Signal(object)
    navigate = Signal(str)
    show_run = Signal(str)
    export_run = Signal(str)

    def __init__(self, history_path: Path, store: PreferenceStore, parent: QObject) -> None:
        super().__init__(parent)
        self.path, self.store = history_path, store
        preferences_path = Path(store.settings.fileName())
        self.preferences_path = preferences_path if preferences_path.is_absolute() else None
        self.preferences = store.load()
        self.jobs = JobHub(self)
        self.targets = 0
        self.monitor_state = "Stopped"
        self.recent: list[str] = []

    @property
    def theme(self) -> Theme:
        return DARK if self.preferences.dark else LIGHT

    def notify(self, text: str, style: str = "", *, remember: bool = False) -> None:
        if remember:
            self.recent = [text, *self.recent[:5]]
        self.status_changed.emit(text, style)

    def set_preferences(self, preferences: Preferences) -> None:
        self.store.save(preferences)
        self.preferences = preferences
        self.preferences_changed.emit()

    def set_monitor(self, status: str, targets: int) -> None:
        self.monitor_state, self.targets = status, targets
        self.monitor_changed.emit()

    def invalidate_history(self, run_id: str | None = None) -> None:
        self.jobs.discard_history_reads()
        self.history_invalidated.emit(run_id)
