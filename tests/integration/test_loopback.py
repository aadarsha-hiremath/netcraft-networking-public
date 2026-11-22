import errno
import socket
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from threading import enumerate as active_threads

import pytest

from netcraft.core.config import MAX_ECHO_PAYLOAD
from netcraft.core.errors import NetworkFailure
from netcraft.core.execution import CancellationToken, DiagnosticRunner
from netcraft.diagnostics.echo import echo_tcp
from netcraft.diagnostics.lab import echo_server
from netcraft.models import ErrorCategory, NetworkResult, Protocol
from netcraft.protocols.tcp import connect_tcp
from netcraft.protocols.udp import exchange_datagram, send_datagram


@pytest.fixture(params=["127.0.0.1", "::1"], ids=["ipv4", "ipv6"])
def loopback(request: pytest.FixtureRequest) -> str:
    host = str(request.param)
    if host == "::1":
        if not socket.has_ipv6:
            pytest.skip("Python has no IPv6 socket support")
        try:
            with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as probe:
                probe.bind((host, 0))
        except OSError as exc:
            if exc.errno in {errno.EAFNOSUPPORT, errno.EPROTONOSUPPORT, errno.EADDRNOTAVAIL} or (
                getattr(exc, "winerror", None) in {10043, 10047, 10049}
            ):
                pytest.skip(f"IPv6 loopback unavailable: {exc}")
            raise
    return host


def test_tcp_connect(loopback: str) -> None:
    with echo_server(Protocol.TCP, host=loopback) as peer:
        result = connect_tcp(loopback, peer.port)
    assert result.success, result.error
    assert result.details is not None
    assert result.details.peer == peer
    assert result.details.connect_ns is not None
    assert 0 <= result.details.connect_ns <= result.duration_ns
    assert result.details.exchange_ns is None
    assert result.details.bytes_sent is None


def test_tcp_refusal(loopback: str) -> None:
    family = socket.AF_INET6 if loopback == "::1" else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as reserved:
        reserved.bind((loopback, 0))
        result = connect_tcp(loopback, reserved.getsockname()[1], timeout=5)
    assert result.error is not None
    assert result.error.category is ErrorCategory.CONNECTION_REFUSED
    assert result.error.stage == "connect"
    assert result.details is None


def test_hostname_falls_back_to_next_resolved_address(monkeypatch: pytest.MonkeyPatch) -> None:
    with (
        socket.socket() as refused,
        echo_server(Protocol.TCP) as peer,
    ):
        refused.bind(("127.0.0.1", 0))
        refused_address = refused.getsockname()

        def resolve(
            host: str, port: int, *args: object, **kwargs: object
        ) -> list[tuple[int, int, int, str, tuple[str, int]]]:
            assert host == "netcraft.example"
            assert port == peer.port
            return [
                (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", refused_address),
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    (peer.address, peer.port),
                ),
            ]

        monkeypatch.setattr(socket, "getaddrinfo", resolve)
        result = connect_tcp("netcraft.example", peer.port, timeout=5)
    assert result.details is not None
    assert result.details.peer == peer
    assert result.target == "netcraft.example"


@pytest.mark.parametrize("payload", [b"binary\x00\xff", b"x" * MAX_ECHO_PAYLOAD])
def test_real_tcp_echo(loopback: str, payload: bytes) -> None:
    with echo_server(Protocol.TCP, host=loopback) as peer:
        result = echo_tcp(loopback, peer.port, payload)
    assert result.details is not None, result.error
    assert result.details.response == payload
    assert result.details.bytes_sent == result.details.bytes_received == len(payload)
    assert result.details.connect_ns is not None
    assert result.details.exchange_ns is not None
    assert 0 <= result.details.connect_ns + result.details.exchange_ns <= result.duration_ns


@pytest.mark.parametrize("payload", [b"", b"binary\x00\xff", b"x" * MAX_ECHO_PAYLOAD])
def test_real_udp_echo(loopback: str, payload: bytes) -> None:
    with echo_server(Protocol.UDP, host=loopback) as peer:
        result = exchange_datagram(loopback, peer.port, payload)
    assert result.details is not None, result.error
    assert result.details.peer == peer
    assert result.details.response == payload
    assert result.details.bytes_sent == result.details.bytes_received == len(payload)
    assert result.details.connect_ns is None
    assert result.details.exchange_ns is not None
    assert 0 <= result.details.exchange_ns <= result.duration_ns


def test_send_only_udp() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver:
        receiver.settimeout(2)
        receiver.bind(("127.0.0.1", 0))
        result = send_datagram("127.0.0.1", receiver.getsockname()[1], b"one datagram")
        observed, _ = receiver.recvfrom(1024)
    assert observed == b"one datagram"
    assert result.details is not None
    assert result.details.bytes_sent == len(observed)
    assert result.details.bytes_received is None
    assert result.details.exchange_ns is None


