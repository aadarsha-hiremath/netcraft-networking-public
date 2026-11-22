"""Bounded, EOF-framed TCP echo measurement for the loopback laboratory."""

import socket
from time import perf_counter_ns

from netcraft.core.config import (
    DEFAULT_TIMEOUT,
    MAX_ECHO_PAYLOAD,
    require_loopback,
    validate_payload,
)
from netcraft.core.errors import NetworkFailure
from netcraft.core.execution import CancellationToken
from netcraft.core.operation import measure, socket_stage
from netcraft.core.transport import open_peer, receive_stream, send_stream
from netcraft.models import Details, ErrorCategory, NetworkResult, Operation, Protocol


def echo_tcp(
    host: str,
    port: int,
    payload: bytes,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    cancel: CancellationToken | None = None,
) -> NetworkResult:
    """Send a loopback echo request, half-close, and verify the echoed bytes."""

    def exchange() -> Details:
        require_loopback(host)
        validate_payload(payload, MAX_ECHO_PAYLOAD)
        if not payload:
            raise NetworkFailure(
                ErrorCategory.INVALID_PAYLOAD, "validation", "TCP echo requires a nonempty payload."
            )
        sock, peer, deadline, connect_ns = open_peer(
            host, port, socket.SOCK_STREAM, timeout, cancel
        )
        with sock:
            start = perf_counter_ns()
            send_stream(sock, payload, deadline, cancel)
            with socket_stage("send"):
                sock.shutdown(socket.SHUT_WR)
            response = receive_stream(sock, len(payload), deadline, cancel)
            if len(response) < len(payload):
                raise NetworkFailure(
                    ErrorCategory.REMOTE_CLOSED, "receive", "Peer closed before the full echo."
                )
            if response != payload:
                raise NetworkFailure(
                    ErrorCategory.UNEXPECTED_RESPONSE, "receive", "Echo differed from the request."
                )
            return Details(
                peer,
                connect_ns=connect_ns,
                exchange_ns=perf_counter_ns() - start,
                bytes_sent=len(payload),
                bytes_received=len(response),
                response=response,
            )

    return measure(Operation.TCP_ECHO, Protocol.TCP, host, port, timeout, cancel, exchange)
