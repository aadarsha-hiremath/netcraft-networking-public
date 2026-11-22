"""Sequential offline DNS and HTTP/TLS timing with ephemeral fixtures."""

import argparse
import json
import math
import platform
import ssl
from pathlib import Path
from statistics import median
from threading import enumerate as active_threads

import dns.message
import dns.rrset

from netcraft.models import ErrorCategory
from netcraft.protocols.dns import inspect_dns
from netcraft.protocols.http import inspect_http
from tests.protocol_fixtures import dns_fixture, fixed_http, server_tls, tcp_fixture

_NAME = "benchmark.example.test."
_REPLY = b"HTTP/1.1 200 OK\r\nContent-Length: 14\r\n\r\nNetCraft local"


def _dns_answer(query: dns.message.QueryMessage, tcp: bool) -> bytes:
    response = dns.message.make_response(query)
    response.answer.append(dns.rrset.from_text(_NAME, 30, "IN", "A", "192.0.2.1"))
    return response.to_wire()


def _summary(samples: list[int]) -> dict[str, int | float]:
    values = sorted(samples)
    return {
        "samples": len(values),
        "median_ms": round(median(values) / 1_000_000, 4),
        "p95_ms": round(values[math.ceil(0.95 * len(values)) - 1] / 1_000_000, 4),
    }


def benchmark(iterations: int) -> dict[str, object]:
    threads_before = set(active_threads())
    server_context, authority = server_tls("127.0.0.1")
    dns_samples: list[int] = []
    http_samples: list[int] = []
    https_samples: list[int] = []
    tcp_samples: list[int] = []
    tls_samples: list[int] = []
    timeouts: list[int] = []
    with (
        authority.cert_pem.tempfile() as ca_pem,
        dns_fixture(_dns_answer) as dns_peer,
        dns_fixture(lambda query, tcp: None) as silent,
        tcp_fixture(fixed_http(_REPLY)) as http_peer,
        tcp_fixture(fixed_http(_REPLY), tls=server_context) as https_peer,
    ):
        http = f"http://127.0.0.1:{http_peer.port}/"
        https = f"https://127.0.0.1:{https_peer.port}/"
        for index in range(iterations + 5):
            dns_result = inspect_dns(_NAME, resolver=dns_peer.address, port=dns_peer.port)
            plain = inspect_http(http)
            secure = inspect_http(https, ca_file=Path(ca_pem))
            if not (dns_result.records and plain.success and secure.success):
                raise RuntimeError((dns_result.error, plain.error, secure.error))
            tcp = plain.timings.tcp_connect_ns
            tls = secure.timings.tls_handshake_ns
            if tcp is None or tls is None:
                raise RuntimeError("Expected successful TCP and TLS timings.")
            if index >= 5:
                dns_samples.append(dns_result.duration_ns)
                tcp_samples.append(tcp)
                http_samples.append(plain.duration_ns)
                https_samples.append(secure.duration_ns)
                tls_samples.append(tls)
        for _ in range(10):
            result = inspect_dns(
                _NAME,
                resolver=silent.address,
                port=silent.port,
                timeout=0.020,
            )
            if result.error is None or result.error.category is not ErrorCategory.TIMEOUT:
                raise RuntimeError("Silent DNS fixture did not time out.")
            timeouts.append(result.duration_ns)
    remaining = set(active_threads()) - threads_before
    if remaining:
        raise RuntimeError("Fixture workers remained after shutdown.")
    return {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "tls_library": ssl.OPENSSL_VERSION,
        "warmups_per_protocol": 5,
        "dns_lookup": _summary(dns_samples),
        "tcp_handshake": _summary(tcp_samples),
        "http_total": _summary(http_samples),
        "https_total": _summary(https_samples),
        "tls_handshake": _summary(tls_samples),
        "dns_timeout": {**_summary(timeouts), "configured_ms": 20},
        "remaining_new_threads": len(remaining),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=100)
    args = parser.parse_args()
    if not 1 <= args.iterations <= 1_000:
        parser.error("Iterations must be 1–1000.")
    print(json.dumps(benchmark(args.iterations), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
