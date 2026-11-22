"""One bounded DNS record query over the system or explicitly selected resolver."""

import ipaddress
import re
import socket
import sys
from dataclasses import dataclass

import dns.exception
import dns.flags
import dns.message
import dns.name
import dns.nameserver
import dns.rcode
import dns.rdatatype
import dns.resolver
import dns.rrset
from dns.rdtypes.ANY.MX import MX

from netcraft.core.config import validate_port, validate_timeout
from netcraft.core.errors import NetworkFailure, classify_socket_error
from netcraft.core.execution import CancellationToken
from netcraft.core.socket_io import SocketIO
from netcraft.core.timing import OperationClock
from netcraft.core.transport import Deadline, connect_addresses, resolve_addresses
from netcraft.diagnostic_models import (
    DNSAttempt,
    DNSRecord,
    DNSResolverMode,
    DNSResult,
    DNSStatus,
    RecordType,
)
from netcraft.models import ErrorCategory, NetworkError, Peer
from netcraft.protocols.dns_windows import query_system

_LABEL = re.compile(r"[A-Za-z0-9_-]{1,63}")
_MAX_RESOLVERS = 3
_WINDOWS_SYSTEM = sys.platform == "win32"
_WINDOWS_NO_RECORDS = 9501
_WINDOWS_RCODE_STATUSES = {*range(9001, 9011), 9016, 9017, 9018}
_IDENTIFIED_ERRORS = {
    dns.rcode.NXDOMAIN: ErrorCategory.DNS_NXDOMAIN,
    dns.rcode.SERVFAIL: ErrorCategory.DNS_SERVFAIL,
    dns.rcode.REFUSED: ErrorCategory.DNS_REFUSED,
}
_RETRYABLE = {
    ErrorCategory.TIMEOUT,
    ErrorCategory.CONNECTION_REFUSED,
    ErrorCategory.NETWORK_UNREACHABLE,
    ErrorCategory.REMOTE_CLOSED,
    ErrorCategory.DNS_MALFORMED,
    ErrorCategory.DNS_SERVFAIL,
    ErrorCategory.DNS_REFUSED,
}


@dataclass(frozen=True, slots=True)
class _Answer:
    canonical_name: str
    records: tuple[DNSRecord, ...]
    aliases: tuple[DNSRecord, ...]
    authoritative: bool | None


def parse_dns_question(name: str, record_type: RecordType) -> dns.name.Name:
    try:
        address = ipaddress.ip_address(name)
    except ValueError:
        try:
            raw = name.encode("idna").decode("ascii")
            if raw == "." or all(
                _LABEL.fullmatch(part) for part in raw.removesuffix(".").split(".")
            ):
                return dns.name.from_text(raw)
        except (dns.exception.DNSException, UnicodeError):
            pass
    else:
        if record_type is RecordType.PTR and "%" not in name:
            return dns.name.from_text(address.reverse_pointer)
    raise NetworkFailure(
        ErrorCategory.INVALID_HOST, "validation", "Use a DNS name or an unscoped IP for PTR."
    )


def _resolvers(resolver: str | None, port: int | None) -> tuple[Peer, ...]:
    if resolver is not None:
        try:
            address = str(ipaddress.ip_address(resolver))
        except ValueError as exc:
            raise NetworkFailure(
                ErrorCategory.INVALID_RESOLVER,
                "validation",
                "Resolver must be an IPv4 or IPv6 address.",
            ) from exc
        return (Peer(address, port if port is not None else 53),)
    if port is not None:
        raise NetworkFailure(
            ErrorCategory.INVALID_RESOLVER,
            "validation",
            "A resolver port requires an explicit resolver.",
        )
    try:
        config = dns.resolver.Resolver()
        configured = tuple(
            dict.fromkeys(
                Peer(
                    str(ipaddress.ip_address(server)),
                    config.nameserver_ports.get(server, config.port),
                )
                if isinstance(server, str)
                else Peer(str(ipaddress.ip_address(server.address)), server.port)
                for server in config.nameservers
                if isinstance(server, (str, dns.nameserver.Do53Nameserver))
            )
        )
        if configured:
            return configured
    except (dns.exception.DNSException, OSError, ValueError) as exc:
        raise NetworkFailure(
            ErrorCategory.DNS_CONFIGURATION,
            "dns_configuration",
            "Cannot read system DNS configuration.",
        ) from exc
    raise NetworkFailure(
        ErrorCategory.DNS_CONFIGURATION, "dns_configuration", "No plain DNS resolver is configured."
    )


def _records(rrset: dns.rrset.RRset | None, record_type: RecordType) -> tuple[DNSRecord, ...]:
    if rrset is None:
        return ()
    return tuple(
        DNSRecord(
            rrset.name.to_text(),
            record_type,
            rrset.ttl,
            item.to_text(),
            item.preference if isinstance(item, MX) else None,
        )
        for item in rrset
    )


