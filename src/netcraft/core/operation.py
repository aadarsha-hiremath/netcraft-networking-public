"""Measure operations and turn expected failures into structured results."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from time import perf_counter_ns

from netcraft.core.config import validate_port, validate_timeout
from netcraft.core.errors import NetworkFailure, classify_socket_error
from netcraft.core.execution import CancellationToken
from netcraft.models import Details, NetworkResult, Operation, Protocol


@contextmanager
def socket_stage(stage: str) -> Iterator[None]:
    try:
        yield
    except OSError as exc:
        error = classify_socket_error(exc, stage)
        raise NetworkFailure(error.category, error.stage, error.message, error.os_code) from exc


def measure(
    operation: Operation,
    protocol: Protocol,
    host: str,
    port: int,
    timeout: float,
    cancel: CancellationToken | None,
    action: Callable[[], Details],
) -> NetworkResult:
    start = perf_counter_ns()
    started_at = datetime.now(UTC)
    details = None
    error = None
    try:
        validate_port(port)
        validate_timeout(timeout)
        if cancel is not None:
            cancel.check("start")
        details = action()
    except NetworkFailure as exc:
        error = exc.error
    except OSError as exc:
        error = classify_socket_error(exc, "socket")
    return NetworkResult(
        operation,
        protocol,
        host,
        port,
        started_at,
        datetime.now(UTC),
        perf_counter_ns() - start,
        details,
        error,
    )
