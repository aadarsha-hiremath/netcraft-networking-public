# ruff: noqa: E402
import csv
import json
import os
import sys
from pathlib import Path
from threading import Event

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox
from pytestqt.qtbot import QtBot

from netcraft.gui import data, report_pages
from netcraft.gui.data import save_diagnostic, save_report, write_atomic
from netcraft.gui.diagnostic_data import DiagnosticRequest, execute_diagnostic
from netcraft.gui.widgets import pick
from netcraft.gui.window import StudioWindow
from netcraft.gui.workers import JobCancelled, JobControl
from netcraft.monitoring.analytics import Grouping
from netcraft.monitoring.export import ExportFormat, Report, TextSink
from netcraft.monitoring.history import HistoryFilter, open_history
from netcraft.monitoring.probes import ProbeOptions, Target
from netcraft.monitoring.telemetry import MonitorProtocol
from tests.gui.test_retention_safety import seed_retention
from tests.protocol_fixtures import fixed_http, tcp_fixture


def test_export_cancellation_and_no_overwrite(tmp_path: Path) -> None:
    target = tmp_path / "observations.json"
    target.write_text("existing", encoding="utf-8")

    def write(stream: TextSink) -> None:
        stream.write("new")

    with pytest.raises(FileExistsError):
        write_atomic(target, False, JobControl(), write)
    assert target.read_text("utf-8") == "existing"
    control = JobControl()

    def interrupted(stream: TextSink) -> None:
        stream.write("partial")
        control.cancel()
        stream.write("cancelled")

    with pytest.raises(JobCancelled):
        write_atomic(target, True, control, interrupted)
    assert target.read_text("utf-8") == "existing"
    assert list(tmp_path.glob(".netcraft-*.tmp")) == []
    write_atomic(target, True, JobControl(), write)
    assert target.read_text("utf-8") == "new"


def test_report_protects_history_file(tmp_path: Path) -> None:
    path = tmp_path / "history.json"
    path.write_text("untouched", encoding="utf-8")
    with pytest.raises(ValueError, match="reserved"):
        save_report(
            path,
            path,
            True,
            ExportFormat.JSON,
            Report.SAMPLES,
            HistoryFilter(),
            Grouping.ALL,
            JobControl(),
        )
    assert path.read_text("utf-8") == "untouched"


