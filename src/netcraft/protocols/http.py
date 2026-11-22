"""Single-request HTTP/1.1 diagnostics with stage-level timing."""

import re
import socket
from dataclasses import dataclass, field, replace
from pathlib import Path
from time import perf_counter_ns
from urllib.parse import quote, urlsplit, urlunsplit

from netcraft.core.config import DEFAULT_TIMEOUT, validate_host, validate_port, validate_timeout
from netcraft.core.errors import NetworkFailure, classify_socket_error
from netcraft.core.execution import CancellationToken
from netcraft.core.socket_io import SocketIO
from netcraft.core.timing import OperationClock
from netcraft.core.transport import Deadline, connect_addresses, resolve_addresses
from netcraft.diagnostic_models import (
    ConnectAttempt,
    HTTPResponseInfo,
    HTTPResult,
    HTTPTimings,
    TLSInfo,
)
from netcraft.models import ErrorCategory, NetworkError, Peer
from netcraft.protocols.http_response import ResponseHead, ResponseReader
from netcraft.protocols.tls import verified_tls

DEFAULT_BODY_LIMIT = 16_384
MAX_BODY_LIMIT = 1_048_576
_MAX_URL = 8_192
_URI_SAFE = "/%:@!$&'()*+,;=-._~"
_IPV6_AUTHORITY = re.compile(r"\[[^\]]+\](?::[0-9]+)?")
_REDIRECT_STATUSES = {300, 301, 302, 303, 307, 308}
_RESPONSE_HEADERS = {
    "age",
    "cache-control",
    "connection",
    "content-encoding",
    "content-length",
    "content-type",
    "date",
    "etag",
    "expires",
    "last-modified",
    "server",
    "transfer-encoding",
    "vary",
}


@dataclass(frozen=True, slots=True)
class HTTPTimeouts:
    connect: float = DEFAULT_TIMEOUT
    tls: float = DEFAULT_TIMEOUT
    read: float = DEFAULT_TIMEOUT
    overall: float = 10.0


@dataclass(frozen=True, slots=True)
class HTTPTarget:
    scheme: str
    host: str
    numeric: bool
    port: int
    authority: str
    request_target: str
    display_url: str
    query_redacted: bool


@dataclass(slots=True)
class _Progress:
    endpoint: Peer | None = None
    attempts: list[ConnectAttempt] = field(default_factory=list)
    response: HTTPResponseInfo | None = None
    tls: TLSInfo | None = None
    timings: HTTPTimings = field(default_factory=HTTPTimings)


def _invalid_url() -> NetworkFailure:
    return NetworkFailure(
        ErrorCategory.INVALID_URL,
        "validation",
        "Use an absolute HTTP(S) URL with a valid authority and no credentials.",
    )


def parse_http_target(url: str) -> HTTPTarget:
    if len(url) > _MAX_URL or "\\" in url or any(char.isspace() or ord(char) < 33 for char in url):
        raise _invalid_url()
    try:
        parts = urlsplit(url)
        if (
            parts.scheme not in {"http", "https"}
            or parts.hostname is None
            or parts.username is not None
            or parts.netloc.endswith(":")
        ):
            raise _invalid_url()
        explicit_port = parts.port
        host, numeric = validate_host(parts.hostname)
        if "%" in host or (
            "[" in parts.netloc and (":" not in host or not _IPV6_AUTHORITY.fullmatch(parts.netloc))
        ):
            raise _invalid_url()
        authority = f"[{host}]" if ":" in host else host
        if explicit_port is not None:
            validate_port(explicit_port)
            authority += f":{explicit_port}"
        port = (
            explicit_port if explicit_port is not None else (443 if parts.scheme == "https" else 80)
        )
        path = quote(parts.path or "/", safe=_URI_SAFE)
        query = quote(parts.query, safe=_URI_SAFE + "?[]")
        request_target = f"{path}?{query}" if query else path
        if len(request_target) > _MAX_URL:
            raise _invalid_url()
        display_url = urlunsplit((parts.scheme, authority, path, "", ""))
        return HTTPTarget(
            parts.scheme, host, numeric, port, authority, request_target, display_url, bool(query)
        )
    except (ValueError, UnicodeError) as exc:
        raise _invalid_url() from exc


def _redirect_target(location: str | None) -> str | None:
    if not location or "\\" in location or any(ord(char) < 33 for char in location):
        return None
    try:
        parts = urlsplit(location)
        if parts.scheme and parts.scheme not in {"http", "https"}:
            return None
        if parts.username is not None or (parts.scheme and not parts.hostname):
            return None
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", "")) or None
    except ValueError:
        return None


def _obs_head(head: ResponseHead, maximum: int) -> HTTPResponseInfo:
    is_redirect = head.status in _REDIRECT_STATUSES
    redirect = _redirect_target(head.header("location")) if is_redirect else None
    headers = tuple(header for header in head.headers if header[0] in _RESPONSE_HEADERS)
    if redirect is not None:
        headers += (("location", redirect),)
    return HTTPResponseInfo(
        head.status,
        head.reason,
        head.version,
        headers,
        head.header("content-type"),
        head.content_length,
        head.header("server"),
        is_redirect,
        redirect,
        maximum,
        0,
        None,
        False,
    )


