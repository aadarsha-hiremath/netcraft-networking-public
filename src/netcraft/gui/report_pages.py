"""Overview, analytics and export pages using the verified SQL and export APIs."""

from PySide6.QtWidgets import QHBoxLayout, QTabWidget

from netcraft.gui.charts import TrendChart
from netcraft.gui.data import (
    AnalyticsData,
    OverviewData,
    WindowFilter,
    analytics,
    overview,
    save_report,
)
from netcraft.gui.history_page import filters_from, protocol_choices
from netcraft.gui.live_page import WINDOW_OPTIONS
from netcraft.gui.session import Session
from netcraft.gui.theme import tone
from netcraft.gui.widgets import (
    Card,
    Metrics,
    Page,
    Table,
    TableRow,
    button,
    choose_destination,
    combo,
    controls,
    form_layout,
    form_row,
    label,
    latency,
    line,
    pick,
    rate,
    timestamp,
)
from netcraft.gui.workers import JobResult
from netcraft.monitoring.analytics import Grouping, Summary
from netcraft.monitoring.export import ExportFormat, Report
from netcraft.monitoring.history import HistoryFilter


class OverviewPage(Page):
    def __init__(self, session: Session) -> None:
        super().__init__(
            "Network workspace", "Review your endpoints, recent runs and measured latency."
        )
        self.session = session
        self.snapshot: OverviewData | None = None
        self.metrics = Metrics(
            ("Configured targets", "Monitoring", "Success rate", "Median latency")
        )
        self.metrics.show_values({"Configured targets": "0", "Monitoring": "Stopped"})
        self.window_picker = combo("Overview time window", WINDOW_OPTIONS)
        pick(self.window_picker, session.preferences.window_minutes)
        diagnose, setup, history = (
            button("New diagnostic", primary=True),
            button("Configure monitor"),
            button("View history"),
        )
        for control, route in (
            (diagnose, "Diagnostics"),
            (setup, "Live Monitor"),
            (history, "History"),
        ):
            control.clicked.connect(
                lambda _checked=False, name=route: self.session.navigate.emit(name)
            )
        self.content.addLayout(
            controls(diagnose, setup, history, label("Window"), self.window_picker)
        )
        self.content.addWidget(self.metrics)

        self.chart = TrendChart(events=False, compact=True)
        plot = Card("Recent latency")
        plot.body.addWidget(self.chart)
        self.last_run = label("No monitoring runs", "value", wrap=True)
        self.last_stamp = label("", "muted", wrap=True)
        self.activity = label("No recent activity.", "muted", wrap=True)
        right = Card("Last monitoring run")
        right.body.addWidget(self.last_run)
        right.body.addWidget(self.last_stamp)
        right.body.addSpacing(8)
        right.body.addWidget(label("Recent activity", "panelTitle"))
        right.body.addWidget(self.activity)
        right.body.addStretch(1)
        split = QHBoxLayout()
        split.addWidget(plot, 7)
        split.addWidget(right, 3)
        self.content.addLayout(split)
        errors = Card("Recent errors and warnings")
        self.errors = label("No errors in this window.", "muted", wrap=True)
        errors.body.addWidget(self.errors)
        self.content.addWidget(errors)
        self.content.addStretch(1)

        self.window_picker.currentIndexChanged.connect(self._visible_reload)
        session.monitor_changed.connect(self._monitor)
        session.history_changed.connect(self._visible_reload)
        session.history_invalidated.connect(self._discard_history)
        session.status_changed.connect(self._activity)
        session.preferences_changed.connect(self._preferences)
        self.chart.set_style(session.theme, session.preferences.unit)

    def _monitor(self) -> None:
        self.metrics.show_values(
            {
                "Configured targets": str(self.session.targets),
                "Monitoring": self.session.monitor_state,
            }
        )

    def _visible_reload(self) -> None:
        if self.isVisible():
            self.load()

    def _discard_history(self) -> None:
        self.snapshot = None
        self._display(OverviewData(Summary(), (), (), ()))
        self.last_run.setText("Refreshing history…")

    def _preferences(self) -> None:
        pick(self.window_picker, self.session.preferences.window_minutes)
        self.chart.set_style(self.session.theme, self.session.preferences.unit)
        if self.snapshot is not None:
            self._display(self.snapshot)

    def _activity(self) -> None:
        records = self.session.recent[:3]
        if self.snapshot is not None and not records:
            records = [
                f"{run.status.value.title()} · {timestamp(run.started_at)}"
                for run in self.snapshot.recent_runs[:3]
            ]
        self.activity.setText("\n".join(records) if records else "No recent activity.")

    def load(self) -> None:
        path = self.session.path
        filters = WindowFilter(int(self.window_picker.currentData()))
        self.session.jobs.submit(
            "history",
            lambda control: overview(path, filters, control),
            self._receive,
            replace_history_read=True,
        )

    def _receive(self, result: JobResult[OverviewData]) -> None:
        if result.value is not None:
            self.snapshot = result.value
            self._display(result.value)
        elif result.error:
            self.errors.setText(result.error)
            tone(self.errors, "error")
            self.session.notify(result.error, "error")

    def _display(self, result: OverviewData) -> None:
        stats = result.summary
        self.metrics.show_values(
            {
                "Success rate": rate(stats.to_dict()["success_rate"]),
                "Median latency": latency(stats.median_ns, self.session.preferences.unit),
            }
        )
        newest = result.recent_runs[0] if result.recent_runs else None
        self.last_run.setText(newest.status.value.title() if newest else "No monitoring runs")
        self.last_stamp.setText(timestamp(newest.started_at) if newest else "")
        self.errors.setText(
            "\n".join(
                f"{sample.target} · "
                f"{sample.observation.error_category or sample.observation.reason}"
                for sample in result.recent_errors
            )
            if result.recent_errors
            else "No errors in this window."
        )
        tone(self.errors, "warn" if result.recent_errors else "")
        self._activity()
        self.chart.show_samples(result.points)


