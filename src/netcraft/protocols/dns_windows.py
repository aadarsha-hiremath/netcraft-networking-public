"""Bounded, cancellable access to the Windows DNS Client wire response API."""

import ctypes
import ipaddress
import itertools
import socket
import sys
import threading
from dataclasses import dataclass, field
from functools import cache
from typing import Any

from netcraft.core.errors import NetworkFailure
from netcraft.core.execution import CancellationToken
from netcraft.core.transport import Deadline, wait_ready
from netcraft.models import ErrorCategory, Peer

_STAGE = "dns_system"
_DNS_REQUEST_PENDING = 9506
_MAX_PENDING = 32
_PROTOCOLS = {1: "udp", 2: "tcp", 3: "doh", 4: "dot", 5: "local"}
_TCP = 2
_LOAD_LIBRARY_SEARCH_SYSTEM32 = 0x00000800
_client_lock = threading.Lock()


class _Request(ctypes.Structure):
    _fields_ = [
        ("version", ctypes.c_uint32),
        ("results_version", ctypes.c_uint32),
        ("raw_size", ctypes.c_uint32),
        ("raw_query", ctypes.c_void_p),
        ("name", ctypes.c_wchar_p),
        ("type", ctypes.c_uint16),
        ("query_options", ctypes.c_uint64),
        ("interface_index", ctypes.c_uint32),
        ("callback", ctypes.c_void_p),
        ("context", ctypes.c_void_p),
        ("raw_options", ctypes.c_uint64),
        ("custom_server_count", ctypes.c_uint32),
        ("custom_servers", ctypes.c_void_p),
        ("protocol", ctypes.c_uint32),
        ("source", ctypes.c_ubyte * 32),
    ]


class _Result(ctypes.Structure):
    _fields_ = [
        ("version", ctypes.c_uint32),
        ("status", ctypes.c_int32),
        ("query_options", ctypes.c_uint64),
        ("raw_options", ctypes.c_uint64),
        ("response_flags", ctypes.c_uint64),
        ("response_size", ctypes.c_uint32),
        ("response", ctypes.c_void_p),
        ("records", ctypes.c_void_p),
        ("protocol", ctypes.c_uint32),
        ("source", ctypes.c_ubyte * 32),
    ]


class _Cancel(ctypes.Structure):
    _fields_ = [("reserved", ctypes.c_ubyte * 32)]


@dataclass(frozen=True, slots=True)
class SystemReply:
    status: int
    transport: str = "system"
    resolver: Peer | None = None
    wire: bytes = b""
    malformed: bool = False


def _peer(source: bytes) -> Peer | None:
    family = int.from_bytes(source[:2], "little")
    port = int.from_bytes(source[2:4], "big")
    if family == 2:
        address = ipaddress.ip_address(source[4:8])
        scope = 0
    elif family == 23:  # Windows AF_INET6
        address = ipaddress.ip_address(source[8:24])
        scope = int.from_bytes(source[24:28], "little")
    else:
        return None
    return Peer(str(address), port, scope) if port and not address.is_unspecified else None


def _copy_result(result: _Result) -> SystemReply:
    transport = _PROTOCOLS.get(result.protocol, "system")
    peer = _peer(bytes(result.source)) if transport not in {"local", "system"} else None
    if not result.response_size:
        return SystemReply(result.status, transport, peer)
    if result.response_size > 65_537 or not result.response:
        return SystemReply(result.status, transport, peer, malformed=True)
    framed = ctypes.string_at(result.response, result.response_size)
    if len(framed) < 14 or int.from_bytes(framed[:2], "big") != len(framed) - 2:
        return SystemReply(result.status, transport, peer, malformed=True)
    return SystemReply(result.status, transport, peer, framed[2:])


