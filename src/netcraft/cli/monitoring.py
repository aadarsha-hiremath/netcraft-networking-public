"""CLI adapters for finite monitoring and local history reports."""

import argparse
import json
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from netcraft.core.config import DEFAULT_TIMEOUT
from netcraft.core.errors import NetworkFailure
from netcraft.diagnostic_models import RecordType
from netcraft.monitoring.analytics import SUMMARY_FIELDS, Grouping, Summary, summarize
from netcraft.monitoring.export import ExportFormat, Report, export_report, write_rows
from netcraft.monitoring.history import History, HistoryBusy, HistoryFilter, open_history
from netcraft.monitoring.telemetry import (
    RUN_FIELDS,
    SAMPLE_FIELDS,
    SCHEMA_VERSION,
    MonitorProtocol,
    RunStatus,
    Sample,
)


def _database(parser: argparse.ArgumentParser, *, json_output: bool = True) -> None:
    parser.add_argument(
        "--db", type=Path, help="History path (default ~/.netcraft/history.sqlite3)."
    )
    if json_output:
        parser.add_argument("--json", action="store_true", help="Write JSON to standard output.")


def _filter_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--run", dest="run_id", help="Exact run ID.")
    parser.add_argument("--protocol", choices=list(MonitorProtocol))
    parser.add_argument("--target", help="Exact stored target identity from history.")
    parser.add_argument("--since", help="Inclusive ISO 8601 timestamp with UTC offset.")
    parser.add_argument("--until", help="Exclusive ISO 8601 timestamp with UTC offset.")


def configure_monitor(parser: argparse.ArgumentParser) -> None:
    _database(parser)
    parser.add_argument("--tcp", action="append", default=[], metavar="HOST:PORT")
    parser.add_argument(
        "--udp", action="append", default=[], metavar="HOST:PORT", help="UDP echo target."
    )
    parser.add_argument("--dns", action="append", default=[], metavar="[TYPE=]NAME")
    parser.add_argument(
        "--http", action="append", default=[], metavar="URL", help="HTTP(S) HEAD target."
    )
    parser.add_argument(
        "--interval", type=float, default=5.0, help="Seconds between slots (default 5)."
    )
    extent = parser.add_mutually_exclusive_group()
    extent.add_argument("--samples", type=int, help="Slots per target (default 10).")
    extent.add_argument("--duration", type=float, help="Total scheduling duration in seconds.")
    parser.add_argument("--workers", type=int, default=4, help="Concurrent probes (default 4).")
    parser.add_argument(
        "--timeout", type=float, default=DEFAULT_TIMEOUT, help="Diagnostic timeout in seconds."
    )
    parser.add_argument("--resolver", help="Explicit numeric DNS resolver.")
    parser.add_argument("--resolver-port", type=int, help="Port of explicit DNS resolver.")
    parser.add_argument("--ca-file", type=Path, help="Additional TLS CA PEM file.")
    parser.add_argument("--quiet", action="store_true", help="Suppress progress on standard error.")


def configure_history(parser: argparse.ArgumentParser) -> None:
    _database(parser)
    _filter_options(parser)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--runs", action="store_true", help="List recent runs.")
    mode.add_argument("--delete-run", metavar="ID", help="Delete one run and its samples.")
    mode.add_argument(
        "--prune-days", type=int, metavar="DAYS", help="Delete samples older than this age."
    )
    parser.add_argument(
        "--keep-samples", type=int, metavar="COUNT", help="Keep at most this many newest samples."
    )
    parser.add_argument("--limit", type=int, default=100, help="Rows to show (default 100).")


def configure_summary(parser: argparse.ArgumentParser) -> None:
    _database(parser)
    _filter_options(parser)
    parser.add_argument("--group", choices=list(Grouping), default=Grouping.ALL)


def configure_export(parser: argparse.ArgumentParser) -> None:
    _database(parser, json_output=False)
    _filter_options(parser)
    parser.add_argument("--group", choices=list(Grouping), default=Grouping.ALL)
    parser.add_argument("--format", choices=list(ExportFormat), default=ExportFormat.JSON)
    parser.add_argument("--report", choices=list(Report), default=Report.SAMPLES)


def _filters(args: argparse.Namespace) -> HistoryFilter:
    try:
        filters = HistoryFilter(
            args.run_id,
            MonitorProtocol(args.protocol) if args.protocol is not None else None,
            args.target,
            datetime.fromisoformat(args.since) if args.since is not None else None,
            datetime.fromisoformat(args.until) if args.until is not None else None,
        )
        filters.sql()
    except ValueError as exc:
        raise ValueError("Use a valid 'since' and 'until' window with UTC offsets.") from exc
    return filters


def _progress(sample: Sample) -> None:
    result = sample.observation
    details = result.error_category or result.reason
    suffix = f" ({details})" if details is not None else ""
    elapsed = f" in {result.latency_ns / 1_000_000:.3f} ms" if result.latency_ns is not None else ""
    identity = f"{sample.protocol} {sample.target}"
    print(
        f"Slot {sample.slot + 1} {identity} {result.status}{suffix}{elapsed}",
        file=sys.stderr,
    )


