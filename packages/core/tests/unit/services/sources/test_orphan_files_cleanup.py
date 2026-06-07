# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for ``cleanup_orphan_source_files``.

Sweeps ``staging_dir/<source_id>/`` directories with no matching
SourceRow.id whose mtime is older than the retention window.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from unittest.mock import MagicMock

from chaoscypher_core.services.sources.orphan_files import cleanup_orphan_source_files


def _touch_old(path: Path, age_seconds: int) -> None:
    """Set ``path``'s mtime to ``age_seconds`` ago so the age gate considers it stale."""
    past = time.time() - age_seconds
    os.utime(path, (past, past))


class TestCleanupOrphanSourceFiles:
    """Behavior across the matched/unmatched/age-gated/error paths."""

    def test_returns_zero_when_staging_dir_missing(self, tmp_path: Path) -> None:
        adapter = MagicMock()
        result = cleanup_orphan_source_files(
            staging_dir=tmp_path / "does-not-exist",
            adapter=adapter,
            database_name="default",
            retention_seconds=86400,
        )
        assert result == 0
        adapter.list_source_ids.assert_not_called()

    def test_keeps_directories_with_matching_source_id(self, tmp_path: Path) -> None:
        staging = tmp_path / "sources"
        staging.mkdir()
        (staging / "src-keep").mkdir()
        (staging / "src-keep" / "x.txt").write_text("content")
        _touch_old(staging / "src-keep", age_seconds=86400 * 30)  # very old, but valid id

        adapter = MagicMock()
        adapter.list_source_ids.return_value = {"src-keep"}

        result = cleanup_orphan_source_files(
            staging_dir=staging,
            adapter=adapter,
            database_name="default",
            retention_seconds=86400,
        )
        assert result == 0
        assert (staging / "src-keep").exists()

    def test_removes_orphan_directories_past_retention(self, tmp_path: Path) -> None:
        staging = tmp_path / "sources"
        staging.mkdir()
        orphan = staging / "src-orphan"
        orphan.mkdir()
        (orphan / "x.txt").write_text("content")
        _touch_old(orphan, age_seconds=86400 * 2)  # 2 days old, retention is 1 day

        adapter = MagicMock()
        adapter.list_source_ids.return_value = set()  # no matching row

        result = cleanup_orphan_source_files(
            staging_dir=staging,
            adapter=adapter,
            database_name="default",
            retention_seconds=86400,
        )
        assert result == 1
        assert not orphan.exists()

    def test_skips_orphan_directories_inside_retention_window(self, tmp_path: Path) -> None:
        staging = tmp_path / "sources"
        staging.mkdir()
        new_orphan = staging / "src-newish"
        new_orphan.mkdir()
        (new_orphan / "x.txt").write_text("content")
        # default mtime is "now" — well inside the retention window

        adapter = MagicMock()
        adapter.list_source_ids.return_value = set()

        result = cleanup_orphan_source_files(
            staging_dir=staging,
            adapter=adapter,
            database_name="default",
            retention_seconds=86400,
        )
        assert result == 0
        assert new_orphan.exists()

    def test_mixed_keeps_removes_skips(self, tmp_path: Path) -> None:
        """One keep + one removable orphan + one in-flight orphan."""
        staging = tmp_path / "sources"
        staging.mkdir()

        keep = staging / "src-keep"
        keep.mkdir()
        (keep / "x.txt").write_text("kept")
        _touch_old(keep, age_seconds=86400 * 30)

        removable = staging / "src-old-orphan"
        removable.mkdir()
        (removable / "x.txt").write_text("orphan")
        _touch_old(removable, age_seconds=86400 * 5)

        in_flight = staging / "src-fresh-orphan"
        in_flight.mkdir()
        (in_flight / "x.txt").write_text("just uploaded")
        # leave mtime at now

        adapter = MagicMock()
        adapter.list_source_ids.return_value = {"src-keep"}

        result = cleanup_orphan_source_files(
            staging_dir=staging,
            adapter=adapter,
            database_name="default",
            retention_seconds=86400,
        )
        assert result == 1
        assert keep.exists()
        assert in_flight.exists()
        assert not removable.exists()

    def test_skips_files_at_top_level(self, tmp_path: Path) -> None:
        """Stray files (not directories) at staging root are left alone."""
        staging = tmp_path / "sources"
        staging.mkdir()
        stray_file = staging / "stray.txt"
        stray_file.write_text("not a source dir")
        _touch_old(stray_file, age_seconds=86400 * 5)

        adapter = MagicMock()
        adapter.list_source_ids.return_value = set()

        result = cleanup_orphan_source_files(
            staging_dir=staging,
            adapter=adapter,
            database_name="default",
            retention_seconds=86400,
        )
        assert result == 0
        assert stray_file.exists()

    def test_returns_zero_when_adapter_query_fails(self, tmp_path: Path) -> None:
        """If list_source_ids raises, the sweep aborts safely (no deletions)."""
        staging = tmp_path / "sources"
        staging.mkdir()
        old_dir = staging / "src-old"
        old_dir.mkdir()
        _touch_old(old_dir, age_seconds=86400 * 5)

        adapter = MagicMock()
        adapter.list_source_ids.side_effect = RuntimeError("DB connection lost")

        result = cleanup_orphan_source_files(
            staging_dir=staging,
            adapter=adapter,
            database_name="default",
            retention_seconds=86400,
        )
        assert result == 0
        assert old_dir.exists()  # nothing deleted on adapter failure
