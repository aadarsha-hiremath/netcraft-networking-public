"""Validated diagnostic snapshots and deliberately limited desktop result projection."""

from dataclasses import dataclass, field
from urllib.parse import urlsplit

from netcraft.core.config import MAX_ECHO_PAYLOAD, validate_timeout
from netcraft.diagnostic_models import DNSResult, HTTPResult, RecordType
from netcraft.gui.workers import JobControl
from netcraft.models import ErrorCategory, NetworkError, NetworkResult
from netcraft.monitoring.probes import ProbeOptions, Target
from netcraft.monitoring.telemetry import MonitorProtocol, utc_text
from netcraft.protocols.dns import inspect_dns
from netcraft.protocols.http import DEFAULT_BODY_LIMIT, HTTPTimeouts, inspect_http
from netcraft.protocols.tcp import connect_tcp
from netcraft.protocols.udp import exchange_datagram

_ERROR_HELP = {
    ErrorCategory.DNS_NXDOMAIN: "The resolver reported that the requested DNS name does not exist.",
    ErrorCategory.DNS_SERVFAIL: "The resolver could not complete this query.",
    ErrorCategory.DNS_REFUSED: "The resolver refused the query.",
    ErrorCategory.DNS_FAILURE: "The operating system could not resolve the hostname.",
    ErrorCategory.DNS_CONFIGURATION: "No usable system DNS resolver is configured.",
    ErrorCategory.CONNECTION_REFUSED: "Connection refused. Check the endpoint service and port.",
    ErrorCategory.NETWORK_UNREACHABLE: "The operating system reported an unreachable endpoint.",
    ErrorCategory.TIMEOUT: "The diagnostic did not finish before its configured deadline.",
    ErrorCategory.CANCELLED: "The operation stopped at a supported cancellation boundary.",
    ErrorCategory.HTTP_MALFORMED: "The server returned an invalid or incomplete HTTP response.",
    ErrorCategory.TLS_CERTIFICATE: "Certificate chain verification failed. Check the CA trust.",
    ErrorCategory.TLS_HOSTNAME: "The certificate does not match the selected hostname or IP.",
    ErrorCategory.TLS_FAILURE: "TLS failed. Check the endpoint and its supported TLS versions.",
    ErrorCategory.TLS_CONFIGURATION: "The TLS trust configuration could not be loaded.",
    ErrorCategory.REMOTE_CLOSED: "The peer closed the socket before the operation could finish.",
}


@dataclass(frozen=True, slots=True)
class DiagnosticRequest:
    target: Target
    options: ProbeOptions
    method: str = "GET"
    payload: bytes = field(default=b"NetCraft echo", repr=False)

    def __post_init__(self) -> None:
        validate_timeout(self.options.timeout)
        if self.method not in {"GET", "HEAD"}:
            raise ValueError("Choose GET or HEAD.")
        if len(self.payload) > MAX_ECHO_PAYLOAD:
            raise ValueError(f"The UDP echo payload must be at most {MAX_ECHO_PAYLOAD} bytes.")


@dataclass(frozen=True, slots=True)
class DiagnosticView:
    target: str
    protocol: MonitorProtocol
    outcome: str
    explanation: str
    error: str | None
    timing_ns: dict[str, int | None]
    metadata: dict[str, object]
    started_at: str

    def sanitized(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "target": self.target,
            "protocol": self.protocol,
            "outcome": self.outcome,
            "explanation": self.explanation,
            "error_category": self.error,
            "timing_ns": self.timing_ns,
            "metadata": self.metadata,
            "started_at": self.started_at,
        }


def _outcome(error: NetworkError | None, protocol: MonitorProtocol) -> tuple[str, str]:
    if error is None:
        return "Success", "The selected endpoint returned the expected diagnostic observation."
    category = error.category
    if category is ErrorCategory.TIMEOUT and protocol is MonitorProtocol.UDP:
        return "Timeout", "No reply arrived. UDP silence alone does not establish network failure."
    status = {
        ErrorCategory.TIMEOUT: "Timeout",
        ErrorCategory.CANCELLED: "Cancelled",
    }.get(category, "Failed")
    return status, _ERROR_HELP.get(category, "The operation failed at the reported protocol stage.")


def _redirect_label(location: str | None) -> str | None:
    if location is None:
        return None
    if not location.startswith(("http://", "https://", "//")):
        return "Relative location (path omitted)"
    parts = urlsplit(location)
    return f"{parts.scheme or 'same-scheme'}://{parts.netloc}"


