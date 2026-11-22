"""Adapt existing diagnostics to privacy-preserving observations."""

from dataclasses import dataclass, field
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlsplit

from netcraft.core.config import DEFAULT_TIMEOUT, validate_host, validate_port, validate_timeout
from netcraft.core.execution import CancellationToken
from netcraft.diagnostic_models import RecordType
from netcraft.models import ErrorCategory
from netcraft.monitoring.telemetry import MonitorProtocol, Observation, SampleStatus
from netcraft.protocols.dns import inspect_dns, parse_dns_question
from netcraft.protocols.http import HTTPTimeouts, inspect_http, parse_http_target
from netcraft.protocols.tcp import connect_tcp
from netcraft.protocols.udp import exchange_datagram

_UDP_MESSAGE = b"NetCraft monitor"


def _authority(host: str, port: int) -> str:
    return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"


@dataclass(frozen=True, slots=True)
class Target:
    protocol: MonitorProtocol
    destination: str = field(repr=False)
    port: int | None = None
    record_type: RecordType = RecordType.A
    label: str = field(init=False)

    def __post_init__(self) -> None:
        if self.protocol in {MonitorProtocol.HTTP, MonitorProtocol.HTTPS}:
            resolved = parse_http_target(self.destination)
            if resolved.scheme != self.protocol or self.port is not None:
                raise ValueError("HTTP target scheme or port is inconsistent.")
            host = resolved.host.lower().removesuffix(".")
            if resolved.numeric:
                host = str(ip_address(host))
            label = f"{resolved.scheme}://{_authority(host, resolved.port)}"
        elif self.protocol is MonitorProtocol.DNS:
            question = parse_dns_question(self.destination, self.record_type)
            label = f"{self.record_type} {question.canonicalize()}"
        else:
            if self.port is None:
                raise ValueError("TCP and UDP targets require a port.")
            validate_port(self.port)
            host, numeric = validate_host(self.destination)
            host = str(ip_address(host)) if numeric else host.lower().removesuffix(".")
            label = _authority(host, self.port)
        object.__setattr__(self, "label", label)


def socket_target(protocol: MonitorProtocol, value: str) -> Target:
    if any(char in value for char in "/?#@\\") or any(char.isspace() for char in value):
        raise ValueError("Use host:port or [IPv6]:port for a TCP or UDP target.")
    try:
        parsed = urlsplit(f"//{value}")
        host, port = parsed.hostname, parsed.port
    except ValueError as exc:
        raise ValueError("Invalid TCP or UDP target authority.") from exc
    if host is None or port is None or ("[" in value and not value.endswith(f"]:{port}")):
        raise ValueError("Use host:port or [IPv6]:port for a TCP or UDP target.")
    return Target(protocol, host, port)


@dataclass(frozen=True, slots=True)
class ProbeOptions:
    timeout: float = DEFAULT_TIMEOUT
    resolver: str | None = None
    resolver_port: int | None = None
    ca_file: Path | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        validate_timeout(self.timeout)
        if self.resolver is not None:
            try:
                ip_address(self.resolver)
            except ValueError as exc:
                raise ValueError("DNS resolver must be a numeric IP address.") from exc
        if self.resolver_port is not None:
            if self.resolver is None:
                raise ValueError("A DNS resolver port requires an explicit resolver.")
            validate_port(self.resolver_port)


def _status(error: ErrorCategory | None) -> SampleStatus:
    return {
        None: SampleStatus.SUCCESS,
        ErrorCategory.TIMEOUT: SampleStatus.TIMEOUT,
        ErrorCategory.CANCELLED: SampleStatus.CANCELLED,
    }.get(error, SampleStatus.FAILURE)


DEFAULT_PROBE_OPTIONS = ProbeOptions()


def run_probe(target: Target, options: ProbeOptions, cancel: CancellationToken) -> Observation:
    protocol = target.protocol
    if protocol is MonitorProtocol.DNS:
        dns = inspect_dns(
            target.destination,
            target.record_type,
            resolver=options.resolver,
            port=options.resolver_port,
            timeout=options.timeout,
            lifetime=options.timeout,
            cancel=cancel,
        )
        error = dns.error.category if dns.error is not None else None
        return Observation(
            _status(error),
            error,
            latency_ns=dns.duration_ns if error is not ErrorCategory.CANCELLED else None,
            dns_status=dns.status,
            dns_rcode=dns.response_code,
            dns_record_count=len(dns.records),
        )
    if protocol in {MonitorProtocol.HTTP, MonitorProtocol.HTTPS}:
        timeouts = HTTPTimeouts(
            connect=options.timeout,
            tls=options.timeout,
            read=options.timeout,
            overall=options.timeout,
        )
        http = inspect_http(
            target.destination,
            method="HEAD",
            timeouts=timeouts,
            ca_file=options.ca_file,
            cancel=cancel,
        )
        times, tls = http.timings, http.tls
        error = http.error.category if http.error is not None else None
        return Observation(
            _status(error),
            error,
            latency_ns=http.duration_ns if error is not ErrorCategory.CANCELLED else None,
            dns_ns=times.dns_ns,
            tcp_ns=times.tcp_connect_ns,
            tls_ns=times.tls_handshake_ns,
            request_send_ns=times.request_send_ns,
            ttfb_ns=times.ttfb_ns,
            headers_read_ns=times.headers_read_ns,
            body_read_ns=times.body_read_ns,
            http_status=http.response.status if http.response is not None else None,
            tls_version=tls.version if tls is not None else None,
            tls_verified=tls.chain_verified and tls.hostname_verified if tls is not None else None,
        )
    assert target.port is not None
    if protocol is MonitorProtocol.TCP:
        result = connect_tcp(
            target.destination, target.port, timeout=options.timeout, cancel=cancel
        )
    else:
        result = exchange_datagram(
            target.destination, target.port, _UDP_MESSAGE, timeout=options.timeout, cancel=cancel
        )
    details = result.details
    error = result.error.category if result.error is not None else None
    if protocol is MonitorProtocol.UDP and details is not None and details.response != _UDP_MESSAGE:
        error = ErrorCategory.UNEXPECTED_RESPONSE
    return Observation(
        _status(error),
        error,
        latency_ns=result.duration_ns if error is not ErrorCategory.CANCELLED else None,
        tcp_ns=details.connect_ns if details is not None else None,
        udp_exchange_ns=details.exchange_ns if details is not None else None,
        bytes_received=details.bytes_received if details is not None else None,
    )
