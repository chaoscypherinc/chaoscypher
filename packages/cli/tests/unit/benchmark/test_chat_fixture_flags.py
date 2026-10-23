# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The Book One chat fixture's ``answerable`` flags and the reference pack's copy of it."""

from __future__ import annotations

from pathlib import Path

from chaoscypher_cli.benchmark.discovery import builtin_dataset_root
from chaoscypher_core.benchmark.queries import BOARD_BANDS, load_queries


REPO = Path(__file__).resolve().parents[5]
BUILTIN = builtin_dataset_root() / "war_and_peace_book1" / "queries.yaml"
PACK = REPO / "internal" / "benchmarks" / "reference" / "war_and_peace_book1-v2" / "queries.yaml"

# Audited 2026-10-01 on the 0.6b pack (15 flagged) and re-audited 2026-10-02 on
# the war_and_peace_book1-v2 pack (entities merged and indexed with the product default embedder) with the hybrid reserve
# (internal/benchmarks/tools/retrieval_mode_check.py): the fact each asks for never
# reaches the GraphRAG context there. Changing this list is a scoring change.
# q008 added 2026-10-03: 'Horse Guards' reached none of the 22 models' contexts.
# q008, q031 and q050 unflagged 2026-10-21 with the 2.1 campaign's archive: each is answered
# by at least one rung's pinned context on pack v5f-literary (the fixture's notes say which).
UNANSWERABLE = {
    "q030",
    "q040",
    "q041",
}


def test_unanswerable_flags_are_the_audited_set_and_in_scope() -> None:
    """Exactly the audited questions are flagged, and none of them is out of scope."""
    queries = load_queries(BUILTIN).queries
    flagged = {q.id for q in queries if not q.answerable}
    assert flagged == UNANSWERABLE
    assert all(q.band != "out_of_scope" for q in queries if not q.answerable)
    assert sum(q.answerable for q in queries if q.band in BOARD_BANDS) == 77


def test_reference_pack_copy_matches_the_builtin_fixture() -> None:
    """The pack scores with the same flags the builtin fixture carries (private tree only)."""
    if not PACK.exists():  # the public export strips internal/
        return
    assert PACK.read_bytes() == BUILTIN.read_bytes()
