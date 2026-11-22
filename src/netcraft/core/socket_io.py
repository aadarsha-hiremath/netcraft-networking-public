"""Deadline-aware I/O for plain and TLS sockets."""

import socket
import ssl
from collections.abc import Callable
from dataclasses import dataclass

from netcraft.core.errors import NetworkFailure
from netcraft.core.execution import CancellationToken
from netcraft.core.operation import socket_stage
from netcraft.core.transport import Deadline, wait_ready
from netcraft.models import ErrorCategory


@dataclass(frozen=True, slots=True)
class SocketIO:
    sock: socket.socket
    deadline: Deadline
    cancel: CancellationToken | None
    stage: str

    def run[Value](self, action: Callable[[], Value], *, write: bool = False) -> Value:
        with socket_stage(self.stage):
            while True:
                if self.cancel is not None:
                    self.cancel.check(self.stage)
                self.deadline.remaining(self.stage)
                try:
                    return action()
                except ssl.SSLWantReadError:
                    wait_for_write = False
                except ssl.SSLWantWriteError:
                    wait_for_write = True
                except BlockingIOError:
                    wait_for_write = write
                except ssl.SSLCertVerificationError as exc:
                    # OpenSSL's X509_V_ERR_HOSTNAME_MISMATCH and X509_V_ERR_IP_ADDRESS_MISMATCH.
                    category = (
                        ErrorCategory.TLS_HOSTNAME
                        if exc.verify_code in {62, 64}
                        else ErrorCategory.TLS_CERTIFICATE
                    )
                    raise NetworkFailure(
                        category, self.stage, exc.verify_message, exc.verify_code
                    ) from exc
                except ssl.SSLError as exc:
                    raise NetworkFailure(
                        ErrorCategory.TLS_FAILURE,
                        self.stage,
                        exc.reason or "TLS I/O failed.",
                        exc.errno,
                    ) from exc
                wait_ready(self.sock, self.stage, self.deadline, self.cancel, write=wait_for_write)

    def send(self, data: bytes | memoryview) -> int:
        return self.run(lambda: self.sock.send(data), write=True)

    def send_all(self, data: bytes) -> None:
        remaining = memoryview(data)
        while remaining:
            written = self.send(remaining)
            if not written:
                raise NetworkFailure(
                    ErrorCategory.REMOTE_CLOSED, self.stage, "Peer closed the stream."
                )
            remaining = remaining[written:]

    def receive(self, maximum: int) -> bytes:
        return self.run(lambda: self.sock.recv(maximum))

    def receive_exact(self, length: int) -> bytes:
        data = bytearray()
        while len(data) < length:
            chunk = self.receive(length - len(data))
            if not chunk:
                raise NetworkFailure(
                    ErrorCategory.REMOTE_CLOSED, self.stage, "Peer closed the stream early."
                )
            data.extend(chunk)
        return bytes(data)
