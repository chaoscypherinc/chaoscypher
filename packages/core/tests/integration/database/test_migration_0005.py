# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Roundtrip test for migration 0005 — chunk ``sentence_offsets`` backfill.

Chunks indexed before the ``_shift_sentence_offsets`` fix (2026-06-20) carry
``chunk_metadata.sentence_offsets`` in a broken coordinate base (chunk-local
plus a ``new_start - old_start`` delta), so every consumer that slices the
chunk's own ``content[start:end]`` — chat citation hover text, the CLI
citation resolver, the source-page sentence highlight — silently produced
wrong or empty text for non-first chunks. 0005 recomputes offsets from
``content`` via the canonical splitter. These tests pin: repair of corrupted
rows, idempotence on already-correct rows, backfill of missing metadata,
preservation of sibling metadata keys, the empty-content skip, and the
documented no-op downgrade.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import chaoscypher_core.adapters.sqlite.models  # noqa: F401 — register metadata
from chaoscypher_core.adapters.sqlite.engine import evict_engine
from chaoscypher_core.database.migrations.runner import downgrade_to, upgrade_to
from chaoscypher_core.services.sources.engine.extraction.utils.sentence_splitter import (
    split_into_sentences_with_offsets,
)


_CONTENT = (
    "Pierre arrived at the gathering late. Natasha had already begun to dance! "
    "Who could blame the prince for watching? The evening ended quietly."
)


def _seed_chunk(db_path: Path, chunk_id: str, content: str, meta: dict | None) -> None:
    """Insert a minimal ``document_chunks`` row (NOT NULL columns only)."""
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO document_chunks "
            "(id, database_name, chunk_index, content, chunk_metadata, status, "
            " created_at, citation_offset_method) "
            "VALUES (?, 'default', 0, ?, ?, 'committed', '2026-06-01T00:00:00', 'exact')",
            (chunk_id, content, json.dumps(meta) if meta is not None else None),
        )
        conn.commit()


def _get_meta(db_path: Path, chunk_id: str) -> dict | None:
    """Read back ``chunk_metadata`` for one chunk (None if column is NULL)."""
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute(
            "SELECT chunk_metadata FROM document_chunks WHERE id = ?", (chunk_id,)
        ).fetchone()
    return json.loads(row[0]) if row and row[0] else None


def _fresh_db_at_0004(tmp_path: Path) -> Path:
    """Create an empty db migrated to 0005's parent revision."""
    db = tmp_path / "app.db"
    sqlite3.connect(str(db)).close()
    upgrade_to(db, "0004")
    return db


def test_0005_repairs_corrupted_offsets(tmp_path: Path) -> None:
    """Corrupted (delta-shifted) offsets are rewritten to the canonical split."""
    db = _fresh_db_at_0004(tmp_path)
    canonical = split_into_sentences_with_offsets(_CONTENT)
    corrupted = [{"start": o["start"] + 37, "end": o["end"] + 37} for o in canonical]
    _seed_chunk(db, "chunk-corrupt", _CONTENT, {"sentence_offsets": corrupted, "page": 3})
    try:
        upgrade_to(db, "0005")
        meta = _get_meta(db, "chunk-corrupt")
        assert meta is not None
        assert meta["sentence_offsets"] == canonical, "offsets not repaired to canonical split"
        assert meta["page"] == 3, "sibling metadata key lost during repair"
        first = meta["sentence_offsets"][0]
        assert _CONTENT[first["start"] : first["end"]] == "Pierre arrived at the gathering late."
    finally:
        evict_engine(db)


def test_0005_leaves_correct_offsets_unchanged(tmp_path: Path) -> None:
    """Rows already carrying the canonical offsets are byte-identical after 0005."""
    db = _fresh_db_at_0004(tmp_path)
    canonical = split_into_sentences_with_offsets(_CONTENT)
    _seed_chunk(db, "chunk-ok", _CONTENT, {"sentence_offsets": canonical, "section": "ch1"})
    try:
        before = _get_meta(db, "chunk-ok")
        upgrade_to(db, "0005")
        assert _get_meta(db, "chunk-ok") == before
    finally:
        evict_engine(db)


def test_0005_backfills_missing_metadata(tmp_path: Path) -> None:
    """Chunks with NULL metadata (or no offsets key) gain canonical offsets."""
    db = _fresh_db_at_0004(tmp_path)
    _seed_chunk(db, "chunk-null-meta", _CONTENT, None)
    _seed_chunk(db, "chunk-no-key", _CONTENT, {"page": 1})
    try:
        upgrade_to(db, "0005")
        canonical = split_into_sentences_with_offsets(_CONTENT)
        for cid in ("chunk-null-meta", "chunk-no-key"):
            meta = _get_meta(db, cid)
            assert meta is not None and meta["sentence_offsets"] == canonical, cid
        assert _get_meta(db, "chunk-no-key")["page"] == 1
    finally:
        evict_engine(db)


def test_0005_skips_empty_content(tmp_path: Path) -> None:
    """Empty-content chunks are untouched (nothing meaningful to split)."""
    db = _fresh_db_at_0004(tmp_path)
    _seed_chunk(db, "chunk-empty", "", None)
    try:
        upgrade_to(db, "0005")
        assert _get_meta(db, "chunk-empty") is None
    finally:
        evict_engine(db)


def test_0005_downgrade_is_noop(tmp_path: Path) -> None:
    """Downgrading past 0005 keeps the repaired offsets (documented no-op)."""
    db = _fresh_db_at_0004(tmp_path)
    _seed_chunk(db, "chunk-corrupt", _CONTENT, {"sentence_offsets": [{"start": 999, "end": 1042}]})
    try:
        upgrade_to(db, "0005")
        downgrade_to(db, "0004")
        meta = _get_meta(db, "chunk-corrupt")
        assert meta["sentence_offsets"] == split_into_sentences_with_offsets(_CONTENT)
    finally:
        evict_engine(db)
