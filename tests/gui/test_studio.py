# ruff: noqa: E402
import csv
import json
import socket
import sqlite3
import subprocess
import sys
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event, Lock

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pyqtgraph")
pytest.importorskip("pytestqt")

import dns.message
import dns.rcode
import dns.rrset
from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox
from pytestqt.qtbot import QtBot

from netcraft.core.execution import CancellationToken
from netcraft.diagnostics.lab import echo_server
from netcraft.gui import history_page, report_pages
from netcraft.gui.charts import TrendChart
from netcraft.gui.data import HistoryPageData, WindowFilter, read_runs
from netcraft.gui.diagnostic_data import DiagnosticRequest, DiagnosticView
from netcraft.gui.theme import LIGHT
from netcraft.gui.widgets import pick
from netcraft.gui.window import ROUTES, StudioWindow
from netcraft.gui.workers import JobControl
from netcraft.models import ErrorCategory, Protocol
from netcraft.monitoring.export import ExportFormat, Report
from netcraft.monitoring.history import HistoryFilter, open_history
from netcraft.monitoring.probes import ProbeOptions, Target
from netcraft.monitoring.telemetry import (
    MonitorProtocol,
    Observation,
    Run,
    RunStatus,
    Sample,
    SampleStatus,
    StopReason,
)
from tests.protocol_fixtures import dns_fixture, fixed_http, server_tls, tcp_fixture

_RESPONSE = b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nSet-Cookie: unsafe=volatile\r\n\r\n"


def _settings(folder: Path) -> QSettings:
    return QSettings(str(folder / "preferences.ini"), QSettings.Format.IniFormat)


def _open(qtbot: QtBot, folder: Path) -> StudioWindow:
    window = StudioWindow(folder / "history.sqlite3", _settings(folder))
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window.overview.snapshot is not None, timeout=5000)
    return window


def _tcp_form(window: StudioWindow, host: str, port: int) -> None:
    window.show_page("Diagnostics")
    window.diagnostics.editor.host.setText(host)
    window.diagnostics.editor.port.setText(str(port))


def _target(window: StudioWindow, port: int) -> None:
    window.live.set_target(
        DiagnosticRequest(Target(MonitorProtocol.TCP, "127.0.0.1", port), ProbeOptions())
    )


def _seed(folder: Path, *, many: bool = False, run_id: str = "verified-test-run") -> str:
    now = datetime.now(UTC) - timedelta(minutes=1)
    run = Run(run_id, RunStatus.RUNNING, now, None, 1_000_000_000, 3, 2, 1, None)
    outcomes = (
        Observation(SampleStatus.SUCCESS, latency_ns=1_000_000),
        Observation(SampleStatus.TIMEOUT, ErrorCategory.TIMEOUT, latency_ns=9_000_000),
        Observation(SampleStatus.SUCCESS, latency_ns=5_000_000),
        Observation(SampleStatus.SKIPPED, reason=StopReason.WORKER_LIMIT),
    )
    with open_history(folder / "history.sqlite3") as history:
        history.add_run(run)
        for index, obs in enumerate(outcomes):
            at = now + timedelta(seconds=index)
            history.insert_sample(
                Sample(
                    f"{run_id}-{index}",
                    run.run_id,
                    index,
                    MonitorProtocol.TCP,
                    "127.0.0.1:9000",
                    at,
                    at,
                    obs,
                )
            )
        history.finish_run(run.run_id, RunStatus.COMPLETED, now + timedelta(seconds=4))
        if many:
            for i in range(36):
                extra = Run(f"extra-{i}", RunStatus.RUNNING, now, None, 1, 1, 1, 1, None)
                history.add_run(extra)
                history.finish_run(extra.run_id, RunStatus.COMPLETED, now)
    return run.run_id


