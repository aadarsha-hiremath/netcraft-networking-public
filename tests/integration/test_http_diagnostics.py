import errno
import json
import socket
import ssl
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from threading import Event
from threading import enumerate as active_threads
from time import sleep

import pytest

from netcraft.core.execution import DiagnosticRunner
from netcraft.models import ErrorCategory
from netcraft.protocols.http import HTTPTimeouts, inspect_http
from tests.protocol_fixtures import fixed_http, http_request, server_tls, tcp_fixture

_OK = b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\nContent-Type: text/plain\r\n\r\nhello"


@pytest.mark.parametrize("status", [200, 404, 500])
def test_status_and_bounded_metadata(status: int) -> None:
    reply = (
        f"HTTP/1.1 {status} Test\r\nContent-Length: 3\r\nContent-Type: text/plain\r\n"
        "Server: local-lab\r\nCache-Control: no-store\r\nSet-Cookie: secret=hidden\r\n\r\nabc"
    ).encode()
    with tcp_fixture(fixed_http(reply)) as peer:
        result = inspect_http(f"http://{peer.address}:{peer.port}/inspect?token=hidden#fragment")
    assert result.success, result.error
    assert result.endpoint == peer
    assert result.attempts[0].peer == peer and result.attempts[0].error is None
    assert result.response is not None
    assert result.response.status == status and result.response.version == "1.1"
    assert result.response.content_length == result.response.body_bytes_read == 3
    assert result.response.body_complete is True
    assert result.response.content_type == "text/plain"
    assert result.response.server == "local-lab"
    assert ("cache-control", "no-store") in result.response.headers
    assert result.query_redacted
    exported = json.dumps(result.to_dict())
    assert "hidden" not in exported and "Set-Cookie" not in exported and "fragment" not in exported
    timing = result.timings
    assert timing.dns_ns is timing.tls_handshake_ns is None
    assert timing.ttfb_ns is not None and timing.headers_read_ns is not None
    assert 0 <= timing.ttfb_ns <= timing.headers_read_ns <= result.duration_ns
    assert timing.body_read_ns is not None and timing.request_send_ns is not None


@pytest.mark.parametrize(
    ("code", "location", "target"),
    [
        (301, "/loop?private=hidden", "/loop"),
        (
            302,
            "http://forbidden.example.test/path?private=hidden",
            "http://forbidden.example.test/path",
        ),
        (302, "https://user:pass@forbidden.example.test", None),
        (302, "file:///tmp/example", None),
    ],
)
def test_redirects_are_reported_without_following(
    code: int, location: str, target: str | None
) -> None:
    requests: list[bytes] = []

    def handler(conn: socket.socket) -> None:
        requests.append(http_request(conn))
        lines = f"HTTP/1.1 {code} Redirect\r\nLocation: {location}\r\n"
        conn.sendall((lines + "Content-Length: 0\r\n\r\n").encode())

    with tcp_fixture(handler) as peer:
        result = inspect_http(f"http://{peer.address}:{peer.port}/loop")
    assert len(requests) == 1 and len(result.attempts) == 1
    assert result.response is not None
    assert result.response.is_redirect and result.response.redirect_target == target
    assert "hidden" not in json.dumps(result.to_dict())


def test_head_and_http_1_0_keep_advertised_length() -> None:
    with tcp_fixture(fixed_http(b"HTTP/1.0 200 OK\r\nContent-Length: 90000\r\n\r\n")) as peer:
        result = inspect_http(f"http://127.0.0.1:{peer.port}/", method="HEAD")
    assert result.success and result.response is not None
    assert result.response.version == "1.0"
    assert result.response.content_length == 90_000
    assert result.response.body_bytes_read == 0 and result.response.body_complete is True
    assert result.timings.body_read_ns is None


@pytest.mark.parametrize(
    ("reply", "limit", "count", "complete"),
    [
        (b"HTTP/1.1 200 OK\r\nContent-Length: 12\r\n\r\nabcdefghijkl", 4, 4, False),
        (_OK, 0, 0, None),
        (
            b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
            b"3\r\nabc\r\n2\r\nde\r\n0\r\nX-Lab: trailer\r\n\r\n",
            12,
            5,
            True,
        ),
        (
            b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
            b"3\r\nabc\r\n2\r\nde\r\n0\r\n\r\n",
            4,
            4,
            False,
        ),
        (b"HTTP/1.0 200 OK\r\n\r\nabc", 5, 3, True),
        (b"HTTP/1.1 103 Early Hints\r\nX-Lab: early\r\n\r\n" + _OK, 8, 5, True),
        (b"HTTP/1.1 204 No Content\r\n\r\n", 8, 0, True),
    ],
)
def test_response_framing_and_body_limits(
    reply: bytes, limit: int, count: int, complete: bool | None
) -> None:
    with tcp_fixture(fixed_http(reply)) as peer:
        result = inspect_http(f"http://127.0.0.1:{peer.port}", body_limit=limit)
    assert result.success, result.error
    assert result.response is not None
    assert result.response.body_bytes_read == count
    assert result.response.body_complete is complete
    assert result.response.body_limit_reached == (complete is False)


