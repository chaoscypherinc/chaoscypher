# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Migration 0008: stored ``sentence_offsets`` equal the current splitter's after it (audit wave 3).

The splitter now breaks after a closing quote (audit C2), so offsets stored
before the change (88 of the Book One pack's 179 chunks) no longer matched
what ``chunk_hydration`` numbers live. 0008 recomputes every chunk's offsets
with ``split_into_sentences_with_offsets``; 0005 and 0008 share that body
(``backfills.recompute_chunk_sentence_offsets``).
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


# Two speakers' turns: the old splitter read them as one sentence (no break after a closing quote).
_DIALOGUE = (
    "“Well, Prince, so Genoa and Lucca are now just family estates.”\n\n"
    "“Heavens! what a virulent attack!” replied the prince, not in the least disconcerted."
)
_PROSE = "Pierre arrived late. Natásha had already begun to dance! The evening ended quietly."


def _old_split(content: str) -> list[dict[str, int]]:
    """Offsets as a pre-C2 row stores them: the two turns as one sentence."""
    return [{"start": 0, "end": len(content)}]


def _seed(db: Path, chunk_id: str, content: str, meta: dict | None) -> None:
    """Insert a minimal ``document_chunks`` row."""
    with sqlite3.connect(str(db)) as conn:
        conn.execute(
            "INSERT INTO document_chunks "
            "(id, database_name, chunk_index, content, chunk_metadata, status, "
            " created_at, citation_offset_method) "
            "VALUES (?, 'default', 0, ?, ?, 'committed', '2026-06-01T00:00:00', 'exact')",
            (chunk_id, content, json.dumps(meta) if meta is not None else None),
        )
        conn.commit()


def _offsets(db: Path) -> dict[str, list[dict[str, int]] | None]:
    """Every chunk's stored offsets (None without metadata)."""
    with sqlite3.connect(str(db)) as conn:
        rows = conn.execute("SELECT id, chunk_metadata FROM document_chunks").fetchall()
    return {cid: (json.loads(m).get("sentence_offsets") if m else None) for cid, m in rows}


def _db_at_0007(tmp_path: Path) -> Path:
    """An empty database migrated to 0008's parent."""
    db = tmp_path / "app.db"
    sqlite3.connect(str(db)).close()
    upgrade_to(db, "0007")
    return db


def test_offsets_equal_the_live_splitter_after_0008(tmp_path: Path) -> None:
    """Stale, current and missing offsets all end as ``split_into_sentences_with_offsets(content)``."""
    db = _db_at_0007(tmp_path)
    assert split_into_sentences_with_offsets(_DIALOGUE) != _old_split(_DIALOGUE)
    _seed(db, "stale", _DIALOGUE, {"sentence_offsets": _old_split(_DIALOGUE), "page": 2})
    _seed(db, "current", _PROSE, {"sentence_offsets": split_into_sentences_with_offsets(_PROSE)})
    _seed(db, "missing", _PROSE, None)
    _seed(db, "empty", "", None)
    try:
        upgrade_to(db, "0008")
        stored = _offsets(db)
        assert stored["stale"] == split_into_sentences_with_offsets(_DIALOGUE)
        assert stored["current"] == split_into_sentences_with_offsets(_PROSE)
        assert stored["missing"] == split_into_sentences_with_offsets(_PROSE)
        assert stored["empty"] is None  # nothing to split
        with sqlite3.connect(str(db)) as conn:
            (meta,) = conn.execute(
                "SELECT chunk_metadata FROM document_chunks WHERE id = 'stale'"
            ).fetchone()
        assert json.loads(meta)["page"] == 2  # sibling keys kept
    finally:
        evict_engine(db)


def test_0008_is_idempotent_and_its_downgrade_a_noop(tmp_path: Path) -> None:
    """Re-running changes nothing; downgrading keeps the recomputed offsets."""
    db = _db_at_0007(tmp_path)
    _seed(db, "stale", _DIALOGUE, {"sentence_offsets": _old_split(_DIALOGUE)})
    try:
        upgrade_to(db, "0008")
        after = _offsets(db)
        downgrade_to(db, "0007")
        assert _offsets(db) == after
        upgrade_to(db, "0008")
        assert _offsets(db) == after
    finally:
        evict_engine(db)
