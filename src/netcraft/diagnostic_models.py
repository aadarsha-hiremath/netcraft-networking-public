"""Immutable observations for DNS and HTTP diagnostics."""

from dataclasses import asdict, dataclass
from datetime import datetime
from enum import StrEnum

from netcraft.models import NetworkError, Peer


class RecordType(StrEnum):
    A = "A"
    AAAA = "AAAA"
    CNAME = "CNAME"
    MX = "MX"
    NS = "NS"
    TXT = "TXT"
    SOA = "SOA"
    CAA = "CAA"
    SRV = "SRV"
    PTR = "PTR"


class DNSStatus(StrEnum):
    ANSWER = "answer"
    EMPTY = "empty"
    FAILURE = "failure"


class DNSResolverMode(StrEnum):
    EXPLICIT = "explicit"
    WINDOWS_SYSTEM = "windows_system"
    SYSTEM_CONFIGURED = "system_configured"


@dataclass(frozen=True, slots=True)
class DNSRecord:
    name: str
    type: RecordType
    ttl: int
    data: str
    preference: int | None = None


@dataclass(frozen=True, slots=True)
class DNSAttempt:
    resolver: Peer | None
    transport: str
    response_code: str | None
    error: NetworkError | None


@dataclass(frozen=True, slots=True)
class DNSResult:
    query_name: str | None
    query_type: str
    configured_resolvers: tuple[Peer, ...]
    attempts: tuple[DNSAttempt, ...]
    resolver: Peer | None
    response_code: str | None
    canonical_name: str | None
    records: tuple[DNSRecord, ...]
    aliases: tuple[DNSRecord, ...]
    authoritative: bool | None
    status: DNSStatus
    started_at: datetime
    ended_at: datetime
    duration_ns: int
    error: NetworkError | None
    resolver_mode: DNSResolverMode = DNSResolverMode.EXPLICIT
    fallback_occurred: bool | None = False

    @property
    def success(self) -> bool:
        return self.status is not DNSStatus.FAILURE

    @property
    def resolver_label(self) -> str | None:
        if self.resolver is not None:
            address = self.resolver.address
            if self.resolver.scope_id:
                address = f"{address}%{self.resolver.scope_id}"
            return f"{address} port {self.resolver.port}"
        if self.resolver_mode is DNSResolverMode.WINDOWS_SYSTEM:
            return "System resolver"
        return None

    def to_dict(self) -> dict[str, object]:
        return _serialize(self, "dns", self.started_at, self.ended_at, self.success)


@dataclass(frozen=True, slots=True)
class ConnectAttempt:
    peer: Peer
    duration_ns: int
    error: NetworkError | None


@dataclass(frozen=True, slots=True)
class HTTPTimings:
    dns_ns: int | None = None
    tcp_connect_ns: int | None = None
    tls_handshake_ns: int | None = None
    request_send_ns: int | None = None
    ttfb_ns: int | None = None
    headers_read_ns: int | None = None
    body_read_ns: int | None = None


@dataclass(frozen=True, slots=True)
class TLSInfo:
    version: str | None
    cipher: str | None
    cipher_bits: int | None
    subject: tuple[tuple[str, str], ...]
    issuer: tuple[tuple[str, str], ...]
    not_before: str | None
    not_after: str | None
    verified_server_name: str
    hostname_verified: bool
    chain_verified: bool
    alpn: str | None


@dataclass(frozen=True, slots=True)
class HTTPResponseInfo:
    status: int
    reason: str
    version: str
    headers: tuple[tuple[str, str], ...]
    content_type: str | None
    content_length: int | None
    server: str | None
    is_redirect: bool
    redirect_target: str | None
    body_limit: int
    body_bytes_read: int
    body_complete: bool | None
    body_limit_reached: bool


@dataclass(frozen=True, slots=True)
class HTTPResult:
    requested_url: str | None
    query_redacted: bool
    method: str
    endpoint: Peer | None
    attempts: tuple[ConnectAttempt, ...]
    response: HTTPResponseInfo | None
    tls: TLSInfo | None
    timings: HTTPTimings
    started_at: datetime
    ended_at: datetime
    duration_ns: int
    error: NetworkError | None

    @property
    def success(self) -> bool:
        return self.error is None

    def to_dict(self) -> dict[str, object]:
        return _serialize(self, "http", self.started_at, self.ended_at, self.success)


def _serialize(
    result: DNSResult | HTTPResult, operation: str, start: datetime, end: datetime, success: bool
) -> dict[str, object]:
    data: dict[str, object] = asdict(result)
    data.update(
        operation=operation, success=success, started_at=start.isoformat(), ended_at=end.isoformat()
    )
    return data
