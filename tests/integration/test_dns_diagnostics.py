import errno
import json
import socket
from ipaddress import ip_address
from threading import Event, Thread, current_thread

import dns.flags
import dns.message
import dns.nameserver
import dns.rcode
import dns.resolver
import dns.rrset
import pytest

from netcraft.core.execution import CancellationToken, DiagnosticRunner
from netcraft.diagnostic_models import DNSResult, DNSStatus
from netcraft.models import ErrorCategory
from netcraft.protocols import dns as dns_protocol
from netcraft.protocols.dns import inspect_dns
from tests.protocol_fixtures import dns_fixture


@pytest.mark.parametrize(
    ("name", "rtype", "data"),
    [
        ("host.example.test", "A", "192.0.2.17"),
        ("host.example.test", "AAAA", "2001:db8::17"),
        ("host.example.test", "CNAME", "alias.example.test."),
        ("example.test", "MX", "10 mail.example.test."),
        ("example.test", "NS", "ns.example.test."),
        ("example.test", "TXT", '"one" "two"'),
        ("example.test", "SOA", "ns.example.test. host.example.test. 1 3600 600 86400 60"),
        ("example.test", "CAA", '0 issue "ca.example.test"'),
        ("_test._tcp.example.test", "SRV", "10 20 1234 host.example.test."),
        ("192.0.2.17", "PTR", "host.example.test."),
        ("2001:db8::17", "PTR", "host.example.test."),
    ],
)
def test_explicit_record_types(name: str, rtype: str, data: str) -> None:
    expected_name = ip_address(name).reverse_pointer + "." if rtype == "PTR" else name + "."

    def handler(query: dns.message.QueryMessage, tcp: bool) -> bytes:
        assert not tcp
        assert query.question[0].name.to_text() == expected_name
        answer = dns.message.make_response(query)
        answer.answer.append(dns.rrset.from_text(expected_name, 71, "IN", rtype, data))
        return answer.to_wire()

    with dns_fixture(handler) as peer:
        result = inspect_dns(name, rtype.lower(), resolver=peer.address, port=peer.port)
    assert result.status is DNSStatus.ANSWER, result.error
    assert result.query_name == expected_name
    assert result.resolver == peer
    assert result.response_code == "NOERROR"
    assert result.duration_ns > 0
    (record,) = result.records
    assert record.data == data and record.ttl == 71
    assert record.preference == (10 if rtype == "MX" else None)
    assert json.loads(json.dumps(result.to_dict()))["records"][0]["type"] == rtype


@pytest.mark.parametrize(
    ("code", "status", "category"),
    [
        (dns.rcode.NOERROR, DNSStatus.EMPTY, None),
        (dns.rcode.NXDOMAIN, DNSStatus.FAILURE, ErrorCategory.DNS_NXDOMAIN),
        (dns.rcode.SERVFAIL, DNSStatus.FAILURE, ErrorCategory.DNS_SERVFAIL),
        (dns.rcode.REFUSED, DNSStatus.FAILURE, ErrorCategory.DNS_REFUSED),
        (dns.rcode.FORMERR, DNSStatus.FAILURE, ErrorCategory.DNS_RESPONSE_ERROR),
    ],
)
def test_rcodes(code: dns.rcode.Rcode, status: DNSStatus, category: ErrorCategory | None) -> None:
    def handler(query: dns.message.QueryMessage, tcp: bool) -> bytes:
        response = dns.message.make_response(query)
        response.set_rcode(code)
        return response.to_wire()

    with dns_fixture(handler) as peer:
        result = inspect_dns("empty.example.test", resolver=peer.address, port=peer.port)
    assert result.response_code == dns.rcode.to_text(code)
    assert result.status is status
    assert (result.error.category if result.error is not None else None) is category
    assert not result.records


@pytest.mark.parametrize("bad_transaction", [False, True])
def test_bad_dns_wire_or_transaction(bad_transaction: bool) -> None:
    def handler(query: dns.message.QueryMessage, tcp: bool) -> bytes:
        if not bad_transaction:
            return b"bad response"
        message = dns.message.make_response(query)
        message.id = (message.id + 1) % 65536
        return message.to_wire()

    with dns_fixture(handler) as peer:
        result = inspect_dns("host.example.test", resolver=peer.address, port=peer.port)
    assert result.error is not None
    assert result.error.category is ErrorCategory.DNS_MALFORMED
    assert result.response_code is None


