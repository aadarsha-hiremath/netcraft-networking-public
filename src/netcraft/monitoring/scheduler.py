"""Fixed-rate, finite sampling with a bounded set of active workers."""

import select
import socket
import sys
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from threading import current_thread, main_thread
from time import monotonic_ns
from uuid import uuid4

from netcraft.core.execution import CancellationToken
from netcraft.monitoring.history import History
from netcraft.monitoring.probes import DEFAULT_PROBE_OPTIONS, ProbeOptions, Target, run_probe
from netcraft.monitoring.telemetry import (
    MonitorProtocol,
    Observation,
    Run,
    RunStatus,
    Sample,
    SampleStatus,
    StopReason,
)

_SECOND = 1_000_000_000
_MAX_TARGETS = 32
_MAX_SLOTS = 100_000
type Probe = Callable[[Target, ProbeOptions, CancellationToken], Observation]
type ProbeResult = tuple[datetime, Observation]
type TargetKey = tuple[MonitorProtocol, str]


@dataclass(frozen=True, slots=True)
class MonitorConfig:
    interval: float = 5.0
    samples: int | None = None
    duration: float | None = None
    workers: int = 4

    def plan(self, target_count: int) -> tuple[int, int, int | None]:
        if not 1 <= target_count <= _MAX_TARGETS:
            raise ValueError(f"Configure between 1 and {_MAX_TARGETS} explicit targets.")
        if isinstance(self.interval, bool) or not 0.1 <= self.interval <= 3600:
            raise ValueError("Interval must be between 0.1 and 3600 seconds.")
        if isinstance(self.workers, bool) or not 1 <= self.workers <= 16:
            raise ValueError("Worker count must be between 1 and 16.")
        if self.duration is not None and self.samples is not None:
            raise ValueError("Choose a sample count or a duration.")
        interval_ns = round(self.interval * _SECOND)
        duration_ns = None
        if self.duration is not None:
            if isinstance(self.duration, bool) or not 0.1 <= self.duration <= 86_400:
                raise ValueError("Duration must be between 0.1 and 86400 seconds.")
            duration_ns = round(self.duration * _SECOND)
            slots = (duration_ns + interval_ns - 1) // interval_ns
        else:
            slots = 10 if self.samples is None else self.samples
        if isinstance(slots, bool) or slots < 1 or slots * target_count > _MAX_SLOTS:
            raise ValueError(f"Set a positive sample count with at most {_MAX_SLOTS} total slots.")
        return interval_ns, slots, duration_ns


class _CompletionWake:
    """Coalesce completion notifications in an OS-bounded socket buffer."""

    def __init__(self, reader: socket.socket, writer: socket.socket) -> None:
        self.reader = reader
        self.writer = writer
        self.writer.setblocking(False)
        self._windows_main = sys.platform == "win32" and current_thread() is main_thread()

    def notify(self, _future: Future[ProbeResult]) -> None:
        with suppress(BlockingIOError):  # A full buffer is already signaled.
            self.writer.send(b"\0")

    def wait(self, until: int | None, external: CancellationToken | None) -> None:
        timeout = max(0.0, (until - monotonic_ns()) / _SECOND) if until is not None else None
        if self._windows_main:
            # Let Python service console interrupts even when no socket becomes ready.
            timeout = 0.25 if timeout is None else min(0.25, timeout)
        readers = [self.reader]
        if external is not None:
            readers.append(external.wake_socket)
        readable, _, _ = select.select(readers, [], [], timeout)
        if self.reader in readable:
            self.reader.recv(4096)


