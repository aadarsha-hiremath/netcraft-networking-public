"""Send one UDP datagram, with an optional reply from the selected peer."""

import socket
from time import perf_counter_ns

from netcraft.core.config import DEFAULT_TIMEOUT, MAX_DATAGRAM, validate_payload
from netcraft.core.errors import NetworkFailure
from netcraft.core.execution import CancellationToken
from netcraft.core.operation import measure, socket_stage
from netcraft.core.transport import Deadline, open_peer, wait_ready
from netcraft.models import Details, ErrorCategory, NetworkResult, Operation, Protocol


def send_datagram(
    host: str,
    port: int,
    payload: bytes,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    cancel: CancellationToken | None = None,
) -> NetworkResult:
    """Report whether the local socket accepted one outgoing datagram."""
    return _datagram(host, port, payload, timeout, cancel, Operation.UDP_SEND)


def exchange_datagram(
    host: str,
    port: int,
    payload: bytes,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    cancel: CancellationToken | None = None,
) -> NetworkResult:
    """Send one datagram and receive a single reply from that peer."""
    return _datagram(host, port, payload, timeout, cancel, Operation.UDP_EXCHANGE)


def _datagram(
    host: str,
    port: int,
    payload: bytes,
    timeout: float,
    cancel: CancellationToken | None,
    operation: Operation,
) -> NetworkResult:
    def communicate() -> Details:
        validate_payload(payload, MAX_DATAGRAM)
        sock, peer, deadline, _ = open_peer(host, port, socket.SOCK_DGRAM, timeout, cancel)
        with sock:
            start = perf_counter_ns()
            written = _send(sock, payload, deadline, cancel)
            if operation is Operation.UDP_SEND:
                return Details(peer, bytes_sent=written)
            response = _receive(sock, deadline, cancel)
            return Details(
                peer,
                exchange_ns=perf_counter_ns() - start,
                bytes_sent=written,
                bytes_received=len(response),
                response=response,
            )

    return measure(operation, Protocol.UDP, host, port, timeout, cancel, communicate)


def _send(
    sock: socket.socket, payload: bytes, deadline: Deadline, cancel: CancellationToken | None
) -> int:
    with socket_stage("send"):
        while True:
            wait_ready(sock, "send", deadline, cancel, write=True)
            try:
                written = sock.send(payload)
            except BlockingIOError:
                continue
            if written != len(payload):
                raise NetworkFailure(ErrorCategory.SOCKET_ERROR, "send", "Incomplete UDP send.")
            return written


def _receive(sock: socket.socket, deadline: Deadline, cancel: CancellationToken | None) -> bytes:
    with socket_stage("receive"):
        while True:
            wait_ready(sock, "receive", deadline, cancel)
            try:
                # Read an entire ordinary IPv4 or IPv6 datagram before applying the size limit.
                response = sock.recv(65_535)
            except BlockingIOError:
                continue
            except ConnectionResetError as exc:
                # Winsock reports an ICMP port-unreachable response as WSAECONNRESET.
                if getattr(exc, "winerror", None) == 10054:
                    raise NetworkFailure(
                        ErrorCategory.CONNECTION_REFUSED,
                        "receive",
                        "UDP peer port unreachable.",
                        10054,
                    ) from exc
                raise
            if len(response) > MAX_DATAGRAM:
                raise NetworkFailure(
                    ErrorCategory.UNEXPECTED_RESPONSE,
                    "receive",
                    "Datagram exceeded its size limit.",
                )
            return response