def _print_summary(result: Summary) -> None:
    identity = " ".join(value for value in (result.protocol, result.target) if value is not None)
    print(
        f"{identity or 'All targets'}  {result.attempts} attempts, {result.successes} successful, "
        f"{result.failures} failed ({result.timeouts} timed out), {result.skipped} skipped, "
        f"{result.cancelled} cancelled, {result.interrupted} interrupted"
    )
    if result.p95_ns is not None and result.median_ns is not None:
        print(
            f"Median {result.median_ns / 1_000_000:.3f} ms, p95 {result.p95_ns / 1_000_000:.3f} ms"
        )
    if result.longest_failure_streak:
        print(f"Longest failure streak {result.longest_failure_streak}")


def _monitor(history: History, args: argparse.Namespace) -> int:
    from netcraft.monitoring.probes import ProbeOptions, Target, socket_target
    from netcraft.monitoring.scheduler import MonitorConfig, monitor

    targets = [socket_target(MonitorProtocol.TCP, value) for value in args.tcp]
    targets.extend(socket_target(MonitorProtocol.UDP, value) for value in args.udp)
    for query in args.dns:
        if "=" in query:
            kind, query = query.split("=", 1)
            try:
                record_type = RecordType(kind.upper())
            except ValueError as exc:
                raise ValueError("Invalid DNS record type.") from exc
        else:
            record_type = RecordType.A
        targets.append(Target(MonitorProtocol.DNS, query, record_type=record_type))
    for url in args.http:
        scheme = MonitorProtocol.HTTPS if url.lower().startswith("https:") else MonitorProtocol.HTTP
        targets.append(Target(scheme, url))
    run = monitor(
        history,
        tuple(targets),
        MonitorConfig(args.interval, args.samples, args.duration, args.workers),
        options=ProbeOptions(args.timeout, args.resolver, args.resolver_port, args.ca_file),
        on_sample=None if args.quiet else _progress,
    )
    with history.snapshot():
        summary = next(summarize(history, HistoryFilter(run_id=run.run_id)))
    if args.json:
        print(
            json.dumps(
                {
                    "schema_version": SCHEMA_VERSION,
                    "run": run.to_dict(),
                    "summary": summary.to_dict(),
                }
            )
        )
    else:
        print(f"Run {run.run_id} {run.status}")
        _print_summary(summary)
    return 130 if run.status is RunStatus.CANCELLED else 0


def _history(history: History, args: argparse.Namespace, filters: HistoryFilter) -> int:
    if filters != HistoryFilter() and (
        args.runs
        or args.delete_run is not None
        or args.prune_days is not None
        or args.keep_samples is not None
    ):
        raise ValueError(
            "Sample filters cannot be combined with run listing or history maintenance."
        )
    if args.keep_samples is not None and (args.runs or args.delete_run is not None):
        raise ValueError("A sample retention limit cannot be combined with this operation.")
    if not 1 <= args.limit <= 10_000:
        raise ValueError("History display limit must be between 1 and 10000.")
    if args.delete_run is not None:
        deleted = history.delete_run(args.delete_run)
        print(
            json.dumps({"deleted": deleted})
            if args.json
            else ("Run deleted." if deleted else "Run not found.")
        )
        return 0 if deleted else 1
    if args.prune_days is not None or args.keep_samples is not None:
        cutoff = None
        if args.prune_days is not None:
            if not 1 <= args.prune_days <= 365_000:
                raise ValueError("Prune age must be between 1 and 365000 days.")
            cutoff = datetime.now(UTC) - timedelta(days=args.prune_days)
        removed, runs = history.prune(before=cutoff, keep_samples=args.keep_samples)
        print(
            json.dumps({"samples_removed": removed, "runs_removed": runs})
            if args.json
            else f"Removed {removed} samples and {runs} empty runs."
        )
        return 0
    history.recover_if_idle()
    if args.runs:
        rows = history.runs(args.limit)
        if args.json:
            write_rows(sys.stdout, ExportFormat.JSON, RUN_FIELDS, (run.to_dict() for run in rows))
        else:
            for run in rows:
                print(f"{run.run_id}  {run.started_at.isoformat()}  {run.status}")
    else:
        samples = history.samples(filters, limit=args.limit, newest=True)
        if args.json:
            write_rows(
                sys.stdout,
                ExportFormat.JSON,
                SAMPLE_FIELDS,
                (sample.to_dict() for sample in samples),
            )
        else:
            for sample in samples:
                item = sample.observation
                print(
                    f"{sample.observed_at.isoformat()}  {sample.protocol} {sample.target}  "
                    f"{item.status}  {item.error_category or item.reason or ''}"
                )
    return 0


def monitoring_command(args: argparse.Namespace) -> int:
    path = args.db if args.db is not None else Path.home() / ".netcraft" / "history.sqlite3"
    try:
        with open_history(path) as history:
            if args.command == "monitor":
                return _monitor(history, args)
            filters = _filters(args)
            if args.command == "history":
                return _history(history, args, filters)
            history.recover_if_idle()
            group = Grouping(args.group)
            if args.command == "export":
                export_report(
                    history,
                    sys.stdout,
                    ExportFormat(args.format),
                    Report(args.report),
                    filters,
                    group,
                )
            else:
                reports = summarize(history, filters, group)
                if args.json:
                    write_rows(
                        sys.stdout,
                        ExportFormat.JSON,
                        SUMMARY_FIELDS,
                        (report.to_dict() for report in reports),
                    )
                else:
                    for report in reports:
                        _print_summary(report)
    except (ValueError, NetworkFailure) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except HistoryBusy as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except (sqlite3.Error, OSError, OverflowError) as exc:
        print(f"Could not complete monitoring command ({type(exc).__name__}).", file=sys.stderr)
        return 1
    return 0
