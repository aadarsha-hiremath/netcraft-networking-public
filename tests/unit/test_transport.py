import errno
import select
import socket

import pytest

from netcraft.core import transport
from netcraft.core.execution import CancellationToken
from netcraft.models import ErrorCategory
from netcraft.protocols.tcp import connect_tcp


def test_pending_tcp_connect_deadline_closes_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    class PendingSocket:
        closed = False

        def setblocking(self, blocking: bool) -> None:
            assert not blocking

        def connect_ex(self, address: transport.SocketAddress) -> int:
            assert address == ("127.0.0.1", 9001)
            return errno.EINPROGRESS

        def close(self) -> None:
            self.closed = True

    sock = PendingSocket()

    def expire(
        read: list[object], write: list[object], exceptional: list[object], timeout: float
    ) -> tuple[list[object], list[object], list[object]]:
        assert not read
        assert write == exceptional == [sock]
        assert 0 < timeout <= 1
        return [], [], []

    monkeypatch.setattr(socket, "socket", lambda *args: sock)
    monkeypatch.setattr(select, "select", expire)
    result = connect_tcp("127.0.0.1", 9001, timeout=1)
    assert sock.closed
    assert result.error is not None
    assert result.error.category is ErrorCategory.TIMEOUT
    assert result.error.stage == "connect"


def test_already_cancelled_attempt_does_not_resolve(monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected_lookup(*args: object, **kwargs: object) -> None:
        pytest.fail("Cancelled attempt reached the resolver")

    monkeypatch.setattr(socket, "getaddrinfo", unexpected_lookup)
    with CancellationToken() as token:
        token.cancel()
        token.cancel()
        result = connect_tcp("127.0.0.1", 9001, cancel=token)
        assert result.error is not None
        assert result.error.category is ErrorCategory.CANCELLED
    assert token.wake_socket.fileno() == -1


def test_cancellation_after_system_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    with CancellationToken() as token:

        def resolve(*args: object, **kwargs: object) -> None:
            token.cancel()
            raise socket.gaierror(socket.EAI_NONAME, "lookup completed after cancellation")

        monkeypatch.setattr(socket, "getaddrinfo", resolve)
        result = connect_tcp("netcraft.example", 9001, cancel=token)
    assert result.error is not None
    assert result.error.category is ErrorCategory.CANCELLED
    assert result.error.stage == "resolve"
