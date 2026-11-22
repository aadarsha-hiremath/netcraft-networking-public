"""Explicit live monitoring backed by the existing bounded Phase 3 scheduler."""

from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Lock
from time import monotonic
from uuid import uuid4

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDoubleSpinBox,
    QHBoxLayout,
    QHeaderView,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from netcraft.core.errors import NetworkFailure
from netcraft.core.execution import CancellationToken
from netcraft.gui.charts import RENDER_POINTS, TrendChart
from netcraft.gui.diagnostic_data import DiagnosticRequest
from netcraft.gui.editor import ProbeEditor
from netcraft.gui.session import Session
from netcraft.gui.theme import tone
from netcraft.gui.widgets import (
    Card,
    Metrics,
    Page,
    Table,
    TableRow,
    button,
    combo,
    controls,
    form_layout,
    form_row,
    label,
    latency,
    pick,
)
from netcraft.gui.workers import JobControl, JobResult
from netcraft.monitoring.history import open_history
from netcraft.monitoring.probes import ProbeOptions, Target, run_probe
from netcraft.monitoring.scheduler import MonitorConfig, Probe, monitor
from netcraft.monitoring.telemetry import Observation, Run, RunStatus, Sample, SampleStatus

WINDOW_OPTIONS = (
    ("5 min", 5),
    ("15 min", 15),
    ("1 hour", 60),
    ("24 hours", 1440),
    ("7 days", 10080),
    ("All retained", 0),
)


def count_box(name: str, minimum: int, maximum: int, value: int) -> QSpinBox:
    field = QSpinBox()
    field.setAccessibleName(name)
    field.setRange(minimum, maximum)
    field.setValue(value)
    return field


def seconds_box(name: str, minimum: float, maximum: float, value: float) -> QDoubleSpinBox:
    field = QDoubleSpinBox()
    field.setAccessibleName(name)
    field.setRange(minimum, maximum)
    field.setDecimals(2)
    field.setValue(value)
    field.setSuffix(" s")
    return field


@dataclass(frozen=True, slots=True)
class LiveSnapshot:
    revision: int
    rows: tuple[Sample, ...]
    successes: int
    failures: int
    timeouts: int
    skips: int
    cancelled: int
    latest_ns: int | None
    current_error: str


class LiveBuffer:
    def __init__(self) -> None:
        self._lock = Lock()
        self._rows: deque[Sample] = deque(maxlen=RENDER_POINTS)
        self._counts: dict[SampleStatus, int] = dict.fromkeys(SampleStatus, 0)
        self._revision = 0
        self._latest: int | None = None
        self._error = ""

    def append(self, sample: Sample) -> None:
        row = sample.observation
        with self._lock:
            self._rows.append(sample)
            self._revision += 1
            self._counts[row.status] += 1
            if row.status is SampleStatus.SUCCESS:
                self._latest = row.latency_ns
                self._error = ""
            elif row.error_category is not None:
                self._error = f"{sample.target} · {row.error_category.value}"

    def snapshot(self, previous: int) -> LiveSnapshot | None:
        with self._lock:
            if self._revision == previous:
                return None
            return LiveSnapshot(
                self._revision,
                tuple(sorted(self._rows, key=lambda row: row.observed_at)),
                self._counts[SampleStatus.SUCCESS],
                self._counts[SampleStatus.FAILURE],
                self._counts[SampleStatus.TIMEOUT],
                self._counts[SampleStatus.SKIPPED],
                self._counts[SampleStatus.CANCELLED],
                self._latest,
                self._error,
            )


