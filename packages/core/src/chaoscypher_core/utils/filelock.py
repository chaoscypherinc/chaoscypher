# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Cross-platform file locking utilities.

Provides file locking functions that work on both Unix (using fcntl) and
Windows (using msvcrt). This enables safe multi-process synchronization
for database initialization and other operations requiring exclusive access.

Usage:
    from chaoscypher_core.utils.filelock import lock_file, unlock_file

    with open("myfile.lock", "w") as f:
        lock_file(f, blocking=True)
        try:
            # Critical section
            pass
        finally:
            unlock_file(f)
"""

import contextlib
import sys
from typing import IO


if sys.platform == "win32":
    import msvcrt

    def lock_file(file: IO, exclusive: bool = True, blocking: bool = True) -> None:
        """Acquire a lock on a file (Windows implementation).

        Uses msvcrt.locking() for Windows file locking. Note that msvcrt locks
        are always exclusive (no shared lock support).

        Args:
            file: Open file object to lock
            exclusive: Whether to acquire exclusive lock (ignored on Windows,
                always exclusive)
            blocking: If True, wait for lock. If False, raise BlockingIOError
                if lock unavailable.

        Raises:
            BlockingIOError: If blocking=False and lock cannot be acquired

        """
        if not blocking:
            try:
                msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                msg = "Could not acquire file lock"
                raise BlockingIOError(msg) from exc
        else:
            msvcrt.locking(file.fileno(), msvcrt.LK_LOCK, 1)

    def unlock_file(file: IO) -> None:
        """Release a lock on a file (Windows implementation).

        Args:
            file: Open file object to unlock

        """
        # Already unlocked or file closed — safe to ignore.
        with contextlib.suppress(OSError):
            msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def lock_file(file: IO, exclusive: bool = True, blocking: bool = True) -> None:
        """Acquire a lock on a file (Unix implementation).

        Uses fcntl.flock() for Unix file locking.

        Args:
            file: Open file object to lock
            exclusive: If True, acquire exclusive lock. If False, shared lock.
            blocking: If True, wait for lock. If False, raise BlockingIOError
                if lock unavailable.

        Raises:
            BlockingIOError: If blocking=False and lock cannot be acquired

        """
        flags = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        if not blocking:
            flags |= fcntl.LOCK_NB
        fcntl.flock(file.fileno(), flags)

    def unlock_file(file: IO) -> None:
        """Release a lock on a file (Unix implementation).

        Args:
            file: Open file object to unlock

        """
        fcntl.flock(file.fileno(), fcntl.LOCK_UN)