def _wire_exchange(
    resolver: Peer,
    wire: bytes,
    transport: str,
    deadline: Deadline,
    cancel: CancellationToken | None,
) -> bytes:
    tcp = transport == "tcp"
    kind = socket.SOCK_STREAM if tcp else socket.SOCK_DGRAM
    addresses = resolve_addresses(resolver.address, resolver.port, kind, True, cancel)
    sock, _, _ = connect_addresses(addresses, kind, deadline, cancel)
    with sock:
        sender = SocketIO(sock, deadline, cancel, "dns_send")
        reader = SocketIO(sock, deadline, cancel, "dns_receive")
        if tcp:
            sender.send_all(len(wire).to_bytes(2, "big") + wire)
            size = int.from_bytes(reader.receive_exact(2), "big")
            return reader.receive_exact(size)
        if sender.send(wire) != len(wire):
            raise NetworkFailure(ErrorCategory.SOCKET_ERROR, "dns_send", "Incomplete DNS datagram.")
        try:
            return reader.receive(65_535)
        except NetworkFailure as exc:
            if exc.error.os_code == 10054:
                raise NetworkFailure(
                    ErrorCategory.CONNECTION_REFUSED,
                    "dns_receive",
                    "Resolver port unreachable.",
                    10054,
                ) from exc
            raise


def _parse_response(
    wire: bytes, query: dns.message.QueryMessage, transport: str, *, system: bool = False
) -> dns.message.QueryMessage:
    try:
        try:
            message = dns.message.from_wire(wire, raise_on_truncation=True)
        except dns.message.Truncated:
            if transport == "tcp":
                raise
            message = dns.message.from_wire(wire, question_only=True)
        if isinstance(message, dns.message.QueryMessage):
            # Windows generated the transaction ID for its own query.
            matches = (
                bool(message.flags & dns.flags.QR)
                and message.opcode() == query.opcode()
                and message.question == query.question
                if system
                else query.is_response(message)
            )
            if matches:
                return message
    except (dns.exception.DNSException, ValueError) as exc:
        raise NetworkFailure(
            ErrorCategory.DNS_MALFORMED, "dns_response", "Invalid DNS wire response."
        ) from exc
    raise NetworkFailure(
        ErrorCategory.DNS_MALFORMED, "dns_response", "DNS response did not match the question."
    )


def _response_failure(rcode: dns.rcode.Rcode, os_code: int | None = None) -> NetworkFailure:
    return NetworkFailure(
        _IDENTIFIED_ERRORS.get(rcode, ErrorCategory.DNS_RESPONSE_ERROR),
        "dns_response",
        f"Resolver returned {dns.rcode.to_text(rcode)}.",
        os_code,
    )


def _answer(message: dns.message.QueryMessage, record_type: RecordType) -> _Answer:
    if message.rcode() != dns.rcode.NOERROR:
        raise _response_failure(message.rcode())
    try:
        chain = message.resolve_chaining()
    except dns.exception.DNSException as exc:
        raise NetworkFailure(
            ErrorCategory.DNS_MALFORMED, "dns_response", "Invalid DNS answer chain."
        ) from exc
    return _Answer(
        chain.canonical_name.to_text(),
        _records(chain.answer, record_type),
        tuple(record for rrset in chain.cnames for record in _records(rrset, RecordType.CNAME)),
        bool(message.flags & dns.flags.AA),
    )


def _system_failure(status: int) -> NetworkFailure:
    category = {
        13: ErrorCategory.DNS_MALFORMED,  # ERROR_INVALID_DATA
        258: ErrorCategory.TIMEOUT,
        995: ErrorCategory.CANCELLED,
        1223: ErrorCategory.CANCELLED,
        1231: ErrorCategory.NETWORK_UNREACHABLE,
        1232: ErrorCategory.NETWORK_UNREACHABLE,
        1460: ErrorCategory.TIMEOUT,
        9502: ErrorCategory.DNS_MALFORMED,  # DNS_ERROR_BAD_PACKET
        9852: ErrorCategory.DNS_CONFIGURATION,  # DNS_ERROR_NO_DNS_SERVERS
        10051: ErrorCategory.NETWORK_UNREACHABLE,
        10060: ErrorCategory.TIMEOUT,
        10065: ErrorCategory.NETWORK_UNREACHABLE,
    }.get(status, ErrorCategory.DNS_FAILURE)
    return NetworkFailure(category, "dns_system", f"Windows DNS Client status {status}.", status)


