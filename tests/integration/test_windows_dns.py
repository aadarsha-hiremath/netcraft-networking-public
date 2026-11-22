"""Offline coverage of the Windows DNS Client boundary and the portable fallback."""

import ctypes
import socket
from collections.abc import Callable
from threading import Event
from threading import enumerate as active_threads
from types import SimpleNamespace
from typing import Any

import dns.message
import dns.nameserver
import dns.rcode
import dns.rdatatype
import dns.resolver
import dns.rrset
import pytest

from netcraft.core.execution import DiagnosticRunner
from netcraft.diagnostic_models import DNSResolverMode, DNSStatus
from netcraft.models import ErrorCategory, Peer
from netcraft.protocols import dns as protocol
from netcraft.protocols import dns_windows as windows
from tests.protocol_fixtures import dns_fixture


def _wire(name: str, kind: int, text: str) -> bytes:
    record_type = dns.rdatatype.RdataType(kind)
    query = dns.message.make_query(name, record_type, id=1)
    reply = dns.message.make_response(query)
    reply.answer.append(dns.rrset.from_text(name, 42, "IN", record_type, text))
    return reply.to_wire()


@pytest.fixture(autouse=True)
def windows_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(protocol, "_WINDOWS_SYSTEM", True)


@pytest.mark.parametrize(
    ("name", "record_type", "value", "source"),
    [
        ("lan.example.test.", "A", "192.0.2.10", Peer("192.0.2.53", 53)),
        ("private.vpn.example.test.", "AAAA", "2001:db8::10", Peer("fe80::53", 53, 13)),
        ("example.test.", "MX", "10 mail.example.test.", None),
    ],
)
def test_delegates_interface_and_split_dns_policy(
    monkeypatch: pytest.MonkeyPatch, name: str, record_type: str, value: str, source: Peer | None
) -> None:
    # The legacy list represents a disconnected adapter followed by an active
    # interface. Windows may select a different scoped/VPN server for a suffix.
    legacy = SimpleNamespace(nameservers=["192.0.2.99", "192.0.2.53"])
    configuration_reads = []

    def read_legacy() -> SimpleNamespace:
        configuration_reads.append(True)
        return legacy

    def os_policy(absolute_name: str, kind: int, *_: object) -> windows.SystemReply:
        assert absolute_name == name
        return windows.SystemReply(
            0, "udp" if source else "local", source, _wire(name, kind, value)
        )

    monkeypatch.setattr(dns.resolver, "Resolver", read_legacy)
    monkeypatch.setattr(protocol, "query_system", os_policy)
    result = protocol.inspect_dns(name, record_type)
    assert not configuration_reads
    assert result.resolver_mode is DNSResolverMode.WINDOWS_SYSTEM
    assert result.configured_resolvers == ()
    assert result.fallback_occurred is None
    assert result.resolver == source
    assert result.records[0].data == value
    assert result.resolver_label == (
        "System resolver"
        if source is None
        else ("fe80::53%13 port 53" if source.scope_id else "192.0.2.53 port 53")
    )


@pytest.mark.parametrize(
    ("status", "rcode", "outcome", "category"),
    [
        (9501, "NOERROR", DNSStatus.EMPTY, None),
        (9003, "NXDOMAIN", DNSStatus.FAILURE, ErrorCategory.DNS_NXDOMAIN),
        (9002, "SERVFAIL", DNSStatus.FAILURE, ErrorCategory.DNS_SERVFAIL),
        (1460, None, DNSStatus.FAILURE, ErrorCategory.TIMEOUT),
        (9852, None, DNSStatus.FAILURE, ErrorCategory.DNS_CONFIGURATION),
        (0, None, DNSStatus.FAILURE, ErrorCategory.DNS_FAILURE),
    ],
)
def test_native_status_mapping(
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    rcode: str | None,
    outcome: DNSStatus,
    category: ErrorCategory | None,
) -> None:
    monkeypatch.setattr(protocol, "query_system", lambda *_: windows.SystemReply(status))
    result = protocol.inspect_dns("host.example.test")
    assert (result.status, result.response_code) == (outcome, rcode)
    assert (result.error.category if result.error else None) is category
    assert len(result.attempts) == 1 and result.resolver_label == "System resolver"


def test_native_question_is_validated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        protocol,
        "query_system",
        lambda *_: windows.SystemReply(0, wire=_wire("other.example.test.", 1, "192.0.2.10")),
    )
    result = protocol.inspect_dns("host.example.test")
    assert result.error and result.error.category is ErrorCategory.DNS_MALFORMED


