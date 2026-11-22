"""Paginated run history and per-sample inspection."""

import json
from datetime import UTC, datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QMessageBox, QPlainTextEdit, QVBoxLayout

from netcraft.gui.data import (
    HistoryPageData,
    RunDetail,
    WindowFilter,
    local_history,
    read_run,
    read_runs,
)
from netcraft.gui.live_page import WINDOW_OPTIONS
from netcraft.gui.session import Session
from netcraft.gui.theme import tone
from netcraft.gui.widgets import (
    Card,
    Page,
    Table,
    TableRow,
    button,
    combo,
    controls,
    label,
    latency,
    line,
    timestamp,
)
from netcraft.gui.workers import JobControl, JobResult
from netcraft.monitoring.telemetry import MonitorProtocol, Sample


def protocol_choices() -> tuple[tuple[str, str], ...]:
    return (
        ("All protocols", ""),
        *((protocol.value.upper(), protocol.value) for protocol in MonitorProtocol),
    )


def filters_from(minutes: int, protocol: str, target: str) -> WindowFilter:
    return WindowFilter(minutes, MonitorProtocol(protocol) if protocol else None, target.strip())


class RunDialog(QDialog):
    def __init__(self, session: Session, run_id: str, parent: Page) -> None:
        super().__init__(parent)
        self.session, self.run_id = session, run_id
        self.page = 0
        self.samples: dict[str, Sample] = {}
        self.setWindowTitle("Monitoring run details")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.info = label(f"Run {run_id}", "panelTitle", wrap=True)
        self.state = label("Loading samples…", "muted", wrap=True)
        self.table = Table(
            "Run samples",
            ("Observed UTC", "Protocol", "Target", "Outcome", "Latency", "Error / reason"),
            height=220,
        )
        self.table.setColumnWidth(0, 190)
        self.table.setColumnWidth(2, 220)
        self.telemetry = QPlainTextEdit()
        self.telemetry.setReadOnly(True)
        self.telemetry.setAccessibleName("Selected sample telemetry")
        self.telemetry.setPlaceholderText("Select a sample to inspect its stored telemetry.")
        self.telemetry.setMinimumHeight(140)
        self.previous, self.next = button("Previous"), button("Next")
        self.page_label = label("Page 1", "muted")
        self.export = button("Export this run")
        close = button("Close")
        close.clicked.connect(self.close)
        self.previous.clicked.connect(lambda: self._page(-1))
        self.next.clicked.connect(lambda: self._page(1))
        self.export.clicked.connect(self._export)
        self.table.selectionModel().selectionChanged.connect(self._inspect)
        self.previous.setEnabled(False)
        self.next.setEnabled(False)
        layout = QVBoxLayout(self)
        for widget in (self.info, self.state, self.table, self.telemetry):
            layout.addWidget(widget)
        layout.addLayout(controls(self.previous, self.next, self.page_label, self.export, close))
        self.resize(920, 625)

    def _page(self, change: int) -> None:
        self.page = max(0, self.page + change)
        self.load()

    def load(self) -> None:
        path, run_id, page = self.session.path, self.run_id, self.page
        self.state.setText("Loading samples…")
        self.previous.setEnabled(False)
        self.next.setEnabled(False)
        self.session.jobs.submit(
            "history",
            lambda control: read_run(path, run_id, page, control),
            self._receive,
            replace_history_read=True,
        )

    def _receive(self, outcome: JobResult[RunDetail]) -> None:
        # The Python wrapper can outlive a closed modal dialog.
        if not self.is_open:
            return
        if outcome.value is None:
            self.state.setText(outcome.error or "Loading cancelled.")
            tone(self.state, "error")
            return
        value = outcome.value
        summary, run = value.summary, value.run
        self.state.setText(
            f"{run.status.title()} · {timestamp(run.started_at)} · {summary.attempts} attempts · "
            f"{summary.successes} successful · {summary.failures} failed · "
            f"{summary.timeouts} timed out"
        )
        tone(self.state, "")
        self.samples = {sample.sample_id: sample for sample in value.samples}
        unit = self.session.preferences.unit
        self.table.rows.change(
            TableRow(
                sample.sample_id,
                (
                    timestamp(sample.observed_at),
                    sample.protocol.value.upper(),
                    sample.target,
                    sample.observation.status.value,
                    latency(sample.observation.latency_ns, unit),
                    str(sample.observation.error_category or sample.observation.reason or "—"),
                ),
            )
            for sample in value.samples
        )
        self.telemetry.clear()
        self.page_label.setText(f"Page {self.page + 1}")
        self.previous.setEnabled(self.page > 0)
        self.next.setEnabled(value.has_next)

    def _inspect(self) -> None:
        selected = self.samples.get(self.table.selected_key() or "")
        self.telemetry.setPlainText(json.dumps(selected.to_dict(), indent=2) if selected else "")

    def _export(self) -> None:
        self.close()
        self.session.export_run.emit(self.run_id)

    is_open = True


