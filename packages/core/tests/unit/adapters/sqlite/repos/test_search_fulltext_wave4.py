# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""A dropped or emptied full-text index is rebuilt on open; a same-length edit is seen (audit wave 4).

Until 2026-10-06 the open-time checks compared the content tables only, never
the FTS5 index over them: ``CREATE VIRTUAL TABLE IF NOT EXISTS`` recreated a
dropped index empty, the content tables' stamps still matched, and the failed
integrity check only logged a warning. On the v3 Book One index a dropped chunk
index changed all 120 benchmark contexts and a dropped node index 96, and no
retrieval was refused. The node index's stamp (counts, lengths, newest
``updated_at``) also missed a label edited to another of the same length.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from structlog.testing import capture_logs

from tests.unit.adapters.sqlite.repos.conftest import (
    add_chunk,
    add_graph_node,
    keyword,
    open_search_repo,
    reopen_search_repo,
)


@pytest.fixture
def indexed(db: Path) -> Path:
    """A database whose node and chunk indexes hold one row each, checked once."""
    add_graph_node(db, "n1", "Corvin Halváne", ["Corvin"])
    add_chunk(db, "c1", "Corvin rode to Kessel.")
    repo = open_search_repo(db)
    assert keyword(repo, "Corvin") == ["n1"]
    assert keyword(repo, "Kessel", "chunk") == ["chunk:c1"]
    return db


def _execute(path: Path, *statements: str) -> None:
    with sqlite3.connect(path) as con:
        for statement in statements:
            con.execute(statement)


def _reopened_with_logs(path: Path) -> tuple[object, list[dict[str, object]]]:
    with capture_logs() as logs:
        repo = reopen_search_repo(path)
    return repo, logs


@pytest.mark.parametrize(
    ("statement", "index"),
    [
        ("DROP TABLE fulltext_index_chunks", "fulltext_index_chunks"),
        (
            "INSERT INTO fulltext_index_chunks(fulltext_index_chunks) VALUES ('delete-all')",
            "fulltext_index_chunks",
        ),
        ("DROP TABLE fulltext_index", "fulltext_index"),
        ("INSERT INTO fulltext_index(fulltext_index) VALUES ('delete-all')", "fulltext_index"),
    ],
    ids=["chunk-dropped", "chunk-emptied", "node-dropped", "node-emptied"],
)
def test_a_dropped_or_emptied_index_is_rebuilt_on_open(
    indexed: Path, statement: str, index: str
) -> None:
    _execute(indexed, statement)
    repo, logs = _reopened_with_logs(indexed)
    assert keyword(repo, "Corvin") == ["n1"]
    assert keyword(repo, "Kessel", "chunk") == ["chunk:c1"]
    assert repo.check_fts5_integrity() == (True, None)  # type: ignore[attr-defined]
    drift = [e for e in logs if e["event"] == "search_index_drift_detected"]
    assert [e["index"] for e in drift] == [index]
    assert any(e["event"] == "search_index_repaired" and e["index"] == index for e in logs)


def test_a_healthy_open_rebuilds_nothing(indexed: Path) -> None:
    _, logs = _reopened_with_logs(indexed)
    assert not [e for e in logs if str(e["event"]).startswith("search_index_")]
    assert not [e for e in logs if str(e["event"]).endswith("_rebuilt")]


def test_the_integrity_check_reports_an_emptied_index(indexed: Path) -> None:
    repo = open_search_repo(indexed)
    _execute(
        indexed, "INSERT INTO fulltext_index_chunks(fulltext_index_chunks) VALUES ('delete-all')"
    )
    ok, error = repo.check_fts5_integrity()
    assert not ok
    assert "fulltext_index_chunks indexes 0 of the 1 rows" in str(error)


def test_a_same_length_label_edit_without_updated_at_is_seen_on_open(indexed: Path) -> None:
    """``Corvin`` -> ``Morvin`` in place: every count and length as before, ``updated_at`` untouched."""
    _execute(
        indexed,
        "UPDATE graph_nodes SET label = 'Morvin Halváne', "
        "properties = '{\"aliases\": [\"Morvin\"]}' WHERE id = 'n1'",
    )
    repo = reopen_search_repo(indexed)
    assert keyword(repo, "Morvin") == ["n1"]
    assert keyword(repo, "Corvin") == []