def _query_windows(
    query: dns.message.QueryMessage,
    record_type: RecordType,
    deadline: Deadline,
    cancel: CancellationToken | None,
    attempts: list[DNSAttempt],
) -> _Answer:
    question = query.question[0]
    resolver = None
    transport = "system"
    code = None
    try:
        reply = query_system(question.name.to_text(), question.rdtype, deadline, cancel)
        resolver, transport = reply.resolver, reply.transport
        if reply.malformed:
            raise NetworkFailure(
                ErrorCategory.DNS_MALFORMED, "dns_response", "Invalid Windows DNS wire response."
            )
        dns_status = reply.status in _WINDOWS_RCODE_STATUSES
        if reply.wire and (reply.status in {0, _WINDOWS_NO_RECORDS} or dns_status):
            message = _parse_response(reply.wire, query, "tcp", system=True)
            if dns_status and message.rcode() != reply.status - 9000:
                raise NetworkFailure(
                    ErrorCategory.DNS_MALFORMED,
                    "dns_response",
                    "Windows DNS status did not match the response code.",
                )
            code = dns.rcode.to_text(message.rcode())
            answer = _answer(message, record_type)
        elif reply.status == _WINDOWS_NO_RECORDS:
            code = "NOERROR"
            answer = _Answer(question.name.to_text(), (), (), None)
        else:
            if dns_status:
                rcode = dns.rcode.Rcode.make(reply.status - 9000)
                code = dns.rcode.to_text(rcode)
                raise _response_failure(rcode, reply.status)
            raise _system_failure(reply.status)
    except NetworkFailure as exc:
        attempts.append(DNSAttempt(resolver, transport, code, exc.error))
        raise
    attempts.append(DNSAttempt(resolver, transport, code, None))
    return answer


def _query_resolver(
    query: dns.message.QueryMessage,
    resolver: Peer,
    record_type: RecordType,
    deadline: Deadline,
    cancel: CancellationToken | None,
    attempts: list[DNSAttempt],
) -> _Answer:
    wire = query.to_wire()
    for transport in ("udp", "tcp"):
        code = None
        try:
            reply = _wire_exchange(resolver, wire, transport, deadline, cancel)
            message = _parse_response(reply, query, transport)
            rcode = message.rcode()
            code = dns.rcode.to_text(rcode)
            if message.flags & dns.flags.TC:
                attempts.append(DNSAttempt(resolver, transport, code, None))
                continue
            answer = _answer(message, record_type)
        except NetworkFailure as exc:
            attempts.append(DNSAttempt(resolver, transport, code, exc.error))
            raise
        attempts.append(DNSAttempt(resolver, transport, code, None))
        return answer
    raise AssertionError("A truncated TCP response must fail parsing.")


def inspect_dns(
    name: str,
    record_type: str = "A",
    *,
    resolver: str | None = None,
    port: int | None = None,
    timeout: float = 2.0,
    lifetime: float = 5.0,
    cancel: CancellationToken | None = None,
) -> DNSResult:
    """Query a single absolute IN name, using a bounded transport fallback if truncated."""
    clock = OperationClock()
    query_type = record_type.upper()
    query_name = None
    configured: tuple[Peer, ...] = ()
    attempts: list[DNSAttempt] = []
    answer = None
    error: NetworkError | None = None
    mode = DNSResolverMode.EXPLICIT
    if resolver is None:
        mode = (
            DNSResolverMode.WINDOWS_SYSTEM if _WINDOWS_SYSTEM else DNSResolverMode.SYSTEM_CONFIGURED
        )
    fallback: bool | None = None if mode is DNSResolverMode.WINDOWS_SYSTEM else False
    try:
        validate_timeout(timeout)
        validate_timeout(lifetime)
        overall = Deadline(clock.tick + max(1, int(lifetime * 1_000_000_000)))
        if port is not None:
            validate_port(port)
        try:
            rtype = RecordType(query_type)
        except ValueError as exc:
            raise NetworkFailure(
                ErrorCategory.INVALID_DNS_TYPE, "validation", "Unsupported DNS record type."
            ) from exc
        question = parse_dns_question(name, rtype)
        query_name = question.to_text()
        if cancel is not None:
            cancel.check("dns_configuration")
        query = dns.message.make_query(question, dns.rdatatype.from_text(rtype.value))
        if mode is DNSResolverMode.WINDOWS_SYSTEM:
            if port is not None:
                raise NetworkFailure(
                    ErrorCategory.INVALID_RESOLVER,
                    "validation",
                    "A resolver port requires an explicit resolver.",
                )
            answer = _query_windows(query, rtype, overall.capped(timeout), cancel, attempts)
        else:
            configured = _resolvers(resolver, port)
            for index, server in enumerate(configured[:_MAX_RESOLVERS]):
                if cancel is not None:
                    cancel.check("dns")
                overall.remaining("dns")
                fallback = index > 0
                try:
                    answer = _query_resolver(
                        query, server, rtype, overall.capped(timeout), cancel, attempts
                    )
                except NetworkFailure as exc:
                    error = exc.error
                    if error.category not in _RETRYABLE:
                        break
                else:
                    error = None
                    break
    except NetworkFailure as exc:
        error = exc.error
    except OSError as exc:
        error = classify_socket_error(exc, "dns")

    last = attempts[-1] if attempts else None
    status = DNSStatus.FAILURE if answer is None else DNSStatus.EMPTY
    if answer is not None and answer.records:
        status = DNSStatus.ANSWER
    return DNSResult(
        query_name,
        query_type,
        configured,
        tuple(attempts),
        last.resolver if last else None,
        last.response_code if last else None,
        answer.canonical_name if answer else None,
        answer.records if answer else (),
        answer.aliases if answer else (),
        answer.authoritative if answer else None,
        status,
        *clock.finish(),
        error,
        mode,
        fallback,
    )
