# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Migration 0012: the shared ``local_index`` backfill runs once more, under its wave-6 rule.

0009 shipped renumbering citations from the first occurrence of their text
(``str.find``); the shared body now places each by its own index under the
offsets it was numbered against. 0012 runs that body again: a chunk with
stale offsets is re-split and its citations follow their own sentence (the
second copy of a repeated sentence stays the second), a canonical row is not
touched, and the numbering the wave-5 0009 left (every copy the first one's)
is a fixed point it cannot undo (pinned here, recorded in the migration).
"""

from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING

import chaoscypher_core.adapters.sqlite.models  # noqa: F401 — register metadata
from chaoscypher_core.adapters.sqlite.engine import evict_engine
from chaoscypher_core.database.migrations.runner import downgrade_to, upgrade_to


if TYPE_CHECKING:
    from pathlib import Path


_REPEATED = "Pierre arrived late."
_CONTENT = f"{_REPEATED} The prince spoke. {_REPEATED}"
"""A chunk that writes one sentence twice: the cited copy is the second."""

_SECOND = _CONTENT.rindex(_REPEATED)


def _offsets() -> list[dict[str, int]]:
    """The canonical splitter's offsets of :data:`_CONTENT`."""
    from chaoscypher_core.services.sources.engine.extraction.utils.sentence_splitter import (
        split_into_sentences_with_offsets,
    )

    return split_into_sentences_with_offsets(_CONTENT)


def _index_at(position: int) -> int:
    """The canonical sentence index starting at ``position`` in :data:`_CONTENT`."""
    return next(i for i, span in enumerate(_offsets()) if span["start"] == position)


def _db_at_0011(tmp_path: Path) -> Path:
    """An empty database migrated to 0012's parent."""
    db = tmp_path / "app.db"
    sqlite3.connect(str(db)).close()
    upgrade_to(db, "0011")
    return db


def _seed(db: Path) -> None:
    """Three chunks of one text and one citation each of the second copy of the repeated sentence.

    ``stale``: offsets an older splitter wrote (the first two sentences as one),
    the citation numbered 1 under them (the second copy). ``canonical``: the
    splitter's offsets, the citation numbered as the commit numbers it.
    ``collapsed``: the citation numbered 0, as the wave-5 0009 (``str.find``)
    left the second copy.
    """
    stale_offsets = [
        {"start": 0, "end": _SECOND - 1},
        {"start": _SECOND, "end": len(_CONTENT)},
    ]
    canonical = {"sentence_offsets": _offsets()}
    rows = (
        ("stale", {"sentence_offsets": stale_offsets, "page": 3}, 1),
        ("canonical", canonical, _index_at(_SECOND)),
        ("collapsed", canonical, _index_at(0)),
    )
    conn = sqlite3.connect(str(db))
    try:
        for n, (cid, meta, index) in enumerate(rows):
            conn.execute(
                "INSERT INTO document_chunks "
                "(id, database_name, chunk_index, content, chunk_metadata, status, "
                " created_at, citation_offset_method) "
                "VALUES (?, 'default', ?, ?, ?, 'committed', '2026-06-01T00:00:00', 'exact')",
                (cid, n, _CONTENT, json.dumps(meta)),
            )
            cited = json.dumps(
                {
                    "sent_ref": "S3",
                    "sentences": [{"text": _REPEATED, "chunk_id": cid, "local_index": index}],
                }
            )
            conn.execute(
                "INSERT INTO source_citations (id, database_name, entity_uri, entity_label, "
                "chunk_id, confidence, extraction_method, created_at, citation_metadata) "
                "VALUES (?, 'default', 'n:1', 'Pierre', ?, 1.0, 'ai_extraction', "
                "'2026-06-01T00:00:00', ?)",
                (f"s-{cid}", cid, cited),
            )
            conn.execute(
                "INSERT INTO relationship_citations (id, database_name, edge_id, edge_label, "
                "source_entity_label, target_entity_label, chunk_id, confidence, "
                "extraction_method, created_at, citation_metadata) VALUES (?, 'default', 'e:1', "
                "'knows', 'Pierre', 'the prince', ?, 1.0, 'ai_extraction', "
                "'2026-06-01T00:00:00', ?)",
                (f"r-{cid}", cid, cited),
            )
        conn.commit()
    finally:
        conn.close()


def _state(db: Path) -> tuple[dict[str, object], dict[str, list[object]]]:
    """Every chunk's metadata, and every citation's sentence numbers by row id."""
    conn = sqlite3.connect(str(db))
    try:
        chunks = {
            cid: json.loads(meta) if meta else None
            for cid, meta in conn.execute("SELECT id, chunk_metadata FROM document_chunks")
        }
        numbers = {
            row_id: [s["local_index"] for s in json.loads(meta)["sentences"]]
            for table in ("source_citations", "relationship_citations")
            for row_id, meta in conn.execute(f"SELECT id, citation_metadata FROM {table}")
        }
    finally:
        conn.close()
    return chunks, numbers


def test_0012_renumbers_a_stale_chunk_by_the_citation_own_place(tmp_path: Path) -> None:
    """The stale chunk is re-split; its citation is the second copy, not the first ``str.find`` gives."""
    db = _db_at_0011(tmp_path)
    _seed(db)
    try:
        upgrade_to(db, "0012")
        chunks, numbers = _state(db)
        assert chunks["stale"] == {"sentence_offsets": _offsets(), "page": 3}
        second = _index_at(_SECOND)
        assert second == 2
        assert _CONTENT.find(_REPEATED) == 0  # the wave-5 rule would have numbered it 0
        assert numbers["s-stale"] == numbers["r-stale"] == [second]
    finally:
        evict_engine(db)


def test_0012_leaves_canonical_rows_and_the_collapsed_numbering_as_they_are(
    tmp_path: Path,
) -> None:
    """A canonical citation keeps its number; the wave-5 0009's collapsed one is a fixed point."""
    db = _db_at_0011(tmp_path)
    _seed(db)
    try:
        before = _state(db)
        upgrade_to(db, "0012")
        chunks, numbers = _state(db)
        for cid in ("canonical", "collapsed"):
            assert chunks[cid] == before[0][cid]
            assert numbers[f"s-{cid}"] == before[1][f"s-{cid}"]
            assert numbers[f"r-{cid}"] == before[1][f"r-{cid}"]
        assert numbers["s-canonical"] == [_index_at(_SECOND)]
        assert numbers["s-collapsed"] == [0]  # not restorable: the rows do not say which copy
    finally:
        evict_engine(db)


def test_0012_is_idempotent_and_its_downgrade_a_noop(tmp_path: Path) -> None:
    """A second run changes nothing; downgrading keeps the recomputed values."""
    db = _db_at_0011(tmp_path)
    _seed(db)
    try:
        upgrade_to(db, "0012")
        after = _state(db)
        downgrade_to(db, "0011")
        assert _state(db) == after
        upgrade_to(db, "0012")
        assert _state(db) == after
    finally:
        evict_engine(db)
