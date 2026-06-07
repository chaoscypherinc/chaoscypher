# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Disk space utilities.

Pre-write disk space checks to prevent silent failures when the filesystem
is full. Raises InsufficientStorageError (mapped to HTTP 507 by the API layer)
when available space falls below the configured threshold.
"""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING

from chaoscypher_core.exceptions import InsufficientStorageError


if TYPE_CHECKING:
    from pathlib import Path

# Default minimum free space: 100 MB
DEFAULT_MIN_BYTES = 100 * 1024 * 1024


def check_disk_space(path: Path, min_bytes: int = DEFAULT_MIN_BYTES) -> None:
    """Check that sufficient disk space is available.

    Args:
        path: Path to check disk space for (uses the filesystem of this path).
        min_bytes: Minimum required bytes (default 100 MB).

    Raises:
        InsufficientStorageError: If available disk space is below min_bytes.

    """
    usage = shutil.disk_usage(path)
    if usage.free < min_bytes:
        free_mb = usage.free / (1024 * 1024)
        required_mb = min_bytes / (1024 * 1024)
        msg = f"Insufficient disk space: {free_mb:.0f}MB available, {required_mb:.0f}MB required"
        raise InsufficientStorageError(
            msg,
            details={"free_mb": round(free_mb, 1), "required_mb": round(required_mb, 1)},
        )
