"""Ephemeral, loopback-only TCP and UDP servers for experiments and tests."""

import socket
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

from netcraft.core.config import MAX_ECHO_PAYLOAD, require_loopback
from netcraft.core.errors import NetworkFailure
from netcraft.core.execution import CancellationToken
from netcraft.core.transport import (
    Deadline,
    peer_from_address,
    receive_stream,
    send_stream,
    wait_ready,
)
from netcraft.models import ErrorCategory, Peer, Protocol

_CLIENT_TIMEOUT = 2.0


@contextmanager
def echo_server(
    protocol: Protocol, *, host: str = "127.0.0.1", reply: bool = True
) -> Iterator[Peer]:
    """Bind an OS-assigned port and join the server thread on context exit."""
    require_loopback(host)
    family = socket.AF_INET6 if host == "::1" else socket.AF_INET
    kind = socket.SOCK_STREAM if protocol is Protocol.TCP else socket.SOCK_DGRAM
    with (
        socket.socket(family, kind) as listener,
        CancellationToken() as stop,
        ThreadPoolExecutor(max_workers=1, thread_name_prefix="netcraft-echo") as executor,
    ):
        if family == socket.AF_INET6:
            listener.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
        if protocol is Protocol.TCP and exclusive is not None:
            listener.setsockopt(socket.SOL_SOCKET, exclusive, 1)
        listener.bind((host, 0))
        listener.setblocking(False)
        if protocol is Protocol.TCP:
            listener.listen(8)
        worker = executor.submit(_serve, listener, protocol, reply, stop)
        try:
            yield peer_from_address(listener.getsockname())
        finally:
            stop.cancel()
            worker.result()


def _serve(sock: socket.socket, protocol: Protocol, reply: bool, stop: CancellationToken) -> None:
    try:
        while True:
            wait_ready(sock, "listen", None, stop)
            try:
                if protocol is Protocol.TCP:
                    _tcp_client(sock, reply, stop)
                else:
                    data, address = sock.recvfrom(65_535)
                    if reply and len(data) <= MAX_ECHO_PAYLOAD:
                        sock.sendto(data, address)
            except (BlockingIOError, ConnectionError):
                # The listener remains usable when a peer disappears.
                continue
    except NetworkFailure as exc:
        if exc.error.category is not ErrorCategory.CANCELLED:
            raise


def _tcp_client(listener: socket.socket, reply: bool, stop: CancellationToken) -> None:
    conn, _ = listener.accept()
    with conn:
        conn.setblocking(False)
        deadline = Deadline.after(_CLIENT_TIMEOUT)
        try:
            data = receive_stream(conn, MAX_ECHO_PAYLOAD, deadline, stop)
            if reply:
                send_stream(conn, data, deadline, stop)
            else:
                stop.wait(deadline.remaining("receive"))
        except NetworkFailure as exc:
            if exc.error.category not in {
                ErrorCategory.TIMEOUT,
                ErrorCategory.REMOTE_CLOSED,
                ErrorCategory.UNEXPECTED_RESPONSE,
            }:
                raise
