"""Local protocol peers shared by deterministic tests and benchmarks."""

import errno
import socket
import ssl
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import dns.message
import trustme

from netcraft.core.errors import NetworkFailure
from netcraft.core.execution import CancellationToken
from netcraft.core.transport import peer_from_address, wait_ready
from netcraft.models import ErrorCategory, Peer

type TCPHandler = Callable[[socket.socket], None]
type DNSHandler = Callable[[dns.message.QueryMessage, bool], bytes | None]


def server_tls(*identities: str) -> tuple[ssl.SSLContext, trustme.CA]:
    authority = trustme.CA()
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    authority.issue_cert(*identities).configure_cert(context)
    if ssl.HAS_ALPN:
        context.set_alpn_protocols(["http/1.1"])
    return context, authority


@contextmanager
def _serve(serve: Callable[[CancellationToken], None]) -> Iterator[None]:
    with CancellationToken() as stop, ThreadPoolExecutor(max_workers=1) as executor:
        worker = executor.submit(serve, stop)
        try:
            yield
        finally:
            stop.cancel()
            try:
                worker.result(timeout=3)
            except NetworkFailure as exc:
                if exc.error.category is not ErrorCategory.CANCELLED:
                    raise


@contextmanager
def tcp_fixture(
    handler: TCPHandler,
    *,
    host: str = "127.0.0.1",
    tls: ssl.SSLContext | None = None,
) -> Iterator[Peer]:
    assert host in {"127.0.0.1", "::1"}
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family) as listener:
        listener.bind((host, 0))
        listener.listen(8)
        listener.setblocking(False)

        def serve(stop: CancellationToken) -> None:
            while True:
                wait_ready(listener, "fixture", None, stop)
                conn, _ = listener.accept()
                with conn:
                    conn.settimeout(1.5)
                    try:
                        if tls is None:
                            handler(conn)
                        else:
                            with tls.wrap_socket(conn, server_side=True) as secured:
                                handler(secured)
                    except (ConnectionError, TimeoutError, ssl.SSLError):
                        continue

        with _serve(serve):
            yield peer_from_address(listener.getsockname())


def http_request(conn: socket.socket) -> bytes:
    with conn.makefile("rb") as request:
        data = bytearray()
        while len(data) < 16_384:
            line = request.readline(8193)
            if not line or line == b"\r\n":
                return bytes(data)
            data.extend(line)
    raise ValueError("Test request exceeded its size limit.")


def fixed_http(reply: bytes) -> TCPHandler:
    def handler(conn: socket.socket) -> None:
        if http_request(conn):
            conn.sendall(reply)

    return handler


@contextmanager
def dns_fixture(handler: DNSHandler, *, host: str = "127.0.0.1") -> Iterator[Peer]:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET

    def respond(wire: bytes, tcp: bool) -> bytes | None:
        query = dns.message.from_wire(wire)
        assert isinstance(query, dns.message.QueryMessage)
        return handler(query, tcp)

    def tcp(conn: socket.socket) -> None:
        with conn.makefile("rb") as stream:
            prefix = stream.read(2)
            if len(prefix) != 2:
                return
            wire = stream.read(int.from_bytes(prefix, "big"))
            response = respond(wire, True)
            if response is not None:
                conn.sendall(len(response).to_bytes(2, "big") + response)

    # TCP and UDP have separate ephemeral allocations. A free TCP port can be busy in UDP.
    for _ in range(16):
        with (
            tcp_fixture(tcp, host=host) as peer,
            socket.socket(family, socket.SOCK_DGRAM) as listener,
        ):
            try:
                listener.bind((peer.address, peer.port))
            except OSError as exc:
                if exc.errno == errno.EADDRINUSE or getattr(exc, "winerror", None) == 10048:
                    continue
                raise
            listener.setblocking(False)

            def serve(stop: CancellationToken, udp_listener: socket.socket = listener) -> None:
                while True:
                    wait_ready(udp_listener, "fixture", None, stop)
                    wire, client = udp_listener.recvfrom(65_535)
                    reply = respond(wire, False)
                    if reply is not None:
                        udp_listener.sendto(reply, client)

            with _serve(serve):
                yield peer
            return
    raise OSError(errno.EADDRINUSE, "Could not reserve a paired TCP/UDP test port.")
