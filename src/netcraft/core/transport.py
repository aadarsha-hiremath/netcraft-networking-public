"""Socket resolution, readiness, and bounded stream I/O."""

import errno
import select
import socket
from dataclasses import dataclass
from time import perf_counter_ns
from typing import Self, cast

from netcraft.core.config import validate_host
from netcraft.core.errors import NetworkFailure, classify_socket_error
from netcraft.core.execution import CancellationToken
from netcraft.core.operation import socket_stage
from netcraft.diagnostic_models import ConnectAttempt
from netcraft.models import ErrorCategory, Peer

_CONNECT_PENDING = {
    errno.EINPROGRESS,
    errno.EALREADY,
    errno.EWOULDBLOCK,
    errno.EINTR,
    10035,
    10036,
    10037,
}
_MAX_ADDRESS_ATTEMPTS = 8

type SocketAddress = tuple[str, int] | tuple[str, int, int, int]
type ResolvedAddress = tuple[socket.AddressFamily, int, SocketAddress]


@dataclass(frozen=True, slots=True)
class Deadline:
    end_ns: int

    @classmethod
    def after(cls, seconds: float) -> Self:
        return cls(perf_counter_ns() + max(1, int(seconds * 1_000_000_000)))

    def remaining(self, stage: str) -> float:
        seconds = (self.end_ns - perf_counter_ns()) / 1_000_000_000
        if seconds <= 0:
            raise NetworkFailure(ErrorCategory.TIMEOUT, stage, "Socket deadline expired.")
        return seconds

    def capped(self, seconds: float) -> Self:
        return type(self)(min(self.end_ns, self.after(seconds).end_ns))


def wait_ready(
    sock: socket.socket,
    stage: str,
    deadline: Deadline | None,
    cancel: CancellationToken | None,
    *,
    write: bool = False,
    connecting: bool = False,
) -> None:
    if cancel is not None:
        cancel.check(stage)
    remaining = deadline.remaining(stage) if deadline is not None else None
    readers = [] if write else [sock]
    if cancel is not None:
        readers.append(cancel.wake_socket)
    # Winsock reports a failed pending connect as exceptional readiness.
    readable, writable, exceptional = select.select(
        readers,
        [sock] if write else [],
        [sock] if connecting else [],
        remaining,
    )
    if cancel is not None:
        cancel.check(stage)
    if not (readable or writable or exceptional):
        raise NetworkFailure(ErrorCategory.TIMEOUT, stage, "Socket deadline expired.")
    if deadline is not None:
        deadline.remaining(stage)


def peer_from_address(address: SocketAddress) -> Peer:
    return Peer(address[0], address[1], address[3] if len(address) == 4 else 0)


def _associate(
    sock: socket.socket,
    address: SocketAddress,
    deadline: Deadline,
    cancel: CancellationToken | None,
) -> int:
    if cancel is not None:
        cancel.check("connect")
    deadline.remaining("connect")
    start = perf_counter_ns()
    code = sock.connect_ex(address)
    if code in _CONNECT_PENDING:
        wait_ready(sock, "connect", deadline, cancel, write=True, connecting=True)
        code = sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
    if code != 0:
        raise OSError(code, f"Socket connect failed (OS error {code}).")
    if cancel is not None:
        cancel.check("connect")
    return perf_counter_ns() - start


def open_peer(
    host: str, port: int, kind: socket.SocketKind, timeout: float, cancel: CancellationToken | None
) -> tuple[socket.socket, Peer, Deadline, int]:
    """Resolve a target and open its first usable socket. The caller owns the socket."""
    normalized, numeric = validate_host(host)
    addresses = resolve_addresses(normalized, port, kind, numeric, cancel)
    deadline = Deadline.after(timeout)
    sock, peer, connect_ns = connect_addresses(addresses, kind, deadline, cancel)
    return sock, peer, deadline, connect_ns


def resolve_addresses(
    host: str, port: int, kind: socket.SocketKind, numeric: bool, cancel: CancellationToken | None
) -> list[ResolvedAddress]:
    if cancel is not None:
        cancel.check("resolve")
    with socket_stage("resolve"):
        try:
            addresses = socket.getaddrinfo(
                host,
                port,
                socket.AF_UNSPEC,
                kind,
                flags=socket.AI_NUMERICHOST if numeric else 0,
            )
        finally:
            if cancel is not None:
                cancel.check("resolve")
    return [
        (family, protocol, cast(SocketAddress, address))
        for family, _, protocol, _, address in addresses
        if family in (socket.AF_INET, socket.AF_INET6)
    ]


def connect_addresses(
    addresses: list[ResolvedAddress],
    kind: socket.SocketKind,
    deadline: Deadline,
    cancel: CancellationToken | None,
    attempts: list[ConnectAttempt] | None = None,
) -> tuple[socket.socket, Peer, int]:
    attempted: set[tuple[int, SocketAddress]] = set()
    last_error: OSError | None = None
    with socket_stage("connect"):
        for family, protocol, endpoint in addresses:
            if (family, endpoint) in attempted:
                continue
            attempted.add((family, endpoint))
            peer = peer_from_address(endpoint)
            start = perf_counter_ns()
            try:
                sock = socket.socket(family, kind, protocol)
                try:
                    sock.setblocking(False)
                    connect_ns = _associate(sock, endpoint, deadline, cancel)
                except BaseException:
                    sock.close()
                    raise
            except OSError as exc:
                last_error = exc
                if attempts is not None:
                    attempts.append(
                        ConnectAttempt(
                            peer, perf_counter_ns() - start, classify_socket_error(exc, "connect")
                        )
                    )
                if len(attempted) >= _MAX_ADDRESS_ATTEMPTS:
                    break
            except NetworkFailure as exc:
                if attempts is not None:
                    attempts.append(ConnectAttempt(peer, perf_counter_ns() - start, exc.error))
                raise
            else:
                if attempts is not None:
                    attempts.append(ConnectAttempt(peer, perf_counter_ns() - start, None))
                return sock, peer, connect_ns
        if last_error is not None:
            raise last_error
    raise NetworkFailure(ErrorCategory.DNS_FAILURE, "resolve", "No IPv4 or IPv6 addresses found.")


def send_stream(
    sock: socket.socket, payload: bytes, deadline: Deadline, cancel: CancellationToken | None
) -> None:
    remaining = memoryview(payload)
    with socket_stage("send"):
        while remaining:
            wait_ready(sock, "send", deadline, cancel, write=True)
            try:
                written = sock.send(remaining)
            except BlockingIOError:
                continue
            if written == 0:
                raise NetworkFailure(ErrorCategory.REMOTE_CLOSED, "send", "Peer closed the stream.")
            remaining = remaining[written:]


def receive_stream(
    sock: socket.socket, limit: int, deadline: Deadline, cancel: CancellationToken | None
) -> bytes:
    data = bytearray()
    with socket_stage("receive"):
        while True:
            wait_ready(sock, "receive", deadline, cancel)
            try:
                chunk = sock.recv(limit + 1 - len(data))
            except BlockingIOError:
                continue
            if not chunk:
                return bytes(data)
            data.extend(chunk)
            if len(data) > limit:
                raise NetworkFailure(
                    ErrorCategory.UNEXPECTED_RESPONSE, "receive", "Stream exceeded its size limit."
                )
