"""SQLite history with bounded queries and recovery after a terminated monitor."""

import json
import sqlite3
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import BinaryIO

from netcraft.models import ErrorCategory
from netcraft.monitoring.telemetry import (
    OBSERVATION_FIELDS,
    RUN_FIELDS,
    SAMPLE_FIELDS,
    SCHEMA_VERSION,
    MonitorProtocol,
    Observation,
    Run,
    RunStatus,
    Sample,
    SampleStatus,
    StopReason,
    utc_text,
)

_APPLICATION_ID = 0x4E435446
_DDL = (
    """
    CREATE TABLE runs (
        run_id TEXT PRIMARY KEY,
        status TEXT NOT NULL CHECK (status IN
            ('running','completed','cancelled','interrupted','failed')),
        started_at TEXT NOT NULL,
        ended_at TEXT,
        interval_ns INTEGER NOT NULL CHECK (interval_ns > 0),
        slots_per_target INTEGER NOT NULL CHECK (slots_per_target > 0),
        target_count INTEGER NOT NULL CHECK (target_count > 0),
        worker_limit INTEGER NOT NULL CHECK (worker_limit > 0),
        duration_limit_ns INTEGER
    )
    """,
    """
    CREATE TABLE samples (
        sample_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
        slot INTEGER NOT NULL CHECK (slot >= 0),
        protocol TEXT NOT NULL CHECK (protocol IN ('tcp','udp','dns','http','https')),
        target TEXT NOT NULL,
        scheduled_at TEXT NOT NULL,
        observed_at TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN
            ('pending','success','failure','timeout','cancelled','skipped','interrupted')),
        error_category TEXT,
        reason TEXT,
        latency_ns INTEGER CHECK (latency_ns >= 0),
        dns_ns INTEGER,
        tcp_ns INTEGER,
        tls_ns INTEGER,
        request_send_ns INTEGER,
        ttfb_ns INTEGER,
        headers_read_ns INTEGER,
        body_read_ns INTEGER,
        udp_exchange_ns INTEGER,
        dns_status TEXT,
        dns_rcode TEXT,
        dns_record_count INTEGER,
        http_status INTEGER,
        tls_version TEXT,
        tls_verified INTEGER,
        bytes_received INTEGER,
        UNIQUE (run_id, protocol, target, slot)
    )
    """,
    "CREATE INDEX samples_time ON samples(observed_at, sample_id)",
    "CREATE INDEX samples_run_time ON samples(run_id, observed_at, sample_id)",
    "CREATE INDEX samples_target_time ON samples(protocol, target, observed_at, sample_id)",
    "CREATE INDEX runs_time ON runs(started_at, run_id)",
)


class HistoryBusy(RuntimeError):
    """Another process holds the monitor guard for this database."""


class RetentionChanged(RuntimeError):
    """The records selected for removal changed after preview."""


@dataclass(frozen=True, slots=True)
class RetentionPreview:
    samples: int
    runs: int
    fingerprint: bytes


def _retention_filters(
    before: datetime | None, keep_samples: int | None
) -> tuple[str, tuple[str | int, ...], str, tuple[str, ...]]:
    if (keep_samples is None and before is None) or (keep_samples is not None and keep_samples < 0):
        raise ValueError("Supply an age cutoff or a nonnegative sample limit.")
    conditions = []
    args: list[str | int] = []
    cutoff = utc_text(before) if before is not None else None
    if cutoff is not None:
        conditions.append("observed_at < ?")
        args.append(cutoff)
    if keep_samples is not None:
        # Rows older than the cutoff always sort after rows that pass it.
        conditions.append(
            """sample_id IN (SELECT sample_id FROM samples
               ORDER BY observed_at DESC, sample_id DESC LIMIT -1 OFFSET ?)"""
        )
        args.append(keep_samples)
    run_filter, run_args = (
        ("ended_at < ?", (cutoff,)) if keep_samples is None and cutoff is not None else ("1", ())
    )
    return " OR ".join(conditions), tuple(args), run_filter, run_args