@dataclass(slots=True)
class _Pending:
    writer: socket.socket | None
    request: _Request
    handle: _Cancel = field(default_factory=_Cancel)
    lock: threading.Lock = field(default_factory=threading.Lock)
    reply: SystemReply | None = None

    def complete(self, reply: SystemReply) -> None:
        with self.lock:
            self.reply = reply
            if self.writer is not None:
                self.writer.send(b"\x01")

    def detach(self) -> bool:
        with self.lock:
            self.writer = None
            return self.reply is None


class _Client:
    def __init__(self, library: Any, callback_factory: Any) -> None:
        self._query = library.DnsQueryRaw
        self._query.argtypes = [ctypes.POINTER(_Request), ctypes.POINTER(_Cancel)]
        self._query.restype = ctypes.c_int32
        self._cancel = library.DnsCancelQueryRaw
        self._cancel.argtypes = [ctypes.POINTER(_Cancel)]
        self._cancel.restype = ctypes.c_int32
        self._free = library.DnsQueryRawResultFree
        self._free.argtypes = [ctypes.POINTER(_Result)]
        self._free.restype = None
        self._callback = callback_factory(None, ctypes.c_void_p, ctypes.POINTER(_Result))(
            self._complete
        )
        self._pending: dict[int, _Pending] = {}
        self._lock = threading.Lock()
        self._ids = itertools.count(1)

    def _complete(self, key: int, result: Any) -> None:
        try:
            reply = _copy_result(result.contents) if result else SystemReply(0, malformed=True)
        except (ValueError, OSError):
            reply = SystemReply(0, malformed=True)
        finally:
            if result:
                self._free(result)
        with self._lock:
            pending = self._pending.pop(key, None)
        if pending is not None:
            pending.complete(reply)

    def query(
        self, name: str, record_type: int, deadline: Deadline, cancel: CancellationToken | None
    ) -> SystemReply:
        if cancel is not None:
            cancel.check(_STAGE)
        deadline.remaining(_STAGE)
        reader, writer = socket.socketpair()
        with reader, writer:
            request = _Request()
            request.version = request.results_version = 1
            request.name = name
            request.type = record_type
            # Request TCP framing for a full-sized reply. Windows chooses its own
            # upstream transport and interface. No custom servers are supplied.
            request.protocol = _TCP
            request.callback = ctypes.cast(self._callback, ctypes.c_void_p).value
            pending = _Pending(writer, request)
            with self._lock:
                if len(self._pending) >= _MAX_PENDING:
                    raise NetworkFailure(
                        ErrorCategory.DNS_FAILURE, _STAGE, "Windows DNS Client is busy."
                    )
                key = next(self._ids)
                request.context = key
                self._pending[key] = pending

            waiting = False
            try:
                status: int = self._query(ctypes.byref(request), ctypes.byref(pending.handle))
                waiting = status == _DNS_REQUEST_PENDING
                if not waiting:
                    if cancel is not None:
                        cancel.check(_STAGE)
                    deadline.remaining(_STAGE)
                    return SystemReply(status, malformed=status == 0)
                wait_ready(reader, _STAGE, deadline, cancel)
                assert pending.reply is not None
                return pending.reply
            finally:
                if not waiting:
                    with self._lock:
                        self._pending.pop(key, None)
                elif pending.detach():
                    # The handle remains alive in the bounded registry until Windows
                    # calls back. Never cancel while holding a callback lock.
                    self._cancel(ctypes.byref(pending.handle))


@cache
def _client() -> _Client:
    if sys.platform == "win32":
        try:
            library = ctypes.WinDLL("dnsapi.dll", winmode=_LOAD_LIBRARY_SEARCH_SYSTEM32)
            return _Client(library, ctypes.WINFUNCTYPE)
        except (AttributeError, OSError):
            pass
    raise NetworkFailure(
        ErrorCategory.DNS_CONFIGURATION,
        _STAGE,
        "Windows DNS Client wire queries are unavailable. Specify an explicit DNS resolver.",
    )


def query_system(
    name: str, record_type: int, deadline: Deadline, cancel: CancellationToken | None
) -> SystemReply:
    with _client_lock:
        client = _client()
    return client.query(name, record_type, deadline, cancel)
