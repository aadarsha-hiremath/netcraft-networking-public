"""Consistent error categories across POSIX and Winsock."""

import errno
import socket

from netcraft.models import ErrorCategory, NetworkError


class NetworkFailure(Exception):
    """An expected operational failure with a user-facing representation."""

    def __init__(
        self, category: ErrorCategory, stage: str, message: str, os_code: int | None = None
    ) -> None:
        super().__init__(message)
        self.error = NetworkError(category, stage, message, os_code)


_ERROR_CODES = {
    ErrorCategory.CONNECTION_REFUSED: {errno.ECONNREFUSED, 10061},
    ErrorCategory.TIMEOUT: {errno.ETIMEDOUT, 10060},
    ErrorCategory.REMOTE_CLOSED: {errno.ECONNRESET, errno.ECONNABORTED, errno.EPIPE, 10053, 10054},
    ErrorCategory.NETWORK_UNREACHABLE: {
        errno.ENETDOWN,
        errno.ENETUNREACH,
        errno.EHOSTUNREACH,
        10050,
        10051,
        10064,
        10065,
    },
    ErrorCategory.PERMISSION_DENIED: {errno.EACCES, errno.EPERM, 10013},
}


def classify_socket_error(exc: OSError, stage: str) -> NetworkError:
    code = getattr(exc, "winerror", None)
    if code is None:
        code = exc.errno
    if isinstance(exc, socket.gaierror):
        category = ErrorCategory.DNS_FAILURE
    elif isinstance(exc, TimeoutError):
        category = ErrorCategory.TIMEOUT
    elif isinstance(exc, PermissionError):
        category = ErrorCategory.PERMISSION_DENIED
    else:
        category = next(
            (category for category, codes in _ERROR_CODES.items() if code in codes),
            ErrorCategory.SOCKET_ERROR,
        )
    return NetworkError(category, stage, exc.strerror or str(exc), code)