def test_permission_failure_leaves_existing_export_untouched(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "samples.csv"
    target.write_text("prior", encoding="utf-8")

    def deny(*_args: object) -> None:
        raise PermissionError("fixture permission denial")

    monkeypatch.setattr(os, "replace", deny)
    with pytest.raises(PermissionError):
        write_atomic(target, True, JobControl(), _rows)
    assert target.read_text("utf-8") == "prior"
    assert list(tmp_path.glob(".netcraft-*.tmp")) == []


def _rows(stream: TextSink) -> None:
    stream.write("a,b\n")


def test_diagnostic_json_cannot_overwrite_workspace_or_expose_redirect_credentials(
    tmp_path: Path,
) -> None:
    history_path = tmp_path / "workspace.json"
    preferences_path = tmp_path / "prefs.json"
    preferences_path.write_bytes(b"[General]\nretention_count=3\n")
    with open_history(history_path):
        pass
    reply = (
        b"HTTP/1.1 302 Found\r\nContent-Length: 11\r\n"
        b"Location: https://fixture-user:fixture-password@example.test:8443/private?token=hidden\r\n"
        b"Set-Cookie: fixture-cookie=secret\r\n\r\nsecret-body"
    )
    with tcp_fixture(fixed_http(reply)) as peer:
        view = execute_diagnostic(
            DiagnosticRequest(
                Target(
                    MonitorProtocol.HTTP,
                    f"http://127.0.0.1:{peer.port}/fixture-path?key=fixture-query",
                ),
                ProbeOptions(),
            ),
            JobControl(),
        )
    assert view.metadata["http_status"] == 302
    for path in (history_path, preferences_path):
        original = path.read_bytes()
        with pytest.raises(ValueError, match="reserved"):
            save_diagnostic(
                path,
                True,
                view,
                JobControl(),
                history_path=history_path,
                preferences_path=preferences_path,
            )
        assert path.read_bytes() == original
    with pytest.raises(ValueError, match="reserved"):
        save_report(
            history_path,
            preferences_path,
            True,
            ExportFormat.JSON,
            Report.SAMPLES,
            HistoryFilter(),
            Grouping.ALL,
            JobControl(),
            preferences_path=preferences_path,
        )
    export = tmp_path / "diagnostic.json"
    save_diagnostic(export, False, view, JobControl(), history_path=history_path)
    output = export.read_text(encoding="utf-8")
    assert json.loads(output)["metadata"]["redirect"] is None
    for secret in (
        "fixture-user",
        "fixture-password",
        "private",
        "token=hidden",
        "fixture-cookie",
        "secret-body",
        "fixture-path",
        "fixture-query",
    ):
        assert secret not in output


def test_filtered_samples_summary_and_trend_match_sqlite(tmp_path: Path) -> None:
    database = tmp_path / "history.sqlite3"
    seed_retention(database)
    filters = HistoryFilter(protocol=MonitorProtocol.HTTP, target="http://127.0.0.1:8787/")
    with open_history(database) as history:
        selected = history.connection.execute(
            """SELECT sample_id, observed_at, latency_ns, http_status FROM samples
                WHERE protocol = 'http' AND target = 'http://127.0.0.1:8787/'
                ORDER BY observed_at, sample_id"""
        ).fetchall()
    assert len(selected) == 2
    for report, format in (
        (Report.SAMPLES, ExportFormat.CSV),
        (Report.SUMMARY, ExportFormat.JSON),
        (Report.TREND, ExportFormat.JSON),
    ):
        path = tmp_path / f"{report}.{format}"
        save_report(database, path, False, format, report, filters, Grouping.ALL, JobControl())
        if report == Report.SAMPLES:
            with path.open(encoding="utf-8", newline="") as stream:
                rows = list(csv.DictReader(stream))
            assert [
                (
                    row["sample_id"],
                    row["observed_at"],
                    int(row["latency_ns"]),
                    int(row["http_status"]),
                )
                for row in rows
            ] == [tuple(row) for row in selected]
        else:
            rows = json.loads(path.read_text("utf-8"))["rows"]
            if report == Report.SUMMARY:
                (row,) = rows
                latencies = [row["latency_ns"] for row in selected]
                assert row["samples"] == row["successes"] == len(latencies)
                assert row["min_ns"] == min(latencies)
                assert row["max_ns"] == max(latencies)
                assert row["median_ns"] == sum(latencies) / len(latencies)
            else:
                assert [
                    (row["sample_id"], row["observed_at"], row["latency_ns"]) for row in rows
                ] == [tuple(row)[:3] for row in selected]


def _export_window(qtbot: QtBot, tmp_path: Path) -> StudioWindow:
    path = tmp_path / "history.sqlite3"
    seed_retention(path)
    settings = QSettings(str(tmp_path / "preferences.ini"), QSettings.Format.IniFormat)
    window = StudioWindow(path, settings)
    qtbot.addWidget(window)
    window.show()
    window.show_page("Export")
    pick(window.export.report, Report.SAMPLES)
    return window


def _answer_export_prompt(
    window: StudioWindow, qtbot: QtBot, answer: QMessageBox.StandardButton
) -> None:
    prompts: list[tuple[str, bool]] = []

    def respond() -> None:
        prompt = QApplication.activeModalWidget()
        if isinstance(prompt, QMessageBox):
            prompts.append(
                (
                    prompt.windowTitle(),
                    prompt.defaultButton() == prompt.button(QMessageBox.StandardButton.No),
                )
            )
            QTest.mouseClick(prompt.button(answer), Qt.MouseButton.LeftButton)

    QTimer.singleShot(0, respond)
    QTest.mouseClick(window.export.save, Qt.MouseButton.LeftButton)
    assert prompts == [("Replace existing export?", True)]
    qtbot.waitUntil(lambda: not window.session.jobs.busy("file"), timeout=5000)


def test_export_confirmation_decline_and_accept(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = _export_window(qtbot, tmp_path)
    destination = tmp_path / "existing.csv"
    original = b"fixture sentinel\r\n"
    destination.write_bytes(original)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_a, **_kw: (str(destination), ""))
    _answer_export_prompt(window, qtbot, QMessageBox.StandardButton.No)
    assert destination.read_bytes() == original
    assert window.export.message.text() == "Export cancelled."

    if sys.platform == "win32":
        # An open reader denies replacement on Windows without changing any ACL.
        with destination.open("rb") as reader:
            _answer_export_prompt(window, qtbot, QMessageBox.StandardButton.Yes)
            assert "Permission denied" in window.export.message.text()
            assert reader.read() == original
        assert destination.read_bytes() == original
        assert list(tmp_path.glob(".netcraft-*.tmp")) == []

    _answer_export_prompt(window, qtbot, QMessageBox.StandardButton.Yes)
    assert window.export.message.text() == "Report saved."
    replacement = destination.read_bytes()
    with destination.open(encoding="utf-8", newline="") as stream:
        assert len(list(csv.DictReader(stream))) == 9
    _answer_export_prompt(window, qtbot, QMessageBox.StandardButton.No)
    assert window.export.message.text() == "Export cancelled."
    assert destination.read_bytes() == replacement
    assert list(tmp_path.glob(".netcraft-*.tmp")) == []


def test_cancelling_large_export_preserves_destination(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = _export_window(qtbot, tmp_path)
    with open_history(window.session.path) as history:
        history.connection.execute(
            """WITH RECURSIVE slots(n) AS (VALUES (100) UNION ALL
                    SELECT n + 1 FROM slots WHERE n < 30099)
                INSERT INTO samples (
                    sample_id, run_id, slot, protocol, target, scheduled_at, observed_at, status
                ) SELECT 'large-' || n, 'mixed', n, 'tcp', '127.0.0.1:9000',
                    '2026-11-21T08:00:00.000000+00:00', '2026-11-21T08:00:00.000000+00:00',
                    'success' FROM slots"""
        )
    destination = tmp_path / "existing.csv"
    original = b"fixture sentinel\r\n"
    destination.write_bytes(original)
    monkeypatch.setattr(report_pages, "choose_destination", lambda *_a: (destination, True))
    reached, resume = Event(), Event()
    stream_write = data._CheckedText.write
    writes = 0

    def observe_write(stream: data._CheckedText, row: str) -> object:
        nonlocal writes
        writes += 1
        if writes == 4096:
            reached.set()
            assert resume.wait(5)
        return stream_write(stream, row)

    monkeypatch.setattr(data._CheckedText, "write", observe_write)
    QTest.mouseClick(window.export.save, Qt.MouseButton.LeftButton)
    try:
        qtbot.waitUntil(reached.is_set, timeout=5000)
        assert window.export.stop.isEnabled()
        assert len(list(tmp_path.glob(".netcraft-*.tmp"))) == 1
        assert destination.read_bytes() == original
        QTest.mouseClick(window.export.stop, Qt.MouseButton.LeftButton)
    finally:
        resume.set()
    qtbot.waitUntil(lambda: window.export.message.text() == "Export cancelled.", timeout=5000)
    assert destination.read_bytes() == original
    assert list(tmp_path.glob(".netcraft-*.tmp")) == []
    assert window.session.jobs.pool.waitForDone(1000)
