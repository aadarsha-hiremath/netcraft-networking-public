"""Drive the running native workspace against local fixtures and capture authentic views."""

import argparse
import gc
import json
import platform
import sys
import tracemalloc
from collections.abc import Callable
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory
from threading import enumerate as active_threads
from time import monotonic, perf_counter_ns

from PySide6.QtCore import QSettings, QTimer, qVersion
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from netcraft.diagnostics.lab import echo_server
from netcraft.gui.diagnostic_data import DiagnosticRequest
from netcraft.gui.window import ROUTES, StudioWindow
from netcraft.models import Protocol
from netcraft.monitoring.analytics import Grouping, summarize
from netcraft.monitoring.history import HistoryFilter, open_history
from netcraft.monitoring.probes import ProbeOptions, Target
from netcraft.monitoring.telemetry import MonitorProtocol
from tests.protocol_fixtures import fixed_http, tcp_fixture


def _until(predicate: Callable[[], bool], seconds: float = 12) -> None:
    end = monotonic() + seconds
    while not predicate():
        if monotonic() >= end:
            raise RuntimeError("Desktop acceptance operation timed out.")
        QTest.qWait(15)


def _capture(window: StudioWindow, folder: Path, name: str, width: int, height: int) -> None:
    window.resize(width, height)
    QTest.qWait(140)
    if (window.width(), window.height()) != (width, height):
        raise RuntimeError(
            f"Unexpected client geometry for {name}: {window.width()}x{window.height()}"
        )
    if not window.grab().save(str(folder / f"{name}.png")):
        raise RuntimeError(f"Could not capture {name}.")


def benchmark(
    app: QApplication, samples: int, interval: float, captures: Path | None, hold: int
) -> None:
    before = set(active_threads())
    with TemporaryDirectory(prefix="netcraft-desktop-") as temp:
        folder = Path(temp)
        history_path = folder / "history.sqlite3"
        settings = QSettings(str(folder / "preferences.ini"), QSettings.Format.IniFormat)
        window = StudioWindow(history_path, settings)
        window.show()
        _until(lambda: window.overview.snapshot is not None)
        if captures is not None:
            captures.mkdir(parents=True, exist_ok=True)
            _capture(window, captures, "overview", 1600, 900)
        with (
            echo_server(Protocol.TCP) as tcp,
            echo_server(Protocol.UDP) as udp,
            tcp_fixture(fixed_http(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n")) as web,
        ):
            window.show_page("Diagnostics")
            window.diagnostics.editor.host.setText(tcp.address)
            window.diagnostics.editor.port.setText(str(tcp.port))
            window.diagnostics.start.click()
            _until(lambda: window.diagnostics.result is not None)
            if captures is not None:
                _capture(window, captures, "diagnostics", 1366, 768)
            window.show_page("Live Monitor")
            for target in (
                Target(MonitorProtocol.TCP, tcp.address, tcp.port),
                Target(MonitorProtocol.UDP, udp.address, udp.port),
                Target(MonitorProtocol.HTTP, f"http://{web.address}:{web.port}/health"),
            ):
                window.live.set_target(DiagnosticRequest(target, ProbeOptions(timeout=2)))
            window.live.slots.setValue(samples)
            window.live.interval.setValue(interval)
            if captures is not None:
                window.resize(1280, 720)

            heartbeat: list[int] = []
            previous = perf_counter_ns()

            def tick() -> None:
                nonlocal previous
                current = perf_counter_ns()
                heartbeat.append(current - previous)
                previous = current

            timer = QTimer()
            timer.setInterval(20)
            timer.timeout.connect(tick)
            started = perf_counter_ns()
            previous = started
            timer.start()
            try:
                window.live.start.click()
                _until(lambda: window.live.last_run is not None, samples * interval + 8)
                elapsed = perf_counter_ns() - started
            finally:
                timer.stop()
            assert window.live.last_run is not None
            rendered = window.live.chart.rendered_count
            if captures is not None:
                _capture(window, captures, "live-monitor", 1280, 720)
            run_id = window.live.last_run.run_id
            with open_history(history_path) as history:
                totals = next(summarize(history, HistoryFilter(run_id=run_id)))
                protocols = tuple(
                    summarize(history, HistoryFilter(run_id=run_id), Grouping.PROTOCOL)
                )
            if totals.samples != samples * 3:
                raise RuntimeError("Telemetry did not account for all scheduled slots.")
            if captures is not None:
                for route, width, height in (
                    ("History", 1366, 768),
                    ("Analytics", 1600, 900),
                    ("Export", 1280, 720),
                    ("Settings", 1280, 720),
                ):
                    window.show_page(route)
                    _until(lambda: not window.session.jobs.busy("history"))
                    _capture(window, captures, route.lower(), width, height)
                window.show_page("Analytics")
                _until(lambda: window.analytics.snapshot is not None)
            window.show_page("Live Monitor")
            window.live.slots.setValue(10)
            tracemalloc.start()
            try:
                gc.collect()
                baseline, _ = tracemalloc.get_traced_memory()
                tracemalloc.reset_peak()
                window.live.start.click()
                _until(
                    lambda: (
                        window.live.last_run is not None and window.live.last_run.run_id != run_id
                    ),
                    10 * interval + 8,
                )
                gc.collect()
                retained, peak = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()
            window.show_page("Analytics")
            _until(lambda: not window.session.jobs.busy("history"))
            if hold:
                QTimer.singleShot(hold * 1000, window.close)
                app.exec()

        if window.isVisible():
            window.close()
        _until(lambda: not window.session.jobs.active)
        qt_joined = window.session.jobs.pool.waitForDone(1000)
        remaining = sum(not thread.daemon for thread in set(active_threads()) - before)
        print(
            json.dumps(
                {
                    "os": platform.platform(),
                    "qt": qVersion(),
                    "qpa": app.platformName(),
                    "device_pixel_ratio": window.devicePixelRatioF(),
                    "interval_seconds": interval,
                    "slots_per_target": samples,
                    "elapsed_ms": round(elapsed / 1_000_000, 3),
                    "successes": totals.successes,
                    "skips": totals.skipped,
                    "attempts": totals.attempts,
                    "rendered_points": rendered,
                    "heartbeat_count": len(heartbeat),
                    "median_heartbeat_ms": round(median(heartbeat) / 1_000_000, 3)
                    if heartbeat
                    else None,
                    "max_heartbeat_ms": round(max(heartbeat) / 1_000_000, 3) if heartbeat else None,
                    "python_peak_bytes_above_baseline": peak - baseline,
                    "python_retained_bytes_delta": retained - baseline,
                    "allocation_slots_per_target": 10,
                    "remaining_engine_and_fixture_threads": remaining,
                    "qt_pool_joined": qt_joined,
                    "protocols": [
                        {"protocol": row.protocol, "median_ns": row.median_ns, "p95_ns": row.p95_ns}
                        for row in protocols
                    ],
                    "pages": ROUTES,
                },
                indent=2,
            ),
            flush=True,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=40)
    parser.add_argument("--interval", type=float, default=0.12)
    parser.add_argument("--captures", type=Path)
    parser.add_argument("--hold-seconds", type=int, default=0)
    args = parser.parse_args()
    if not 1 <= args.samples <= 300 or not 0.1 <= args.interval <= 1:
        parser.error("Use between 1 and 300 samples and a 0.1 to 1 second interval.")
    if not 0 <= args.hold_seconds <= 600:
        parser.error("Hold duration must be zero to 600 seconds.")
    app = QApplication(sys.argv[:1])
    benchmark(app, args.samples, args.interval, args.captures, args.hold_seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