def _exchange(
    sock: socket.socket,
    target: HTTPTarget,
    method: str,
    maximum: int,
    timeout: float,
    overall: Deadline,
    cancel: CancellationToken | None,
    progress: _Progress,
) -> None:
    request = (
        f"{method} {target.request_target} HTTP/1.1\r\nHost: {target.authority}\r\n"
        "User-Agent: NetCraft/0.2\r\nAccept: */*\r\nAccept-Encoding: identity\r\n"
        "Connection: close\r\n\r\n"
    ).encode("ascii")
    start = perf_counter_ns()
    SocketIO(sock, overall.capped(timeout), cancel, "http_send").send_all(request)
    sent_at = perf_counter_ns()
    progress.timings = replace(progress.timings, request_send_ns=sent_at - start)
    source = SocketIO(sock, overall.capped(timeout), cancel, "http_headers")
    with ResponseReader(source) as reader:
        try:
            head = reader.read_head()
            progress.timings = replace(
                progress.timings, headers_read_ns=perf_counter_ns() - sent_at
            )
            progress.response = _obs_head(head, maximum)
            if method == "HEAD" or head.status in {204, 205, 304} or head.content_length == 0:
                progress.response = replace(progress.response, body_complete=True)
            elif maximum:
                start = perf_counter_ns()
                complete = reader.consume_body(head, maximum)
                progress.timings = replace(progress.timings, body_read_ns=perf_counter_ns() - start)
                progress.response = replace(
                    progress.response, body_complete=complete, body_limit_reached=not complete
                )
        finally:
            if reader.first_byte_at is not None:
                progress.timings = replace(progress.timings, ttfb_ns=reader.first_byte_at - sent_at)
            if progress.response is not None:
                progress.response = replace(progress.response, body_bytes_read=reader.body_bytes)


def inspect_http(
    url: str,
    *,
    method: str = "GET",
    timeouts: HTTPTimeouts = HTTPTimeouts(),
    body_limit: int = DEFAULT_BODY_LIMIT,
    ca_file: Path | None = None,
    cancel: CancellationToken | None = None,
) -> HTTPResult:
    """Inspect one HTTP or verified HTTPS response without following redirects."""
    clock = OperationClock()
    normalized_method = method.upper()
    target = None
    progress = _Progress()
    error: NetworkError | None = None
    try:
        if normalized_method not in {"GET", "HEAD"}:
            raise NetworkFailure(
                ErrorCategory.INVALID_METHOD, "validation", "HTTP method must be GET or HEAD."
            )
        if isinstance(body_limit, bool) or not 0 <= body_limit <= MAX_BODY_LIMIT:
            raise NetworkFailure(
                ErrorCategory.INVALID_BODY_LIMIT,
                "validation",
                f"Body limit must be between 0 and {MAX_BODY_LIMIT} bytes.",
            )
        for timeout in (timeouts.connect, timeouts.tls, timeouts.read, timeouts.overall):
            validate_timeout(timeout)
        target = parse_http_target(url)
        overall = Deadline(clock.tick + max(1, int(timeouts.overall * 1_000_000_000)))
        overall.remaining("resolve")
        start = perf_counter_ns()
        addresses = resolve_addresses(
            target.host, target.port, socket.SOCK_STREAM, target.numeric, cancel
        )
        if not target.numeric and addresses:
            progress.timings = replace(progress.timings, dns_ns=perf_counter_ns() - start)
        overall.remaining("resolve")
        raw, progress.endpoint, connect_ns = connect_addresses(
            addresses,
            socket.SOCK_STREAM,
            overall.capped(timeouts.connect),
            cancel,
            progress.attempts,
        )
        with raw:
            progress.timings = replace(progress.timings, tcp_connect_ns=connect_ns)
            if target.scheme == "https":
                server_name = target.host.removesuffix(".")
                with verified_tls(
                    raw, server_name, ca_file, overall.capped(timeouts.tls), cancel
                ) as tls:
                    sock, progress.tls, elapsed = tls
                    progress.timings = replace(progress.timings, tls_handshake_ns=elapsed)
                    _exchange(
                        sock,
                        target,
                        normalized_method,
                        body_limit,
                        timeouts.read,
                        overall,
                        cancel,
                        progress,
                    )
            else:
                _exchange(
                    raw,
                    target,
                    normalized_method,
                    body_limit,
                    timeouts.read,
                    overall,
                    cancel,
                    progress,
                )
    except NetworkFailure as exc:
        error = exc.error
    except OSError as exc:
        error = classify_socket_error(exc, "http")

    return HTTPResult(
        target.display_url if target is not None else None,
        target.query_redacted if target is not None else False,
        normalized_method,
        progress.endpoint,
        tuple(progress.attempts),
        progress.response,
        progress.tls,
        progress.timings,
        *clock.finish(),
        error,
    )