def _comparison(key: str, summary: Summary, unit: str) -> TableRow:
    return TableRow(
        key,
        (
            key,
            str(summary.attempts),
            rate(summary.to_dict()["success_rate"]),
            latency(summary.median_ns, unit),
            latency(summary.p95_ns, unit),
        ),
        (
            key,
            summary.attempts,
            summary.successes / summary.attempts if summary.attempts else -1,
            summary.median_ns if summary.median_ns is not None else -1,
            summary.p95_ns if summary.p95_ns is not None else -1,
        ),
    )


class AnalyticsPage(Page):
    def __init__(self, session: Session) -> None:
        super().__init__(
            "Measure reliability",
            "Latency includes successful timings only. Rates use completed attempts.",
        )
        self.session = session
        self.snapshot: AnalyticsData | None = None
        self.window_picker = combo("Analytics time window", WINDOW_OPTIONS)
        self.protocol = combo("Analytics protocol filter", protocol_choices())
        self.target = line("Analytics exact target", "Exact stored target identity")
        self.target.setMinimumWidth(210)
        pick(self.window_picker, session.preferences.window_minutes)
        refine = button("Apply filters", primary=True)
        zoom = button("Reset zoom")
        self.content.addLayout(
            controls(
                label("Window"),
                self.window_picker,
                self.protocol,
                self.target,
                refine,
            )
        )
        self.metrics = Metrics(
            (
                "Median",
                "p95",
                "p99",
                "Jitter",
                "Success rate",
                "Failure rate",
                "Timeout rate",
                "Longest failure streak",
            )
        )
        self.metrics.values["Jitter"].setToolTip(
            "Mean absolute change between consecutive successful timings for each target."
        )
        self.content.addWidget(self.metrics)
        self.message = label("No observations in this window.", "muted", wrap=True)
        self.content.addWidget(self.message)

        self.chart = TrendChart()
        self.chart.follow = False
        plot = Card("Latency timeline")
        plot.body.addWidget(self.chart)
        plot.body.addLayout(controls(zoom))
        self.tabs = QTabWidget()
        self.targets = Table(
            "Target comparison", ("Target", "Attempts", "Success", "Median", "p95")
        )
        for index, width in enumerate((169, 60, 70, 82, 80)):
            self.targets.setColumnWidth(index, width)
        self.protocols = Table(
            "Protocol comparison", ("Protocol", "Attempts", "Success", "Median", "p95")
        )
        self.errors = Table("Error categories", ("Category", "Occurrences"))
        self.tabs.addTab(self.targets, "Targets")
        self.tabs.addTab(self.protocols, "Protocols")
        self.tabs.addTab(self.errors, "Errors")
        comparisons = Card("Compare results")
        comparisons.body.addWidget(self.tabs)
        split = QHBoxLayout()
        split.setSpacing(13)
        split.addWidget(plot, 7)
        split.addWidget(comparisons, 4)
        self.content.addLayout(split)
        self.content.addStretch(1)
        refine.clicked.connect(self.load)
        zoom.clicked.connect(self.chart.reset_zoom)
        self.target.returnPressed.connect(self.load)
        self.window_picker.currentIndexChanged.connect(self._visible_reload)
        self.protocol.currentIndexChanged.connect(self._visible_reload)
        session.history_changed.connect(self._visible_reload)
        session.history_invalidated.connect(self._discard_history)
        session.preferences_changed.connect(self._preferences)
        self.chart.set_style(session.theme, session.preferences.unit)

    def _visible_reload(self) -> None:
        if self.isVisible():
            self.load()

    def _discard_history(self) -> None:
        self.snapshot = None
        self._display(AnalyticsData(Summary(), (), (), (), False, False))
        self.message.setText("Reading analytics…")

    def _preferences(self) -> None:
        pick(self.window_picker, self.session.preferences.window_minutes)
        self.chart.set_style(self.session.theme, self.session.preferences.unit)
        if self.snapshot:
            self._display(self.snapshot)

    def load(self) -> None:
        path = self.session.path
        filters = filters_from(
            int(self.window_picker.currentData()),
            str(self.protocol.currentData()),
            self.target.text(),
        )
        self.message.setText("Reading analytics…")
        self.session.jobs.submit(
            "history",
            lambda control: analytics(path, filters, control),
            self._receive,
            replace_history_read=True,
        )

    def _receive(self, result: JobResult[AnalyticsData]) -> None:
        if result.error:
            self.message.setText(result.error)
            tone(self.message, "error")
            self.session.notify(result.error, "error")
        elif result.value is not None:
            self.snapshot = result.value
            self._display(result.value)
            self.chart.reset_zoom()

    def _display(self, data: AnalyticsData) -> None:
        summary = data.overall
        unit = self.session.preferences.unit
        rates = summary.to_dict()
        self.metrics.show_values(
            {
                "Median": latency(summary.median_ns, unit),
                "p95": latency(summary.p95_ns, unit),
                "p99": latency(summary.p99_ns, unit),
                "Jitter": latency(summary.jitter_ns, unit),
                "Success rate": rate(rates["success_rate"]),
                "Failure rate": rate(rates["failure_rate"]),
                "Timeout rate": rate(rates["timeout_rate"]),
                "Longest failure streak": str(summary.longest_failure_streak)
                if summary.attempts
                else "—",
            }
        )
        description = (
            (
                f"{summary.attempts} attempts · {summary.timing_count} timed successes · "
                f"{summary.skipped} skipped · "
                f"{summary.cancelled} cancelled · {summary.interrupted} interrupted · "
                f"current failure streak {summary.current_failure_streak}"
            )
            if summary.samples
            else "No observations in this window."
        )
        if data.trend_limited or data.target_list_limited:
            description += (
                " · Chart or comparison shows a bounded subset. The metrics use the full filter."
            )
        self.message.setText(description)
        tone(self.message, "")
        self.chart.show_samples(data.points)
        self.targets.rows.change(
            _comparison(
                f"{row.protocol or ''} · {row.target or ''}",
                row,
                unit,
            )
            for row in data.targets
        )
        self.protocols.rows.change(
            _comparison(row.protocol or "", row, unit) for row in data.protocols
        )
        self.errors.rows.change(
            TableRow(key, (key, str(count)), (key, count))
            for key, count in sorted(
                summary.error_categories.items(), key=lambda entry: (-entry[1], entry[0])
            )
        )