@pytest.mark.parametrize(
    ("reply", "category", "stage"),
    [
        (b"not an HTTP reply\r\n", ErrorCategory.HTTP_MALFORMED, "http_headers"),
        (
            b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n",
            ErrorCategory.HTTP_MALFORMED,
            "http_headers",
        ),
        (
            b"HTTP/1.1 200 OK\r\nContent-Length: 9\r\n\r\nshort",
            ErrorCategory.HTTP_MALFORMED,
            "http_body",
        ),
        (
            b"HTTP/1.1 200 OK\r\nX-Oversize: " + b"x" * 9000,
            ErrorCategory.HTTP_MALFORMED,
            "http_headers",
        ),
        (b"HTTP/2.0 200 OK\r\n\r\n", ErrorCategory.HTTP_UNSUPPORTED, "http_headers"),
        (
            b"HTTP/1.1 101 Switching Protocols\r\n\r\n",
            ErrorCategory.HTTP_UNSUPPORTED,
            "http_headers",
        ),
    ],
)
def test_invalid_or_incomplete_response(reply: bytes, category: ErrorCategory, stage: str) -> None:
    with tcp_fixture(fixed_http(reply)) as peer:
        result = inspect_http(f"http://127.0.0.1:{peer.port}/")
    assert result.error is not None and result.error.category is category
    assert result.error.stage == stage
    if stage == "http_body":
        assert result.response is not None and result.response.body_bytes_read == 5
        assert result.timings.body_read_ns is None


@pytest.mark.parametrize("sent_headers", [False, True])
def test_http_read_deadline(sent_headers: bool) -> None:
    done = Event()

    def handler(conn: socket.socket) -> None:
        http_request(conn)
        if sent_headers:
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 9\r\n\r\n")
        done.wait(2)

    with tcp_fixture(handler) as peer:
        try:
            result = inspect_http(
                f"http://127.0.0.1:{peer.port}/", timeouts=HTTPTimeouts(read=0.12)
            )
        finally:
            done.set()
    assert result.error is not None and result.error.category is ErrorCategory.TIMEOUT
    assert result.error.stage == ("http_body" if sent_headers else "http_headers")
    assert (result.timings.ttfb_ns is not None) == sent_headers


def test_connection_refusal_and_attempt_metadata() -> None:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        result = inspect_http(
            f"http://127.0.0.1:{sock.getsockname()[1]}/", timeouts=HTTPTimeouts(connect=5)
        )
    assert result.error is not None and result.error.category is ErrorCategory.CONNECTION_REFUSED
    assert result.endpoint is None and result.response is None
    assert result.timings.tcp_connect_ns is None
    assert len(result.attempts) == 1 and result.attempts[0].error == result.error


@pytest.mark.parametrize(
    "url",
    [
        "ftp://localhost/",
        "/relative",
        "http://user:pw@localhost/",
        "http://localhost:/",
        "http://[::1]x/",
        "http://localhost/name\nbad",
        "http://localhost:99999/",
    ],
)
def test_http_url_validation(url: str) -> None:
    result = inspect_http(url)
    assert result.error is not None and result.error.stage == "validation"
    assert result.requested_url is None and not result.attempts


def test_http_ipv6() -> None:
    try:
        with socket.socket(socket.AF_INET6) as check:
            check.bind(("::1", 0))
    except OSError as exc:
        if exc.errno in {errno.EAFNOSUPPORT, errno.EPROTONOSUPPORT, errno.EADDRNOTAVAIL} or (
            getattr(exc, "winerror", None) in {10043, 10047, 10049}
        ):
            pytest.skip(f"IPv6 loopback unavailable: {exc}")
        raise
    with tcp_fixture(fixed_http(_OK), host="::1") as peer:
        result = inspect_http(f"http://[::1]:{peer.port}/")
    assert result.success and result.endpoint == peer


def test_overall_deadline_stops_connect_after_blocking_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def slow_lookup(
        host: str, port: int, *args: object, **kwargs: object
    ) -> list[tuple[int, int, int, str, tuple[str, int]]]:
        assert host == "overdue.example.test"
        sleep(0.05)
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", port))]

    monkeypatch.setattr(socket, "getaddrinfo", slow_lookup)
    result = inspect_http("http://overdue.example.test:8000", timeouts=HTTPTimeouts(overall=0.02))
    assert result.error is not None and result.error.category is ErrorCategory.TIMEOUT
    assert result.error.stage == "resolve"
    assert result.timings.dns_ns is not None and not result.attempts