class TargetDialog(QDialog):
    def __init__(self, parent: Page, timeout: float, initial: DiagnosticRequest | None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit monitor target" if initial else "Add monitor target")
        self.editor = ProbeEditor(timeout, monitoring=True)
        self.selection_result: DiagnosticRequest | None = None
        if initial is not None:
            self.editor.load(initial)
        self.error = label("", wrap=True)
        tone(self.error, "error")
        save, cancel = button("Save target", primary=True), button("Cancel")
        save.clicked.connect(self._save)
        cancel.clicked.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(self.editor)
        layout.addWidget(self.error)
        layout.addLayout(controls(save, cancel))
        self.resize(500, 350)

    def _save(self) -> None:
        try:
            self.selection_result = self.editor.selection()
        except (ValueError, NetworkFailure) as exc:
            self.error.setText(str(exc))
        else:
            self.accept()


class LivePage(Page):
    def __init__(self, session: Session, perform: Probe = run_probe) -> None:
        super().__init__(
            "Monitor targets", "Configure explicit targets and run a finite measurement session."
        )
        self.session, self.perform = session, perform
        self.configured: dict[str, DiagnosticRequest] = {}
        self.last_run: Run | None = None
        self._buffer = LiveBuffer()
        self._revision = -1
        self._started = 0.0
        self._last: LiveSnapshot | None = None
        self.table = Table(
            "Configured monitor targets", ("Protocol", "Target identity", "Timeout"), height=126
        )
        self.table.setColumnWidth(0, 72)
        self.table.setColumnWidth(2, 75)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setMaximumHeight(190)
        self.add = button("Add target")
        self.edit = button("Edit target")
        self.remove = button("Remove target")
        self.start = button("Start monitoring", primary=True)
        self.stop = button("Stop monitoring")
        self.details = button("View run details")
        self.interval = seconds_box("Sampling interval", 0.1, 3600, session.preferences.interval)
        self.slots = count_box("Slots per target", 1, 100_000, 10)
        self.workers = count_box("Worker limit", 1, 16, session.preferences.workers)
        self.status = label("Stopped", "accent")
        self.error = label("", wrap=True)
        tone(self.error, "error")
        self.stats = Metrics(
            (
                "Elapsed",
                "Attempts",
                "Successful",
                "Failures",
                "Timeouts",
                "Skipped",
                "Cancelled",
                "Latest latency",
            )
        )
        self.chart = TrendChart()
        self.window_picker = combo("Live chart window", WINDOW_OPTIONS)
        pick(self.window_picker, session.preferences.window_minutes)
        self.follow = QCheckBox("Follow latest")
        self.follow.setAccessibleName("Follow latest chart values")
        self.follow.setChecked(True)
        self.reset_zoom = button("Reset zoom")

        config = Card("Targets and schedule")
        config.body.addWidget(self.table)
        config.body.addLayout(controls(self.add, self.edit, self.remove, stretch=False))
        schedule = form_layout()
        form_row(schedule, "Interval", self.interval)
        form_row(schedule, "Slots / target", self.slots)
        form_row(schedule, "Workers", self.workers)
        config.body.addLayout(schedule)
        config.body.addWidget(self.error)
        config.body.addLayout(controls(self.start, self.stop, stretch=False))
        config.body.addLayout(controls(self.details, self.status, stretch=False))
        config.body.addStretch(1)
        plot = Card("Live latency")
        plot.body.addLayout(
            controls(label("Window"), self.window_picker, self.follow, self.reset_zoom)
        )
        plot.body.addWidget(self.chart)
        detail = QWidget()
        detail_column = QVBoxLayout(detail)
        detail_column.setContentsMargins(0, 0, 0, 0)
        detail_column.addWidget(self.stats)
        detail_column.addWidget(plot)
        detail_column.addStretch(1)
        split = QHBoxLayout()
        split.setSpacing(13)
        split.addWidget(config, 3)
        split.addWidget(detail, 7)
        self.content.addLayout(split)
        self.content.addStretch(1)

        self.timer = QTimer(self)
        self.timer.setInterval(180)
        self.timer.timeout.connect(self.refresh)
        self.add.clicked.connect(lambda: self._edit(None))
        self.edit.clicked.connect(self.edit_selected)
        self.remove.clicked.connect(self.remove_selected)
        self.table.selectionModel().selectionChanged.connect(self._controls)
        self.start.clicked.connect(self.request)
        self.stop.clicked.connect(self.cancel)
        self.details.clicked.connect(self._show_run)
        self.window_picker.currentIndexChanged.connect(self._render_chart)
        self.follow.toggled.connect(self._follow)
        self.reset_zoom.clicked.connect(self.chart.reset_zoom)
        self.session.preferences_changed.connect(self._preferences)
        self._preferences()
        self._status_counts(None)
        self._refresh_targets()

    def _preferences(self) -> None:
        self.interval.setValue(self.session.preferences.interval)
        self.workers.setValue(self.session.preferences.workers)
        pick(self.window_picker, self.session.preferences.window_minutes)
        self.chart.set_style(self.session.theme, self.session.preferences.unit)
        self._status_counts(self._last)

    def _controls(self) -> None:
        working = self.session.jobs.busy("monitor")
        selected = self.table.selected_key() is not None
        self.start.setEnabled(bool(self.configured) and not working)
        self.stop.setEnabled(working and self.session.monitor_state == "Running")
        self.details.setEnabled(self.last_run is not None)
        self.add.setEnabled(not working and len(self.configured) < 32)
        self.edit.setEnabled(not working and selected)
        self.remove.setEnabled(not working and selected)
        for widget in (self.interval, self.slots, self.workers):
            widget.setEnabled(not working)

    def _refresh_targets(self) -> None:
        self.table.rows.change(
            TableRow(
                key,
                (
                    request.target.protocol.value.upper(),
                    request.target.label,
                    f"{request.options.timeout:g} s",
                ),
            )
            for key, request in self.configured.items()
        )
        self.session.set_monitor(self.session.monitor_state, len(self.configured))
        self._controls()

    def set_target(self, request: DiagnosticRequest, key: str | None = None) -> None:
        identity = (request.target.protocol, request.target.label)
        if any(
            (existing.target.protocol, existing.target.label) == identity
            for existing_key, existing in self.configured.items()
            if existing_key != key
        ):
            raise ValueError("That protocol and target identity is already configured.")
        if key is None and len(self.configured) >= 32:
            raise ValueError("A run can have at most 32 targets.")
        if self.session.jobs.busy("monitor"):
            raise ValueError("Stop monitoring before changing targets.")
        self.configured[key or uuid4().hex] = request
        self._refresh_targets()

    def _edit(self, key: str | None) -> None:
        dialog = TargetDialog(
            self, self.session.preferences.timeout, self.configured.get(key or "")
        )
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.selection_result is not None:
            try:
                self.set_target(dialog.selection_result, key)
                self.error.clear()
            except ValueError as exc:
                self.error.setText(str(exc))
        dialog.deleteLater()

    def edit_selected(self) -> None:
        key = self.table.selected_key()
        if key is not None:
            self._edit(key)

    def remove_selected(self) -> None:
        key = self.table.selected_key()
        if key is not None and not self.session.jobs.busy("monitor"):
            del self.configured[key]
            self._refresh_targets()

    def request(self) -> None:
        options = {entry.target: entry.options for entry in self.configured.values()}
        config = MonitorConfig(
            self.interval.value(), self.slots.value(), workers=self.workers.value()
        )
        try:
            config.plan(len(options))
        except ValueError as exc:
            self.error.setText(str(exc))
            return
        self.error.clear()
        buffer = LiveBuffer()
        path, perform = self.session.path, self.perform

        def work(control: JobControl) -> Run:
            def selected(
                target: Target, _opts: ProbeOptions, token: CancellationToken
            ) -> Observation:
                return perform(target, options[target], token)

            with control.network() as cancel, open_history(path) as history:
                return monitor(
                    history,
                    tuple(options),
                    config,
                    cancel=cancel,
                    on_sample=buffer.append,
                    probe=selected,
                )

        if not self.session.jobs.submit("monitor", work, self._done):
            return
        self._buffer, self._revision, self._last = buffer, -1, None
        self._started = monotonic()
        self.session.set_monitor("Running", len(self.configured))
        self.status.setText("Running")
        self.session.notify("Monitoring started.", "good")
        self._controls()
        self.refresh()
        self.timer.start()

    def cancel(self) -> None:
        self.session.jobs.cancel("monitor")
        self.session.set_monitor("Stopping…", len(self.configured))
        self.status.setText("Stopping…")
        self.session.notify("Waiting for active probes to stop.", "warn")
        self._controls()

    def _done(self, outcome: JobResult[Run]) -> None:
        self.refresh()
        self.timer.stop()
        self.session.set_monitor("Stopped", len(self.configured))
        self.status.setText("Stopped")
        if outcome.value is not None:
            self.last_run = outcome.value
            message = (
                "Monitoring cancelled."
                if outcome.value.status is RunStatus.CANCELLED
                else "Monitoring completed."
            )
            self.session.notify(message, "good", remember=True)
            self.error.setText(self._last.current_error if self._last is not None else "")
        else:
            message = outcome.error or "Monitoring cancelled."
            self.error.setText(message)
            self.session.notify(message, "error" if outcome.error else "warn", remember=True)
        self.session.history_changed.emit()
        self._controls()

    def refresh(self) -> None:
        update = self._buffer.snapshot(self._revision)
        if update is not None:
            self._revision, self._last = update.revision, update
            self._render_chart()
        if self._started:
            self.stats.show_values({"Elapsed": f"{monotonic() - self._started:.1f} s"})
        self._status_counts(self._last)

    def _status_counts(self, state: LiveSnapshot | None) -> None:
        if state is None:
            self.stats.show_values({name: "—" for name in self.stats.values if name != "Elapsed"})
            return
        self.stats.show_values(
            {
                "Attempts": str(state.successes + state.failures + state.timeouts),
                "Successful": str(state.successes),
                "Failures": str(state.failures + state.timeouts),
                "Timeouts": str(state.timeouts),
                "Skipped": str(state.skips),
                "Cancelled": str(state.cancelled),
                "Latest latency": latency(state.latest_ns, self.session.preferences.unit),
            }
        )
        if self.session.jobs.busy("monitor"):
            self.error.setText(state.current_error)

    def _render_chart(self) -> None:
        minutes = int(self.window_picker.currentData())
        rows = self._last.rows if self._last else ()
        if minutes:
            cutoff = datetime.now(UTC) - timedelta(minutes=minutes)
            rows = tuple(row for row in rows if row.observed_at >= cutoff)
        self.chart.show_samples(rows)

    def _follow(self, enabled: bool) -> None:
        self.chart.follow = enabled
        if enabled:
            self.chart.reset_zoom()

    def _show_run(self) -> None:
        if self.last_run is not None:
            self.session.show_run.emit(self.last_run.run_id)
