"""Versioned monitoring records. Only approved diagnostic fields enter history."""

from dataclasses import asdict, dataclass, fields
from datetime import UTC, datetime
from enum import StrEnum

from netcraft.models import ErrorCategory

SCHEMA_VERSION = 1


class MonitorProtocol(StrEnum):
    TCP = "tcp"
    UDP = "udp"
    DNS = "dns"
    HTTP = "http"
    HTTPS = "https"


class SampleStatus(StrEnum):
    PENDING = "pending"
    SUCCESS = "success"
    FAILURE = "failure"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"
    INTERRUPTED = "interrupted"


class StopReason(StrEnum):
    TARGET_BUSY = "target_busy"
    WORKER_LIMIT = "worker_limit"
    SCHEDULER_LATE = "scheduler_late"
    CANCEL_REQUESTED = "cancel_requested"
    DURATION_LIMIT = "duration_limit"
    PROCESS_INTERRUPTED = "process_interrupted"
    RUN_FAILED = "run_failed"


class RunStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"
    FAILED = "failed"


def utc_text(value: datetime) -> str:
    if value.utcoffset() is None:
        raise ValueError("Time must include a UTC offset.")
    return value.astimezone(UTC).isoformat(timespec="microseconds")


@dataclass(frozen=True, slots=True)
class Observation:
    status: SampleStatus
    error_category: ErrorCategory | None = None
    reason: StopReason | None = None
    latency_ns: int | None = None
    dns_ns: int | None = None
    tcp_ns: int | None = None
    tls_ns: int | None = None
    request_send_ns: int | None = None
    ttfb_ns: int | None = None
    headers_read_ns: int | None = None
    body_read_ns: int | None = None
    udp_exchange_ns: int | None = None
    dns_status: str | None = None
    dns_rcode: str | None = None
    dns_record_count: int | None = None
    http_status: int | None = None
    tls_version: str | None = None
    tls_verified: bool | None = None
    bytes_received: int | None = None


@dataclass(frozen=True, slots=True)
class Sample:
    sample_id: str
    run_id: str
    slot: int
    protocol: MonitorProtocol
    target: str
    scheduled_at: datetime
    observed_at: datetime
    observation: Observation

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "sample_id": self.sample_id,
            "run_id": self.run_id,
            "slot": self.slot,
            "protocol": self.protocol,
            "target": self.target,
            "scheduled_at": utc_text(self.scheduled_at),
            "observed_at": utc_text(self.observed_at),
            **asdict(self.observation),
        }


@dataclass(frozen=True, slots=True)
class Run:
    run_id: str
    status: RunStatus
    started_at: datetime
    ended_at: datetime | None
    interval_ns: int
    slots_per_target: int
    target_count: int
    worker_limit: int
    duration_limit_ns: int | None

    def to_dict(self) -> dict[str, object]:
        return {
            **asdict(self),
            "schema_version": SCHEMA_VERSION,
            "started_at": utc_text(self.started_at),
            "ended_at": utc_text(self.ended_at) if self.ended_at is not None else None,
        }


OBSERVATION_FIELDS = tuple(field.name for field in fields(Observation))
SAMPLE_FIELDS = (
    "schema_version",
    "sample_id",
    "run_id",
    "slot",
    "protocol",
    "target",
    "scheduled_at",
    "observed_at",
    *OBSERVATION_FIELDS,
)
RUN_FIELDS = ("schema_version", *(field.name for field in fields(Run)))
