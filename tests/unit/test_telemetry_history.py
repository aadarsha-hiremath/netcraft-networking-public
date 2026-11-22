import csv
import io
import json
import sqlite3
import subprocess
import sys
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from netcraft.models import ErrorCategory
from netcraft.monitoring.analytics import Grouping, summarize
from netcraft.monitoring.export import ExportFormat, Report, export_report, write_rows
from netcraft.monitoring.history import History, HistoryBusy, HistoryFilter, open_history
from netcraft.monitoring.telemetry import (
    MonitorProtocol,
    Observation,
    Run,
    RunStatus,
    Sample,
    SampleStatus,
    StopReason,
)

_START = datetime(2026, 1, 2, 3, tzinfo=UTC)


def _run(run_id: str = "run-1", start: datetime = _START) -> Run:
    return Run(run_id, RunStatus.RUNNING, start, None, 1_000_000_000, 50, 2, 2, None)


def _sample(
    run: Run,
    index: int,
    observation: Observation,
    target: str = "127.0.0.1:8000",
    protocol: MonitorProtocol = MonitorProtocol.TCP,
) -> Sample:
    at = run.started_at + timedelta(seconds=index)
    return Sample(f"{run.run_id}-{index}", run.run_id, index, protocol, target, at, at, observation)


def _populate(history: History) -> Run:
    run = _run()
    history.add_run(run)
    for index, status in enumerate(
        (
            Observation(SampleStatus.SUCCESS, latency_ns=10, tcp_ns=5),
            Observation(SampleStatus.FAILURE, ErrorCategory.CONNECTION_REFUSED, latency_ns=99),
            Observation(SampleStatus.TIMEOUT, ErrorCategory.TIMEOUT, latency_ns=100),
            Observation(SampleStatus.SKIPPED, reason=StopReason.TARGET_BUSY),
            Observation(SampleStatus.CANCELLED, ErrorCategory.CANCELLED),
            Observation(SampleStatus.SUCCESS, latency_ns=40),
            Observation(SampleStatus.TIMEOUT, ErrorCategory.TIMEOUT, latency_ns=100),
        ),
    ):
        history.insert_sample(_sample(run, index, status))
    history.insert_sample(
        _sample(
            run, 7, Observation(SampleStatus.SUCCESS, latency_ns=80), "origin", MonitorProtocol.HTTP
        )
    )
    history.finish_run(run.run_id, RunStatus.COMPLETED, _START + timedelta(seconds=10))
    return run


def test_sqlite_reopens_and_uses_indexes(tmp_path: Path) -> None:
    path = tmp_path / "δ-history.sqlite3"
    with open_history(path) as history:
        run = _populate(history)
        assert history.connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert history.connection.execute("PRAGMA user_version").fetchone()[0] == 1
        indexes = {row["name"] for row in history.connection.execute("PRAGMA index_list(samples)")}
        assert {"samples_run_time", "samples_time", "samples_target_time"} <= indexes
    with open_history(path) as reopened:
        saved = reopened.get_run(run.run_id)
        assert saved is not None and saved.status is RunStatus.COMPLETED
        assert len(list(reopened.samples(HistoryFilter(run_id=run.run_id)))) == 8
        before = _START.astimezone(timezone(timedelta(hours=5, minutes=30)))
        window = HistoryFilter(since=before, until=before + timedelta(seconds=2))
        assert [sample.slot for sample in reopened.samples(window)] == [0, 1]


def test_rates_latency_jitter_and_failure_streaks(tmp_path: Path) -> None:
    with open_history(tmp_path / "summary.db") as history:
        _populate(history)
        (result,) = summarize(history)
        summaries = list(summarize(history, group=Grouping.TARGET))
        protocols = {row.protocol for row in summarize(history, group=Grouping.PROTOCOL)}
    assert (
        result.samples,
        result.attempts,
        result.successes,
        result.failures,
        result.timeouts,
    ) == (8, 6, 3, 3, 2)
    assert (result.cancelled, result.skipped, result.pending, result.interrupted) == (1, 1, 0, 0)
    assert result.to_dict()["success_rate"] == 0.5
    assert result.to_dict()["failure_rate"] == 0.5
    assert result.to_dict()["timeout_rate"] == pytest.approx(2 / 6)
    assert result.timing_count == 3
    assert result.mean_ns == pytest.approx(130 / 3)
    assert (result.min_ns, result.median_ns, result.max_ns) == (10, 40, 80)
    assert (result.p95_ns, result.p99_ns) == (80, 80)
    assert (result.jitter_ns, result.jitter_pairs) == (30, 1)
    assert (result.longest_failure_streak, result.current_failure_streak) == (2, 1)
    assert result.error_categories == {"connection_refused": 1, "timeout": 2}
    assert protocols == {"http", "tcp"}
    assert [(row.protocol, row.target) for row in summaries] == [
        ("http", "origin"),
        ("tcp", "127.0.0.1:8000"),
    ]
    assert summaries[0].jitter_ns is None
    assert summaries[1].median_ns == 25