_REPORTS = (
    ("Entire run", Report.RUN),
    ("Filtered samples", Report.SAMPLES),
    ("Reliability summary", Report.SUMMARY),
    ("Latency summary", Report.LATENCY),
    ("Latency trend", Report.TREND),
)
_DESCRIPTIONS = {
    Report.RUN: "Includes the selected run's metadata and all retained samples.",
    Report.SAMPLES: "Includes all retained sample states and timing fields in the selected scope.",
    Report.SUMMARY: "Includes counts, failure streaks, rates and latency statistics.",
    Report.LATENCY: "Includes successful latency statistics and jitter for the selected grouping.",
    Report.TREND: "Includes timestamped successful latency observations, in nanoseconds.",
}


class ExportPage(Page):
    def __init__(self, session: Session) -> None:
        super().__init__(
            "Export observations", "Write an actual history snapshot to a local CSV or JSON file."
        )
        self.session = session
        self.report = combo("Export report", _REPORTS)
        self.format = combo(
            "Export file format", (("CSV", ExportFormat.CSV), ("JSON", ExportFormat.JSON))
        )
        self.run_id = line("Export run ID", "Paste a stored run ID")
        self.target = line("Export exact target", "Optional stored target identity")
        self.protocol = combo("Export protocol filter", protocol_choices())
        self.window_picker = combo(
            "Export time window", (*WINDOW_OPTIONS[-1:], *WINDOW_OPTIONS[:-1])
        )
        self.group = combo(
            "Export grouping",
            (
                ("All", Grouping.ALL),
                ("By protocol", Grouping.PROTOCOL),
                ("By target", Grouping.TARGET),
            ),
        )
        self.preview = label("", "muted", wrap=True)
        self.message = label("", wrap=True)
        self.save = button("Choose destination and export…", primary=True)
        self.stop = button("Cancel export")
        self.stop.setEnabled(False)
        panel = Card("Report options")
        form = form_layout()
        for name, widget in (
            ("Report", self.report),
            ("Format", self.format),
            ("Run ID", self.run_id),
            ("Target", self.target),
            ("Protocol", self.protocol),
            ("Window", self.window_picker),
            ("Group", self.group),
        ):
            form_row(form, name, widget)
        panel.body.addLayout(form)
        panel.body.addWidget(self.preview)
        panel.body.addWidget(self.message)
        panel.body.addLayout(controls(self.save, self.stop))
        self.content.addWidget(panel)
        self.content.addStretch(1)
        self.report.currentIndexChanged.connect(self._preview)
        self.format.currentIndexChanged.connect(self._preview)
        self.save.clicked.connect(self.request)
        self.stop.clicked.connect(self.cancel)
        session.preferences_changed.connect(self._preferences)
        self._preferences()

    def _preferences(self) -> None:
        pick(self.format, self.session.preferences.export_format)
        self._preview()

    def _preview(self) -> None:
        report = Report(self.report.currentData())
        for field in (self.target, self.protocol, self.window_picker):
            field.setEnabled(report is not Report.RUN)
        self.group.setEnabled(report in {Report.SUMMARY, Report.LATENCY})
        self.preview.setText(f"{_DESCRIPTIONS[report]} Sensitive HTTP fields are excluded.")

    def select_run(self, run_id: str) -> None:
        pick(self.report, Report.RUN)
        self.run_id.setText(run_id)

    def request(self) -> None:
        report = Report(self.report.currentData())
        run_id = self.run_id.text().strip() or None
        filters = HistoryFilter(run_id=run_id)
        if report is Report.RUN and not run_id:
            self.message.setText("Enter an exact stored run ID.")
            tone(self.message, "error")
            return
        if report is not Report.RUN:
            criteria = filters_from(
                int(self.window_picker.currentData()),
                str(self.protocol.currentData()),
                self.target.text(),
            ).history_filter()
            filters = HistoryFilter(
                run_id=run_id,
                protocol=criteria.protocol,
                target=criteria.target,
                since=criteria.since,
            )
        format = ExportFormat(self.format.currentData())
        grouping = Grouping(self.group.currentData())
        if self.session.jobs.busy("file"):
            self.message.setText("Wait for the current file operation to finish.")
            return
        destination = choose_destination(
            self, format.value, f"netcraft-{report.value}.{format.value}"
        )
        if destination is None:
            self._receive(JobResult(cancelled=True))
            return
        path, overwrite = destination
        history_path = self.session.path
        preferences_path = self.session.preferences_path
        if self.session.jobs.submit(
            "file",
            lambda control: save_report(
                history_path,
                path,
                overwrite,
                format,
                report,
                filters,
                grouping,
                control,
                preferences_path=preferences_path,
            ),
            self._receive,
        ):
            self.save.setEnabled(False)
            self.stop.setEnabled(True)
            self.message.setText("Writing report…")
            tone(self.message, "")

    def cancel(self) -> None:
        self.session.jobs.cancel("file")
        self.stop.setEnabled(False)
        self.message.setText("Cancelling export…")

    def _receive(self, result: JobResult[None]) -> None:
        self.save.setEnabled(True)
        self.stop.setEnabled(False)
        message = result.error or ("Export cancelled." if result.cancelled else "Report saved.")
        self.message.setText(message)
        style = "error" if result.error else ("warn" if result.cancelled else "good")
        tone(self.message, style)
        self.session.notify(message, style, remember=True)
