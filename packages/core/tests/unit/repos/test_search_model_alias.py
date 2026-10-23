# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""The CPU and Ollama spellings of one embedding model are one model to the search repository.

Gap 10 (2026-10-20): ``_check_model_change`` compared the stored and configured model names
as strings, so switching the same curated model between its local and Ollama names flagged a
full re-embed of vectors that were already in the right space.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chaoscypher_core.adapters.sqlite.engine import evict_engine, get_engine
from chaoscypher_core.adapters.sqlite.repos.search import SearchRepository
from chaoscypher_core.models import Node


def _repo(db_path: Path, model: str) -> SearchRepository:
    return SearchRepository(engine=get_engine(db_path), vector_dim=4, embedding_model=model)


@pytest.mark.parametrize(
    ("first", "second", "reindex"),
    [
        ("Qwen/Qwen3-Embedding-0.6B", "qwen3-embedding:0.6b", False),
        ("qwen3-embedding:0.6b", "Qwen/Qwen3-Embedding-0.6B", False),
        ("Qwen/Qwen3-Embedding-0.6B", "Qwen/Qwen3-Embedding-4B", True),
    ],
)
def test_an_alias_of_the_stored_model_needs_no_reindex(
    tmp_path: Path, first: str, second: str, reindex: bool
) -> None:
    db_path = tmp_path / "app.db"
    repo = _repo(db_path, first)
    repo.index_node(
        Node(id="n1", label="Pierre", template_id="person", properties={}, embedding=[1, 0, 0, 0])
    )
    evict_engine(db_path)
    reopened = _repo(db_path, second)
    try:
        assert reopened.needs_full_reindex is reindex
    finally:
        evict_engine(db_path)
