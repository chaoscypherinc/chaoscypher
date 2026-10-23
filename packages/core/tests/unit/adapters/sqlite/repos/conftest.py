# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The search repository's full-text tests share one database fixture and its writers.

``db`` is a fresh file-backed app.db with every SQLModel table; the helpers
write rows straight into it, as a writer that skips the search repository
would (a snapshot copied in, a hand edit), and open a repository over it.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.engine import evict_engine, get_engine
from chaoscypher_core.adapters.sqlite.repos.search import SearchRepository


DIM = 4


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Path]:
    """A fresh app.db with every SQLModel table; its cached engine is evicted afterwards."""
    path = tmp_path / "app.db"
    SQLModel.metadata.create_all(get_engine(str(path)), checkfirst=True)
    yield path
    evict_engine(path)


def open_search_repo(path: Path) -> SearchRepository:
    """A search repository over ``path`` (an open runs the full-text checks)."""
    return SearchRepository(get_engine(str(path)), vector_dim=DIM, embedding_model="m")


def reopen_search_repo(path: Path) -> SearchRepository:
    """A repository over ``path`` on a fresh engine, as the next process would open it."""
    evict_engine(path)
    return open_search_repo(path)


def add_graph_node(path: Path, nid: str, label: str, aliases: list[str]) -> None:
    """A node written straight into ``graph_nodes``, as a writer that skips the search index would."""
    with sqlite3.connect(path) as con:
        con.execute(
            "INSERT INTO graph_nodes (id, database_name, graph_name, template_id, label, "
            "properties, created_at, updated_at) VALUES (?, 'db', 'knowledge', 't', ?, ?, "
            "'2026-10-06', '2026-10-06')",
            (nid, label, json.dumps({"aliases": aliases})),
        )


def add_chunk(path: Path, cid: str, content: str) -> None:
    """A chunk written straight into ``document_chunks`` (the triggers index it)."""
    with sqlite3.connect(path) as con:
        con.execute(
            "INSERT INTO document_chunks (id, database_name, source_id, chunk_index, content, "
            "status, created_at, citation_offset_method) "
            "VALUES (?, 'db', 's1', 0, ?, 'indexed', '2026-10-06', 'none')",
            (cid, content),
        )


def keyword(repo: SearchRepository, query: str, item_type: str = "node") -> list[str]:
    """The ids a keyword search returns, best first."""
    return [i for i, _ in repo.keyword_search(query, item_type=item_type)]
