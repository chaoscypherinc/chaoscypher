# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Migration 0009: empty chunks lose stale offsets, citations are renumbered (audit wave 5).

0008 left a chunk with no content holding whatever offsets it had, and every
stored citation's ``citation_metadata.sentences[].local_index`` numbered its
sentence under the old splitter. The shared backfill now does both; 0009
runs it on a database already past 0008.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import chaoscypher_core.adapters.sqlite.models  # noqa: F401 — register metadata
from chaoscypher_core.adapters.sqlite.engine import evict_engine
from chaoscypher_core.database.migrations.runner import downgrade_to, upgrade_to


# Two speakers' turns, then prose: the old splitter read the turns as one sentence.
_CONTENT = (
    "“Well, Prince, so Genoa and Lucca are now just family estates.”\n\n"
    "“Heavens! what a virulent attack!” replied the prince. Pierre arrived late."
)
_CITED = "Pierre arrived late."


def _db_at_0008(tmp_path: Path) -> Path:
    """An empty database migrated to 0009's parent."""
    db = tmp_path / "app.db"
    sqlite3.connect(str(db)).close()
    upgrade_to(db, "0008")
    return db


def _seed(db: Path) -> None:
    """A chunk, an empty chunk with stale offsets, and one citation of each kind numbered wrongly."""
    with sqlite3.connect(str(db)) as conn:
        for cid, content, meta in (
            ("c1", _CONTENT, None),
            ("c0", "", {"sentence_offsets": [{"start": 0, "end": 12}], "page": 1}),
        ):
            conn.execute(
                "INSERT INTO document_chunks "
                "(id, database_name, chunk_index, content, chunk_metadata, status, "
                " created_at, citation_offset_method) "
                "VALUES (?, 'default', 0, ?, ?, 'committed', '2026-06-01T00:00:00', 'exact')",
                (cid, content, json.dumps(meta) if meta is not None else None),
            )
        stale = json.dumps(
            {"sent_ref": "S3", "sentences": [{"text": _CITED, "chunk_id": "c1", "local_index": 1}]}
        )
        conn.execute(
            "INSERT INTO source_citations (id, database_name, entity_uri, entity_label, chunk_id, "
            "confidence, extraction_method, created_at, citation_metadata) "
            "VALUES ('s1', 'default', 'n:1', 'Pierre', 'c1', 1.0, 'ai_extraction', "
            "'2026-06-01T00:00:00', ?)",
            (stale,),
        )
        conn.execute(
            "INSERT INTO relationship_citations (id, database_name, edge_id, edge_label, "
            "source_entity_label, target_entity_label, chunk_id, confidence, extraction_method, "
            "created_at, citation_metadata) VALUES ('r1', 'default', 'e:1', 'knows', 'Pierre', "
            "'the prince', 'c1', 1.0, 'ai_extraction', '2026-06-01T00:00:00', ?)",
            (stale,),
        )
        conn.commit()


def _state(db: Path) -> tuple[dict[str, object], list[object]]:
    """Every chunk's metadata and every citation's sentence numbers."""
    with sqlite3.connect(str(db)) as conn:
        chunks = {
            cid: json.loads(meta) if meta else None
            for cid, meta in conn.execute("SELECT id, chunk_metadata FROM document_chunks")
        }
        numbers = [
            [s["local_index"] for s in json.loads(meta)["sentences"]]
            for table in ("source_citations", "relationship_citations")
            for (meta,) in conn.execute(f"SELECT citation_metadata FROM {table}")
        ]
    return chunks, numbers


def test_0009_clears_empty_chunks_and_renumbers_citations(tmp_path: Path) -> None:
    """The empty chunk has no sentences; ``Pierre arrived late.`` is sentence 2 of its chunk."""
    from chaoscypher_core.services.sources.engine.extraction.utils.sentence_splitter import (
        split_into_sentences_with_offsets,
    )

    db = _db_at_0008(tmp_path)
    _seed(db)
    try:
        upgrade_to(db, "0009")
        chunks, numbers = _state(db)
        assert chunks["c0"] == {"sentence_offsets": [], "page": 1}
        offsets = split_into_sentences_with_offsets(_CONTENT)
        assert chunks["c1"] == {"sentence_offsets": offsets}
        expected = next(
            i for i, span in enumerate(offsets) if _CONTENT[span["start"] :].startswith(_CITED)
        )
        assert expected == 2
        assert numbers == [[expected], [expected]]
    finally:
        evict_engine(db)


def test_0009_is_idempotent_and_its_downgrade_a_noop(tmp_path: Path) -> None:
    """Re-running changes nothing; downgrading keeps the recomputed values."""
    db = _db_at_0008(tmp_path)
    _seed(db)
    try:
        upgrade_to(db, "0009")
        after = _state(db)
        downgrade_to(db, "0008")
        assert _state(db) == after
        upgrade_to(db, "0009")
        assert _state(db) == after
    finally:
        evict_engine(db)
