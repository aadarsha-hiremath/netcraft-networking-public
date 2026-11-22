"""Keep isolated loopback peers running while the portable executable is tested."""

import argparse
import json
import socket
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from threading import Event, Thread
from time import monotonic

import dns.message
import dns.rrset

from netcraft.diagnostics.lab import echo_server
from netcraft.models import Protocol
from tests.protocol_fixtures import dns_fixture, fixed_http, server_tls, tcp_fixture


def answer(query: dns.message.QueryMessage, _tcp: bool) -> bytes:
    response = dns.message.make_response(query)
    response.answer.append(dns.rrset.from_text(query.question[0].name, 60, "IN", "A", "192.0.2.55"))
    return response.to_wire()


@contextmanager
def silence(marker: Path) -> Iterator[int]:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.settimeout(0.2)
        finished = Event()

        def receive() -> None:
            while not finished.is_set():
                try:
                    sock.recvfrom(65535)
                except TimeoutError:
                    continue
                marker.touch()

        worker = Thread(target=receive, name="portable-silent-udp")
        worker.start()
        try:
            yield sock.getsockname()[1]
        finally:
            finished.set()
            worker.join()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace", type=Path)
    root = parser.parse_args().workspace.resolve()
    root.mkdir(parents=True, exist_ok=False)
    ca_path = root / "fixture-ca.pem"
    tls, ca = server_tls("127.0.0.1")
    ca.cert_pem.write_to_path(ca_path)
    response = fixed_http(
        b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\nSet-Cookie: fixture=yes\r\n\r\n"
    )
    with ExitStack() as peers:
        tcp = peers.enter_context(echo_server(Protocol.TCP))
        udp = peers.enter_context(echo_server(Protocol.UDP))
        dns = peers.enter_context(dns_fixture(answer))
        http = peers.enter_context(tcp_fixture(response))
        https = peers.enter_context(tcp_fixture(response, tls=tls))
        silent = peers.enter_context(silence(root / "silent-received"))
        data = {
            "tcp": tcp.port,
            "udp": udp.port,
            "dns": dns.port,
            "http": http.port,
            "https": https.port,
            "silent_udp": silent,
            "ca": str(ca_path),
        }
        pending = root / "endpoints.tmp"
        pending.write_text(json.dumps(data), encoding="utf-8")
        pending.rename(root / "endpoints.json")
        print(json.dumps(data), flush=True)
        cut = monotonic() + 600
        stop = root / "finished"
        wait = Event()
        while monotonic() < cut and not stop.exists():
            wait.wait(0.2)


if __name__ == "__main__":
    main()
