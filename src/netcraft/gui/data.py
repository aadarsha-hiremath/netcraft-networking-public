"""Asynchronous job payloads backed by the Phase 3 storage and report APIs."""

import json
import os
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import islice
from pathlib import Path
from tempfile import NamedTemporaryFile

from netcraft.gui.diagnostic_data import DiagnosticView
from netcraft.gui.workers import JobControl, ReservedExportDestination
from netcraft.monitoring.analytics import Grouping, Summary, summarize
from netcraft.monitoring.export import ExportFormat, Report, TextSink, export_report
from netcraft.monitoring.history import History, HistoryFilter, open_history
from netcraft.monitoring.telemetry import MonitorProtocol, Run, Sample

RUN_PAGE = 35
SAMPLE_PAGE = 60
TREND_LIMIT = 1_200
COMPARISON_LIMIT = 50


@contextmanager
def local_history(path: Path, control: JobControl) -> Iterator[History]:
    control.check()
    with open_history(path) as history:
        history.connection.set_progress_handler(lambda: int(control.cancelled), 1000)
        yield history


@dataclass(frozen=True, slots=True)
class WindowFilter:
    minutes: int = 0
    protocol: MonitorProtocol | None = None
    target: str = ""

    def history_filter(self) -> HistoryFilter:
        since = datetime.now(UTC) - timedelta(minutes=self.minutes) if self.minutes else None
        return HistoryFilter(protocol=self.protocol, target=self.target or None, since=since)


@dataclass(frozen=True, slots=True)
class OverviewData:
    summary: Summary
    recent_runs: tuple[Run, ...]
    recent_errors: tuple[Sample, ...]
    points: tuple[Sample, ...]


def overview(path: Path, filters: WindowFilter, control: JobControl) -> OverviewData:
    query = filters.history_filter()
    with local_history(path, control) as history:
        history.recover_if_idle()
        with history.snapshot():
            recent = tuple(history.samples(query, newest=True, limit=60))
            errors = tuple(
                row
                for row in recent
                if row.observation.error_category is not None or row.observation.reason is not None
            )[:4]
            return OverviewData(
                next(summarize(history, query)),
                tuple(history.runs(4)),
                errors,
                tuple(
                    reversed(
                        tuple(history.samples(query, newest=True, limit=180, successes_only=True))
                    )
                ),
            )


@dataclass(frozen=True, slots=True)
class HistoryPageData:
    runs: tuple[Run, ...]
    has_next: bool


def read_runs(path: Path, filters: WindowFilter, page: int, control: JobControl) -> HistoryPageData:
    query = filters.history_filter()
    with local_history(path, control) as history:
        history.recover_if_idle()
        data = tuple(
            history.runs(
                RUN_PAGE + 1,
                offset=page * RUN_PAGE,
                filters=HistoryFilter(protocol=query.protocol, since=query.since),
                target_contains=filters.target,
            )
        )
        return HistoryPageData(data[:RUN_PAGE], len(data) > RUN_PAGE)


@dataclass(frozen=True, slots=True)
class RunDetail:
    run: Run
    summary: Summary
    samples: tuple[Sample, ...]
    has_next: bool


def read_run(path: Path, run_id: str, page: int, control: JobControl) -> RunDetail:
    query = HistoryFilter(run_id=run_id)
    with local_history(path, control) as history, history.snapshot():
        run = history.get_run(run_id)
        if run is None:
            raise ValueError("Run is unavailable.")
        rows = tuple(
            history.samples(
                query,
                newest=True,
                limit=SAMPLE_PAGE + 1,
                offset=page * SAMPLE_PAGE,
            )
        )
        return RunDetail(
            run, next(summarize(history, query)), rows[:SAMPLE_PAGE], len(rows) > SAMPLE_PAGE
        )


@dataclass(frozen=True, slots=True)
class AnalyticsData:
    overall: Summary
    targets: tuple[Summary, ...]
    protocols: tuple[Summary, ...]
    points: tuple[Sample, ...]
    target_list_limited: bool
    trend_limited: bool


def analytics(path: Path, filters: WindowFilter, control: JobControl) -> AnalyticsData:
    query = filters.history_filter()
    with local_history(path, control) as history:
        history.recover_if_idle()
        with history.snapshot():
            overall = next(summarize(history, query))
            targets = tuple(
                islice(summarize(history, query, Grouping.TARGET), COMPARISON_LIMIT + 1)
            )
            protocols = tuple(summarize(history, query, Grouping.PROTOCOL))
            points = tuple(history.samples(query, newest=True, limit=TREND_LIMIT + 1))
            return AnalyticsData(
                overall,
                targets[:COMPARISON_LIMIT],
                protocols,
                tuple(reversed(points[:TREND_LIMIT])),
                len(targets) > COMPARISON_LIMIT,
                len(points) > TREND_LIMIT,
            )


class _CheckedText:
    def __init__(self, stream: TextSink, control: JobControl) -> None:
        self.stream, self.control = stream, control

    def write(self, text: str) -> object:
        self.control.check()
        return self.stream.write(text)


def write_atomic(
    path: Path, overwrite: bool, control: JobControl, produce: Callable[[TextSink], None]
) -> None:
    control.check()
    temporary: Path | None = None
    try:
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            prefix=".netcraft-",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            produce(_CheckedText(stream, control))
            stream.flush()
            os.fsync(stream.fileno())
        control.check()
        if overwrite:
            os.replace(temporary, path)
        elif sys.platform == "win32":
            os.rename(temporary, path)
        else:
            os.link(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _check_export_destination(
    destination: Path, history_path: Path, preferences_path: Path | None
) -> None:
    source = history_path.resolve()
    suffixes = ("-wal", "-shm", "-journal", ".monitor.lock")
    reserved = {source, *(Path(str(source) + suffix) for suffix in suffixes)}
    if preferences_path is not None:
        reserved.add(preferences_path.resolve())
    if destination.resolve() in reserved:
        raise ReservedExportDestination(
            "This destination is reserved for workspace storage. Choose another file."
        )


def save_diagnostic(
    path: Path,
    overwrite: bool,
    result: DiagnosticView,
    control: JobControl,
    *,
    history_path: Path,
    preferences_path: Path | None = None,
) -> None:
    _check_export_destination(path, history_path, preferences_path)

    def produce(stream: TextSink) -> None:
        json.dump(result.sanitized(), stream, ensure_ascii=True, indent=2)
        stream.write("\n")

    write_atomic(path, overwrite, control, produce)


def save_report(
    history_path: Path,
    destination: Path,
    overwrite: bool,
    format: ExportFormat,
    report: Report,
    filters: HistoryFilter,
    grouping: Grouping,
    control: JobControl,
    *,
    preferences_path: Path | None = None,
) -> None:
    _check_export_destination(destination, history_path, preferences_path)
    with local_history(history_path, control) as history:

        def produce(stream: TextSink) -> None:
            export_report(history, stream, format, report, filters, grouping)

        write_atomic(destination, overwrite, control, produce)