class _MonitorLoop:
    def __init__(
        self,
        history: History,
        run: Run,
        targets: tuple[Target, ...],
        options: ProbeOptions,
        pool: ThreadPoolExecutor,
        wake: _CompletionWake,
        token: CancellationToken,
        probe: Probe,
        on_sample: Callable[[Sample], None] | None,
    ) -> None:
        self.history = history
        self.run = run
        self.targets = targets
        self.options = options
        self.pool = pool
        self.wake = wake
        self.token = token
        self.probe = probe
        self.on_sample = on_sample
        self.active: dict[TargetKey, tuple[Sample, Future[ProbeResult]]] = {}

    def _run_probe(self, target: Target) -> ProbeResult:
        observed = datetime.now(UTC)
        return observed, self.probe(target, self.options, self.token)

    def _record_slot(
        self,
        slot: int,
        scheduled: datetime,
        late: bool,
        cutoff: int | None,
        external: CancellationToken | None,
    ) -> None:
        size = len(self.targets)
        offset = (slot * self.run.worker_limit) % size
        for index in range(size):
            if (external is not None and external.cancelled) or (
                cutoff is not None and monotonic_ns() >= cutoff
            ):
                return
            target = self.targets[(index + offset) % size]
            key = (target.protocol, target.label)
            reason = None
            if late:
                reason = StopReason.SCHEDULER_LATE
            elif key in self.active:
                reason = StopReason.TARGET_BUSY
            elif len(self.active) >= self.run.worker_limit:
                reason = StopReason.WORKER_LIMIT
            item = Sample(
                uuid4().hex,
                self.run.run_id,
                slot,
                target.protocol,
                target.label,
                scheduled,
                datetime.now(UTC),
                Observation(SampleStatus.SKIPPED, reason=reason)
                if reason is not None
                else Observation(SampleStatus.PENDING),
            )
            self.history.insert_sample(item)
            if reason is None:
                future = self.pool.submit(self._run_probe, target)
                self.active[key] = (item, future)
                future.add_done_callback(self.wake.notify)
            elif self.on_sample is not None:
                self.on_sample(item)

    def _reap(self, reason: StopReason | None, *, notify: bool) -> None:
        for key, (item, future) in tuple(self.active.items()):
            if not future.done():
                continue
            observed, observation = future.result()
            if observation.status is SampleStatus.CANCELLED:
                observation = replace(observation, reason=reason or StopReason.CANCEL_REQUESTED)
            finished = replace(item, observed_at=observed, observation=observation)
            self.history.settle(finished)
            del self.active[key]
            if notify and self.on_sample is not None:
                self.on_sample(finished)

    def schedule(self, external: CancellationToken | None) -> tuple[RunStatus, StopReason | None]:
        epoch = monotonic_ns()
        wall_epoch = datetime.now(UTC)
        duration = self.run.duration_limit_ns
        cutoff = epoch + duration if duration is not None else None
        interval = self.run.interval_ns
        slot = 0
        while True:
            if external is not None and external.cancelled:
                return RunStatus.CANCELLED, StopReason.CANCEL_REQUESTED
            self._reap(None, notify=True)
            now = monotonic_ns()
            if cutoff is not None and now >= cutoff:
                return RunStatus.COMPLETED, StopReason.DURATION_LIMIT
            due = epoch + slot * interval
            if slot < self.run.slots_per_target and now >= due:
                scheduled = wall_epoch + timedelta(microseconds=(slot * interval) // 1000)
                self._record_slot(slot, scheduled, now >= due + interval, cutoff, external)
                slot += 1
                continue
            if slot == self.run.slots_per_target and not self.active and cutoff is None:
                return RunStatus.COMPLETED, None
            next_event = due if slot < self.run.slots_per_target else cutoff
            self.wake.wait(next_event, external)

    def drain(self, reason: StopReason | None, *, notify: bool) -> None:
        while self.active:
            self._reap(reason, notify=notify)
            if self.active:
                self.wake.wait(None, None)


def monitor(
    history: History,
    targets: tuple[Target, ...],
    config: MonitorConfig = MonitorConfig(),
    *,
    options: ProbeOptions = DEFAULT_PROBE_OPTIONS,
    cancel: CancellationToken | None = None,
    on_sample: Callable[[Sample], None] | None = None,
    probe: Probe = run_probe,
) -> Run:
    """Run finite monitoring and durably write each started or skipped slot."""
    interval, slots, duration = config.plan(len(targets))
    if len({(target.protocol, target.label) for target in targets}) != len(targets):
        raise ValueError("Configure each protocol and target identity only once per run.")
    run = Run(
        uuid4().hex,
        RunStatus.RUNNING,
        datetime.now(UTC),
        None,
        interval,
        slots,
        len(targets),
        config.workers,
        duration,
    )
    reader, writer = socket.socketpair()
    with (
        reader,
        writer,
        CancellationToken() as token,
        ThreadPoolExecutor(
            max_workers=config.workers, thread_name_prefix="netcraft-monitor"
        ) as pool,
        history.monitoring(run),
    ):
        wake = _CompletionWake(reader, writer)
        loop = _MonitorLoop(history, run, targets, options, pool, wake, token, probe, on_sample)
        try:
            try:
                status, reason = loop.schedule(cancel)
                if reason is not None:
                    token.cancel()
                loop.drain(reason, notify=True)
            except KeyboardInterrupt:
                status, reason = RunStatus.CANCELLED, StopReason.CANCEL_REQUESTED
                token.cancel()
                loop.drain(reason, notify=False)
        except BaseException:
            token.cancel()
            pool.shutdown(wait=True)
            history.finish_run(
                run.run_id, RunStatus.FAILED, datetime.now(UTC), StopReason.RUN_FAILED
            )
            raise
        ended = datetime.now(UTC)
        history.finish_run(run.run_id, status, ended, reason)
        return replace(run, status=status, ended_at=ended)
