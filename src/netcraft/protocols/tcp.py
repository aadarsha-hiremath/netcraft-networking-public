"""TCP connection-establishment diagnostics."""

import socket

from netcraft.core.config import DEFAULT_TIMEOUT
from netcraft.core.execution import CancellationToken
from netcraft.core.operation import measure
from netcraft.core.transport import open_peer
from netcraft.models import Details, NetworkResult, Operation, Protocol


def connect_tcp(
    host: str,
    port: int,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    cancel: CancellationToken | None = None,
) -> NetworkResult:
    """Attempt a TCP handshake and close the socket without sending application data."""

    def connect() -> Details:
        sock, peer, _, connect_ns = open_peer(host, port, socket.SOCK_STREAM, timeout, cancel)
        with sock:
            return Details(peer, connect_ns=connect_ns)

    return measure(Operation.TCP_CONNECT, Protocol.TCP, host, port, timeout, cancel, connect)
