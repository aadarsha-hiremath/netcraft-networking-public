"""Native NetCraft workspace shell and explicit shutdown coordination."""

from collections.abc import Callable
from pathlib import Path
from typing import override

from PySide6.QtCore import QSettings, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence, QResizeEvent
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from netcraft.gui.diagnostic_data import DiagnosticRequest, DiagnosticView, execute_diagnostic
from netcraft.gui.diagnostics_page import DiagnosticsPage
from netcraft.gui.history_page import HistoryPage
from netcraft.gui.live_page import LivePage
from netcraft.gui.preferences import PreferenceStore
from netcraft.gui.report_pages import AnalyticsPage, ExportPage, OverviewPage
from netcraft.gui.session import Session
from netcraft.gui.settings_page import SettingsPage
from netcraft.gui.theme import apply_theme, icon, tone
from netcraft.gui.widgets import button, label
from netcraft.gui.workers import JobControl
from netcraft.monitoring.probes import run_probe
from netcraft.monitoring.scheduler import Probe

ROUTES = ("Overview", "Diagnostics", "Live Monitor", "History", "Analytics", "Export", "Settings")


class StudioWindow(QMainWindow):
    def __init__(
        self,
        history_path: Path,
        settings: QSettings,
        *,
        diagnostic: Callable[[DiagnosticRequest, JobControl], DiagnosticView] = execute_diagnostic,
        probe: Probe = run_probe,
    ) -> None:
        super().__init__()
        self.setWindowTitle("NetCraft — Network Diagnostics Studio")
        self.setMinimumSize(760, 440)
        self.resize(1400, 810)
        self._route = "Overview"
        self._collapsed = False
        self._manual_collapse: bool | None = None
        self.waiting_to_close = False
        self.session = Session(history_path, PreferenceStore(settings), self)
        self.overview = OverviewPage(self.session)
        self.diagnostics = DiagnosticsPage(self.session, diagnostic)
        self.live = LivePage(self.session, probe)
        self.history = HistoryPage(self.session)
        self.analytics = AnalyticsPage(self.session)
        self.export = ExportPage(self.session)
        self.settings = SettingsPage(self.session)
        self.pages = dict(
            zip(
                ROUTES,
                (
                    self.overview,
                    self.diagnostics,
                    self.live,
                    self.history,
                    self.analytics,
                    self.export,
                    self.settings,
                ),
                strict=True,
            )
        )
        self._actions: dict[str, tuple[str, Callable[[], None]]] = {
            "Overview": ("Refresh overview", self.overview.load),
            "Diagnostics": ("Start diagnostic", self.diagnostics.request),
            "Live Monitor": ("Add target", self.live.add.click),
            "History": ("Refresh history", self.history.load),
            "Analytics": ("Refresh analytics", self.analytics.load),
            "Export": ("Choose destination…", self.export.request),
            "Settings": ("Save preferences", self.settings.save_preferences),
        }
        self.stack = QStackedWidget()
        for page in self.pages.values():
            self.stack.addWidget(page)

        self.sidebar = QFrame()
        self.sidebar.setObjectName("sidebar")
        nav = QVBoxLayout(self.sidebar)
        nav.setContentsMargins(11, 17, 11, 13)
        nav.setSpacing(7)
        self.mark = label()
        self.mark.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.brand = label("NetCraft", "brand")
        self.subtitle = label("Network Diagnostics\nStudio", "muted")
        nav.addWidget(self.mark)
        nav.addWidget(self.brand)
        nav.addWidget(self.subtitle)
        nav.addSpacing(18)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.navigation = {}
        for index, name in enumerate(ROUTES, start=1):
            control = button(name)
            control.setCheckable(True)
            control.setObjectName("nav")
            control.setToolTip(f"{name} (Ctrl+{index})")
            control.setIconSize(QSize(19, 19))
            control.clicked.connect(lambda _checked=False, page=name: self.show_page(page))
            self.group.addButton(control)
            self.navigation[name] = control
            nav.addWidget(control)
            shortcut = QAction(f"Open {name}", self)
            shortcut.setShortcut(QKeySequence(f"Ctrl+{index}"))
            shortcut.triggered.connect(lambda _checked=False, page=name: self.show_page(page))
            self.addAction(shortcut)
        nav.addStretch(1)
        self.local = label("Local workspace", "muted")
        nav.addWidget(self.local)

        toolbar = QFrame()
        toolbar.setObjectName("toolbar")
        top = QHBoxLayout(toolbar)
        top.setContentsMargins(17, 9, 20, 9)
        top.setSpacing(11)
        self.collapse = button("Menu")
        self.collapse.setAccessibleName("Collapse or expand navigation")
        self.collapse.setToolTip("Toggle sidebar")
        self.collapse.clicked.connect(self._toggle)
        self.title = label("Overview", "pageTitle")
        self.context_action = button("Refresh overview")
        self.context_action.clicked.connect(self._context_action)
        self.monitor_status = label("Monitoring · Stopped", "accent")
        self.monitor_status.setAccessibleName("Monitoring status")
        self.settings_button = button("Settings")
        self.settings_button.clicked.connect(lambda: self.show_page("Settings"))
        for widget in (self.collapse, self.title):
            top.addWidget(widget)
        top.addStretch(1)
        for widget in (self.monitor_status, self.context_action, self.settings_button):
            top.addWidget(widget)
        workspace = QWidget()
        body = QVBoxLayout(workspace)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(toolbar)
        body.addWidget(self.stack, 1)
        central = QWidget()
        shell = QHBoxLayout(central)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)
        shell.addWidget(self.sidebar)
        shell.addWidget(workspace, 1)
        self.setCentralWidget(central)

        bar = QStatusBar()
        self.engine_status = label("Engine ready", "muted")
        self.engine_status.setAccessibleName("Engine readiness")
        self.bottom_monitor = label("Monitoring stopped", "muted")
        self.activity = label("Ready", "muted")
        self.activity.setAccessibleName("Recent operation")
        bar.addWidget(self.engine_status)
        bar.addWidget(self.bottom_monitor)
        bar.addPermanentWidget(self.activity, 1)
        self.setStatusBar(bar)
        self.session.navigate.connect(self.show_page)
        self.session.show_run.connect(self._show_run)
        self.session.export_run.connect(self._export_run)
        self.session.monitor_changed.connect(self._monitor_state)
        self.session.status_changed.connect(self._status)
        self.session.preferences_changed.connect(self._theme)
        self.session.jobs.idle.connect(self._try_close)
        self.navigation["Overview"].setChecked(True)
        self._theme()
        self._layout_sidebar()
        QTimer.singleShot(0, self._load_first_view)

    @property
    def current_page(self) -> str:
        return self._route

    @property
    def sidebar_collapsed(self) -> bool:
        return self._collapsed

    def _load_first_view(self) -> None:
        if self._route == "Overview" and not self.waiting_to_close:
            self.overview.load()

    def show_page(self, name: str) -> None:
        if self.waiting_to_close or name not in self.pages:
            return
        self._route = name
        self.navigation[name].setChecked(True)
        self.stack.setCurrentWidget(self.pages[name])
        self.navigation[name].setFocus(Qt.FocusReason.ShortcutFocusReason)
        self.title.setText(name)
        self.context_action.setText(self._actions[name][0])
        self.context_action.setAccessibleName(self._actions[name][0])
        self._monitor_state()
        if name in {"Overview", "History", "Analytics"}:
            self._actions[name][1]()

    def _context_action(self) -> None:
        self._actions[self._route][1]()

    def _show_run(self, run_id: str) -> None:
        self.show_page("History")
        self.history.open_run(run_id)

    def _export_run(self, run_id: str) -> None:
        self.show_page("Export")
        self.export.select_run(run_id)

    def _monitor_state(self) -> None:
        state = self.session.monitor_state
        self.monitor_status.setText(f"Monitoring · {state}")
        self.bottom_monitor.setText(f"Monitoring {state.lower()}")
        tone(
            self.monitor_status,
            "good" if state == "Running" else ("warn" if state == "Stopping…" else ""),
        )
        self.context_action.setEnabled(self._route != "Live Monitor" or state == "Stopped")

    def _status(self, message: str, style: str) -> None:
        self.activity.setText(message)
        self.activity.setToolTip(message)
        tone(self.activity, style)

    def _theme(self) -> None:
        app = QApplication.instance()
        assert isinstance(app, QApplication)
        theme = self.session.theme
        apply_theme(app, theme)
        self.setWindowIcon(icon("Diagnostics", theme.cyan))
        self.mark.setPixmap(icon("Diagnostics", theme.cyan).pixmap(27, 27))
        self.collapse.setIcon(icon("Menu", theme.muted))
        self.settings_button.setIcon(icon("Settings", theme.muted))
        for name, control in self.navigation.items():
            control.setIcon(icon(name, theme.cyan))

    def _toggle(self) -> None:
        self._manual_collapse = not self._collapsed
        self._layout_sidebar()

    def _layout_sidebar(self) -> None:
        automatic = self.width() < 1320
        self._collapsed = automatic if self._manual_collapse is None else self._manual_collapse
        self.sidebar.setFixedWidth(62 if self._collapsed else 196)
        self.brand.setVisible(not self._collapsed)
        self.subtitle.setVisible(not self._collapsed)
        self.local.setVisible(not self._collapsed)
        for name, control in self.navigation.items():
            control.setText("" if self._collapsed else name)
            control.setAccessibleName(name)

    @override
    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if hasattr(self, "sidebar"):
            self._layout_sidebar()

    def confirm_monitor_close(self) -> bool:
        return (
            QMessageBox.question(
                self,
                "Stop monitoring and close?",
                "Cancel active probes and close when they stop. Hostname lookup may delay this.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            == QMessageBox.StandardButton.Yes
        )

    def _try_close(self) -> None:
        if self.waiting_to_close:
            QTimer.singleShot(25, self.close)

    @override
    def closeEvent(self, event: QCloseEvent) -> None:
        jobs = self.session.jobs
        if not jobs.active:
            event.accept()
            return
        event.ignore()
        if not self.waiting_to_close:
            if jobs.busy("monitor") and not self.confirm_monitor_close():
                return
            self.waiting_to_close = True
            self.live.timer.stop()
            self.session.notify("Waiting for background work to stop…", "warn")
            central = self.centralWidget()
            if central is not None:
                central.setEnabled(False)
            jobs.shutdown()
        if not any(jobs.busy(lane) for lane in ("diagnostic", "monitor", "history", "file")):
            self._try_close()
