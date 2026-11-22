"""Cooperative cancellation and opt-in bounded background execution."""

import socket
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from threading import BoundedSemaphore, Event, Lock
from types import TracebackType
from typing import Self

from netcraft.core.errors import NetworkFailure
from netcraft.models import ErrorCategory


class CancellationToken:
    """Share a token across calls and keep its context open until those calls finish."""

    def __init__(self) -> None:
        self._event = Event()
        self._lock = Lock()
        self._reader, self._writer = socket.socketpair()

    @property
    def wake_socket(self) -> socket.socket:
        """A read-ready socket once cancellation has been requested."""
        return self._reader

    def cancel(self) -> None:
        with self._lock:
            if not self._event.is_set():
                self._event.set()
                self._writer.sendall(b"\x01")

    def check(self, stage: str) -> None:
        if self.cancelled:
            raise NetworkFailure(ErrorCategory.CANCELLED, stage, "Operation cancelled.")

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def wait(self, timeout: float) -> None:
        self._event.wait(timeout)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        with self._lock:
            self._event.set()
            self._reader.close()
            self._writer.close()


class DiagnosticRunner:
    """Run diagnostics off the caller's thread, with no pending job backlog."""

    def __init__(self, max_workers: int = 4) -> None:
        if isinstance(max_workers, bool) or not 1 <= max_workers <= 16:
            raise ValueError("max_workers must be 1–16.")
        self._lock = Lock()
        self._closed = False
        self._slots = BoundedSemaphore(max_workers)
        self._token = CancellationToken()
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="netcraft")

    def submit[Result](self, operation: Callable[[CancellationToken], Result]) -> Future[Result]:
        with self._lock:
            if self._closed:
                raise RuntimeError("Diagnostic runner is closed.")
            if not self._slots.acquire(blocking=False):
                raise RuntimeError("All diagnostic workers are busy.")
            try:
                future = self._executor.submit(self._run, operation)
            except BaseException:
                self._slots.release()
                raise
            future.add_done_callback(self._release_cancelled_slot)
            return future

    def _run[Result](self, operation: Callable[[CancellationToken], Result]) -> Result:
        try:
            return operation(self._token)
        finally:
            self._slots.release()

    def _release_cancelled_slot[Result](self, future: Future[Result]) -> None:
        if future.cancelled():
            self._slots.release()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        with self._lock:
            self._closed = True
        with self._token:
            self._token.cancel()
            self._executor.shutdown(wait=True)
