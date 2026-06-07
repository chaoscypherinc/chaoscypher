# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression: upload_source either creates row+file together or leaves nothing."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine


@pytest.fixture
def adapter(tmp_path: Path) -> Generator[SqliteAdapter]:
    db_path = tmp_path / "test.db"
    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)
    adp = SqliteAdapter(str(db_path), database_name="default")
    adp.connect()
    yield adp
    adp.disconnect()


def test_happy_path_creates_row_and_file(adapter: SqliteAdapter, tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    staging.mkdir()
    src_id = "src_1"
    payload = b"hello world"
    result = adapter.upload_source(
        source_id=src_id,
        database_name="default",
        filename="doc.txt",
        file_content=payload,
        staging_dir=str(staging),
    )
    assert result["id"] == src_id
    assert result["filepath"] != ""
    file_path = Path(result["filepath"])
    assert file_path.exists()
    assert file_path.read_bytes() == payload
    refreshed = adapter.get_file(src_id, "default")
    assert refreshed is not None
    assert refreshed["filepath"] == str(file_path)


def test_no_orphan_row_when_db_insert_fails(adapter: SqliteAdapter, tmp_path: Path) -> None:
    """Simulated DB failure leaves zero rows AND zero staged files."""
    staging = tmp_path / "staging"
    staging.mkdir()

    with patch.object(SqliteAdapter, "_maybe_commit", side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError, match="boom"):
            adapter.upload_source(
                source_id="src_failure",
                database_name="default",
                filename="doc.txt",
                file_content=b"x",
                staging_dir=str(staging),
            )

    assert adapter.get_file("src_failure", "default") is None
    expected_path = staging / "src_failure" / "doc.txt"
    assert not expected_path.exists()


def test_path_traversal_blocked(adapter: SqliteAdapter, tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    staging.mkdir()
    with pytest.raises(ValueError, match="traversal"):
        adapter.upload_source(
            source_id="../escape",
            database_name="default",
            filename="../../etc/passwd",
            file_content=b"x",
            staging_dir=str(staging),
        )
    assert adapter.get_file("../escape", "default") is None


def test_staged_path_input_succeeds(adapter: SqliteAdapter, tmp_path: Path) -> None:
    """When staged_file_path is provided (vs file_content), it is moved to staging."""
    staging = tmp_path / "staging"
    staging.mkdir()
    staged = tmp_path / "staged.txt"
    staged.write_bytes(b"hello from staged")

    result = adapter.upload_source(
        source_id="src_staged",
        database_name="default",
        filename="staged.txt",
        staged_file_path=staged,
        staging_dir=str(staging),
    )
    assert result["id"] == "src_staged"
    final = Path(result["filepath"])
    assert final.exists()
    assert final.read_bytes() == b"hello from staged"
    # The original staged file should have been moved (not copied)
    assert not staged.exists()


def test_commit_succeeds_but_refresh_fails_keeps_row_and_file(
    adapter: SqliteAdapter, tmp_path: Path
) -> None:
    """If refresh() raises after a successful commit, the row and file both survive.

    Regression for the symmetry bug: previously the cleanup branch unconditionally
    unlinked the staged file on any exception in the try block, even after the
    row was already committed.
    """
    staging = tmp_path / "staging"
    staging.mkdir()
    src_id = "src_after_commit"
    payload = b"survive me"

    # Patch session.refresh to raise — but ONLY after _maybe_commit returns.
    real_session = adapter.session

    def boom_refresh(obj: object) -> None:
        raise RuntimeError("simulated refresh failure")

    with patch.object(real_session, "refresh", side_effect=boom_refresh):
        with pytest.raises(RuntimeError, match="simulated refresh failure"):
            adapter.upload_source(
                source_id=src_id,
                database_name="default",
                filename="doc.txt",
                file_content=payload,
                staging_dir=str(staging),
            )

    # Row WAS committed before refresh raised; both row and file should survive.
    refreshed = adapter.get_file(src_id, "default")
    assert refreshed is not None
    assert refreshed["filepath"] != ""
    assert Path(refreshed["filepath"]).exists()
    assert Path(refreshed["filepath"]).read_bytes() == payload
