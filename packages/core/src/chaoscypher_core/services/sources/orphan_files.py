# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Periodic cleanup of orphaned source files in the staging directory.

``upload_source`` writes the staged file under ``staging_dir/<source_id>/``
before committing the ``SourceRow``. The in-process ``except`` branch in
``upload_source`` calls ``_cleanup_staged_file`` to remove the file when
an exception fires. But a hard kill (SIGKILL, OOM, container crash)
between the write and the row commit leaves a directory with no matching
row — and these accumulate until they fill disk.

This module provides the sweep that closes that gap:

1. List every immediate child directory under ``staging_dir/``.
2. Diff against ``adapter.list_source_ids(database_name)``.
3. For each unmatched directory, age-gate against ``retention_seconds``
   (mtime of the directory) so in-flight uploads aren't reaped mid-commit.
4. Remove the survivors with ``shutil.rmtree``.

Best-effort throughout — a per-directory failure is logged and skipped;
the loop continues so one bad directory doesn't block the rest.
"""

from __future__ import annotations

import shutil
import time
from typing import TYPE_CHECKING, Any

import structlog


if TYPE_CHECKING:
    from pathlib import Path


logger = structlog.get_logger(__name__)


def cleanup_orphan_source_files(
    *,
    staging_dir: Path,
    adapter: Any,
    database_name: str,
    retention_seconds: int,
) -> int:
    """Remove staging_dir entries with no matching SourceRow, age-gated.

    Args:
        staging_dir: ``settings.database_dir / "sources"`` for the active
            database. Each top-level entry is expected to be a directory
            named after a SourceRow.id.
        adapter: Storage adapter exposing ``list_source_ids(database_name)``.
        database_name: Active database name (multi-DB isolation).
        retention_seconds: Age threshold. Directories whose mtime is newer
            than ``now - retention_seconds`` are skipped — this preserves
            in-flight uploads where the file landed but the row hasn't
            committed yet.

    Returns:
        Number of orphan directories removed. Zero is the common case.
    """
    if not staging_dir.exists():
        return 0

    try:
        valid_ids = adapter.list_source_ids(database_name)
    except Exception as exc:
        logger.warning(
            "orphan_files_list_source_ids_failed",
            staging_dir=str(staging_dir),
            error_type=type(exc).__name__,
            error_message=str(exc),
        )
        return 0

    cutoff_ts = time.time() - retention_seconds
    deleted_count = 0

    for entry in staging_dir.iterdir():
        if not entry.is_dir():
            continue
        source_id = entry.name
        if source_id in valid_ids:
            continue

        try:
            mtime = entry.stat().st_mtime
        except OSError as exc:
            logger.warning(
                "orphan_files_stat_failed",
                path=str(entry),
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            continue
        if mtime > cutoff_ts:
            continue  # Newer than the retention window — skip, may be in-flight.

        try:
            shutil.rmtree(entry)
            deleted_count += 1
            logger.info(
                "orphan_source_file_removed",
                source_id=source_id,
                path=str(entry),
                age_seconds=int(time.time() - mtime),
            )
        except OSError as exc:
            logger.warning(
                "orphan_source_file_remove_failed",
                source_id=source_id,
                path=str(entry),
                error_type=type(exc).__name__,
                error_message=str(exc),
            )

    return deleted_count