class HistoryPage(Page):
    def __init__(self, session: Session) -> None:
        super().__init__(
            "Recorded runs", "Filter stored runs or inspect the telemetry for a single run."
        )
        self.session = session
        self.page = 0
        self._deleting = False
        self.target = line("Search by target", "Target contains…")
        self.protocol = combo("History protocol filter", protocol_choices())
        self.window_picker = combo(
            "History time window", (*WINDOW_OPTIONS[-1:], *WINDOW_OPTIONS[:-1])
        )
        self.search = button("Apply filters")
        self.refresh = button("Refresh")
        self.open = button("Open run details", primary=True)
        self.delete = button("Delete selected…")
        self.previous, self.next = button("Previous"), button("Next")
        self.page_label = label("Page 1", "muted")
        self.message = label("No stored runs.", "muted", wrap=True)
        self.table = Table(
            "Stored monitoring runs",
            (
                "Started UTC",
                "Finished UTC",
                "Duration",
                "Status",
                "Targets",
                "Slots / target",
                "Run ID",
            ),
            height=320,
        )
        for index, size in enumerate((182, 182, 91, 110, 67, 113)):
            self.table.setColumnWidth(index, size)
        panel = Card()
        panel.body.addLayout(
            controls(
                self.target,
                self.protocol,
                self.window_picker,
                self.search,
                self.refresh,
                stretch=False,
            )
        )
        panel.body.addWidget(self.table)
        panel.body.addWidget(self.message)
        panel.body.addLayout(
            controls(self.previous, self.next, self.page_label, self.open, self.delete)
        )
        self.content.addWidget(panel)
        self.content.addStretch(1)
        self.search.clicked.connect(self.first)
        self.target.returnPressed.connect(self.first)
        self.protocol.currentIndexChanged.connect(self.first)
        self.window_picker.currentIndexChanged.connect(self.first)
        self.refresh.clicked.connect(self.load)
        self.previous.clicked.connect(lambda: self._page(-1))
        self.next.clicked.connect(lambda: self._page(1))
        self.open.clicked.connect(self.open_selected)
        self.table.doubleClicked.connect(self.open_selected)
        self.delete.clicked.connect(self.delete_selected)
        self.table.selectionModel().selectionChanged.connect(self._selection)
        self.session.history_changed.connect(self._refresh_visible)
        self.session.history_invalidated.connect(self._discard_history)
        self.previous.setEnabled(False)
        self.next.setEnabled(False)
        self._selection()

    def _refresh_visible(self) -> None:
        if self.isVisible():
            self.load()

    def _discard_history(self, run_id: str | None) -> None:
        if run_id is None:
            self.page = 0
            self.table.rows.change(())
        else:
            self.table.rows.change(row for row in self.table.rows.rows if row.key != run_id)
        self._selection()

    def _selection(self) -> None:
        selected = self.table.selected_key() is not None
        self.open.setEnabled(selected)
        self.delete.setEnabled(selected and not self._deleting)

    def _page(self, delta: int) -> None:
        self.page = max(0, self.page + delta)
        self.load()

    def first(self) -> None:
        self.page = 0
        if self.isVisible():
            self.load()

    def load(self) -> None:
        path, page = self.session.path, self.page
        filters = filters_from(
            int(self.window_picker.currentData()),
            str(self.protocol.currentData()),
            self.target.text(),
        )
        self.message.setText("Loading history…")
        self.previous.setEnabled(False)
        self.next.setEnabled(False)
        self.session.jobs.submit(
            "history",
            lambda control: read_runs(path, filters, page, control),
            self._receive,
            replace_history_read=True,
        )

    def _receive(self, outcome: JobResult[HistoryPageData]) -> None:
        if outcome.value is None:
            self.message.setText(outcome.error or "Loading cancelled.")
            tone(self.message, "error")
            self.session.notify(self.message.text(), "error")
            return
        result = outcome.value
        rows = []
        for run in result.runs:
            seconds = (run.ended_at - run.started_at).total_seconds() if run.ended_at else None
            cells = (
                timestamp(run.started_at),
                timestamp(run.ended_at),
                f"{seconds:.2f} s" if seconds is not None else "—",
                run.status.value.title(),
                str(run.target_count),
                str(run.slots_per_target),
                run.run_id,
            )
            rows.append(
                TableRow(
                    run.run_id,
                    cells,
                    (
                        run.started_at.isoformat(),
                        run.ended_at.isoformat() if run.ended_at else "",
                        seconds if seconds is not None else -1,
                        run.status.value,
                        run.target_count,
                        run.slots_per_target,
                        run.run_id,
                    ),
                )
            )
        self.table.rows.change(rows)
        self.page_label.setText(f"Page {self.page + 1}")
        self.previous.setEnabled(self.page > 0)
        self.next.setEnabled(result.has_next)
        item = "run" if len(rows) == 1 else "runs"
        self.message.setText(
            f"{len(rows)} {item} on this page" if rows else "No runs match this selection."
        )
        tone(self.message, "")
        self._selection()

    def open_selected(self) -> None:
        run_id = self.table.selected_key()
        if run_id is not None:
            self.open_run(run_id)

    def open_run(self, run_id: str) -> RunDialog:
        dialog = RunDialog(self.session, run_id, self)
        dialog.finished.connect(lambda: setattr(dialog, "is_open", False))
        dialog.show()
        dialog.load()
        return dialog

    def delete_selected(self) -> None:
        run_id = self.table.selected_key()
        if run_id is None or self._deleting or self.session.jobs.busy("file"):
            return
        if (
            QMessageBox.question(
                self,
                "Delete run?",
                "Delete the selected run and its samples from local history?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        path = self.session.path

        def work(control: JobControl) -> bool:
            with local_history(path, control) as history:
                return history.delete_run(run_id)

        def receive(result: JobResult[bool]) -> None:
            self._deleting = False
            absent = result.value is not None
            if absent:
                # The transaction has finished. An older read must not restore the row.
                self.session.invalidate_history(run_id)
            self._selection()
            if result.error:
                message, style = result.error, "error"
            elif result.cancelled:
                message, style = "Run deletion cancelled.", "warn"
            elif result.value:
                message, style = "Run deleted.", "good"
            else:
                message, style = "Run is unavailable.", "warn"
            self.message.setText(message)
            tone(self.message, style)
            self.session.notify(message, style, remember=True)
            if absent:
                self.session.history_changed.emit()

        self._deleting = self.session.jobs.submit("file", work, receive)
        self._selection()


def recent_cutoff(days: int) -> datetime:
    from datetime import timedelta

    return datetime.now(UTC) - timedelta(days=days)