def _lock(file: BinaryIO) -> None:
    try:
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        raise HistoryBusy("History is already in use or its monitor lock is unavailable.") from exc


@dataclass(frozen=True, slots=True)
class HistoryFilter:
    run_id: str | None = None
    protocol: MonitorProtocol | None = None
    target: str | None = None
    since: datetime | None = None
    until: datetime | None = None

    def sql(self) -> tuple[str, tuple[str, ...]]:
        since = utc_text(self.since) if self.since is not None else None
        until = utc_text(self.until) if self.until is not None else None
        if since is not None and until is not None and since >= until:
            raise ValueError("The start of the history window must precede the end.")
        criteria = (
            ("run_id = ?", self.run_id),
            ("protocol = ?", self.protocol),
            ("target = ?", self.target),
            ("observed_at >= ?", since),
            ("observed_at < ?", until),
        )
        selected = [(fragment, value) for fragment, value in criteria if value is not None]
        return " AND ".join(fragment for fragment, _ in selected) or "1", tuple(
            value for _, value in selected
        )


def _insert(table: str, columns: tuple[str, ...]) -> str:
    return f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})"


NO_FILTER = HistoryFilter()


def _read_sample(row: sqlite3.Row) -> Sample:
    values = {key: row[key] for key in OBSERVATION_FIELDS}
    values["status"] = SampleStatus(values["status"])
    if values["error_category"] is not None:
        values["error_category"] = ErrorCategory(values["error_category"])
    if values["reason"] is not None:
        values["reason"] = StopReason(values["reason"])
    if values["tls_verified"] is not None:
        values["tls_verified"] = bool(values["tls_verified"])
    return Sample(
        row["sample_id"],
        row["run_id"],
        row["slot"],
        MonitorProtocol(row["protocol"]),
        row["target"],
        datetime.fromisoformat(row["scheduled_at"]),
        datetime.fromisoformat(row["observed_at"]),
        Observation(**values),
    )


def _read_run(row: sqlite3.Row) -> Run:
    return Run(
        row["run_id"],
        RunStatus(row["status"]),
        datetime.fromisoformat(row["started_at"]),
        datetime.fromisoformat(row["ended_at"]) if row["ended_at"] is not None else None,
        row["interval_ns"],
        row["slots_per_target"],
        row["target_count"],
        row["worker_limit"],
        row["duration_limit_ns"],
    )


