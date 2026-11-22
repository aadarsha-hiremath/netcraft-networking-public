"""Verified TLS for an already connected socket."""

import socket
import ssl
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter_ns
from typing import TypedDict, cast

from netcraft.core.errors import NetworkFailure
from netcraft.core.execution import CancellationToken
from netcraft.core.socket_io import SocketIO
from netcraft.core.transport import Deadline
from netcraft.diagnostic_models import TLSInfo
from netcraft.models import ErrorCategory

type DistinguishedName = tuple[tuple[tuple[str, str], ...], ...]


class _PeerCertificate(TypedDict, total=False):
    subject: DistinguishedName
    issuer: DistinguishedName
    notBefore: str
    notAfter: str


def _name_parts(name: DistinguishedName) -> tuple[tuple[str, str], ...]:
    return tuple(pair for group in name for pair in group)


def _certificate_date(value: str | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(ssl.cert_time_to_seconds(value), UTC).isoformat()


@contextmanager
def verified_tls(
    raw: socket.socket,
    server_name: str,
    ca_file: Path | None,
    deadline: Deadline,
    cancel: CancellationToken | None,
) -> Iterator[tuple[ssl.SSLSocket, TLSInfo, int]]:
    try:
        context = ssl.create_default_context()
        if ca_file is not None:
            context.load_verify_locations(cafile=str(ca_file))
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        if ssl.HAS_ALPN:
            context.set_alpn_protocols(["http/1.1"])
    except (OSError, ValueError) as exc:
        raise NetworkFailure(
            ErrorCategory.TLS_CONFIGURATION, "tls_configuration", "Could not prepare TLS trust."
        ) from exc

    try:
        wrapped = context.wrap_socket(
            raw,
            server_hostname=server_name,
            do_handshake_on_connect=False,
            suppress_ragged_eofs=False,
        )
    except (OSError, ValueError) as exc:
        raise NetworkFailure(
            ErrorCategory.TLS_FAILURE, "tls_handshake", "Could not initialize TLS."
        ) from exc
    with wrapped:
        start = perf_counter_ns()
        SocketIO(wrapped, deadline, cancel, "tls_handshake").run(wrapped.do_handshake)
        elapsed = perf_counter_ns() - start
        cert = cast(_PeerCertificate | None, wrapped.getpeercert())
        if not cert:
            raise NetworkFailure(
                ErrorCategory.TLS_CERTIFICATE,
                "tls_handshake",
                "Verified peer certificate unavailable.",
            )
        cipher = wrapped.cipher()
        info = TLSInfo(
            wrapped.version(),
            cipher[0] if cipher else None,
            cipher[2] if cipher else None,
            _name_parts(cert.get("subject", ())),
            _name_parts(cert.get("issuer", ())),
            _certificate_date(cert.get("notBefore")),
            _certificate_date(cert.get("notAfter")),
            server_name,
            True,
            True,
            wrapped.selected_alpn_protocol() if ssl.HAS_ALPN else None,
        )
        yield wrapped, info, elapsed
