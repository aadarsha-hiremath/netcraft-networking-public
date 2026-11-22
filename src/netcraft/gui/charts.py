"""Bounded, interactive latency trends and separate categorical outcome markers."""

from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QStackedWidget, QVBoxLayout, QWidget

from netcraft.gui.theme import DARK, Theme
from netcraft.gui.widgets import label, latency
from netcraft.monitoring.telemetry import Sample, SampleStatus

_COLORS = ("#32b8db", "#409975", "#cf9650", "#8e78d8", "#d36a9b", "#879bc9", "#52a8ac", "#c77e5e")
_EVENTS = (
    SampleStatus.FAILURE,
    SampleStatus.TIMEOUT,
    SampleStatus.SKIPPED,
    SampleStatus.CANCELLED,
    SampleStatus.INTERRUPTED,
)
RENDER_POINTS = 1_200


class TrendChart(QWidget):
    """The event strip uses categorical coordinates. Failure markers have no latency."""

    def __init__(self, *, events: bool = True, compact: bool = False) -> None:
        super().__init__()
        self.setAccessibleName("Latency timeline and probe outcomes")
        self._samples: tuple[Sample, ...] = ()
        self._series: dict[str, Any] = {}
        self._unit = "ms"
        self._theme = DARK
        self.follow = True
        self.rendered_count = 0
        self.latency_count = 0
        self.event_count = 0
        self.main_plot = pg.PlotWidget(axisItems={"bottom": pg.DateAxisItem(utcOffset=0)})
        self.main_plot.setMenuEnabled(False)
        self.main_plot.showGrid(x=True, y=True, alpha=0.17)
        self.main_plot.getViewBox().setLimits(yMin=0)
        self.main_plot.setLabel("bottom", "UTC")
        self.main_plot.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.main_plot.setAccessibleName("Latency chart. Mouse wheel zooms and dragging pans.")
        self.legend = self.main_plot.addLegend(offset=(7, 5))
        self.event_plot = pg.PlotWidget(axisItems={"bottom": pg.DateAxisItem(utcOffset=0)})
        self.event_plot.setMaximumHeight(98)
        self.event_plot.setMenuEnabled(False)
        self.event_plot.setMouseEnabled(x=True, y=False)
        self.event_plot.setXLink(self.main_plot)
        self.event_plot.getAxis("left").setTicks(
            [[(i + 1, status.value.title()) for i, status in enumerate(_EVENTS)]]
        )
        self.event_plot.setYRange(0.5, len(_EVENTS) + 0.5, padding=0)
        self._events_enabled = events
        self._marker = self.event_plot.plot([], [], pen=None, symbol="o", symbolSize=7)
        self._empty = label("No latency observations for this window.", "muted")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._stack = QStackedWidget()
        self._stack.addWidget(self._empty)
        self._stack.addWidget(self.main_plot)
        self._stack.setMinimumHeight(155 if compact else 190)
        self._stack.setMaximumHeight(217 if compact else 250)
        self.caption = label("Hover a point to inspect its time and latency.", "muted", wrap=True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._stack)
        layout.addWidget(self.event_plot)
        layout.addWidget(self.caption)
        self.event_plot.hide()
        self._hover_proxy = pg.SignalProxy(
            self.main_plot.scene().sigMouseMoved,
            rateLimit=20,
            slot=self._hover,
        )
        self.set_style(DARK, "ms")

    def set_style(self, theme: Theme, unit: str) -> None:
        self._theme, self._unit = theme, unit
        for plot in (self.main_plot, self.event_plot):
            plot.setBackground(theme.field)
            for name in ("bottom", "left"):
                axis = plot.getAxis(name)
                axis.setPen(pg.mkPen(theme.border))
                axis.setTextPen(pg.mkPen(theme.muted))
        self.legend.setLabelTextColor(theme.text)
        self.main_plot.setLabel("left", "Latency", units=unit)
        self.show_samples(self._samples)

    def reset_zoom(self) -> None:
        self.main_plot.enableAutoRange()
        self.main_plot.autoRange()
        if self._samples:
            low = self._samples[0].observed_at.timestamp()
            high = self._samples[-1].observed_at.timestamp()
            pad = max(0.05, (high - low) * 0.035)
            self.main_plot.setXRange(low - pad, high + pad, padding=0)

    def show_samples(self, rows: tuple[Sample, ...]) -> None:
        self._samples = rows[-RENDER_POINTS:]
        self.rendered_count = len(self._samples)
        groups: dict[str, list[Sample]] = defaultdict(list)
        for row in self._samples:
            groups[f"{row.protocol} · {row.target}"].append(row)
        with_latency = tuple(
            name
            for name, data in groups.items()
            if any(
                row.observation.status is SampleStatus.SUCCESS
                and row.observation.latency_ns is not None
                for row in data
            )
        )
        names = with_latency[-8:]
        for name in tuple(self._series):
            if name not in names:
                self.main_plot.removeItem(self._series.pop(name))
                self.legend.removeItem(name)
        factor = 1_000_000 if self._unit == "ms" else 1000
        self.latency_count = self.event_count = 0
        for i, name in enumerate(names):
            data = groups[name]
            series = self._series.get(name)
            color = _COLORS[i]
            if series is None:
                series = self.main_plot.plot(name=name, connect="finite", symbol="o", symbolSize=4)
                self._series[name] = series
            stamps: list[float] = []
            values: list[float] = []
            prior = None
            for row in data:
                at = row.observed_at.timestamp()
                if prior is not None and (row.run_id != prior.run_id or row.slot != prior.slot + 1):
                    stamps.append(at)
                    values.append(float("nan"))
                ns = row.observation.latency_ns
                valid = row.observation.status is SampleStatus.SUCCESS and ns is not None
                stamps.append(at)
                values.append(ns / factor if valid and ns is not None else float("nan"))
                self.latency_count += int(valid)
                prior = row
            series.setData(
                stamps,
                values,
                pen=pg.mkPen(color, width=2),
                symbolBrush=pg.mkBrush(color),
                symbolPen=None,
            )
        events = [row for row in self._samples if row.observation.status in _EVENTS]
        self.event_count = len(events)
        colors = (
            self._theme.failure,
            self._theme.warning,
            self._theme.muted,
            self._theme.cyan,
            self._theme.muted,
        )
        self._marker.setData(
            [row.observed_at.timestamp() for row in events],
            [_EVENTS.index(row.observation.status) + 1 for row in events],
            symbolBrush=[
                pg.mkBrush(colors[_EVENTS.index(row.observation.status)]) for row in events
            ],
            pen=None,
            symbol="o",
        )
        show_events = bool(events) and self._events_enabled
        self._stack.setCurrentIndex(int(bool(self.latency_count) or show_events))
        self.event_plot.setVisible(show_events)
        if show_events and not self.latency_count:
            low, high = events[0].observed_at.timestamp(), events[-1].observed_at.timestamp()
            self.main_plot.setXRange(low - 1, high + 1, padding=0)
        elif self.follow and self.latency_count:
            self.reset_zoom()
        if len(with_latency) > len(names):
            self.caption.setText("Latency lines show the 8 most recently represented targets.")
        elif self.latency_count:
            self.caption.setText("Hover for details. Wheel to zoom and drag to pan.")
        elif show_events:
            self.caption.setText("Outcome markers are shown without latency values.")
        else:
            self.caption.setText("")

    def _hover(self, event: tuple[Any, ...]) -> None:
        if not self.latency_count or not event:
            return
        at = self.main_plot.getViewBox().mapSceneToView(event[0]).x()
        timings = [
            row
            for row in self._samples
            if row.observation.status is SampleStatus.SUCCESS
            and row.observation.latency_ns is not None
            and f"{row.protocol} · {row.target}" in self._series
        ]
        if timings:
            nearest = min(timings, key=lambda row: abs(row.observed_at.timestamp() - at))
            stamp = datetime.fromtimestamp(nearest.observed_at.timestamp(), UTC).strftime(
                "%H:%M:%S UTC"
            )
            elapsed = latency(nearest.observation.latency_ns, self._unit)
            self.caption.setText(f"{stamp} · {nearest.target} · {elapsed}")
