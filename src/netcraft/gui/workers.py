"""Four bounded GUI job lanes with Qt-thread delivery and cooperative cancellation."""

import errno
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from threading import Event, Lock
from typing import cast

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

from netcraft.core.errors import NetworkFailure
from netcraft.core.execution import CancellationToken
from netcraft.monitoring.history import HistoryBusy, RetentionChanged

LANES = ("diagnostic", "monitor", "history", "file")


class JobCancelled(Exception):
    """A job stopped at a defined cancellation boundary."""


class ReservedExportDestination(ValueError):
    """An export targets a workspace file."""


class JobControl:
    def __init__(self) -> None:
        self._cancel = Event()
        self._lock = Lock()
        self._token: CancellationToken | None = None

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def cancel(self) -> None:
        self._cancel.set()
        with self._lock:
            if self._token is not None:
                self._token.cancel()

    def check(self) -> None:
        if self.cancelled:
            raise JobCancelled

    @contextmanager
    def network(self) -> Iterator[CancellationToken]:
        self.check()
        with CancellationToken() as token:
            with self._lock:
                self._token = token
                if self.cancelled:
                    token.cancel()
            try:
                yield token
            finally:
                with self._lock:
                    self._token = None


@dataclass(frozen=True, slots=True)
class JobResult[Result]:
    value: Result | None = None
    error: str | None = None
    cancelled: bool = False


def _error_message(exc: Exception) -> str:
    if isinstance(exc, (RetentionChanged, ReservedExportDestination)):
        return str(exc)
    if isinstance(exc, HistoryBusy):
        return "History is busy. Stop the other monitor and try again."
    if isinstance(exc, NetworkFailure):
        return f"Diagnostic failed ({exc.error.category}). Check the target and settings."
    if isinstance(exc, PermissionError):
        return "Permission denied. Choose a writable local location and retry."
    if isinstance(exc, FileExistsError):
        return "The destination already exists. Choose another file or confirm replacement."
    if isinstance(exc, sqlite3.Error):
        code = getattr(exc, "sqlite_errorcode", 0) & 0xFF
        if code in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
            return "SQLite history is busy. Wait briefly and retry."
        return "Could not read or write history. Check the path and database integrity."
    if isinstance(exc, ValueError):
        return "The history schema, report selection, or input is invalid. Check the settings."
    if isinstance(exc, OSError):
        if exc.errno == errno.ENOSPC:
            return "The destination disk is full. Free space and retry."
        return "The selected local file or directory is unavailable."
    return f"Unexpected worker error ({type(exc).__name__}). The application is still available."


@dataclass(slots=True)
class _Job:
    control: JobControl
    work: Callable[[JobControl], object]
    deliver: Callable[[JobResult[object]], None]


class _Task(QRunnable):
    def __init__(self, hub: "JobHub", lane: str, job: _Job) -> None:
        super().__init__()
        self.hub, self.lane, self.job = hub, lane, job

    def run(self) -> None:
        job = self.job
        try:
            job.control.check()
            result = JobResult(job.work(job.control))
        except JobCancelled:
            result = JobResult(cancelled=True)
        except Exception as exc:
            result = (
                JobResult(cancelled=True)
                if job.control.cancelled
                else JobResult(error=_error_message(exc))
            )
        self.hub.completed.emit(self.lane, result)


class JobHub(QObject):
    completed = Signal(str, object)
    idle = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(len(LANES))
        self._jobs: dict[str, _Job] = {}
        self._tasks: dict[str, _Task] = {}
        self._pending_history: _Job | None = None
        self.closing = False
        self.completed.connect(self._finished)

    def busy(self, lane: str) -> bool:
        return lane in self._jobs

    @property
    def active(self) -> bool:
        return bool(self._jobs) or self.pool.activeThreadCount() > 0

    def submit[Result](
        self,
        lane: str,
        work: Callable[[JobControl], Result],
        deliver: Callable[[JobResult[Result]], None],
        *,
        replace_history_read: bool = False,
    ) -> bool:
        if self.closing or lane not in LANES:
            return False

        def send(result: JobResult[object]) -> None:
            deliver(cast(JobResult[Result], result))

        job = _Job(JobControl(), work, send)
        if lane in self._jobs:
            if lane != "history" or not replace_history_read:
                return False
            self._pending_history = job
            self._jobs[lane].control.cancel()
        else:
            self._start(lane, job)
        return True

    def _start(self, lane: str, job: _Job) -> None:
        self._jobs[lane] = job
        task = _Task(self, lane, job)
        self._tasks[lane] = task
        self.pool.start(task)

    def cancel(self, lane: str) -> None:
        job = self._jobs.get(lane)
        if job is not None:
            job.control.cancel()

    def discard_history_reads(self) -> None:
        """Drop reads started before a committed history change."""
        self._pending_history = None
        self.cancel("history")

    def shutdown(self) -> None:
        self.closing = True
        self._pending_history = None
        for job in self._jobs.values():
            job.control.cancel()

    @Slot(str, object)
    def _finished(self, lane: str, result: JobResult[object]) -> None:
        job = self._jobs.pop(lane)
        self._tasks.pop(lane)
        if lane == "history" and self._pending_history is not None and not self.closing:
            pending = self._pending_history
            self._pending_history = None
            self._start(lane, pending)
        elif not self.closing and not (lane == "history" and job.control.cancelled):
            job.deliver(result)
        if not self._jobs:
            self.idle.emit()
