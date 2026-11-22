from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path
from select import select
from threading import Barrier, Event, Lock
from threading import enumerate as active_threads
from time import monotonic, sleep

import pytest

from netcraft.core.execution import CancellationToken
from netcraft.models import ErrorCategory
from netcraft.monitoring import scheduler
from netcraft.monitoring.history import open_history
from netcraft.monitoring.probes import ProbeOptions, Target
from netcraft.monitoring.scheduler import MonitorConfig, monitor
from netcraft.monitoring.telemetry import (
    MonitorProtocol,
    Observation,
    RunStatus,
    Sample,
    SampleStatus,
    StopReason,
)

_SUCCESS = Observation(SampleStatus.SUCCESS, latency_ns=100)


def _targets(count: int) -> tuple[Target, ...]:
    return tuple(Target(MonitorProtocol.TCP, "127.0.0.1", port=9000 + n) for n in range(count))


def test_defaults_and_duration_plan() -> None:
    assert MonitorConfig().plan(2) == (5_000_000_000, 10, None)
    assert MonitorConfig(interval=0.2, duration=0.41).plan(2) == (200_000_000, 3, 410_000_000)


@pytest.mark.parametrize(
    ("config", "count"),
    [
        (MonitorConfig(), 0),
        (MonitorConfig(), 33),
        (MonitorConfig(interval=0), 1),
        (MonitorConfig(interval=float("nan")), 1),
        (MonitorConfig(samples=0), 1),
        (MonitorConfig(samples=10, duration=1), 1),
        (MonitorConfig(samples=100_001), 1),
        (MonitorConfig(workers=17), 1),
    ],
)
def test_schedule_validation(config: MonitorConfig, count: int) -> None:
    with pytest.raises(ValueError):
        config.plan(count)


