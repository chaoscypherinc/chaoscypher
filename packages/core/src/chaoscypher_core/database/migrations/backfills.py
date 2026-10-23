# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Data backfills more than one migration runs: one definition each.

A chunk's ``chunk_metadata.sentence_offsets`` is derived data: the canonical
sentence splitter over the chunk's ``content``. Migration 0005 recomputed it
to repair a broken coordinate base; 0008 recomputes it again because the
splitter itself changed (it now breaks after a closing quote, audit C2), so
stored offsets stopped matching what ``chunk_hydration`` numbers live. Both
call :func:`recompute_chunk_sentence_offsets`, which since audit wave 5 also
clears the offsets of a chunk with no content and renumbers the stored
citations' ``local_index`` (:func:`recompute_citation_local_indices`); 0009
runs it once more for a database already past 0008. Since audit wave 6 a
citation is renumbered from its own place in the chunk (the span its old
``local_index`` named), not from the first occurrence of its text: a
sentence the chunk repeats keeps its own index.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any


def recompute_chunk_sentence_offsets(conn: Any) -> int:
    """Rewrite ``sentence_offsets`` of every chunk whose stored value differs from the splitter's.

    Uses the canonical splitter (``split_into_sentences_with_offsets``), so
    the stored offsets are exactly what the chunker stores today and what
    every consumer slices against. A chunk with no content has no sentences:
    stored offsets on one are cleared (until audit wave 5 such rows kept
    whatever they had). Then every citation's ``local_index`` is recomputed
    against the new offsets (:func:`recompute_citation_local_indices`).
    Deterministic and idempotent: a row that already matches is not written.
    Sibling metadata keys are kept; a NULL, unreadable or non-object
    ``chunk_metadata`` becomes ``{"sentence_offsets": ...}``.

    Args:
        conn: The migration's bound connection (``op.get_bind()``).

    Returns:
        The number of rows rewritten (chunks and citations).
    """
    import sqlalchemy as sa

    from chaoscypher_core.services.sources.engine.extraction.utils.sentence_splitter import (
        split_into_sentences_with_offsets,
    )

    rows = conn.execute(
        sa.text("SELECT id, content, chunk_metadata FROM document_chunks")
    ).fetchall()
    rewritten = 0
    previous: dict[str, Any] = {}
    for chunk_id, content, meta_json in rows:
        meta = _metadata(meta_json)
        previous[chunk_id] = meta.get("sentence_offsets")
        if content:
            new_offsets = split_into_sentences_with_offsets(content)
            if meta.get("sentence_offsets") == new_offsets:
                continue
        elif not meta.get("sentence_offsets"):
            continue
        else:
            new_offsets = []
        meta["sentence_offsets"] = new_offsets
        conn.execute(
            sa.text("UPDATE document_chunks SET chunk_metadata = :meta WHERE id = :id"),
            {"meta": json.dumps(meta), "id": chunk_id},
        )
        rewritten += 1
    return rewritten + recompute_citation_local_indices(conn, previous_offsets=previous)


def _metadata(meta_json: Any) -> dict[str, Any]:
    """A stored JSON object column as a dict (empty when NULL, unreadable or not an object)."""
    if isinstance(meta_json, dict):
        return dict(meta_json)
    try:
        meta = json.loads(meta_json) if meta_json else {}
    except json.JSONDecodeError, TypeError:
        meta = {}
    return meta if isinstance(meta, dict) else {}


CITATION_TABLES = ("source_citations", "relationship_citations")
"""The tables whose ``citation_metadata.sentences`` carry a chunk-local ``local_index``."""


