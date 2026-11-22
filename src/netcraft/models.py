"""Immutable results shared by the engine, CLI, and future consumers."""

from __future__ import annotations

from base64 import b64encode
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import StrEnum
from typing import TypedDict


class Protocol(StrEnum):
    TCP = "tcp"
    UDP = "udp"


class Operation(StrEnum):
    TCP_CONNECT = "tcp_connect"
    TCP_ECHO = "tcp_echo"
    UDP_SEND = "udp_send"
    UDP_EXCHANGE = "udp_exchange"


class ErrorCategory(StrEnum):
    INVALID_HOST = "invalid_host"
    INVALID_PORT = "invalid_port"
    INVALID_TIMEOUT = "invalid_timeout"
    INVALID_PAYLOAD = "invalid_payload"
    DNS_FAILURE = "dns_failure"
    CONNECTION_REFUSED = "connection_refused"
    TIMEOUT = "timeout"
    REMOTE_CLOSED = "remote_closed"
    NETWORK_UNREACHABLE = "network_unreachable"
    PERMISSION_DENIED = "permission_denied"
    CANCELLED = "cancelled"
    SOCKET_ERROR = "socket_error"
    UNEXPECTED_RESPONSE = "unexpected_response"
    INVALID_DNS_TYPE = "invalid_dns_type"
    INVALID_RESOLVER = "invalid_resolver"
    DNS_NXDOMAIN = "dns_nxdomain"
    DNS_SERVFAIL = "dns_servfail"
    DNS_REFUSED = "dns_refused"
    DNS_RESPONSE_ERROR = "dns_response_error"
    DNS_MALFORMED = "dns_malformed"
    DNS_CONFIGURATION = "dns_configuration"
    INVALID_URL = "invalid_url"
    INVALID_METHOD = "invalid_method"
    INVALID_BODY_LIMIT = "invalid_body_limit"
    HTTP_MALFORMED = "http_malformed"
    HTTP_UNSUPPORTED = "http_unsupported"
    TLS_CONFIGURATION = "tls_configuration"
    TLS_CERTIFICATE = "tls_certificate"
    TLS_HOSTNAME = "tls_hostname"
    TLS_FAILURE = "tls_failure"


@dataclass(frozen=True, slots=True)
class NetworkError:
    category: ErrorCategory
    stage: str
    message: str
    os_code: int | None = None


@dataclass(frozen=True, slots=True)
class Peer:
    address: str
    port: int
    scope_id: int = 0


@dataclass(frozen=True, slots=True)
class Details:
    peer: Peer
    connect_ns: int | None = None
    exchange_ns: int | None = None
    bytes_sent: int | None = None
    bytes_received: int | None = None
    response: bytes | None = None


class ResultData(TypedDict):
    operation: str
    protocol: str
    target: str
    port: int
    started_at: str
    ended_at: str
    duration_ns: int
    success: bool
    details: dict[str, object] | None
    error: dict[str, object] | None


@dataclass(frozen=True, slots=True)
class NetworkResult:
    operation: Operation
    protocol: Protocol
    target: str
    port: int
    started_at: datetime
    ended_at: datetime
    duration_ns: int
    details: Details | None = None
    error: NetworkError | None = None

    def __post_init__(self) -> None:
        if (self.details is None) == (self.error is None):
            raise ValueError("A result requires exactly one of details or error.")
        if self.duration_ns < 0:
            raise ValueError("Result duration must be nonnegative.")

    @property
    def success(self) -> bool:
        return self.error is None

    def to_dict(self) -> ResultData:
        """Return JSON-ready data, including a lossless encoding for arbitrary bytes."""
        details = None
        if self.details is not None:
            details = asdict(self.details)
            response = self.details.response
            del details["response"]
            details["response_base64"] = (
                b64encode(response).decode("ascii") if response is not None else None
            )
        return {
            "operation": self.operation.value,
            "protocol": self.protocol.value,
            "target": self.target,
            "port": self.port,
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat(),
            "duration_ns": self.duration_ns,
            "success": self.success,
            "details": details,
            "error": asdict(self.error) if self.error is not None else None,
        }