def test_fixed_cadence_and_fairness_without_backlog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    targets = _targets(3)
    interval_ns = 100_000_000
    now, completed = 0, 0
    wall_epoch = datetime(2026, 1, 1, tzinfo=UTC)

    # History orders samples by wall time, with a random sample ID as the tie-breaker.
    class ScheduleClock:
        @staticmethod
        def now(tz: tzinfo = UTC) -> datetime:
            return (wall_epoch + timedelta(microseconds=now // 1_000)).astimezone(tz)

    def progress(sample: Sample) -> None:
        nonlocal completed
        if sample.observation.status is SampleStatus.SUCCESS:
            completed += 1

    def wait_for_slot(
        wake: scheduler._CompletionWake, due: int | None, cancel: CancellationToken | None
    ) -> None:
        nonlocal now
        assert cancel is None
        # Model a host that has time to finish and persist each probe between slots.
        if due is not None and due <= completed * interval_ns:
            now = due
            return
        readable, _, _ = select([wake.reader], [], [], 3)
        assert readable, "The active probe did not signal completion"
        wake.reader.recv(4096)

    monkeypatch.setattr(scheduler, "monotonic_ns", lambda: now)
    monkeypatch.setattr(scheduler, "datetime", ScheduleClock)
    monkeypatch.setattr(scheduler._CompletionWake, "wait", wait_for_slot)
    with open_history(tmp_path / "schedule.sqlite3") as history:
        run = monitor(
            history,
            targets,
            MonitorConfig(interval=0.1, samples=3, workers=1),
            probe=lambda *_: _SUCCESS,
            on_sample=progress,
        )
        samples = list(history.samples())
    assert run.status is RunStatus.COMPLETED
    assert len(samples) == 9
    scheduled = {sample.slot: sample.scheduled_at for sample in samples}
    assert (scheduled[1] - scheduled[0]).total_seconds() == 0.1
    assert (scheduled[2] - scheduled[1]).total_seconds() == 0.1
    successes = [sample for sample in samples if sample.observation.status is SampleStatus.SUCCESS]
    assert [sample.target for sample in successes] == [target.label for target in targets]
    assert [sample.slot for sample in successes] == [0, 1, 2]
    assert all(
        sample.observation.reason is StopReason.WORKER_LIMIT
        for sample in samples
        if sample.observation.status is not SampleStatus.SUCCESS
    )
    assert all(sample.observed_at.tzinfo is not None for sample in samples)


def test_slow_target_never_overlaps_and_workers_are_bounded(tmp_path: Path) -> None:
    a, b, c = _targets(3)
    started = Barrier(2)
    release = Event()
    lock = Lock()
    active: set[str] = set()
    peak = 0
    threads_before = set(active_threads())

    def probe(target: Target, _options: ProbeOptions, _token: CancellationToken) -> Observation:
        nonlocal peak
        with lock:
            assert target.label not in active
            active.add(target.label)
            peak = max(peak, len(active))
        started.wait(timeout=2)
        assert release.wait(2)
        with lock:
            active.remove(target.label)
        return _SUCCESS

    def progress(sample: Sample) -> None:
        if sample.slot == 1 and sample.target == b.label:
            assert sample.observation.reason is StopReason.TARGET_BUSY
            release.set()

    with open_history(tmp_path / "bounded.sqlite3") as history:
        monitor(
            history, (a, b, c), MonitorConfig(0.15, 2, workers=2), probe=probe, on_sample=progress
        )
        observations = {(row.slot, row.target): row.observation for row in history.samples()}
    assert peak == 2
    assert observations[0, c.label].reason is StopReason.WORKER_LIMIT
    assert observations[1, a.label].reason is StopReason.TARGET_BUSY
    assert observations[1, b.label].reason is StopReason.TARGET_BUSY
    assert observations[1, c.label].reason is StopReason.WORKER_LIMIT
    assert all(obs.latency_ns is None for obs in observations.values() if obs.reason is not None)
    assert not (set(active_threads()) - threads_before)


def test_late_slots_are_skipped_without_catching_up(tmp_path: Path) -> None:
    def slow_consumer(sample: Sample) -> None:
        if sample.slot == 0:
            sleep(0.25)

    with open_history(tmp_path / "late.sqlite3") as history:
        monitor(
            history,
            _targets(1),
            MonitorConfig(0.1, 4),
            probe=lambda *_: _SUCCESS,
            on_sample=slow_consumer,
        )
        by_slot = {item.slot: item.observation for item in history.samples()}
    assert by_slot[0] == _SUCCESS
    assert by_slot[1].reason is StopReason.SCHEDULER_LATE
    assert by_slot[1].latency_ns is None


def test_cancel_wakes_monitor_and_joins_workers(tmp_path: Path) -> None:
    threads_before = set(active_threads())
    with open_history(tmp_path / "cancel.sqlite3") as history, CancellationToken() as control:

        def probe(_target: Target, _options: ProbeOptions, token: CancellationToken) -> Observation:
            control.cancel()
            token.wait(2)
            assert token.cancelled
            return Observation(SampleStatus.CANCELLED, ErrorCategory.CANCELLED)

        started = monotonic()
        run = monitor(history, _targets(1), MonitorConfig(), cancel=control, probe=probe)
        (row,) = history.samples()
    assert monotonic() - started < 2
    assert run.status is RunStatus.CANCELLED
    assert row.observation.status is SampleStatus.CANCELLED
    assert row.observation.reason is StopReason.CANCEL_REQUESTED
    assert row.observation.latency_ns is None
    assert not (set(active_threads()) - threads_before)


def test_duration_requests_shutdown_and_preserves_sample(tmp_path: Path) -> None:
    def probe(_target: Target, _options: ProbeOptions, token: CancellationToken) -> Observation:
        token.wait(2)
        assert token.cancelled
        return Observation(SampleStatus.CANCELLED, ErrorCategory.CANCELLED)

    with open_history(tmp_path / "duration.sqlite3") as history:
        before = monotonic()
        run = monitor(history, _targets(1), MonitorConfig(duration=0.15), probe=probe)
        (row,) = history.samples()
    assert 0.14 <= monotonic() - before < 2
    assert run.status is RunStatus.COMPLETED
    assert row.observation.reason is StopReason.DURATION_LIMIT


def test_unexpected_worker_error_finalizes_failed_run(tmp_path: Path) -> None:
    def fail(*_args: object) -> Observation:
        raise RuntimeError("test failure")

    with open_history(tmp_path / "failure.sqlite3") as history:
        with pytest.raises(RuntimeError, match="test failure"):
            monitor(history, _targets(1), MonitorConfig(samples=1), probe=fail)
        (run,) = history.runs(5)
        (row,) = history.samples()
        assert history.recover_if_idle() == 0
    assert run.status is RunStatus.FAILED
    assert row.observation.status is SampleStatus.INTERRUPTED
    assert row.observation.reason is StopReason.RUN_FAILED
