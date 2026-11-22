"""Drive native Qt widgets and verify files from an extracted portable runtime."""

import argparse
import csv
import ctypes
import json
import socket
import sys
from collections.abc import Callable
from pathlib import Path
from statistics import median
from time import perf_counter_ns
from unittest.mock import patch

from PySide6.QtCore import QEventLoop, QLibraryInfo, QSettings, QTimer
from PySide6.QtWidgets import QApplication, QPushButton

import netcraft
from netcraft.gui import report_pages
from netcraft.gui.diagnostic_data import DiagnosticRequest
from netcraft.gui.widgets import pick
from netcraft.gui.window import ROUTES, StudioWindow
from netcraft.monitoring.export import ExportFormat, Report
from netcraft.monitoring.history import open_history
from netcraft.monitoring.probes import ProbeOptions, Target
from netcraft.monitoring.telemetry import MonitorProtocol, RunStatus, SampleStatus


def until(predicate: Callable[[], bool], description: str, timeout: int = 20_000) -> None:
    if predicate():
        return
    loop, clock, deadline = QEventLoop(), QTimer(), QTimer()
    clock.timeout.connect(lambda: loop.quit() if predicate() else None)
    deadline.setSingleShot(True)
    deadline.timeout.connect(loop.quit)
    clock.start(10)
    deadline.start(timeout)
    loop.exec()
    assert predicate(), description


def click(widget: QPushButton) -> None:
    assert widget.isEnabled(), widget.text()
    widget.click()


def diagnose(window: StudioWindow, requests: tuple[DiagnosticRequest, ...]) -> dict[str, int]:
    click(window.navigation["Diagnostics"])
    diagnostic = window.diagnostics
    timings: dict[str, int] = {}
    for request in requests:
        diagnostic.editor.load(request)
        click(diagnostic.start)
        until(lambda: not window.session.jobs.busy("diagnostic"), "Finish diagnostic")
        result = diagnostic.result
        assert result is not None and result.error is None
        protocol = request.target.protocol
        total = result.timing_ns["total"]
        assert total is not None and total >= 0
        timings[protocol] = total
        if protocol == MonitorProtocol.DNS:
            answers = result.metadata["answers"]
            assert isinstance(answers, list) and answers[0]["data"] == "192.0.2.55"
        if protocol in (MonitorProtocol.HTTP, MonitorProtocol.HTTPS):
            assert result.metadata["http_status"] == 200
            assert "token=" not in diagnostic.details.toPlainText()
            assert "fixture=yes" not in diagnostic.details.toPlainText()
        if protocol == MonitorProtocol.HTTPS:
            tls = result.metadata["tls"]
            assert isinstance(tls, dict) and tls["chain_verified"] and tls["hostname_verified"]
    return timings


