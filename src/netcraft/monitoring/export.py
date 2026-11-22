"""Streaming JSON and spreadsheet-safe CSV using stable report columns."""

import csv
import json
from collections.abc import Iterable, Iterator
from enum import StrEnum
from typing import Protocol

from netcraft.monitoring.analytics import (
    LATENCY_FIELDS,
    SUMMARY_FIELDS,
    TREND_FIELDS,
    Grouping,
    summarize,
)
from netcraft.monitoring.history import NO_FILTER, History, HistoryFilter
from netcraft.monitoring.telemetry import RUN_FIELDS, SAMPLE_FIELDS, SCHEMA_VERSION


class Report(StrEnum):
    RUN = "run"
    SAMPLES = "samples"
    SUMMARY = "summary"
    LATENCY = "latency"
    TREND = "trend"


class ExportFormat(StrEnum):
    CSV = "csv"
    JSON = "json"


class TextSink(Protocol):
    def write(self, text: str, /) -> object: ...


def _csv_value(value: object) -> object:
    if isinstance(value, (dict, list, tuple, bool)):
        return json.dumps(value, ensure_ascii=True, sort_keys=True)
    if isinstance(value, str) and (
        value.startswith(("\t", "\r", "\n")) or value.lstrip().startswith(("=", "+", "-", "@"))
    ):
        return "'" + value
    return value


def write_rows(
    output: TextSink,
    format: ExportFormat,
    columns: tuple[str, ...],
    rows: Iterable[dict[str, object]],
    *,
    metadata: dict[str, object] | None = None,
) -> None:
    if format is ExportFormat.CSV:
        writer = csv.DictWriter(output, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _csv_value(value) for key, value in row.items()})
        return
    envelope = {"schema_version": SCHEMA_VERSION, **(metadata or {})}
    output.write(json.dumps(envelope, ensure_ascii=True)[:-1] + ', "rows": [')
    comma = ""
    for row in rows:
        output.write(comma)
        output.write(json.dumps(row, ensure_ascii=True, allow_nan=False))
        comma = ","
    output.write("]}\n")


def _select(data: dict[str, object], columns: tuple[str, ...]) -> dict[str, object]:
    return {key: data[key] for key in columns}


def export_report(
    history: History,
    output: TextSink,
    format: ExportFormat,
    report: Report,
    filters: HistoryFilter = NO_FILTER,
    group: Grouping = Grouping.ALL,
) -> None:
    filters.sql()
    with history.snapshot():
        metadata: dict[str, object] = {"report": report}
        if report is Report.RUN:
            if filters.run_id is None or filters != HistoryFilter(run_id=filters.run_id):
                raise ValueError(
                    "An entire run export requires --run without other sample filters."
                )
            run = history.get_run(filters.run_id)
            if run is None:
                raise ValueError("Monitoring run was not found.")
            run_data = run.to_dict()
            if format is ExportFormat.CSV:
                prefix_fields = tuple(
                    name for name in RUN_FIELDS if name not in {"schema_version", "run_id"}
                )
                run_columns = tuple(f"run_{name}" for name in prefix_fields)

                def run_rows() -> Iterator[dict[str, object]]:
                    yield {
                        "record_type": "run",
                        "schema_version": SCHEMA_VERSION,
                        "run_id": run.run_id,
                        **{f"run_{key}": run_data[key] for key in prefix_fields},
                    }
                    for sample in history.samples(filters):
                        yield {"record_type": "sample", **sample.to_dict()}

                columns = ("record_type", *run_columns, *SAMPLE_FIELDS)
                write_rows(output, format, columns, run_rows())
                return
            metadata["run"] = run_data
        if report in {Report.SUMMARY, Report.LATENCY}:
            columns = LATENCY_FIELDS if report is Report.LATENCY else SUMMARY_FIELDS
            rows = (
                _select(summary.to_dict(), columns)
                for summary in summarize(history, filters, group)
            )
        elif report is Report.TREND:
            columns = TREND_FIELDS
            rows = (
                _select(sample.to_dict(), columns)
                for sample in history.samples(filters, successes_only=True)
            )
        else:
            columns = SAMPLE_FIELDS
            rows = (sample.to_dict() for sample in history.samples(filters))
        write_rows(output, format, columns, rows, metadata=metadata)
