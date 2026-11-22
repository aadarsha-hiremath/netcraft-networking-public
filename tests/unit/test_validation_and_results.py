import errno
import json
import socket
from base64 import b64decode
from datetime import UTC, datetime

import pytest

from netcraft.core.config import MAX_DATAGRAM, MAX_ECHO_PAYLOAD
from netcraft.core.errors import classify_socket_error
from netcraft.diagnostics.echo import echo_tcp
from netcraft.models import Details, ErrorCategory, NetworkResult, Operation, Peer, Protocol
from netcraft.protocols.tcp import connect_tcp
from netcraft.protocols.udp import send_datagram


@pytest.mark.parametrize(
    "host", ["", "a b", "a\0b", "bad_label", "-a.net", "a..net", "256.0.0.1", "::not-ip", "a" * 64]
)
def test_invalid_host_never_resolves(host: str, monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected_lookup(*args: object, **kwargs: object) -> None:
        pytest.fail("Invalid input reached the resolver")

    monkeypatch.setattr(socket, "getaddrinfo", unexpected_lookup)
    result = connect_tcp(host, 80)
    assert result.error is not None
    assert result.error.category is ErrorCategory.INVALID_HOST
    assert result.error.stage == "validation"


@pytest.mark.parametrize("port", [-1, 0, 65_536, True])
def test_invalid_port(port: int) -> None:
    result = connect_tcp("127.0.0.1", port)
    assert result.error is not None
    assert result.error.category is ErrorCategory.INVALID_PORT


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf"), 60.01, True])
def test_invalid_timeout(timeout: float) -> None:
    result = connect_tcp("127.0.0.1", 1, timeout=timeout)
    assert result.error is not None
    assert result.error.category is ErrorCategory.INVALID_TIMEOUT


def test_echo_rejects_non_loopback() -> None:
    result = echo_tcp("0.0.0.0", 1, b"x")
    assert result.error is not None
    assert result.error.category is ErrorCategory.INVALID_HOST


@pytest.mark.parametrize("payload", [b"", b"x" * (MAX_ECHO_PAYLOAD + 1)])
def test_tcp_echo_payload_validation(payload: bytes) -> None:
    result = echo_tcp("127.0.0.1", 1, payload)
    assert result.error is not None
    assert result.error.category is ErrorCategory.INVALID_PAYLOAD


def test_udp_payload_limit() -> None:
    result = send_datagram("127.0.0.1", 1, b"x" * (MAX_DATAGRAM + 1))
    assert result.error is not None
    assert result.error.category is ErrorCategory.INVALID_PAYLOAD


@pytest.mark.parametrize(
    ("exception", "category"),
    [
        (socket.gaierror(socket.EAI_NONAME, "unknown"), ErrorCategory.DNS_FAILURE),
        (OSError(errno.ECONNREFUSED, "refused"), ErrorCategory.CONNECTION_REFUSED),
        (OSError(10061, "refused"), ErrorCategory.CONNECTION_REFUSED),
        (TimeoutError("timed out"), ErrorCategory.TIMEOUT),
        (OSError(10060, "timed out"), ErrorCategory.TIMEOUT),
        (OSError(errno.ECONNRESET, "reset"), ErrorCategory.REMOTE_CLOSED),
        (OSError(10054, "reset"), ErrorCategory.REMOTE_CLOSED),
        (OSError(errno.ENETUNREACH, "offline"), ErrorCategory.NETWORK_UNREACHABLE),
        (OSError(10065, "offline"), ErrorCategory.NETWORK_UNREACHABLE),
        (PermissionError("blocked"), ErrorCategory.PERMISSION_DENIED),
        (OSError(12345, "unknown"), ErrorCategory.SOCKET_ERROR),
    ],
)
def test_error_categories(exception: OSError, category: ErrorCategory) -> None:
    error = classify_socket_error(exception, "connect")
    assert error.category is category
    assert error.stage == "connect"
    assert error.os_code == exception.errno
    assert error.message


def test_dns_failure_is_structured(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(host: str, *args: object, **kwargs: object) -> None:
        assert host == "xn--bcher-kva.example."
        raise socket.gaierror(socket.EAI_NONAME, "test resolver miss")

    monkeypatch.setattr(socket, "getaddrinfo", missing)
    result = connect_tcp("bücher.example.", 443)
    assert result.target == "bücher.example."
    assert result.error is not None
    assert result.error.category is ErrorCategory.DNS_FAILURE
    assert result.error.stage == "resolve"
    assert result.error.os_code == socket.EAI_NONAME
    assert result.to_dict()["error"] == {
        "category": "dns_failure",
        "stage": "resolve",
        "message": "test resolver miss",
        "os_code": socket.EAI_NONAME,
    }


def test_result_json_preserves_binary_data() -> None:
    now = datetime.now(UTC)
    payload = b"\x00\x80\xff"
    result = NetworkResult(
        Operation.UDP_EXCHANGE,
        Protocol.UDP,
        "::1",
        1234,
        now,
        now,
        100,
        details=Details(Peer("::1", 1234), exchange_ns=50, response=payload),
    )
    raw = json.loads(json.dumps(result.to_dict()))
    assert raw["success"]
    assert raw["error"] is None
    assert raw["protocol"] == "udp"
    assert raw["started_at"] == now.isoformat()
    assert raw["details"]["peer"]["address"] == "::1"
    assert b64decode(raw["details"]["response_base64"]) == payload


def test_result_invariants() -> None:
    now = datetime.now(UTC)
    with pytest.raises(ValueError, match="exactly one"):
        NetworkResult(Operation.TCP_CONNECT, Protocol.TCP, "::1", 1, now, now, 100)
    with pytest.raises(ValueError, match="nonnegative"):
        NetworkResult(
            Operation.TCP_CONNECT, Protocol.TCP, "::1", 1, now, now, -1, Details(Peer("::1", 1))
        )
