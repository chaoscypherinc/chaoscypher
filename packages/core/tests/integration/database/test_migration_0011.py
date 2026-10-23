# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Roundtrip test for migration 0011 — attempt-history fallback and the rescue counter.

0011 adds ``chunk_extraction_attempts.decode_fallback`` (the decoding a
snapshotted try asked with, kept when a rerun moves the task into the
history) and ``sources.llm_chunks_rescued_by_fallback`` (groups whose kept
output came from a rung of the decode-fallback ladder). Pins both columns'
shape after the upgrade, that a migrated database keeps a fallback through
a real rerun, and that the downgrade removes them.
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

import chaoscypher_core.adapters.sqlite.models  # noqa: F401 — register metadata
from chaoscypher_core.adapters.sqlite.engine import evict_engine
from chaoscypher_core.database.migrations.runner import downgrade_to, upgrade_to

from ._schema import table_columns


if TYPE_CHECKING:
    from pathlib import Path


def _fresh_db(tmp_path: Path) -> Path:
    """An empty SQLite file for the runner to migrate."""
    db = tmp_path / "app.db"
    sqlite3.connect(str(db)).close()
    return db


def test_0011_adds_nullable_attempt_decode_fallback(tmp_path: Path) -> None:
    """``chunk_extraction_attempts.decode_fallback`` lands as a nullable JSON column."""
    db = _fresh_db(tmp_path)
    try:
        upgrade_to(db, "0011")
        column = table_columns(db, "chunk_extraction_attempts")["decode_fallback"]
        assert column.type.upper() == "JSON"
        assert not column.notnull, "NULL is a pinned try"
    finally:
        evict_engine(db)


def test_0011_counter_column_is_not_null_without_server_default(tmp_path: Path) -> None:
    """The counter lands NOT NULL and default-free, matching the SQLModel (two-step pattern)."""
    db = _fresh_db(tmp_path)
    try:
        upgrade_to(db, "0011")
        column = table_columns(db, "sources")["llm_chunks_rescued_by_fallback"]
        assert column.notnull
        assert column.default is None, (
            "server_default must be dropped after the backfill step so the schema "
            "matches SQLModel.metadata (test_no_undeclared_changes)"
        )
    finally:
        evict_engine(db)


def test_0011_migrated_database_keeps_a_fallback_through_a_rerun(tmp_path: Path) -> None:
    """On a database migrated to 0011, a rerun copies the task's fallback into the history."""
    from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
    from chaoscypher_core.services.sources.engine.extraction.chunk_failures import (
        decode_fallback,
    )

    db = _fresh_db(tmp_path)
    fallback = decode_fallback(2, seed=42, temperature=0.0)
    assert fallback is not None
    try:
        # To head, through 0011: the adapter is today's ORM, which writes every column a
        # later migration adds (0013's counter broke a database left at 0011).
        upgrade_to(db, "head")
        adapter = SqliteAdapter(str(db), database_name="default")
        adapter.connect()
        try:
            adapter.upload_source(
                source_id="src-0011",
                database_name="default",
                filename="t.txt",
                file_content=b"hello world",
                staging_dir=str(tmp_path),
            )
            adapter.update_source_columns(
                source_id="src-0011", database_name="default", updates={"status": "committed"}
            )
            adapter.create_extraction_job(
                job_id="job-0011", source_id="src-0011", database_name="default"
            )
            adapter.create_chunk_task(
                task_id="tsk-0011", job_id="job-0011", database_name="default", chunk_index=0
            )
            adapter.update_chunk_task(
                "tsk-0011", {"status": "completed", "decode_fallback": fallback.record()}
            )
            adapter.reset_chunk_task_for_rerun(task_id="tsk-0011", source_id="src-0011")
            attempts = adapter.list_chunk_attempts(chunk_task_id="tsk-0011")
        finally:
            adapter.disconnect()
        assert [a["decode_fallback"] for a in attempts] == [fallback.record()]
    finally:
        evict_engine(db)


def test_0011_downgrade_removes_both_columns(tmp_path: Path) -> None:
    """Downgrading 0011 → 0010 removes the two columns and keeps 0010's."""
    db = _fresh_db(tmp_path)
    try:
        upgrade_to(db, "0011")
        downgrade_to(db, "0010")
        assert "decode_fallback" not in table_columns(db, "chunk_extraction_attempts")
        assert "llm_chunks_rescued_by_fallback" not in table_columns(db, "sources")
        assert "decode_fallback" in table_columns(db, "chunk_extraction_tasks")
    finally:
        evict_engine(db)