class History:
    """Use from the owning thread. Workers return results to the scheduler for writing."""

    def __init__(self, path: Path, connection: sqlite3.Connection) -> None:
        self.path = path
        self.connection = connection

    @contextmanager
    def _transaction(self, *, write: bool = True) -> Iterator[None]:
        self.connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        try:
            yield
            self.connection.execute("COMMIT")
        except BaseException:
            if self.connection.in_transaction:
                self.connection.execute("ROLLBACK")
            raise

    @contextmanager
    def snapshot(self) -> Iterator[None]:
        """Keep a consistent read view while streaming a multi-part report."""
        with self._transaction(write=False):
            yield

    def initialize(self) -> None:
        db = self.connection
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("PRAGMA temp_store = FILE")
        if db.execute("PRAGMA user_version").fetchone()[0] == 0:
            with self._transaction():
                if db.execute("PRAGMA user_version").fetchone()[0] == 0:
                    if db.execute(
                        "SELECT 1 FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%'"
                    ).fetchone():
                        raise ValueError("Path points to an existing non-NetCraft SQLite database.")
                    for statement in _DDL:
                        db.execute(statement)
                    db.execute(f"PRAGMA application_id = {_APPLICATION_ID}")
                    db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        if (
            db.execute("PRAGMA application_id").fetchone()[0] != _APPLICATION_ID
            or db.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION
        ):
            raise ValueError("Unsupported NetCraft history schema or invalid database.")
        if (
            db.execute("PRAGMA journal_mode").fetchone()[0] != "wal"
            and db.execute("PRAGMA journal_mode = WAL").fetchone()[0] != "wal"
        ):
            raise ValueError("NetCraft history requires SQLite WAL on a local filesystem.")

    @contextmanager
    def exclusive(self) -> Iterator[None]:
        """The OS releases this one-byte lock on close or process exit."""
        with self.path.with_name(self.path.name + ".monitor.lock").open("a+b") as handle:
            handle.seek(0)
            _lock(handle)
            yield

    def _mark_interrupted(self) -> int:
        self.connection.execute(
            """
            UPDATE samples SET status = 'interrupted', reason = 'process_interrupted'
            WHERE status = 'pending'
              AND run_id IN (SELECT run_id FROM runs WHERE status = 'running')
            """
        )
        return self.connection.execute(
            "UPDATE runs SET status = 'interrupted', ended_at = ? WHERE status = 'running'",
            (utc_text(datetime.now(UTC)),),
        ).rowcount

    def _recover(self) -> int:
        with self._transaction():
            return self._mark_interrupted()

    def recover_if_idle(self) -> int:
        try:
            with self.exclusive():
                # A WAL reader can continue while another connection is writing.
                # Take a database write lock only when recovery has work to do.
                if (
                    self.connection.execute(
                        "SELECT 1 FROM runs WHERE status = 'running' LIMIT 1"
                    ).fetchone()
                    is None
                ):
                    return 0
                return self._recover()
        except HistoryBusy:
            return 0

    def add_run(self, run: Run) -> None:
        columns = RUN_FIELDS[1:]
        data = run.to_dict()
        self.connection.execute(_insert("runs", columns), tuple(data[key] for key in columns))

    @contextmanager
    def monitoring(self, run: Run) -> Iterator[None]:
        with self.exclusive():
            self._recover()
            self.add_run(run)
            yield

    def finish_run(
        self, run_id: str, status: RunStatus, ended_at: datetime, reason: StopReason | None = None
    ) -> None:
        with self._transaction():
            self.connection.execute(
                "UPDATE samples SET status = ?, reason = ? WHERE run_id = ? AND status = 'pending'",
                (
                    SampleStatus.CANCELLED
                    if status is RunStatus.CANCELLED
                    else SampleStatus.INTERRUPTED,
                    reason or StopReason.RUN_FAILED,
                    run_id,
                ),
            )
            updated = self.connection.execute(
                "UPDATE runs SET status = ?, ended_at = ? WHERE run_id = ? AND status = 'running'",
                (status, utc_text(ended_at), run_id),
            )
            if updated.rowcount != 1:
                raise RuntimeError("Run could not be finalized.")

    def insert_sample(self, sample: Sample) -> None:
        columns = SAMPLE_FIELDS[1:]
        data = sample.to_dict()
        self.connection.execute(_insert("samples", columns), tuple(data[key] for key in columns))

    def settle(self, sample: Sample) -> None:
        columns = ("observed_at", *OBSERVATION_FIELDS)
        data = sample.to_dict()
        values = tuple(data[key] for key in columns)
        assignment = ", ".join(f"{key} = ?" for key in columns)
        updated = self.connection.execute(
            f"UPDATE samples SET {assignment} WHERE sample_id = ? AND status = 'pending'",
            (*values, sample.sample_id),
        )
        if updated.rowcount != 1:
            raise RuntimeError("Pending sample could not be finalized.")

    def get_run(self, run_id: str) -> Run | None:
        row = self.connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        return _read_run(row) if row is not None else None

    def runs(
        self,
        limit: int,
        *,
        offset: int = 0,
        filters: HistoryFilter = NO_FILTER,
        target_contains: str = "",
    ) -> Iterator[Run]:
        predicate = ""
        parameters: tuple[str, ...] = ()
        if filters != NO_FILTER or target_contains:
            criteria, parameters = filters.sql()
            if target_contains:
                criteria += " AND instr(lower(target), lower(?)) > 0"
                parameters = (*parameters, target_contains)
            predicate = (
                f"WHERE EXISTS (SELECT 1 FROM samples WHERE run_id = r.run_id AND {criteria})"
            )
        with_cursor = self.connection.execute(
            f"""SELECT r.* FROM runs AS r {predicate}
                ORDER BY r.started_at DESC, r.run_id DESC LIMIT ? OFFSET ?""",
            (*parameters, limit, offset),
        )
        for row in with_cursor:
            yield _read_run(row)

    def samples(
        self,
        filters: HistoryFilter = NO_FILTER,
        *,
        limit: int | None = None,
        newest: bool = False,
        successes_only: bool = False,
        offset: int = 0,
    ) -> Iterator[Sample]:
        criteria, args = filters.sql()
        if successes_only:
            criteria += " AND status = 'success' AND latency_ns IS NOT NULL"
        direction = "DESC" if newest else "ASC"
        cursor = self.connection.execute(
            f"""SELECT * FROM samples WHERE {criteria}
                ORDER BY observed_at {direction}, sample_id {direction} LIMIT ? OFFSET ?""",
            (*args, limit if limit is not None else -1, offset),
        )
        for row in cursor:
            yield _read_sample(row)

    def _preview_retention(
        self,
        sample_filter: str,
        sample_args: tuple[str | int, ...],
        run_filter: str,
        run_args: tuple[str, ...],
    ) -> RetentionPreview:
        rows = self.connection.execute(
            f"""WITH removed AS (SELECT sample_id FROM samples WHERE {sample_filter})
                SELECT 0 AS kind, sample_id AS id FROM removed
                UNION ALL
                SELECT 1, r.run_id FROM runs AS r WHERE {run_filter}
                    AND NOT EXISTS (
                        SELECT 1 FROM samples WHERE run_id = r.run_id
                            AND sample_id NOT IN (SELECT sample_id FROM removed)
                    )
                ORDER BY kind, id""",
            (*sample_args, *run_args),
        )
        counts = [0, 0]
        digest = sha256()
        for kind, key in rows:
            counts[kind] += 1
            digest.update(json.dumps((kind, key), ensure_ascii=True).encode("ascii") + b"\n")
        return RetentionPreview(counts[0], counts[1], digest.digest())

    def preview_retention(
        self, *, before: datetime | None, keep_samples: int | None
    ) -> RetentionPreview:
        filters = _retention_filters(before, keep_samples)
        with self.exclusive(), self.snapshot():
            return self._preview_retention(*filters)

    def prune(
        self,
        *,
        before: datetime | None,
        keep_samples: int | None,
        expected: RetentionPreview | None = None,
    ) -> tuple[int, int]:
        filters = _retention_filters(before, keep_samples)
        sample_filter, sample_args, run_filter, run_args = filters
        with self.exclusive(), self._transaction():
            self._mark_interrupted()
            if expected is not None and self._preview_retention(*filters) != expected:
                raise RetentionChanged("History changed. Preview retention again.")
            db = self.connection
            samples = db.execute(f"DELETE FROM samples WHERE {sample_filter}", sample_args).rowcount
            runs = db.execute(
                f"""DELETE FROM runs WHERE {run_filter}
                    AND NOT EXISTS (
                        SELECT 1 FROM samples WHERE samples.run_id = runs.run_id
                    )""",
                run_args,
            ).rowcount
        return samples, runs

    def delete_run(self, run_id: str) -> bool:
        with self.exclusive():
            self._recover()
            with self._transaction():
                return (
                    self.connection.execute("DELETE FROM runs WHERE run_id = ?", (run_id,)).rowcount
                    == 1
                )


@contextmanager
def open_history(path: Path) -> Iterator[History]:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=5, autocommit=True)
    db.row_factory = sqlite3.Row
    try:
        history = History(path, db)
        history.initialize()
        yield history
    finally:
        db.close()
