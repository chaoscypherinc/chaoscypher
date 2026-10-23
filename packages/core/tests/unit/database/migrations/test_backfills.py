# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The citation renumbering reads each citation's own place, not its text's first occurrence (audit wave 6).

``backfills.recompute_citation_local_indices`` (run by migrations 0005, 0008
and 0009) found every cited sentence with ``str.find``: a sentence a chunk
repeats ("He bowed." twice) took the first copy's number whichever copy was
cited. Now the stored ``local_index`` names a span of the offsets it was
numbered against (the chunk's offsets before the backfill rewrote them), and
the text found from that span's start is renumbered under the new offsets; a
span that no longer holds the text (the chunk was re-split) gives ``None``.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
import sqlalchemy as sa

from chaoscypher_core.database.migrations.backfills import (
    citation_position,
    recompute_chunk_sentence_offsets,
)
from chaoscypher_core.services.sources.engine.extraction.utils.sentence_splitter import (
    split_into_sentences_with_offsets,
)


_REPEATED = "He bowed. She smiled at him and said nothing at all. He bowed."
_MERGED_OLD = [{"start": 0, "end": 52}, {"start": 53, "end": 62}]
"""What an older splitter stored: the first two sentences as one."""


@pytest.fixture
def conn() -> Any:
    """An in-memory database with the three tables the backfill reads."""
    engine = sa.create_engine("sqlite://")
    with engine.begin() as c:
        c.execute(
            sa.text("CREATE TABLE document_chunks (id TEXT, content TEXT, chunk_metadata TEXT)")
        )
        for table in ("source_citations", "relationship_citations"):
            c.execute(sa.text(f"CREATE TABLE {table} (id TEXT, citation_metadata TEXT)"))
    with engine.begin() as c:
        yield c


def _chunk(conn: Any, cid: str, content: str, offsets: list[dict[str, int]] | None) -> None:
    """Insert a chunk with its stored offsets."""
    meta = json.dumps({"sentence_offsets": offsets}) if offsets is not None else None
    conn.execute(
        sa.text("INSERT INTO document_chunks VALUES (:id, :c, :m)"),
        {"id": cid, "c": content, "m": meta},
    )


def _cite(conn: Any, cid: str, *sentences: tuple[str, int | None]) -> None:
    """Insert one entity citation of ``sentences`` on chunk ``cid``."""
    meta = {"sentences": [{"text": t, "chunk_id": cid, "local_index": i} for t, i in sentences]}
    conn.execute(
        sa.text("INSERT INTO source_citations VALUES (:id, :m)"),
        {"id": f"s-{cid}", "m": json.dumps(meta)},
    )


def _numbers(conn: Any) -> list[int | None]:
    """The stored citation's sentence numbers, in order."""
    (meta,) = conn.execute(sa.text("SELECT citation_metadata FROM source_citations")).one()
    return [s["local_index"] for s in json.loads(meta)["sentences"]]


def test_a_repeated_sentence_keeps_its_own_index(conn: Any) -> None:
    """The second "He bowed." (old index 1) is the third sentence now, not the first."""
    new = split_into_sentences_with_offsets(_REPEATED)
    assert len(new) == 3 and new != _MERGED_OLD
    _chunk(conn, "c1", _REPEATED, _MERGED_OLD)
    _cite(conn, "c1", ("He bowed.", 1), ("He bowed.", 0))
    recompute_chunk_sentence_offsets(conn)
    assert _numbers(conn) == [2, 0]


def test_a_citation_whose_chunk_was_re_split_gets_none(conn: Any) -> None:
    """The old span no longer holds the text: no number, as the docstring says."""
    _chunk(
        conn, "c1", "A new opening. He bowed.", [{"start": 0, "end": 30}, {"start": 31, "end": 40}]
    )
    _cite(conn, "c1", ("Somebody else spoke here.", 0), ("He bowed.", 5))
    recompute_chunk_sentence_offsets(conn)
    assert _numbers(conn) == [None, None]


def test_without_an_index_a_sentence_is_placed_only_when_the_chunk_holds_it_once(conn: Any) -> None:
    """No stored number: a unique sentence is found; a repeated one cannot be told apart."""
    _chunk(conn, "c1", _REPEATED, None)
    _cite(conn, "c1", ("She smiled at him and said nothing at all.", None), ("He bowed.", None))
    recompute_chunk_sentence_offsets(conn)
    assert _numbers(conn) == [1, None]


def test_the_backfill_is_idempotent(conn: Any) -> None:
    """A second run changes nothing: the indices now refer to the offsets stored."""
    _chunk(conn, "c1", _REPEATED, _MERGED_OLD)
    _cite(conn, "c1", ("He bowed.", 1))
    recompute_chunk_sentence_offsets(conn)
    first = _numbers(conn)
    assert recompute_chunk_sentence_offsets(conn) == 0
    assert _numbers(conn) == first == [2]


@pytest.mark.parametrize(
    ("item", "offsets", "position"),
    [
        ({"text": "He bowed.", "local_index": 1}, _MERGED_OLD, 53),
        ({"text": "He bowed.", "local_index": 0}, _MERGED_OLD, 0),
        ({"text": "He bowed.", "local_index": 7}, _MERGED_OLD, None),
        ({"text": "He bowed.", "local_index": True}, _MERGED_OLD, None),  # no index: two copies
        ({"text": "She smiled", "local_index": None}, _MERGED_OLD, 10),
        ({"text": "", "local_index": 0}, _MERGED_OLD, None),
    ],
)
def test_citation_position(
    item: dict[str, Any], offsets: list[dict[str, int]], position: int | None
) -> None:
    """The citation's own span; an index out of range or a bool is no index (two copies: no guess)."""
    assert citation_position(item, _REPEATED, offsets) == position