def exercise(window: StudioWindow, root: Path, fixture: Path) -> dict[str, object]:
    assert (
        window.overview.snapshot is not None
        and not window.overview.snapshot.recent_runs
        and not window.overview.snapshot.points
        and window.session.targets == 0
        and window.session.monitor_state == "Stopped"
    )
    assert window.grab().save(str(root / "portable-overview.png"))
    for route in ROUTES:
        click(window.navigation[route])
        until(lambda: not window.session.jobs.busy("history"), f"Load {route}")
        assert window.current_page == route and window.pages[route].isVisible()

    peers = json.loads((fixture / "endpoints.json").read_text(encoding="utf-8"))
    tcp = Target(MonitorProtocol.TCP, "127.0.0.1", peers["tcp"])
    options = ProbeOptions(timeout=3)
    requests = (
        DiagnosticRequest(tcp, options),
        DiagnosticRequest(Target(MonitorProtocol.UDP, "127.0.0.1", peers["udp"]), options),
        DiagnosticRequest(
            Target(MonitorProtocol.DNS, "fixture.example.test"),
            ProbeOptions(timeout=3, resolver="127.0.0.1", resolver_port=peers["dns"]),
        ),
        DiagnosticRequest(
            Target(MonitorProtocol.HTTP, f"http://127.0.0.1:{peers['http']}/qa?token=omitted"),
            options,
        ),
        DiagnosticRequest(
            Target(MonitorProtocol.HTTPS, f"https://127.0.0.1:{peers['https']}/qa"),
            ProbeOptions(timeout=3, ca_file=Path(peers["ca"])),
        ),
    )
    timings = diagnose(window, requests)
    assert window.grab().save(str(root / "portable-https.png"))

    click(window.navigation["Live Monitor"])
    live = window.live
    live.set_target(requests[0])
    live.slots.setValue(3)
    live.interval.setValue(1.0)
    click(live.start)
    until(lambda: live.last_run is not None, "Complete finite monitor")
    completed_run = live.last_run
    assert completed_run is not None and completed_run.status is RunStatus.COMPLETED
    completed = completed_run.run_id
    assert live.chart.latency_count == 3
    assert window.grab().save(str(root / "portable-monitor.png"))
    live.table.selectRow(0)
    click(live.remove)
    live.set_target(
        DiagnosticRequest(
            Target(MonitorProtocol.UDP, "127.0.0.1", peers["silent_udp"]),
            ProbeOptions(timeout=10),
        )
    )
    live.slots.setValue(4)
    live.interval.setValue(3)
    click(live.start)
    until(lambda: (fixture / "silent-received").is_file(), "Silent peer saw active probe")
    click(live.stop)
    until(lambda: not window.session.jobs.busy("monitor"), "Cancel active monitor")
    cancelled_run = live.last_run
    assert cancelled_run is not None and cancelled_run.status is RunStatus.CANCELLED
    assert cancelled_run.run_id != completed

    click(window.navigation["History"])
    until(lambda: len(window.history.table.rows.rows) == 2, "List saved monitor runs")
    run = window.history.open_run(completed)
    until(lambda: len(run.table.rows.rows) == 3, "Show the completed run")
    run.close()
    click(window.navigation["Analytics"])
    until(lambda: not window.session.jobs.busy("history"), "Compute latency and reliability")
    assert window.analytics.snapshot is not None
    assert window.analytics.snapshot.overall.successes == 3
    assert window.analytics.snapshot.overall.cancelled >= 1
    assert window.grab().save(str(root / "portable-analytics.png"))

    with open_history(window.session.path) as history:
        rows = tuple(history.samples())
        assert history.connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert history.connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert sum(row.observation.status is SampleStatus.SUCCESS for row in rows) == 3
    assert any(row.observation.status is SampleStatus.CANCELLED for row in rows)
    assert all(row.observation.status is not SampleStatus.PENDING for row in rows)
    click(window.navigation["Export"])
    pick(window.export.report, Report.SAMPLES)
    sizes: dict[str, int] = {}
    for format_name in (ExportFormat.CSV, ExportFormat.JSON):
        destination = root / f"samples.{format_name}"
        pick(window.export.format, format_name)
        with patch.object(report_pages, "choose_destination", return_value=(destination, False)):
            click(window.export.save)
            until(lambda: not window.session.jobs.busy("file"), f"Write {format_name}")
        assert window.export.message.text() == "Report saved."
        sizes[format_name] = destination.stat().st_size
        if format_name == ExportFormat.CSV:
            with destination.open(newline="", encoding="utf-8") as stream:
                assert [r["sample_id"] for r in csv.DictReader(stream)] == [
                    r.sample_id for r in rows
                ]
        else:
            assert json.loads(destination.read_text(encoding="utf-8"))["rows"] == [
                row.to_dict() for row in rows
            ]
    click(window.navigation["Settings"])
    window.settings.timeout.setValue(4.25)
    click(window.settings.save)
    assert window.session.preferences.timeout == 4.25
    metadata = {"run_ids": [completed, cancelled_run.run_id], "samples": len(rows)}
    (root / "expected.json").write_text(json.dumps(metadata), encoding="utf-8")
    return {"diagnostic_total_ns": timings, "csv_json_bytes": sizes, **metadata}


def reopen(window: StudioWindow, root: Path) -> dict[str, object]:
    expected = json.loads((root / "expected.json").read_text(encoding="utf-8"))
    assert window.session.preferences.timeout == 4.25 and window.session.targets == 0
    assert window.session.monitor_state == "Stopped" and window.overview.snapshot is not None
    assert len(window.overview.snapshot.points) == 3
    assert sorted(r.run_id for r in window.overview.snapshot.recent_runs) == sorted(
        expected["run_ids"]
    )
    with open_history(window.session.path) as history, history.exclusive():
        history.connection.execute("BEGIN IMMEDIATE")
        assert len(tuple(history.samples())) == expected["samples"]
        assert history.connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert history.connection.execute("PRAGMA foreign_key_check").fetchall() == []
        history.connection.execute("ROLLBACK")
    return {"restored_timeout": 4.25, "restored_runs": 2, "restored_samples": expected["samples"]}


class _MemoryCounters(ctypes.Structure):
    _fields_ = [("size", ctypes.c_ulong), ("faults", ctypes.c_ulong)] + [
        (name, ctypes.c_size_t)
        for name in (
            "peak_working_set",
            "working_set",
            "peak_paged_pool",
            "paged_pool",
            "peak_nonpaged_pool",
            "nonpaged_pool",
            "pagefile",
            "peak_pagefile",
        )
    ]