def test_read_snapshot_is_stable_while_monitor_appends(tmp_path: Path) -> None:
    path = tmp_path / "snapshot.sqlite3"
    run = _run()
    observation = Observation(SampleStatus.SUCCESS, latency_ns=1)
    with open_history(path) as owner, owner.monitoring(run), open_history(path) as reader:
        owner.insert_sample(_sample(run, 0, observation))
        with reader.snapshot():
            assert [row.slot for row in reader.samples()] == [0]
            owner.insert_sample(_sample(run, 1, observation))
            assert [row.slot for row in reader.samples()] == [0]
        assert [row.slot for row in reader.samples()] == [0, 1]
        owner.finish_run(run.run_id, RunStatus.COMPLETED, _START + timedelta(seconds=2))


def test_exact_nearest_rank_percentiles_and_empty_window(tmp_path: Path) -> None:
    with open_history(tmp_path / "percentiles.db") as history:
        run = _run()
        history.add_run(run)
        for number in range(1, 21):
            history.insert_sample(
                _sample(run, number, Observation(SampleStatus.SUCCESS, latency_ns=number))
            )
        (result,) = summarize(history)
        (empty,) = summarize(history, HistoryFilter(until=_START))
    assert (result.median_ns, result.p95_ns, result.p99_ns) == (10.5, 19, 20)
    assert result.jitter_ns == 1
    assert empty.attempts == 0 and empty.to_dict()["failure_rate"] is None
    assert empty.mean_ns is empty.median_ns is empty.p95_ns is None


def test_retention_by_age_count_and_cascading_delete(tmp_path: Path) -> None:
    with open_history(tmp_path / "retention.db") as history:
        old = _run("old", _START - timedelta(days=10))
        fresh = _run("fresh")
        for run in (old, fresh):
            history.add_run(run)
            for index in range(3):
                history.insert_sample(
                    _sample(run, index, Observation(SampleStatus.SUCCESS, latency_ns=7))
                )
            history.finish_run(
                run.run_id, RunStatus.COMPLETED, run.started_at + timedelta(seconds=3)
            )
        removed = history.prune(before=_START - timedelta(days=1), keep_samples=2)
        assert removed == (4, 1)
        assert [row.slot for row in history.samples()] == [1, 2]
        assert history.get_run(old.run_id) is None
        assert history.delete_run(fresh.run_id)
        assert not history.delete_run(fresh.run_id)
        assert list(history.samples()) == []


def test_live_owner_blocks_maintenance_and_recovery(tmp_path: Path) -> None:
    path = tmp_path / "guard.sqlite3"
    run = _run()
    with open_history(path) as owner, owner.monitoring(run):
        owner.insert_sample(_sample(run, 0, Observation(SampleStatus.PENDING)))
        with open_history(path) as reader:
            assert reader.recover_if_idle() == 0
            current = reader.get_run(run.run_id)
            assert current is not None and current.status is RunStatus.RUNNING
            with pytest.raises(HistoryBusy):
                reader.delete_run(run.run_id)
            with pytest.raises(HistoryBusy):
                reader.prune(before=None, keep_samples=0)
            with pytest.raises(HistoryBusy), reader.monitoring(_run("competing")):
                pytest.fail("A second monitor acquired the same history lock.")
    with open_history(path) as history:
        assert history.recover_if_idle() == 1
        (row,) = history.samples()
    assert row.observation.status is SampleStatus.INTERRUPTED
    assert row.observation.reason is StopReason.PROCESS_INTERRUPTED
    assert row.observation.latency_ns is None


