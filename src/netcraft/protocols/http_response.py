"""Bounded parsing and body accounting for one HTTP/1.x response."""

import io
import re
from collections.abc import Buffer
from dataclasses import dataclass, replace
from time import perf_counter_ns
from typing import override

from netcraft.core.errors import NetworkFailure
from netcraft.core.socket_io import SocketIO
from netcraft.models import ErrorCategory

_MAX_METADATA = 32_768
_MAX_LINE = 8_192
_MAX_INFORMATIONAL = 5
_STATUS_LINE = re.compile(rb"HTTP/(1\.[01]) ([1-5][0-9]{2})(?: ([^\x00-\x08\x0a-\x1f\x7f]*))?")
_FIELD_NAME = re.compile(rb"[!#$%&'*+.^_\x60|~0-9A-Za-z-]+")
_FIELD_CONTROL = re.compile(rb"[\x00-\x08\x0a-\x1f\x7f]")
_CHUNK_LENGTH = re.compile(rb"[0-9a-fA-F]{1,16}")
_CONTENT_LENGTH = re.compile(r"[0-9]{1,20}")


@dataclass(frozen=True, slots=True)
class ResponseHead:
    status: int
    reason: str
    version: str
    headers: tuple[tuple[str, str], ...]
    content_length: int | None
    chunked: bool

    def header(self, name: str) -> str | None:
        return next((value for key, value in self.headers if key == name), None)


def _malformed(stage: str, message: str) -> NetworkFailure:
    return NetworkFailure(ErrorCategory.HTTP_MALFORMED, stage, message)


class _WireReader(io.RawIOBase):
    def __init__(self, source: SocketIO) -> None:
        super().__init__()
        self.source = source
        self.first_byte_at: int | None = None

    @override
    def readable(self) -> bool:
        return True

    @override
    def readinto(self, buffer: Buffer) -> int:
        # BufferedReader supplies a writable contiguous byte buffer.
        view = memoryview(buffer)
        data = self.source.receive(view.nbytes)
        if data and self.first_byte_at is None:
            self.first_byte_at = perf_counter_ns()
        view[: len(data)] = data
        return len(data)


class ResponseReader(io.BufferedReader):
    """Owns bounded response buffering while the caller owns the socket."""

    def __init__(self, source: SocketIO) -> None:
        self.wire = _WireReader(source)
        super().__init__(self.wire, buffer_size=4096)
        self._remaining_metadata = _MAX_METADATA
        self.body_bytes = 0

    @property
    def first_byte_at(self) -> int | None:
        return self.wire.first_byte_at

    def _line(self, stage: str) -> bytes:
        maximum = min(_MAX_LINE + 1, self._remaining_metadata + 1)
        data = self.readline(maximum)
        self._remaining_metadata -= len(data)
        if len(data) > _MAX_LINE or self._remaining_metadata < 0:
            raise _malformed(stage, "HTTP response metadata exceeded its size limit.")
        if not data.endswith(b"\r\n"):
            raise _malformed(stage, "Missing or incomplete HTTP line.")
        return data[:-2]

    def _headers(self, stage: str) -> tuple[tuple[str, str], ...]:
        headers: list[tuple[str, str]] = []
        while line := self._line(stage):
            name, separator, content = line.partition(b":")
            if not separator or not _FIELD_NAME.fullmatch(name) or _FIELD_CONTROL.search(content):
                raise _malformed(stage, "Invalid HTTP header field.")
            headers.append((name.decode("ascii").lower(), content.decode("latin-1").strip(" \t")))
        return tuple(headers)

    def read_head(self) -> ResponseHead:
        for _ in range(_MAX_INFORMATIONAL + 1):
            line = self._line("http_headers")
            match = _STATUS_LINE.fullmatch(line)
            if match is None:
                if line.startswith(b"HTTP/") and not line.startswith((b"HTTP/1.0 ", b"HTTP/1.1 ")):
                    raise NetworkFailure(
                        ErrorCategory.HTTP_UNSUPPORTED, "http_headers", "Unsupported HTTP version."
                    )
                raise _malformed("http_headers", "Invalid HTTP status line.")
            version = match[1].decode("ascii")
            status = int(match[2])
            reason = (match[3] or b"").decode("latin-1")
            if status == 101:
                raise NetworkFailure(
                    ErrorCategory.HTTP_UNSUPPORTED,
                    "http_headers",
                    "HTTP protocol upgrades are unsupported.",
                )
            headers = self._headers("http_headers")
            if status >= 200:
                length, chunked = _framing(headers, version)
                return ResponseHead(status, reason, version, headers, length, chunked)
        raise _malformed("http_headers", "Too many informational responses.")

    def _take_body(self, count: int) -> bool:
        # Count only payload bytes returned by the reader, including partial progress.
        remaining = count
        while remaining:
            data = self.read1(min(remaining, 4096))
            if not data:
                return False
            self.body_bytes += len(data)
            remaining -= len(data)
        return True

    def consume_body(self, head: ResponseHead, maximum: int) -> bool:
        """Return whether framing proved the entire body ended within the cap."""
        self.wire.source = replace(self.wire.source, stage="http_body")
        if head.chunked:
            return self._consume_chunked(maximum)
        expected = head.content_length
        if expected is None:
            return not self._take_body(maximum)
        if not self._take_body(min(expected, maximum)):
            raise _malformed("http_body", "Body ended before its advertised length.")
        return expected <= maximum

    def _consume_chunked(self, maximum: int) -> bool:
        while self.body_bytes < maximum:
            line = self._line("http_body")
            length, _, _ = line.partition(b";")
            if not _CHUNK_LENGTH.fullmatch(length) or _FIELD_CONTROL.search(line):
                raise _malformed("http_body", "Invalid HTTP chunk size.")
            count = int(length, 16)
            if not count:
                self._headers("http_body")
                return True
            budget = maximum - self.body_bytes
            if not self._take_body(min(count, budget)):
                raise _malformed("http_body", "Incomplete HTTP chunk.")
            if count < budget and self.read(2) != b"\r\n":
                raise _malformed("http_body", "Missing HTTP chunk terminator.")
        return False


def _framing(headers: tuple[tuple[str, str], ...], version: str) -> tuple[int | None, bool]:
    lengths = [
        item.strip()
        for name, value in headers
        if name == "content-length"
        for item in value.split(",")
    ]
    transfer = [
        item.strip().lower()
        for name, value in headers
        if name == "transfer-encoding"
        for item in value.split(",")
    ]
    if transfer:
        if lengths or version != "1.1":
            raise _malformed("http_headers", "Conflicting or invalid HTTP response framing.")
        if transfer != ["chunked"]:
            raise NetworkFailure(
                ErrorCategory.HTTP_UNSUPPORTED,
                "http_headers",
                "Unsupported HTTP transfer encoding.",
            )
        return None, True
    if not lengths:
        return None, False
    if not all(_CONTENT_LENGTH.fullmatch(length) for length in lengths):
        raise _malformed("http_headers", "Invalid Content-Length.")
    sizes = {int(length) for length in lengths}
    if len(sizes) != 1:
        raise _malformed("http_headers", "Conflicting Content-Length values.")
    return sizes.pop(), False
