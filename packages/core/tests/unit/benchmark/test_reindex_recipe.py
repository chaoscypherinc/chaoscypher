# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""reindex_recipe: computed from the embedding text functions, so a text change cannot go unnoticed."""

from __future__ import annotations

import pytest

from chaoscypher_core.benchmark import reindex


def test_the_recipe_is_stable_and_moves_with_either_text(monkeypatch: pytest.MonkeyPatch) -> None:
    first = reindex.reindex_recipe()
    assert first == reindex.reindex_recipe() and len(first) == 12
    monkeypatch.setattr(reindex, "node_embedding_text", lambda node: str(node.label))
    assert reindex.reindex_recipe() != first
    monkeypatch.undo()
    monkeypatch.setattr(reindex, "chunk_embedding_text", lambda chunk: chunk["content"].upper())
    assert reindex.reindex_recipe() != first