def citation_position(
    item: Mapping[str, Any], content: str, offsets: Sequence[Any] | None
) -> int | None:
    """Where in its chunk a stored citation sentence stands, read from its own ``local_index``.

    The sentence the old ``local_index`` named under ``offsets`` (the
    offsets the index was numbered against) holds the citation's text at its
    start, or the occurrence of the text that starts inside that span: that
    occurrence's position. A sentence the chunk repeats is placed where its
    own index put it (until audit wave 6 every copy took the first one's,
    ``str.find``). Without an index (or offsets to read it by) the text is
    placed only when the chunk holds it once. ``None`` when the index's span no longer holds the
    text (the chunk was re-split or rewritten) or the text is nowhere, or
    twice with nothing to say which.

    Args:
        item: The citation sentence (``text``, ``local_index``).
        content: The chunk's content.
        offsets: The ``sentence_offsets`` the stored index refers to.

    Returns:
        The character position, or ``None``.
    """
    text = str(item.get("text") or "")
    if not text or not content:
        return None
    index = item.get("local_index")
    spans = list(offsets or [])
    if isinstance(index, int) and not isinstance(index, bool) and spans:
        if not 0 <= index < len(spans) or not isinstance(spans[index], Mapping):
            return None  # its own index names no sentence of the chunk it was numbered in
        start, end = spans[index].get("start"), spans[index].get("end")
        if isinstance(start, int) and isinstance(end, int):
            pos = content.find(text, start)
            return pos if 0 <= pos < max(end, start + 1) else None
        return None
    first = content.find(text)
    if first < 0 or content.find(text, first + 1) >= 0:
        return None
    return first


def recompute_citation_local_indices(
    conn: Any, *, previous_offsets: Mapping[str, Any] | None = None
) -> int:
    """Recompute every stored citation sentence's ``local_index`` against its chunk's offsets.

    A citation sentence (``{"text", "chunk_id", "local_index"}``, written at
    commit) numbers the sentence inside its chunk; when the splitter changes,
    the stored numbers point at other sentences. Each is placed by its own
    index under the offsets it was numbered against (``previous_offsets``,
    the chunk offsets before this backfill rewrote them; the stored ones
    when a chunk was not rewritten or none are given: :func:`citation_position`)
    and numbered as extraction numbers it
    (``sentence_splitter.local_sentence_index``); a citation whose own place
    no longer holds its text (the chunk was re-split) gets ``None``. Rows
    that already match are not written.

    Args:
        conn: The migration's bound connection.
        previous_offsets: Chunk id -> the ``sentence_offsets`` the stored
            indices were numbered against.

    Returns:
        The number of citation rows rewritten.
    """
    import sqlalchemy as sa

    from chaoscypher_core.services.sources.engine.extraction.utils.sentence_splitter import (
        local_sentence_index,
    )

    chunks = {
        cid: {"content": content or "", "chunk_metadata": _metadata(meta)}
        for cid, content, meta in conn.execute(
            sa.text("SELECT id, content, chunk_metadata FROM document_chunks")
        ).fetchall()
    }
    existing = {
        row[0]
        for row in conn.execute(
            sa.text("SELECT name FROM sqlite_master WHERE type = 'table'")
        ).fetchall()
    }
    rewritten = 0
    for table in CITATION_TABLES:
        if table not in existing:
            continue
        for row_id, meta_json in conn.execute(
            sa.text(
                f"SELECT id, citation_metadata FROM {table} WHERE citation_metadata IS NOT NULL"
            )
        ).fetchall():
            meta = _metadata(meta_json)
            sentences = meta.get("sentences")
            if not isinstance(sentences, list):
                continue
            changed = False
            for item in sentences:
                if not isinstance(item, dict) or item.get("chunk_id") not in chunks:
                    continue
                chunk_id = item["chunk_id"]
                chunk = chunks[chunk_id]
                numbered_against = (
                    previous_offsets[chunk_id]
                    if previous_offsets is not None and chunk_id in previous_offsets
                    else chunk["chunk_metadata"].get("sentence_offsets")
                )
                pos = citation_position(item, chunk["content"], numbered_against)
                index = local_sentence_index(chunk, pos) if pos is not None else None
                if item.get("local_index") != index:
                    item["local_index"] = index
                    changed = True
            if changed:
                conn.execute(
                    sa.text(f"UPDATE {table} SET citation_metadata = :meta WHERE id = :id"),
                    {"meta": json.dumps(meta), "id": row_id},
                )
                rewritten += 1
    return rewritten


__all__ = [
    "CITATION_TABLES",
    "citation_position",
    "recompute_chunk_sentence_offsets",
    "recompute_citation_local_indices",
]