def test_actual_process_exit_recovers_committed_rows(tmp_path: Path) -> None:
    path = tmp_path / "crash.sqlite3"
    code = """
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from netcraft.monitoring.history import open_history
from netcraft.monitoring.telemetry import (
    MonitorProtocol, Observation, Run, RunStatus, Sample, SampleStatus,
)
now = datetime.now(UTC)
with open_history(Path(sys.argv[1])) as history:
    run = Run("abrupt", RunStatus.RUNNING, now, None, 1000000000, 2, 1, 1, None)
    with history.monitoring(run):
        for slot, status in enumerate((SampleStatus.SUCCESS, SampleStatus.PENDING)):
            result = Observation(status, latency_ns=17 if slot == 0 else None)
            history.insert_sample(Sample(str(slot), run.run_id, slot, MonitorProtocol.TCP,
                                         "127.0.0.1:8000", now, now, result))
        os._exit(7)
"""
    child = subprocess.run([sys.executable, "-c", code, str(path)], check=False, timeout=10)
    assert child.returncode == 7
    with open_history(path) as restarted:
        assert restarted.recover_if_idle() == 1
        run = restarted.get_run("abrupt")
        samples = list(restarted.samples())
        assert restarted.recover_if_idle() == 0
    assert run is not None and run.status is RunStatus.INTERRUPTED and run.ended_at is not None
    assert [row.observation.status for row in samples] == [
        SampleStatus.SUCCESS,
        SampleStatus.INTERRUPTED,
    ]
    assert samples[0].observation.latency_ns == 17


def test_foreign_and_future_database_schema_are_rejected(tmp_path: Path) -> None:
    foreign = tmp_path / "foreign.db"
    db = sqlite3.connect(foreign)
    db.execute("CREATE TABLE other(value TEXT)")
    db.close()
    with pytest.raises(ValueError, match="non-NetCraft"), open_history(foreign):
        pytest.fail("A foreign schema was opened.")
    future = tmp_path / "future.db"
    with open_history(future) as history:
        history.connection.execute("PRAGMA user_version = 2")
    with pytest.raises(ValueError, match="Unsupported"), open_history(future):
        pytest.fail("A future schema was opened.")


def test_stable_report_exports_and_csv_escaping(tmp_path: Path) -> None:
    output = io.StringIO(newline="")
    write_rows(
        output,
        ExportFormat.CSV,
        ("text", "optional"),
        [
            {"text": 'two, "quoted"\nlines', "optional": None},
            {"text": "  =1+2", "optional": False},
        ],
    )
    csv_rows = list(csv.DictReader(io.StringIO(output.getvalue())))
    assert csv_rows == [
        {"text": 'two, "quoted"\nlines', "optional": ""},
        {"text": "'  =1+2", "optional": "false"},
    ]

    with open_history(tmp_path / "exports.db") as history:
        run = _populate(history)
        filters = HistoryFilter(run_id=run.run_id)
        for report, expected in (
            (Report.RUN, 8),
            (Report.SAMPLES, 8),
            (Report.SUMMARY, 1),
            (Report.LATENCY, 1),
            (Report.TREND, 3),
        ):
            result = io.StringIO()
            export_report(history, result, ExportFormat.JSON, report, filters)
            document = json.loads(result.getvalue())
            assert document["schema_version"] == 1 and document["report"] == report
            assert len(document["rows"]) == expected
            if report is Report.RUN:
                assert document["run"]["run_id"] == run.run_id
                assert document["rows"][0]["dns_ns"] is None
        result = io.StringIO(newline="")
        export_report(history, result, ExportFormat.CSV, Report.RUN, filters)
        run_csv = list(csv.DictReader(io.StringIO(result.getvalue())))
        assert run_csv[0]["record_type"] == "run" and run_csv[0]["run_id"] == run.run_id
        assert len(run_csv) == 9
        assert all(row["record_type"] == "sample" for row in run_csv[1:])


def test_json_writer_streams_before_requesting_next_row() -> None:
    output = io.StringIO()

    def rows() -> Iterator[dict[str, object]]:
        yield {"marker": "first"}
        assert '"marker": "first"' in output.getvalue()
        yield {"marker": None}

    write_rows(output, ExportFormat.JSON, ("marker",), rows())
    assert json.loads(output.getvalue())["rows"] == [{"marker": "first"}, {"marker": None}]