def test_tcp_fallback_and_canonical_chain() -> None:
    def handler(query: dns.message.QueryMessage, tcp: bool) -> bytes:
        message = dns.message.make_response(query)
        if not tcp:
            message.flags |= dns.flags.TC
        else:
            message.answer.extend(
                [
                    dns.rrset.from_text(
                        "host.example.test.", 30, "IN", "CNAME", "real.example.test."
                    ),
                    dns.rrset.from_text("real.example.test.", 90, "IN", "A", "192.0.2.1"),
                ]
            )
        return message.to_wire()

    with dns_fixture(handler) as peer:
        result = inspect_dns("host.example.test", resolver=peer.address, port=peer.port)
    assert result.success, result.error
    assert [attempt.transport for attempt in result.attempts] == ["udp", "tcp"]
    assert result.canonical_name == "real.example.test."
    assert result.aliases[0].ttl == 30
    assert result.records[0].ttl == 90


def test_dns_timeout_respects_lifetime() -> None:
    with dns_fixture(lambda _query, _tcp: None) as peer:
        result = inspect_dns(
            "silent.example.test", resolver=peer.address, port=peer.port, lifetime=0.075
        )
    assert result.error is not None
    assert result.error.category is ErrorCategory.TIMEOUT
    assert result.error.stage == "dns_receive"
    assert 60_000_000 <= result.duration_ns < 1_500_000_000


def test_ipv6_resolver_transport() -> None:
    try:
        with socket.socket(socket.AF_INET6, socket.SOCK_DGRAM) as check:
            check.bind(("::1", 0))
    except OSError as exc:
        if exc.errno in {errno.EAFNOSUPPORT, errno.EPROTONOSUPPORT, errno.EADDRNOTAVAIL} or (
            getattr(exc, "winerror", None) in {10043, 10047, 10049}
        ):
            pytest.skip(f"IPv6 loopback unavailable: {exc}")
        raise

    def handler(query: dns.message.QueryMessage, tcp: bool) -> bytes:
        message = dns.message.make_response(query)
        message.answer.append(
            dns.rrset.from_text("host.example.test.", 60, "IN", "AAAA", "2001:db8::1")
        )
        return message.to_wire()

    with dns_fixture(handler, host="::1") as peer:
        result = inspect_dns("host.example.test", "AAAA", resolver=peer.address, port=peer.port)
    assert result.success and result.resolver == peer
    assert result.records[0].data == "2001:db8::1"


def test_system_resolver_and_bounded_failover(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dns_protocol, "_WINDOWS_SYSTEM", False)

    def failure(query: dns.message.QueryMessage, tcp: bool) -> bytes:
        message = dns.message.make_response(query)
        message.set_rcode(dns.rcode.SERVFAIL)
        return message.to_wire()

    with (
        dns_fixture(failure) as first,
        dns_fixture(lambda query, tcp: dns.message.make_response(query).to_wire()) as second,
    ):
        config = dns.resolver.Resolver(configure=False)
        config.nameservers = [
            dns.nameserver.Do53Nameserver(first.address, first.port),
            dns.nameserver.Do53Nameserver(second.address, second.port),
        ]
        monkeypatch.setattr(dns.resolver, "Resolver", lambda: config)
        result = inspect_dns("host.example.test")
    assert result.configured_resolvers == (first, second)
    assert result.resolver == second and result.status is DNSStatus.EMPTY
    assert result.fallback_occurred is True
    assert [attempt.response_code for attempt in result.attempts] == ["SERVFAIL", "NOERROR"]


def test_dns_cancel_and_fixture_cleanup() -> None:
    participants: list[Thread] = []
    received = Event()

    def handler(query: dns.message.QueryMessage, tcp: bool) -> None:
        participants.append(current_thread())
        received.set()

    with dns_fixture(handler) as peer:

        def probe(token: CancellationToken) -> DNSResult:
            participants.append(current_thread())
            return inspect_dns(
                "host.example.test", resolver=peer.address, port=peer.port, cancel=token
            )

        with DiagnosticRunner(1) as runner:
            future = runner.submit(probe)
            assert received.wait(2)
        result = future.result(timeout=1)
    assert result.error is not None and result.error.category is ErrorCategory.CANCELLED
    assert len(set(participants)) == 2
    assert all(not thread.is_alive() for thread in participants)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as former:
        former.bind((peer.address, peer.port))


@pytest.mark.parametrize(
    ("name", "rtype", "resolver", "port", "category"),
    [
        ("a..test", "A", None, None, ErrorCategory.INVALID_HOST),
        ("test", "AXFR", None, None, ErrorCategory.INVALID_DNS_TYPE),
        ("test", "A", "resolver.test", None, ErrorCategory.INVALID_RESOLVER),
        ("test", "A", None, 53, ErrorCategory.INVALID_RESOLVER),
        ("test", "A", "127.0.0.1", 0, ErrorCategory.INVALID_PORT),
    ],
)
def test_dns_input_validation(
    name: str, rtype: str, resolver: str | None, port: int | None, category: ErrorCategory
) -> None:
    result = inspect_dns(name, rtype, resolver=resolver, port=port)
    assert result.error is not None and result.error.category is category
    assert not result.attempts