def test_first_launch_and_routes_do_not_probe(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_lookup(*_args: object, **_kwargs: object) -> None:
        pytest.fail("Automatic network lookup")

    monkeypatch.setattr(socket, "getaddrinfo", unexpected_lookup)
    window = _open(qtbot, tmp_path)
    values = window.overview.metrics.values
    assert values["Configured targets"].text() == "0"
    assert values["Monitoring"].text() == "Stopped"
    assert values["Success rate"].text() == values["Median latency"].text() == "—"
    assert window.overview.chart.latency_count == 0
    assert window.overview.activity.text() == "No recent activity."
    for route in ROUTES:
        QTest.mouseClick(window.navigation[route], Qt.MouseButton.LeftButton)
        assert window.current_page == route
        assert window.stack.currentWidget() is window.pages[route]
    assert not window.session.jobs.busy("monitor")
    assert not window.session.jobs.busy("diagnostic")


@pytest.mark.parametrize(
    ("width", "height", "collapsed"),
    [
        (1600, 900, False),
        (1366, 768, False),
        (1280, 720, True),
    ],
)
def test_responsive_sidebar_and_accessible_names(
    qtbot: QtBot,
    tmp_path: Path,
    width: int,
    height: int,
    collapsed: bool,
) -> None:
    window = _open(qtbot, tmp_path)
    window.resize(width, height)
    qtbot.waitUntil(lambda: window.sidebar_collapsed is collapsed)
    for route in ROUTES:
        assert window.navigation[route].accessibleName() == route
        assert window.navigation[route].focusPolicy() != Qt.FocusPolicy.NoFocus
        window.show_page(route)
        current = window.stack.currentWidget()
        assert current is not None and current.width() <= width
    window.show_page("Diagnostics")
    assert window.diagnostics.editor.host.accessibleName() == "Hostname or IP address"
    assert window.diagnostics.start.focusPolicy() != Qt.FocusPolicy.NoFocus
    window.diagnostics.editor.host.setFocus()
    QTest.keyClick(window.diagnostics.editor.host, Qt.Key.Key_Tab)
    assert window.diagnostics.editor.port.hasFocus()


@pytest.mark.parametrize(
    ("protocol", "field", "udp_payload"),
    [
        (MonitorProtocol.TCP, "host", False),
        (MonitorProtocol.UDP, "host", True),
        (MonitorProtocol.DNS, "dns_name", False),
        (MonitorProtocol.HTTP, "url", False),
        (MonitorProtocol.HTTPS, "url", False),
    ],
)
def test_protocol_fields(
    qtbot: QtBot,
    tmp_path: Path,
    protocol: MonitorProtocol,
    field: str,
    udp_payload: bool,
) -> None:
    window = _open(qtbot, tmp_path)
    window.show_page("Diagnostics")
    editor = window.diagnostics.editor
    pick(editor.protocol, protocol)
    assert getattr(editor, field).isVisible()
    assert editor.payload.isVisible() is udp_payload
    assert editor.ca_button.isVisible() is (protocol is MonitorProtocol.HTTPS)


@pytest.mark.parametrize(
    ("host", "port", "timeout", "message"),
    [
        ("invalid/host", 80, "3", "hostname"),
        ("127.0.0.1", 65536, "3", "Port"),
        ("127.0.0.1", 80, "nan", "timeout"),
    ],
)
def test_validation_precedes_dispatch(
    qtbot: QtBot,
    tmp_path: Path,
    host: str,
    port: int,
    timeout: str,
    message: str,
) -> None:
    window = _open(qtbot, tmp_path)
    _tcp_form(window, host, port)
    window.diagnostics.editor.timeout.setText(timeout)
    window.diagnostics.request()
    assert message.lower() in window.diagnostics.error.text().lower()
    assert not window.session.jobs.busy("diagnostic")


@pytest.mark.parametrize("protocol", [Protocol.TCP, Protocol.UDP])
def test_local_socket_diagnostic_and_result_snapshot(
    qtbot: QtBot,
    tmp_path: Path,
    protocol: Protocol,
) -> None:
    window = _open(qtbot, tmp_path)
    with echo_server(protocol) as peer:
        _tcp_form(window, peer.address, peer.port)
        pick(window.diagnostics.editor.protocol, MonitorProtocol(protocol.value))
        window.diagnostics.request()
        window.diagnostics.editor.host.setText("edited.example.test")
        qtbot.waitUntil(lambda: window.diagnostics.result is not None, timeout=5000)
    result = window.diagnostics.result
    assert result is not None and result.outcome == "Success"
    assert result.target == f"{peer.address}:{peer.port}"
    assert result.timing_ns["tcp" if protocol is Protocol.TCP else "udp_exchange"] is not None
    assert "edited.example.test" not in window.diagnostics.details.toPlainText()
    window.diagnostics.reset_result()
    assert window.diagnostics.result is None and not window.diagnostics.copy.isEnabled()


@pytest.mark.parametrize("missing", [False, True])
def test_dns_answer_and_failure_rendering(qtbot: QtBot, tmp_path: Path, missing: bool) -> None:
    window = _open(qtbot, tmp_path)

    def reply(query: dns.message.QueryMessage, _tcp: bool) -> bytes:
        response = dns.message.make_response(query)
        if missing:
            response.set_rcode(dns.rcode.NXDOMAIN)
        else:
            response.answer.append(
                dns.rrset.from_text("gui.example.test.", 47, "IN", "A", "192.0.2.1")
            )
        return response.to_wire()

    with dns_fixture(reply) as resolver:
        window.show_page("Diagnostics")
        editor = window.diagnostics.editor
        pick(editor.protocol, MonitorProtocol.DNS)
        editor.dns_name.setText("gui.example.test")
        editor.resolver.setText(resolver.address)
        editor.resolver_port.setText(str(resolver.port))
        window.diagnostics.request()
        qtbot.waitUntil(lambda: window.diagnostics.result is not None, timeout=5000)
    result = window.diagnostics.result
    assert result is not None and result.error == ("dns_nxdomain" if missing else None)
    assert result.metadata["response_code"] == ("NXDOMAIN" if missing else "NOERROR")
    if not missing:
        assert '"ttl": 47' in window.diagnostics.details.toPlainText()


def test_blank_dns_field_reaches_windows_policy(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from netcraft.protocols import dns as dns_protocol
    from netcraft.protocols.dns_windows import SystemReply

    def system_query(name: str, kind: int, *_: object) -> SystemReply:
        assert name == "gui.example.test." and kind == 1
        return SystemReply(9501)

    monkeypatch.setattr(dns_protocol, "_WINDOWS_SYSTEM", True)
    monkeypatch.setattr(dns_protocol, "query_system", system_query)
    window = _open(qtbot, tmp_path)
    window.show_page("Diagnostics")
    editor = window.diagnostics.editor
    pick(editor.protocol, MonitorProtocol.DNS)
    editor.dns_name.setText("gui.example.test")
    assert not editor.resolver.text()
    QTest.mouseClick(window.diagnostics.start, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: window.diagnostics.result is not None, timeout=5000)
    result = window.diagnostics.result
    assert result is not None
    assert result.metadata["resolver"] == "System resolver"
    assert result.metadata["response_code"] == "NOERROR"
    assert result.metadata["fallback_occurred"] is None


@pytest.mark.parametrize("encrypted", [False, True])
def test_web_diagnostic_is_verified_and_sanitized(
    qtbot: QtBot, tmp_path: Path, encrypted: bool
) -> None:
    window = _open(qtbot, tmp_path)
    app = window.diagnostics
    tls, ca = server_tls("127.0.0.1")
    pem = tmp_path / "test-ca.pem"
    ca.cert_pem.write_to_path(pem)
    with tcp_fixture(fixed_http(_RESPONSE), tls=tls if encrypted else None) as peer:
        window.show_page("Diagnostics")
        protocol = MonitorProtocol.HTTPS if encrypted else MonitorProtocol.HTTP
        pick(app.editor.protocol, protocol)
        app.editor.ca_path = pem if encrypted else None
        app.editor.url.setText(f"{protocol}://127.0.0.1:{peer.port}/private-path?key=volatile")
        pick(app.editor.method, "HEAD")
        app.request()
        qtbot.waitUntil(lambda: app.result is not None, timeout=5000)
    result = app.result
    assert result is not None and result.outcome == "HTTP error status"
    assert result.metadata["http_status"] == 404
    assert ("tls" in result.metadata) is encrypted
    assert "volatile" not in app.details.toPlainText()
    assert "private-path" not in app.details.toPlainText()


def test_live_completion_updates_chart_and_history(qtbot: QtBot, tmp_path: Path) -> None:
    window = _open(qtbot, tmp_path)
    with echo_server(Protocol.TCP) as peer:
        window.show_page("Live Monitor")
        _target(window, peer.port)
        window.live.interval.setValue(0.15)
        window.live.slots.setValue(3)
        QTest.mouseClick(window.live.start, Qt.MouseButton.LeftButton)
        assert window.session.monitor_state == "Running"
        window.show_page("Settings")
        assert window.monitor_status.text() == "Monitoring · Running"
        window.show_page("Live Monitor")
        qtbot.waitUntil(lambda: window.live.last_run is not None, timeout=5000)
    assert window.live.stats.values["Attempts"].text() == "3"
    assert window.live.stats.values["Successful"].text() == "3"
    assert window.live.chart.latency_count == 3
    assert window.session.monitor_state == "Stopped"
    with open_history(tmp_path / "history.sqlite3") as history:
        assert len(tuple(history.samples())) == 3


def test_saturation_cancellation_and_gui_heartbeat(qtbot: QtBot, tmp_path: Path) -> None:
    entered, cancelled = Event(), Event()
    active: set[str] = set()
    peak = 0
    lock = Lock()

    def slow(target: Target, _options: ProbeOptions, token: CancellationToken) -> Observation:
        nonlocal peak
        with lock:
            assert target.label not in active
            active.add(target.label)
            peak = max(peak, len(active))
        entered.set()
        token.wait(4)
        if token.cancelled:
            cancelled.set()
        with lock:
            active.remove(target.label)
        return Observation(SampleStatus.CANCELLED, ErrorCategory.CANCELLED)

    window = StudioWindow(tmp_path / "history.sqlite3", _settings(tmp_path), probe=slow)
    qtbot.addWidget(window)
    window.show()
    window.show_page("Live Monitor")
    _target(window, 9010)
    _target(window, 9011)
    window.live.workers.setValue(1)
    window.live.interval.setValue(0.13)
    window.live.request()
    qtbot.waitUntil(entered.is_set, timeout=2000)
    ticks: list[int] = []
    pulse = QTimer(window)
    pulse.setInterval(15)
    pulse.timeout.connect(lambda: ticks.append(1))
    pulse.start()
    qtbot.waitUntil(lambda: int(window.live.stats.values["Skipped"].text().replace("—", "0")) >= 2)
    window.live.cancel()
    qtbot.waitUntil(lambda: window.live.last_run is not None, timeout=5000)
    pulse.stop()
    assert len(ticks) >= 2 and peak == 1 and cancelled.is_set()
    assert window.live.last_run is not None and window.live.last_run.status is RunStatus.CANCELLED
    assert window.live.stats.values["Cancelled"].text() == "1"
    assert window.live.chart.latency_count == 0
    assert window.live.chart.event_count >= 3


def test_chart_excludes_unavailable_latency(qtbot: QtBot) -> None:
    chart = TrendChart()
    qtbot.addWidget(chart)
    now = datetime.now(UTC)
    rows = tuple(
        Sample(str(i), "run", i, MonitorProtocol.TCP, "local", now, now + timedelta(seconds=i), row)
        for i, row in enumerate(
            (
                Observation(SampleStatus.TIMEOUT, ErrorCategory.TIMEOUT, latency_ns=999),
                Observation(SampleStatus.SUCCESS, latency_ns=None),
                Observation(SampleStatus.SUCCESS, latency_ns=1_000_000),
            )
        )
    )
    chart.show_samples(rows)
    assert chart.latency_count == 1 and chart.event_count == 1
    assert chart.rendered_count == 3
    chart.show_samples(())
    assert chart.latency_count == chart.event_count == 0


def test_history_filters_details_and_analytics_use_phase_three_metrics(
    qtbot: QtBot, tmp_path: Path
) -> None:
    run_id = _seed(tmp_path)
    window = _open(qtbot, tmp_path)
    window.show_page("History")
    qtbot.waitUntil(lambda: len(window.history.table.rows.rows) == 1)
    window.history.target.setText("unrelated")
    window.history.first()
    qtbot.waitUntil(lambda: len(window.history.table.rows.rows) == 0)
    window.history.target.setText("127.0.0.1")
    pick(window.history.protocol, "tcp")
    window.history.first()
    qtbot.waitUntil(lambda: len(window.history.table.rows.rows) == 1)
    dialog = window.history.open_run(run_id)
    qtbot.waitUntil(lambda: len(dialog.table.rows.rows) == 4)
    dialog.table.selectRow(0)
    assert "latency_ns" in dialog.telemetry.toPlainText()
    dialog.close()
    window.show_page("Analytics")
    qtbot.waitUntil(lambda: window.analytics.snapshot is not None)
    assert window.analytics.metrics.values["Median"].text() == "3.000 ms"
    assert window.analytics.metrics.values["p95"].text() == "5.000 ms"
    assert window.analytics.metrics.values["p99"].text() == "5.000 ms"
    assert window.analytics.metrics.values["Success rate"].text() == "66.7%"
    assert window.analytics.metrics.values["Timeout rate"].text() == "33.3%"
    assert window.analytics.metrics.values["Jitter"].text() == "4.000 ms"
    assert window.analytics.chart.latency_count == 2
    assert window.analytics.errors.rows.rows[0].cells == ("timeout", "1")


def test_history_pagination_and_reload_after_restart(qtbot: QtBot, tmp_path: Path) -> None:
    _seed(tmp_path, many=True)
    first = _open(qtbot, tmp_path)
    assert first.close()
    second = _open(qtbot, tmp_path)
    second.show_page("History")
    qtbot.waitUntil(lambda: second.history.next.isEnabled())
    assert len(second.history.table.rows.rows) == 35
    QTest.mouseClick(second.history.next, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: second.history.page_label.text() == "Page 2")
    assert len(second.history.table.rows.rows) == 2


def test_settings_persist_across_window_reopen(qtbot: QtBot, tmp_path: Path) -> None:
    window = _open(qtbot, tmp_path)
    window.show_page("Settings")
    settings = window.settings
    settings.dark.setChecked(False)
    settings.workers.setValue(8)
    settings.timeout.setValue(2.1)
    pick(settings.unit, "µs")
    pick(settings.format, "json")
    settings.save_preferences()
    assert window.session.theme == LIGHT
    assert window.close()
    reopened = _open(qtbot, tmp_path)
    assert reopened.session.theme == LIGHT
    assert reopened.session.preferences.unit == "µs"
    assert reopened.live.workers.value() == 8
    assert reopened.diagnostics.editor.timeout.text() == "2.1"
    assert reopened.export.format.currentText() == "JSON"
    reopened.settings.reset_preferences()
    assert reopened.session.preferences.dark and reopened.session.preferences.workers == 4


def test_preferences_are_readable_by_a_new_process(qtbot: QtBot, tmp_path: Path) -> None:
    window = _open(qtbot, tmp_path)
    window.settings.timeout.setValue(7.25)
    window.settings.save_preferences()
    assert window.close()
    code = (
        "import sys; from PySide6.QtCore import QSettings; "
        "from netcraft.gui.preferences import PreferenceStore; "
        "print(PreferenceStore(QSettings(sys.argv[1], QSettings.Format.IniFormat)).load().timeout)"
    )
    process = subprocess.run(
        [sys.executable, "-I", "-c", code, str(tmp_path / "preferences.ini")],
        timeout=10,
        check=False,
        capture_output=True,
        text=True,
    )
    assert process.returncode == 0, process.stderr
    assert process.stdout.strip() == "7.25"


def test_history_delete_requires_confirmation(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id = _seed(tmp_path)
    window = _open(qtbot, tmp_path)
    window.show_page("History")
    qtbot.waitUntil(lambda: len(window.history.table.rows.rows) == 1)

    def unexpected_confirmation(*_args: object) -> None:
        pytest.fail("A confirmation opened without a selected run.")

    monkeypatch.setattr(QMessageBox, "question", unexpected_confirmation)
    assert not window.history.delete.isEnabled()
    window.history.delete_selected()
    window.history.table.selectRow(0)
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.No)
    window.history.delete_selected()
    with open_history(tmp_path / "history.sqlite3") as history:
        assert history.get_run(run_id) is not None
    assert not window.session.jobs.busy("file")
    assert window.activity.text() != "Run deleted."
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.Yes)
    window.history.delete_selected()
    qtbot.waitUntil(lambda: len(window.history.table.rows.rows) == 0, timeout=5000)
    with open_history(tmp_path / "history.sqlite3") as history:
        assert history.get_run(run_id) is None and list(history.samples()) == []


def test_failed_history_delete_retains_row_and_allows_retry(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id = _seed(tmp_path)
    path = tmp_path / "history.sqlite3"
    with open_history(path) as history:
        original = tuple(history.samples())
        history.connection.execute(
            "CREATE TRIGGER reject_deletion BEFORE DELETE ON runs "
            "BEGIN SELECT RAISE(ABORT, 'fixture'); END"
        )
    window = _open(qtbot, tmp_path)
    window.show_page("History")
    page = window.history
    qtbot.waitUntil(lambda: page.table.model().rowCount() == 1)
    page.table.selectRow(0)
    confirmations = []

    def confirm(*_args: object) -> QMessageBox.StandardButton:
        confirmations.append(True)
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", confirm)
    QTest.mouseClick(page.delete, Qt.MouseButton.LeftButton)
    assert not page.delete.isEnabled()
    page.delete_selected()
    assert confirmations == [True]
    qtbot.waitUntil(lambda: not window.session.jobs.busy("file"))
    assert window.activity.text().startswith("Could not read or write history.")
    assert page.table.selected_key() == run_id
    assert page.delete.isEnabled()
    with open_history(path) as history:
        assert history.get_run(run_id) is not None and tuple(history.samples()) == original
        history.connection.execute("DROP TRIGGER reject_deletion")

    QTest.mouseClick(page.delete, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: page.table.model().rowCount() == 0)
    assert window.activity.text() == "Run deleted."
    assert confirmations == [True, True]


def test_history_delete_already_removed_run_does_not_report_success(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id = _seed(tmp_path)
    window = _open(qtbot, tmp_path)
    window.show_page("History")
    page = window.history
    qtbot.waitUntil(lambda: page.table.model().rowCount() == 1)
    page.table.selectRow(0)
    with open_history(tmp_path / "history.sqlite3") as history:
        assert history.delete_run(run_id)
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.Yes)
    QTest.mouseClick(page.delete, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: page.table.model().rowCount() == 0)
    assert window.activity.text() == "Run is unavailable."
    assert not page.delete.isEnabled()


def test_old_history_read_cannot_restore_deleted_run_after_navigation(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(tmp_path)
    window = _open(qtbot, tmp_path)
    window.show_page("History")
    page = window.history
    qtbot.waitUntil(lambda: page.table.model().rowCount() == 1)
    snapshot_ready, finish_read = Event(), Event()

    def delayed_read(
        path: Path, filters: WindowFilter, index: int, control: JobControl
    ) -> HistoryPageData:
        snapshot = read_runs(path, filters, index, control)
        snapshot_ready.set()
        assert finish_read.wait(5)
        return snapshot

    monkeypatch.setattr(history_page, "read_runs", delayed_read)
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.Yes)
    page.load()
    try:
        qtbot.waitUntil(snapshot_ready.is_set)
        page.table.selectRow(0)
        QTest.mouseClick(page.delete, Qt.MouseButton.LeftButton)
        window.show_page("Diagnostics")
        qtbot.waitUntil(lambda: window.activity.text() == "Run deleted.")
        assert page.table.model().rowCount() == 0
    finally:
        finish_read.set()
    qtbot.waitUntil(lambda: not window.session.jobs.busy("history"))
    assert page.table.model().rowCount() == 0


def test_committed_run_leaves_history_when_another_connection_is_writing(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "history.sqlite3"
    deleted = _seed(tmp_path, run_id="z-selected-run")
    survivor = _seed(tmp_path, run_id="a-unrelated-run")
    with open_history(path) as history:
        survivor_run = history.get_run(survivor)
        survivor_samples = tuple(history.samples(HistoryFilter(run_id=survivor)))

    window = _open(qtbot, tmp_path)
    window.show_page("Analytics")
    qtbot.waitUntil(lambda: window.analytics.snapshot is not None)
    window.show_page("History")
    page = window.history
    table = page.table
    qtbot.waitUntil(lambda: table.model().rowCount() == 2)
    table.sortByColumn(6, Qt.SortOrder.DescendingOrder)
    assert table.model().index(0, 6).data() == deleted
    table.selectRow(0)
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.Yes)
    at_confirmation = []

    def visible_ids() -> list[str]:
        return [str(table.model().index(i, 6).data()) for i in range(table.model().rowCount())]

    with (
        closing(sqlite3.connect(path, timeout=1, autocommit=True)) as writer,
        closing(sqlite3.connect(path, timeout=1, autocommit=True)) as reader,
    ):
        reader.execute("BEGIN")
        assert reader.execute("SELECT count(*) FROM runs").fetchone()[0] == 2

        def hold_write_lock(message: str, _style: str) -> None:
            if message == "Run deleted.":
                writer.execute("BEGIN IMMEDIATE")
                at_confirmation.append(visible_ids())

        window.session.status_changed.connect(hold_write_lock)
        QTest.mouseClick(page.delete, Qt.MouseButton.LeftButton)
        qtbot.waitUntil(lambda: bool(at_confirmation))
        assert at_confirmation == [[survivor]]
        assert window.overview.snapshot is None
        assert window.analytics.snapshot is None
        qtbot.waitUntil(lambda: not window.session.jobs.busy("history"))
        assert page.message.text() == "1 run on this page"
        QTest.mouseClick(window.context_action, Qt.MouseButton.LeftButton)
        qtbot.waitUntil(lambda: not window.session.jobs.busy("history"))
        assert page.message.text() == "1 run on this page"
        assert visible_ids() == [survivor]

        window.show_page("Overview")
        qtbot.waitUntil(lambda: window.overview.snapshot is not None)
        assert window.overview.snapshot is not None
        assert [row.run_id for row in window.overview.snapshot.recent_runs] == [survivor]
        window.show_page("Analytics")
        qtbot.waitUntil(lambda: window.analytics.snapshot is not None)
        assert window.analytics.snapshot is not None
        assert window.analytics.snapshot.overall.samples == len(survivor_samples)
        window.show_page("Export")
        export_path = tmp_path / "current.json"
        pick(window.export.report, Report.SAMPLES)
        pick(window.export.format, ExportFormat.JSON)
        monkeypatch.setattr(report_pages, "choose_destination", lambda *_args: (export_path, False))
        QTest.mouseClick(window.export.save, Qt.MouseButton.LeftButton)
        qtbot.waitUntil(window.export.save.isEnabled)
        assert window.export.message.text() == "Report saved."
        exported = json.loads(export_path.read_text("utf-8"))["rows"]
        assert exported == [sample.to_dict() for sample in survivor_samples]
        window.show_page("History")
        qtbot.waitUntil(lambda: not window.session.jobs.busy("history"))
        assert visible_ids() == [survivor]
        # The older transaction sees its original snapshot and advances after commit.
        assert reader.execute("SELECT count(*) FROM runs").fetchone()[0] == 2
        writer.execute("ROLLBACK")
        reader.execute("COMMIT")
        assert reader.execute("SELECT run_id FROM runs").fetchall() == [(survivor,)]

    assert window.close()
    reopened = _open(qtbot, tmp_path)
    reopened.show_page("History")
    qtbot.waitUntil(lambda: reopened.history.table.model().rowCount() == 1)
    assert reopened.history.table.model().index(0, 6).data() == survivor
    with open_history(path) as history:
        assert history.get_run(deleted) is None
        assert tuple(history.runs(10)) == (survivor_run,)
        assert tuple(history.samples()) == survivor_samples
        assert history.connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert history.connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_gui_exports_csv_and_json_using_the_same_history(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id = _seed(tmp_path)
    window = _open(qtbot, tmp_path)
    window.show_page("Export")
    page = window.export
    pick(page.report, Report.SAMPLES)
    page.run_id.setText(run_id)
    paths: dict[str, Path] = {}
    for suffix in (ExportFormat.JSON, ExportFormat.CSV):
        path = tmp_path / f"observation.{suffix}"
        paths[suffix] = path
        pick(page.format, suffix)
        monkeypatch.setattr(
            report_pages, "choose_destination", lambda *_args, dest=path: (dest, False)
        )
        page.request()
        qtbot.waitUntil(path.is_file, timeout=5000)
        qtbot.waitUntil(page.save.isEnabled)
    rows = json.loads(paths["json"].read_text("utf-8"))["rows"]
    with paths["csv"].open(encoding="utf-8", newline="") as stream:
        csv_rows = list(csv.DictReader(stream))
    assert [row["sample_id"] for row in rows] == [row["sample_id"] for row in csv_rows]
    assert [row["latency_ns"] for row in rows] == [
        int(row["latency_ns"]) if row["latency_ns"] else None for row in csv_rows
    ]


def test_busy_and_unavailable_history_are_user_facing(qtbot: QtBot, tmp_path: Path) -> None:
    window = _open(qtbot, tmp_path)
    window.show_page("Live Monitor")
    _target(window, 9033)
    window.live.slots.setValue(1)
    with open_history(tmp_path / "history.sqlite3") as history, history.exclusive():
        window.live.request()
        qtbot.waitUntil(lambda: "busy" in window.live.error.text(), timeout=5000)
    unavailable = StudioWindow(tmp_path, _settings(tmp_path))
    qtbot.addWidget(unavailable)
    unavailable.show()
    unavailable.show_page("History")
    qtbot.waitUntil(lambda: "Could not read" in unavailable.history.message.text(), timeout=5000)


def test_close_waits_for_monitor_and_honors_decline(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered = Event()

    def blocking(_target: Target, _options: ProbeOptions, token: CancellationToken) -> Observation:
        entered.set()
        token.wait(4)
        assert token.cancelled
        return Observation(SampleStatus.CANCELLED, ErrorCategory.CANCELLED)

    window = StudioWindow(tmp_path / "history.sqlite3", _settings(tmp_path), probe=blocking)
    qtbot.addWidget(window)
    window.show()
    window.show_page("Live Monitor")
    _target(window, 9044)
    window.live.request()
    qtbot.waitUntil(entered.is_set)
    monkeypatch.setattr(window, "confirm_monitor_close", lambda: False)
    assert not window.close() and window.isVisible()
    monkeypatch.setattr(window, "confirm_monitor_close", lambda: True)
    window.close()
    qtbot.waitUntil(lambda: not window.isVisible(), timeout=5000)
    with open_history(tmp_path / "history.sqlite3") as history:
        (run,) = history.runs(2)
    assert run.status is RunStatus.CANCELLED


def test_slow_diagnostic_keeps_route_and_ignores_untrusted_exception(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    entered, release = Event(), Event()

    def slow(_request: DiagnosticRequest, _control: JobControl) -> DiagnosticView:
        entered.set()
        release.wait(2)
        raise RuntimeError("sensitive query should never appear")

    window = StudioWindow(tmp_path / "history.sqlite3", _settings(tmp_path), diagnostic=slow)
    qtbot.addWidget(window)
    window.show()
    _tcp_form(window, "127.0.0.1", 9000)
    window.diagnostics.request()
    qtbot.waitUntil(entered.is_set)
    window.show_page("Overview")
    qtbot.waitUntil(lambda: window.overview.snapshot is not None)
    assert window.session.jobs.busy("diagnostic")
    release.set()
    qtbot.waitUntil(lambda: not window.session.jobs.busy("diagnostic"))
    assert window.current_page == "Overview"
    assert "Unexpected worker error" in window.diagnostics.explanation.text()
    assert "sensitive query" not in window.diagnostics.explanation.text()
