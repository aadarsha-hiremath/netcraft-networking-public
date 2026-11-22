"""Sequential localhost timings and a separate Python allocation smoke check."""

import argparse
import gc
import json
import math
import platform
import tracemalloc
from collections.abc import Callable
from statistics import median
from threading import enumerate as active_threads

from netcraft.diagnostics.lab import echo_server
from netcraft.models import ErrorCategory, Protocol
from netcraft.protocols.tcp import connect_tcp
from netcraft.protocols.udp import exchange_datagram

_PAYLOAD = b"NetCraft loopback"
_TIMEOUT_SECONDS = 0.020
_TIMEOUT_TRIALS = 10


def _summary(run: Callable[[], int], count: int) -> dict[str, int | float]:
    samples = sorted(run() for _ in range(count))
    return {
        "samples": count,
        "median_ms": round(median(samples) / 1_000_000, 4),
        "p95_ms": round(samples[math.ceil(0.95 * count) - 1] / 1_000_000, 4),
    }


def _allocations(
    run_tcp: Callable[[], int], run_udp: Callable[[], int], count: int
) -> dict[str, int]:
    tracemalloc.start()
    try:
        for _ in range(20):
            run_tcp()
            run_udp()
        gc.collect()
        baseline, _ = tracemalloc.get_traced_memory()
        tracemalloc.reset_peak()
        for _ in range(count):
            run_tcp()
            run_udp()
        gc.collect()
        after, peak = tracemalloc.get_traced_memory()
        return {
            "operations_per_protocol": count,
            "retained_delta_bytes": after - baseline,
            "peak_above_baseline_bytes": peak - baseline,
        }
    finally:
        tracemalloc.stop()


def benchmark(count: int) -> dict[str, object]:
    threads_before = set(active_threads())
    with (
        echo_server(Protocol.TCP) as tcp_peer,
        echo_server(Protocol.UDP) as udp_peer,
        echo_server(Protocol.UDP, reply=False) as silent_peer,
    ):

        def tcp() -> int:
            result = connect_tcp(tcp_peer.address, tcp_peer.port)
            if result.details is None or result.details.connect_ns is None:
                raise RuntimeError(f"TCP connect failed: {result.error}")
            return result.details.connect_ns

        def udp() -> int:
            result = exchange_datagram(udp_peer.address, udp_peer.port, _PAYLOAD)
            details = result.details
            if details is None or details.exchange_ns is None or details.response != _PAYLOAD:
                raise RuntimeError(f"UDP echo failed: {result.error}")
            return details.exchange_ns

        def timeout() -> int:
            result = exchange_datagram(
                silent_peer.address,
                silent_peer.port,
                _PAYLOAD,
                timeout=_TIMEOUT_SECONDS,
            )
            if (
                result.error is None
                or result.error.category is not ErrorCategory.TIMEOUT
                or result.error.stage != "receive"
            ):
                raise RuntimeError("The silent fixture did not cause a receive timeout.")
            return result.duration_ns

        for _ in range(20):
            tcp()
            udp()
        results: dict[str, object] = {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "payload_bytes": len(_PAYLOAD),
            "tcp_connect": _summary(tcp, count),
            "udp_exchange": _summary(udp, count),
            "udp_timeout": {
                **_summary(timeout, _TIMEOUT_TRIALS),
                "configured_ms": _TIMEOUT_SECONDS * 1000,
            },
            "python_allocations": _allocations(tcp, udp, count),
        }
    new_threads = set(active_threads()) - threads_before
    if new_threads:
        raise RuntimeError(f"Workers remained after fixture shutdown: {new_threads}")
    results["remaining_new_threads"] = len(new_threads)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=250)
    args = parser.parse_args()
    if not 1 <= args.iterations <= 2_000:
        parser.error("Iterations must be 1–2000.")
    print(json.dumps(benchmark(args.iterations), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
