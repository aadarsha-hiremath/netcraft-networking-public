"""Persistent workspace preferences and explicit history maintenance."""

from PySide6.QtWidgets import QCheckBox, QMessageBox

from netcraft.gui.data import local_history
from netcraft.gui.history_page import recent_cutoff
from netcraft.gui.live_page import WINDOW_OPTIONS, count_box, seconds_box
from netcraft.gui.preferences import Preferences
from netcraft.gui.session import Session
from netcraft.gui.theme import tone
from netcraft.gui.widgets import (
    Card,
    Page,
    button,
    combo,
    controls,
    form_layout,
    form_row,
    label,
    pick,
)
from netcraft.gui.workers import JobControl, JobResult
from netcraft.monitoring.history import RetentionPreview


class SettingsPage(Page):
    def __init__(self, session: Session) -> None:
        super().__init__("Workspace preferences", "Preferences apply to this desktop workspace.")
        self.session = session
        self.dark = QCheckBox("Use dark theme")
        self.dark.setAccessibleName("Use dark theme")
        self.window_picker = combo("Preferred chart window", WINDOW_OPTIONS[:-1])
        self.unit = combo(
            "Latency display unit", (("Milliseconds (ms)", "ms"), ("Microseconds (µs)", "µs"))
        )
        self.timeout = seconds_box("Default timeout", 0.1, 60, 3)
        self.interval = seconds_box("Default sample interval", 0.1, 3600, 5)
        self.workers = count_box("Default worker limit", 1, 16, 4)
        self.format = combo("Preferred export format", (("CSV", "csv"), ("JSON", "json")))
        self.age = count_box("History retention age in days", 1, 3650, 30)
        self.count = count_box("History retention sample limit", 1, 1_000_000, 10_000)
        self.location = label(str(session.path), "muted", wrap=True)
        self.message = label("", wrap=True)
        self.save = button("Save preferences", primary=True)
        self.reset = button("Reset preferences")
        self.prune = button("Apply retention…")

        display = Card("Display and diagnostics")
        form = form_layout()
        for name, widget in (
            ("Appearance", self.dark),
            ("Chart window", self.window_picker),
            ("Latency units", self.unit),
            ("Timeout", self.timeout),
            ("Sample interval", self.interval),
            ("Worker limit", self.workers),
            ("Export format", self.format),
        ):
            form_row(form, name, widget)
        display.body.addLayout(form)
        display.body.addLayout(controls(self.save, self.reset))
        history = Card("Local history")
        history.body.addWidget(self.location)
        history_form = form_layout()
        form_row(history_form, "Keep days", self.age)
        form_row(history_form, "Keep samples", self.count)
        history.body.addLayout(history_form)
        history.body.addWidget(
            label("Retention is applied only when you confirm it here.", "muted")
        )
        history.body.addLayout(controls(self.prune))
        history.body.addWidget(self.message)
        self.content.addWidget(display)
        self.content.addWidget(history)
        self.content.addStretch(1)
        self.save.clicked.connect(self.save_preferences)
        self.reset.clicked.connect(self.reset_preferences)
        self.prune.clicked.connect(self.apply_retention)
        self.load()

    def load(self) -> None:
        value = self.session.preferences
        self.dark.setChecked(value.dark)
        pick(self.window_picker, value.window_minutes)
        pick(self.unit, value.unit)
        self.timeout.setValue(value.timeout)
        self.interval.setValue(value.interval)
        self.workers.setValue(value.workers)
        pick(self.format, value.export_format)
        self.age.setValue(value.retention_days)
        self.count.setValue(value.retention_count)

    def save_preferences(self) -> None:
        self.session.set_preferences(
            Preferences(
                self.dark.isChecked(),
                int(self.window_picker.currentData()),
                str(self.unit.currentData()),
                self.timeout.value(),
                self.interval.value(),
                self.workers.value(),
                str(self.format.currentData()),
                self.age.value(),
                self.count.value(),
            )
        )
        self.session.notify("Preferences saved.", "good")

    def reset_preferences(self) -> None:
        self.session.set_preferences(Preferences())
        self.load()
        self.session.notify("Default preferences restored.", "good")

    def apply_retention(self) -> None:
        if self.session.jobs.busy("file"):
            return
        days, count = self.age.value(), self.count.value()
        path, cutoff = self.session.path, recent_cutoff(days)

        def preview(control: JobControl) -> RetentionPreview:
            with local_history(path, control) as history:
                return history.preview_retention(before=cutoff, keep_samples=count)

        def confirm(result: JobResult[RetentionPreview]) -> None:
            plan = result.value
            if plan is None:
                self._finish_retention(result.error or "Retention cancelled.", result.error)
                return
            answer = QMessageBox.question(
                self,
                "Apply retention?",
                f"Remove {plan.samples:,} samples and {plan.runs:,} empty runs?\n\n"
                f"Remove samples older than {days} days, then keep at most "
                f"{count:,} of the newest remaining samples across all runs. "
                "Runs with no remaining samples are removed.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                self._finish_retention("Retention cancelled.")
                return

            def work(control: JobControl) -> tuple[int, int]:
                with local_history(path, control) as history:
                    return history.prune(before=cutoff, keep_samples=count, expected=plan)

            if self.session.jobs.submit("file", work, receive):
                self.message.setText("Applying retention…")
            else:
                self._finish_retention("Retention cancelled.")

        def receive(result: JobResult[tuple[int, int]]) -> None:
            if result.value is not None:
                samples, runs = result.value
                self.session.invalidate_history()
                message = f"Removed {samples:,} samples and {runs:,} empty runs."
                self._finish_retention(message, success=True)
                self.session.history_changed.emit()
            else:
                self._finish_retention(result.error or "Retention cancelled.", result.error)

        if self.session.jobs.submit("file", preview, confirm):
            self.prune.setEnabled(False)
            self.message.setText("Calculating retention preview…")
            tone(self.message, "")

    def _finish_retention(
        self, message: str, error: str | None = None, *, success: bool = False
    ) -> None:
        self.prune.setEnabled(True)
        style = "error" if error else ("good" if success else "warn")
        self.message.setText(message)
        tone(self.message, style)
        self.session.notify(message, style, remember=True)