def _http_metadata(result: HTTPResult) -> dict[str, object]:
    info: dict[str, object] = {"method": result.method}
    if result.endpoint is not None:
        info["resolved_address"] = result.endpoint.address
        info["port"] = result.endpoint.port
    if result.response is not None:
        response = result.response
        info.update(
            http_status=response.status,
            version=response.version,
            advertised_content_length=response.content_length,
            body_bytes_read=response.body_bytes_read,
            body_complete=response.body_complete,
            body_limit_reached=response.body_limit_reached,
            redirect=_redirect_label(response.redirect_target),
        )
    if result.tls is not None:
        tls = result.tls
        info["tls"] = {
            "version": tls.version,
            "cipher": tls.cipher,
            "alpn": tls.alpn,
            "server_name": tls.verified_server_name,
            "subject": tls.subject,
            "issuer": tls.issuer,
            "not_before": tls.not_before,
            "not_after": tls.not_after,
            "hostname_verified": tls.hostname_verified,
            "chain_verified": tls.chain_verified,
        }
    return info


def _dns_metadata(result: DNSResult) -> dict[str, object]:
    return {
        "query": result.query_name,
        "record_type": result.query_type,
        "dns_status": result.status,
        "response_code": result.response_code,
        "resolver": result.resolver_label,
        "resolver_mode": result.resolver_mode,
        "fallback_occurred": result.fallback_occurred,
        "record_count": len(result.records),
        "alias_count": len(result.aliases),
        "answers": [
            {"name": row.name, "type": row.type, "ttl": row.ttl, "data": row.data[:1024]}
            for row in (*result.aliases[:40], *result.records[:100])
        ],
        "records_limited": len(result.aliases) > 40
        or len(result.records) > 100
        or any(len(row.data) > 1024 for row in (*result.aliases, *result.records)),
    }


def execute_diagnostic(request: DiagnosticRequest, control: JobControl) -> DiagnosticView:
    target, options = request.target, request.options
    protocol = target.protocol
    result: DNSResult | HTTPResult | NetworkResult
    observations: dict[str, int | None] = {}
    metadata: dict[str, object] = {}
    with control.network() as token:
        if protocol is MonitorProtocol.DNS:
            result = inspect_dns(
                target.destination,
                target.record_type,
                resolver=options.resolver,
                port=options.resolver_port,
                timeout=options.timeout,
                lifetime=options.timeout,
                cancel=token,
            )
            metadata = _dns_metadata(result)
        elif protocol in {MonitorProtocol.HTTP, MonitorProtocol.HTTPS}:
            timeout = options.timeout
            result = inspect_http(
                target.destination,
                method=request.method,
                timeouts=HTTPTimeouts(timeout, timeout, timeout, timeout),
                body_limit=DEFAULT_BODY_LIMIT,
                ca_file=options.ca_file,
                cancel=token,
            )
            timings = result.timings
            observations.update(
                dns=timings.dns_ns,
                tcp=timings.tcp_connect_ns,
                tls=timings.tls_handshake_ns,
                request_send=timings.request_send_ns,
                first_byte=timings.ttfb_ns,
                headers=timings.headers_read_ns,
                body_read=timings.body_read_ns,
            )
            metadata = _http_metadata(result)
        else:
            assert target.port is not None
            if protocol is MonitorProtocol.TCP:
                result = connect_tcp(
                    target.destination, target.port, timeout=options.timeout, cancel=token
                )
            else:
                result = exchange_datagram(
                    target.destination,
                    target.port,
                    request.payload,
                    timeout=options.timeout,
                    cancel=token,
                )
            if result.details is not None:
                details = result.details
                observations.update(tcp=details.connect_ns, udp_exchange=details.exchange_ns)
                metadata.update(
                    resolved_address=details.peer.address,
                    port=details.peer.port,
                    bytes_sent=details.bytes_sent,
                    bytes_received=details.bytes_received,
                    echo_matches=details.response == request.payload
                    if protocol is MonitorProtocol.UDP
                    else None,
                )

    error = result.error
    if protocol is MonitorProtocol.UDP and metadata.get("echo_matches") is False:
        error = NetworkError(ErrorCategory.UNEXPECTED_RESPONSE, "receive", "Echo mismatch")
    outcome, explanation = _outcome(error, protocol)
    if error is not None:
        metadata.update(error_stage=error.stage, os_code=error.os_code)
    elif isinstance(result, DNSResult) and not result.records:
        outcome, explanation = (
            "Empty answer",
            "The resolver replied with no records of the requested type.",
        )
    elif isinstance(result, HTTPResult) and result.response is not None:
        status = result.response.status
        outcome = "HTTP response" if status < 400 else "HTTP error status"
        explanation = f"HTTP {status} was received. The network exchange completed."
    observations["total"] = result.duration_ns if outcome != "Cancelled" else None
    return DiagnosticView(
        target.label,
        protocol,
        outcome,
        explanation,
        error.category if error else None,
        observations,
        metadata,
        utc_text(result.started_at),
    )


def dns_record(value: str) -> RecordType:
    return RecordType(value)
