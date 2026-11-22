"""Operation timestamps and elapsed measurement for protocol diagnostics."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from time import perf_counter_ns


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class OperationClock:
    tick: int = field(default_factory=perf_counter_ns)
    started_at: datetime = field(default_factory=_utc_now)

    def finish(self) -> tuple[datetime, datetime, int]:
        return self.started_at, _utc_now(), perf_counter_ns() - self.tick
