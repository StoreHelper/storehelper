"""Stable StoreHelper process exit codes."""

from enum import IntEnum


class ExitCode(IntEnum):
    """Public CLI exit codes."""

    SUCCESS = 0
    USAGE = 2
    AUTHENTICATION = 3
    PACKAGE_VALIDATION = 4
    VENDOR_REJECTION = 5
    RESUMABLE_TIMEOUT = 6
    LOCAL_STATE = 7
    NETWORK = 8
    INTERRUPTED = 130
