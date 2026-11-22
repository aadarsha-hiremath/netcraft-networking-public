"""Limits and input validation shared by the networking operations."""

import ipaddress
import math
import re

from netcraft.core.errors import NetworkFailure
from netcraft.models import ErrorCategory

DEFAULT_TIMEOUT = 3.0
MAX_TIMEOUT = 60.0
MAX_DATAGRAM = 65_507
MAX_ECHO_PAYLOAD = 4_096
LOOPBACK_HOSTS = ("127.0.0.1", "::1")
_LABEL = re.compile(r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?")


def validate_host(host: str) -> tuple[str, bool]:
    if host and not any(char.isspace() for char in host) and "\x00" not in host:
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            return host, True
        try:
            ascii_host = host.encode("idna").decode("ascii")
        except UnicodeError:
            ascii_host = ""
        name = ascii_host.removesuffix(".")
        labels = name.split(".")
        if (
            0 < len(name) <= 253
            and all(_LABEL.fullmatch(label) for label in labels)
            and not name.replace(".", "").isdigit()
        ):
            return ascii_host, False
    raise NetworkFailure(
        ErrorCategory.INVALID_HOST, "validation", "Expected a hostname or an IPv4/IPv6 literal."
    )


def require_loopback(host: str) -> None:
    if host not in LOOPBACK_HOSTS:
        raise NetworkFailure(
            ErrorCategory.INVALID_HOST, "validation", "Use loopback address 127.0.0.1 or ::1."
        )


def validate_port(port: int) -> None:
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65_535:
        raise NetworkFailure(ErrorCategory.INVALID_PORT, "validation", "Port must be 1–65535.")


def validate_timeout(timeout: float) -> None:
    if isinstance(timeout, bool) or not 0 < timeout <= MAX_TIMEOUT or not math.isfinite(timeout):
        raise NetworkFailure(
            ErrorCategory.INVALID_TIMEOUT,
            "validation",
            f"Timeout must be above 0 and at most {MAX_TIMEOUT:g} seconds.",
        )


def validate_payload(payload: bytes, maximum: int) -> None:
    if not isinstance(payload, bytes) or len(payload) > maximum:
        raise NetworkFailure(
            ErrorCategory.INVALID_PAYLOAD, "validation", f"Payload must be bytes (up to {maximum})."
        )