@pytest.mark.parametrize(
    ("protocol", "exchange"), [(Protocol.TCP, echo_tcp), (Protocol.UDP, exchange_datagram)]
)
def test_receive_timeout(
    protocol: Protocol,
    exchange: Callable[..., NetworkResult],
) -> None:
    with echo_server(protocol, reply=False) as peer:
        result = exchange(peer.address, peer.port, b"no reply", timeout=0.075)
    assert result.error is not None
    assert result.error.category is ErrorCategory.TIMEOUT
    assert result.error.stage == "receive"
    assert result.duration_ns >= 70_000_000


@pytest.mark.parametrize(
    ("reply", "category"),
    [
        (b"", ErrorCategory.REMOTE_CLOSED),
        (b"wrong", ErrorCategory.UNEXPECTED_RESPONSE),
        (b"excessive", ErrorCategory.UNEXPECTED_RESPONSE),
    ],
)
def test_tcp_rejects_incomplete_or_wrong_echo(reply: bytes, category: ErrorCategory) -> None:
    with socket.socket() as listener, ThreadPoolExecutor(max_workers=1) as executor:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        listener.settimeout(2)

        def reply_with_fixture_data() -> None:
            client, _ = listener.accept()
            with client:
                client.settimeout(2)
                while client.recv(1024):
                    pass
                client.sendall(reply)

        server = executor.submit(reply_with_fixture_data)
        result = echo_tcp("127.0.0.1", listener.getsockname()[1], b"right")
        server.result(timeout=3)
    assert result.error is not None
    assert result.error.category is category
    assert result.error.stage == "receive"


def test_loopback_lab_refuses_public_binding() -> None:
    with pytest.raises(NetworkFailure, match="loopback"), echo_server(Protocol.UDP, host="0.0.0.0"):
        pytest.fail("Server accepted a non-loopback bind")


def test_laboratory_joins_threads_on_exception_and_releases_port() -> None:
    threads_before = set(active_threads())
    with pytest.raises(RuntimeError, match="test body"), echo_server(Protocol.UDP) as peer:
        raise RuntimeError("test body")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as replacement:
        replacement.bind((peer.address, peer.port))
    assert not (set(active_threads()) - threads_before)


def test_repeat_operation_with_same_lab() -> None:
    with echo_server(Protocol.TCP) as peer:
        for _ in range(25):
            assert connect_tcp(peer.address, peer.port).success
            assert echo_tcp(peer.address, peer.port, b"repeat").success
    with echo_server(Protocol.UDP) as peer:
        for _ in range(25):
            assert exchange_datagram(peer.address, peer.port, b"repeat").success


def test_cancel_pending_udp_receive() -> None:
    with (
        socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver,
        CancellationToken() as token,
        ThreadPoolExecutor(max_workers=1) as executor,
    ):
        receiver.bind(("127.0.0.1", 0))
        receiver.settimeout(2)
        future = executor.submit(
            exchange_datagram,
            "127.0.0.1",
            receiver.getsockname()[1],
            b"pending",
            timeout=5,
            cancel=token,
        )
        try:
            assert receiver.recvfrom(1024)[0] == b"pending"
        finally:
            token.cancel()
        result = future.result(timeout=2)
    assert result.error is not None
    assert result.error.category is ErrorCategory.CANCELLED
    assert result.error.stage == "receive"


def test_runner_bounds_calls_and_cancels_on_exit() -> None:
    threads_before = set(active_threads())
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver:
        receiver.bind(("127.0.0.1", 0))
        receiver.settimeout(2)
        port = receiver.getsockname()[1]
        with DiagnosticRunner(max_workers=1) as runner:
            future = runner.submit(
                lambda token: exchange_datagram(
                    "127.0.0.1", port, b"pending", timeout=5, cancel=token
                )
            )
            assert receiver.recvfrom(1024)[0] == b"pending"
            with pytest.raises(RuntimeError, match="busy"):
                runner.submit(lambda token: connect_tcp("127.0.0.1", port, cancel=token))
        with pytest.raises(RuntimeError, match="closed"):
            runner.submit(lambda token: connect_tcp("127.0.0.1", port, cancel=token))
    result = future.result(timeout=1)
    assert result.error is not None
    assert result.error.category is ErrorCategory.CANCELLED
    assert not (set(active_threads()) - threads_before)


def test_runner_slot_is_available_after_result() -> None:
    with echo_server(Protocol.TCP) as peer, DiagnosticRunner(max_workers=1) as runner:
        for _ in range(10):
            future = runner.submit(lambda token: connect_tcp(peer.address, peer.port, cancel=token))
            assert future.result(timeout=3).success
