"""Measure finite loopback monitoring, local storage, and cleanup."""

import argparse
import gc
import json
import platform
import sqlite3
import tracemalloc
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import enumerate as active_threads
from time import perf_counter_ns

from netcraft.diagnostics.lab import echo_server
from netcraft.models import Protocol
from netcraft.monitoring.analytics import Grouping, summarize
from netcraft.monitoring.history import HistoryFilter, open_history
from netcraft.monitoring.probes import Target
from netcraft.monitoring.scheduler import MonitorConfig, monitor
from netcraft.monitoring.telemetry import MonitorProtocol, Sample
from tests.protocol_fixtures import fixed_http, tcp_fixture


def benchmark(slots: int, interval: float) -> dict[str, object]:
    before = set(active_threads())
    with TemporaryDirectory(prefix="netcraft-monitor-") as folder:
        path = Path(folder) / "benchmark.sqlite3"
        with (
            echo_server(Protocol.TCP) as tcp,
            echo_server(Protocol.UDP) as udp,
            tcp_fixture(fixed_http(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n")) as http,
            open_history(path) as history,
        ):
            targets = (
                Target(MonitorProtocol.TCP, tcp.address, tcp.port),
                Target(MonitorProtocol.UDP, udp.address, udp.port),
                Target(MonitorProtocol.HTTP, f"http://{http.address}:{http.port}/"),
            )
            monitor(history, targets, MonitorConfig(interval, 3))
            peak_workers = 0

            def observe(_sample: Sample) -> None:
                nonlocal peak_workers
                live = sum(
                    thread.name.startswith("netcraft-monitor") for thread in active_threads()
                )
                peak_workers = max(peak_workers, live)

            start = perf_counter_ns()
            run = monitor(history, targets, MonitorConfig(interval, slots), on_sample=observe)
            elapsed = perf_counter_ns() - start
            results = list(summarize(history, HistoryFilter(run_id=run.run_id), Grouping.PROTOCOL))
            if any(result.timing_count == 0 for result in results) or len(results) != len(targets):
                raise RuntimeError(
                    "Benchmark did not obtain timing observations for each protocol."
                )
            resource_slots = min(slots, 10)
            tracemalloc.start()
            try:
                gc.collect()
                baseline, _ = tracemalloc.get_traced_memory()
                tracemalloc.reset_peak()
                monitor(history, targets, MonitorConfig(interval, resource_slots))
                gc.collect()
                retained, peak = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()
        db_bytes = path.stat().st_size
    remaining = set(active_threads()) - before
    if remaining:
        raise RuntimeError("Monitoring or fixture workers remained after shutdown.")
    return {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "sqlite": sqlite3.sqlite_version,
        "interval_seconds": interval,
        "slots_per_target": slots,
        "elapsed_ms": elapsed / 1_000_000,
        "workers_limit": 4,
        "peak_live_monitor_workers": peak_workers,
        "remaining_threads": len(remaining),
        "database_bytes_after_three_runs": db_bytes,
        "allocation_run_slots_per_target": resource_slots,
        "python_peak_above_baseline_bytes": peak - baseline,
        "python_retained_delta_bytes": retained - baseline,
        "protocols": [
            {
                "protocol": row.protocol,
                "samples": row.samples,
                "successful_timing_count": row.timing_count,
                "skipped": row.skipped,
                "median_ns": row.median_ns,
                "p95_ns": row.p95_ns,
            }
            for row in results
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=40)
    parser.add_argument("--interval", type=float, default=0.1)
    args = parser.parse_args()
    if not 1 <= args.samples <= 300 or not 0.1 <= args.interval <= 1:
        parser.error("Use 1–300 samples and an interval between 0.1 and 1 second.")
    print(json.dumps(benchmark(args.samples, args.interval), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