def working_set() -> int:
    counters = _MemoryCounters()
    counters.size = ctypes.sizeof(counters)
    if not ctypes.windll.psapi.GetProcessMemoryInfo(
        ctypes.c_void_p(-1), ctypes.byref(counters), counters.size
    ):
        raise ctypes.WinError()
    return int(counters.working_set)


def stress(window: StudioWindow, fixture: Path) -> dict[str, object]:
    peers = json.loads((fixture / "endpoints.json").read_text(encoding="utf-8"))
    click(window.navigation["Live Monitor"])
    live = window.live
    for target in (
        Target(MonitorProtocol.TCP, "127.0.0.1", peers["tcp"]),
        Target(MonitorProtocol.UDP, "127.0.0.1", peers["udp"]),
        Target(MonitorProtocol.HTTP, f"http://127.0.0.1:{peers['http']}/"),
    ):
        live.set_target(DiagnosticRequest(target, ProbeOptions(timeout=3)))
    live.interval.setValue(0.5)
    live.slots.setValue(60)
    resident = [working_set()]
    gaps: list[int] = []
    previous = perf_counter_ns()
    timer = QTimer()

    def tick() -> None:
        nonlocal previous
        now = perf_counter_ns()
        gaps.append(now - previous)
        previous = now
        resident.append(working_set())

    timer.timeout.connect(tick)
    start = perf_counter_ns()
    timer.start(20)
    try:
        click(live.start)
        for route in ("History", "Overview", "Analytics", "Live Monitor"):
            click(window.navigation[route])
            until(lambda: not window.session.jobs.busy("history"), f"Refresh {route}")
        until(lambda: live.last_run is not None, "Complete packaged stress run", 55_000)
    finally:
        timer.stop()
    elapsed_ms = (perf_counter_ns() - start) / 1_000_000
    resident.append(working_set())
    with open_history(window.session.path) as history, history.exclusive():
        samples = tuple(history.samples())
        assert len(samples) == 180
        assert all(sample.observation.status is not SampleStatus.PENDING for sample in samples)
        assert history.connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    return {
        "samples": len(samples),
        "successes": sum(s.observation.status is SampleStatus.SUCCESS for s in samples),
        "skips": sum(s.observation.status is SampleStatus.SKIPPED for s in samples),
        "elapsed_ms": elapsed_ms,
        "rendered_points": live.chart.rendered_count,
        "heartbeat_count": len(gaps),
        "heartbeat_median_ms": median(gaps) / 1_000_000,
        "heartbeat_max_ms": max(gaps) / 1_000_000,
        "resident_start_bytes": resident[0],
        "resident_sample_peak_bytes": max(resident),
        "resident_end_bytes": resident[-1],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("exercise", "reopen", "stress"))
    parser.add_argument("workspace", type=Path)
    parser.add_argument("fixture", type=Path)
    args = parser.parse_args()
    bundle = Path(sys.executable).resolve().parent.parent
    root = args.workspace.resolve()
    assert not root.is_relative_to(bundle)
    assert Path(netcraft.__file__).resolve().is_relative_to(bundle / "app")
    if args.mode != "reopen":
        root.mkdir(parents=True, exist_ok=False)
    path, prefs = root / "history.sqlite3", root / "preferences.ini"
    app = QApplication(["netcraft-portable-qa"])
    assert app.platformName() == "windows"
    plugins = Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath)).resolve()
    assert plugins.is_relative_to(bundle / "app") and (plugins / "platforms/qwindows.dll").is_file()
    with patch.object(
        socket, "getaddrinfo", side_effect=AssertionError("Unexpected startup lookup")
    ):
        window = StudioWindow(path, QSettings(str(prefs), QSettings.Format.IniFormat))
        window.resize(1366, 768)
        window.show()
        until(lambda: window.overview.snapshot is not None, "Load Overview")
        assert not window.session.jobs.busy("diagnostic")
        assert not window.session.jobs.busy("monitor")
    try:
        if args.mode == "exercise":
            result = exercise(window, root, args.fixture)
        elif args.mode == "reopen":
            result = reopen(window, root)
        else:
            result = stress(window, args.fixture)
    finally:
        window.close()
        until(lambda: not window.session.jobs.active and not window.isVisible(), "Close studio")
        assert window.session.jobs.pool.waitForDone(1000)
    print(json.dumps({"qt_backend": app.platformName(), "routes": ROUTES, **result}), flush=True)


if __name__ == "__main__":
    main()