def test_failed_lookup_has_no_invented_timings(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(*args: object, **kwargs: object) -> None:
        raise socket.gaierror(socket.EAI_NONAME, "test lookup failed")

    monkeypatch.setattr(socket, "getaddrinfo", missing)
    result = inspect_http("https://missing.example.test/")
    assert result.error is not None and result.error.category is ErrorCategory.DNS_FAILURE
    assert result.error.stage == "resolve"
    assert result.timings.dns_ns is None and not result.attempts


def test_verified_https_hostname_sni_alpn_and_certificate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context, ca = server_tls("lab.example.test")
    ca_file = tmp_path / "ca.pem"
    ca.cert_pem.write_to_path(ca_file)
    observed_sni: list[str | None] = []
    context.set_servername_callback(lambda _s, name, _ctx: observed_sni.append(name))
    lookup_calls: list[str] = []
    requests: list[bytes] = []

    def handler(conn: socket.socket) -> None:
        requests.append(http_request(conn))
        conn.sendall(_OK)

    def local_resolver(
        host: str, port: int, *args: object, **kwargs: object
    ) -> list[tuple[int, int, int, str, tuple[str, int]]]:
        assert host == "lab.example.test"
        lookup_calls.append(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", port))]

    with tcp_fixture(handler, tls=context) as peer:
        monkeypatch.setattr(socket, "getaddrinfo", local_resolver)
        result = inspect_http(f"https://lab.example.test:{peer.port}/", ca_file=ca_file)
    assert result.success, result.error
    assert lookup_calls == observed_sni == ["lab.example.test"]
    assert f"Host: lab.example.test:{peer.port}".encode() in requests[0]
    assert result.endpoint == peer and result.tls is not None
    assert result.tls.hostname_verified and result.tls.chain_verified
    assert result.tls.alpn == ("http/1.1" if ssl.HAS_ALPN else None)
    assert result.tls.version in {"TLSv1.2", "TLSv1.3"} and result.tls.cipher
    assert result.tls.issuer and result.tls.subject
    assert result.tls.not_before is not None and result.tls.not_after is not None
    assert datetime.fromisoformat(result.tls.not_before) < datetime.fromisoformat(
        result.tls.not_after
    )
    assert result.timings.dns_ns is not None and result.timings.tls_handshake_ns is not None


@pytest.mark.parametrize("mismatch", [None, "ip", "hostname"])
def test_https_rejects_untrusted_or_wrong_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mismatch: str | None,
) -> None:
    context, ca = server_tls("other.example.test" if mismatch else "127.0.0.1")
    ca_file = tmp_path / "ca.pem"
    ca.cert_pem.write_to_path(ca_file)
    host = "127.0.0.1"
    if mismatch == "hostname":
        host = "wrong.example.test"

        def local_resolver(
            name: str, port: int, *args: object, **kwargs: object
        ) -> list[tuple[int, int, int, str, tuple[str, int]]]:
            assert name == host
            return [
                (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", port))
            ]

        monkeypatch.setattr(socket, "getaddrinfo", local_resolver)
    with tcp_fixture(fixed_http(_OK), tls=context) as peer:
        result = inspect_http(f"https://{host}:{peer.port}/", ca_file=ca_file if mismatch else None)
    category = ErrorCategory.TLS_HOSTNAME if mismatch else ErrorCategory.TLS_CERTIFICATE
    assert result.error is not None and result.error.category is category
    assert result.error.stage == "tls_handshake"
    assert result.tls is None and result.response is None
    assert result.timings.tls_handshake_ns is None
    assert result.timings.tcp_connect_ns is not None


@pytest.mark.parametrize("silence", [False, True])
def test_tls_failure_or_handshake_timeout(silence: bool) -> None:
    done = Event()

    def handler(conn: socket.socket) -> None:
        conn.recv(4096)
        if silence:
            done.wait(2)
        else:
            conn.sendall(b"HTTP/1.1 400 Bad Request\r\n\r\n")

    with tcp_fixture(handler) as peer:
        try:
            result = inspect_http(f"https://127.0.0.1:{peer.port}/", timeouts=HTTPTimeouts(tls=0.4))
        finally:
            done.set()
    assert result.error is not None
    assert result.error.category is (
        ErrorCategory.TIMEOUT if silence else ErrorCategory.TLS_FAILURE
    )
    assert result.error.stage == "tls_handshake"


def test_socket_overall_deadline_and_cancelled_read() -> None:
    received, done = Event(), Event()
    threads = set(active_threads())

    def handler(conn: socket.socket) -> None:
        http_request(conn)
        received.set()
        done.wait(2)

    with tcp_fixture(handler) as peer:
        url = f"http://127.0.0.1:{peer.port}/"
        try:
            timeout = inspect_http(url, timeouts=replace(HTTPTimeouts(), overall=0.12))
        finally:
            done.set()
    assert timeout.error is not None and timeout.error.category is ErrorCategory.TIMEOUT
    assert timeout.duration_ns < 1_500_000_000
    received.clear()
    done.clear()
    with tcp_fixture(handler) as peer:
        try:
            with DiagnosticRunner(1) as runner:
                future = runner.submit(
                    lambda token: inspect_http(f"http://127.0.0.1:{peer.port}", cancel=token)
                )
                assert received.wait(2)
            cancelled = future.result(timeout=1)
        finally:
            done.set()
    assert cancelled.error is not None and cancelled.error.category is ErrorCategory.CANCELLED
    assert not (set(active_threads()) - threads)