def test_explicit_server_never_uses_windows_client(monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected(*_: object) -> windows.SystemReply:
        raise AssertionError("explicit resolver called the Windows DNS Client")

    monkeypatch.setattr(protocol, "query_system", unexpected)
    with dns_fixture(lambda query, _: dns.message.make_response(query).to_wire()) as server:
        result = protocol.inspect_dns(
            "host.example.test", resolver=server.address, port=server.port
        )
    assert result.success and result.resolver == server and result.fallback_occurred is False


@pytest.mark.parametrize("available", [True, False])
def test_portable_timeout_and_eligible_fallback(
    monkeypatch: pytest.MonkeyPatch, available: bool
) -> None:
    monkeypatch.setattr(protocol, "_WINDOWS_SYSTEM", False)

    def alternate(query: dns.message.QueryMessage, _: bool) -> bytes | None:
        return dns.message.make_response(query).to_wire() if available else None

    with dns_fixture(lambda *_: None) as primary, dns_fixture(alternate) as secondary:
        configuration = dns.resolver.Resolver(configure=False)
        configuration.nameservers = [
            dns.nameserver.Do53Nameserver(peer.address, peer.port) for peer in (primary, secondary)
        ]
        monkeypatch.setattr(dns.resolver, "Resolver", lambda: configuration)
        result = protocol.inspect_dns("host.example.test", timeout=0.06, lifetime=0.4)
    assert result.fallback_occurred is True
    assert [item.resolver for item in result.attempts] == [primary, secondary]
    assert result.success is available
    assert result.attempts[0].error and result.attempts[0].error.category is ErrorCategory.TIMEOUT
    if not available:
        assert result.error and result.error.category is ErrorCategory.TIMEOUT


class _FakeFunction:
    def __init__(self, call: Callable[..., Any]) -> None:
        self.call = call

    def __call__(self, *args: Any) -> Any:
        return self.call(*args)


class _FakeWinDNS:
    def __init__(self) -> None:
        self.started = Event()
        self.cancellations = 0
        self.frees = 0
        self.context: tuple[int, int] | None = None
        self.DnsQueryRaw = _FakeFunction(self._query)
        self.DnsCancelQueryRaw = _FakeFunction(self._cancel)
        self.DnsQueryRawResultFree = _FakeFunction(self._free)

    def _query(self, request_pointer: Any, _: object) -> int:
        request = ctypes.cast(request_pointer, ctypes.POINTER(windows._Request)).contents
        assert request.version == request.results_version == 1
        assert request.name == "host.example.test." and request.type == 1
        assert request.interface_index == request.custom_server_count == request.query_options == 0
        assert request.custom_servers is None and request.protocol == 2
        self.context = request.callback, request.context
        self.started.set()
        return windows._DNS_REQUEST_PENDING

    def _cancel(self, _: object) -> int:
        self.cancellations += 1
        return 0

    def _free(self, _: object) -> None:
        self.frees += 1

    def complete(self, result: windows._Result) -> None:
        assert self.context is not None
        address, key = self.context
        callback = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.POINTER(windows._Result))(address)
        callback(key, ctypes.byref(result))
        self.context = None


def test_native_deadline_cancellation_and_late_callback_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _FakeWinDNS()
    client = windows._Client(api, ctypes.CFUNCTYPE)
    monkeypatch.setattr(protocol, "query_system", client.query)
    before = set(active_threads())
    sockets: list[socket.socket] = []
    socketpair = socket.socketpair

    def track_socketpair() -> tuple[socket.socket, socket.socket]:
        pair = socketpair()
        sockets.extend(pair)
        return pair

    monkeypatch.setattr(socket, "socketpair", track_socketpair)
    for _ in range(8):
        result = protocol.inspect_dns("host.example.test", timeout=1, lifetime=0.025)
        assert result.error and result.error.category is ErrorCategory.TIMEOUT
        assert 20_000_000 <= result.duration_ns < 800_000_000
        assert len(client._pending) == 1
        api.complete(windows._Result(status=1223))
        assert not client._pending
    api.started.clear()
    with DiagnosticRunner(1) as runner:
        future = runner.submit(
            lambda token: protocol.inspect_dns("host.example.test", cancel=token)
        )
        assert api.started.wait(1)
    result = future.result(timeout=1)
    assert result.error and result.error.category is ErrorCategory.CANCELLED
    api.complete(windows._Result(status=1223))
    assert api.cancellations == api.frees == 9
    assert not client._pending
    assert all(sock.fileno() == -1 for sock in sockets)
    assert not (set(active_threads()) - before)


def test_native_result_framing_ipv4_ipv6_and_no_wire_source() -> None:
    packet = _wire("host.example.test.", 1, "192.0.2.10")
    framed = ctypes.create_string_buffer(len(packet).to_bytes(2, "big") + packet)
    reply = windows._Result(
        status=0, response_size=len(packet) + 2, response=ctypes.addressof(framed), protocol=1
    )
    reply.source[:8] = bytes.fromhex("02000035c0000235")
    assert windows._copy_result(reply) == windows.SystemReply(
        0, "udp", Peer("192.0.2.53", 53), packet
    )
    reply.source[:28] = bytes.fromhex("1700035500000000fe8000000000000000000000000000530d000000")
    reply.protocol = 4
    assert windows._copy_result(reply).resolver == Peer("fe80::53", 853, 13)
    reply.protocol = 5
    assert windows._copy_result(reply).resolver is None
    reply.response_size -= 1
    assert windows._copy_result(reply).malformed


def test_native_completion_wakes_worker_and_frees_result(monkeypatch: pytest.MonkeyPatch) -> None:
    api = _FakeWinDNS()
    client = windows._Client(api, ctypes.CFUNCTYPE)
    monkeypatch.setattr(protocol, "query_system", client.query)
    packet = _wire("host.example.test.", 1, "192.0.2.10")
    framed = ctypes.create_string_buffer(len(packet).to_bytes(2, "big") + packet)
    reply = windows._Result(
        status=0, response_size=len(packet) + 2, response=ctypes.addressof(framed), protocol=5
    )
    with DiagnosticRunner(1) as runner:
        future = runner.submit(
            lambda token: protocol.inspect_dns("host.example.test", cancel=token)
        )
        assert api.started.wait(1)
        api.complete(reply)
        result = future.result(timeout=1)
    assert result.success and result.records[0].data == "192.0.2.10"
    assert result.resolver_label == "System resolver"
    assert api.cancellations == 0 and api.frees == 1
    assert not client._pending
