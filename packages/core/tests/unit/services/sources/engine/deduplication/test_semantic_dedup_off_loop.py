# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The semantic-dedup pair loop runs off the event loop.

``_find_semantic_duplicates`` is O(n^2) pure Python (seconds at a few thousand
same-type entities); run inline it blocks the single-worker LLM queue's loop
and every chat turn queued behind the extraction finalizer.
"""

from __future__ import annotations

import threading
from typing import Any
from unittest.mock import MagicMock

import pytest

from chaoscypher_core.services.sources.engine.deduplication import service as dedup_service_mod
from chaoscypher_core.services.sources.engine.deduplication.service import EntityProcessor


@pytest.mark.asyncio
async def test_pair_loop_runs_on_a_worker_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every pair comparison happens on a thread other than the loop's."""
    loop_thread = threading.get_ident()
    threads: list[int] = []
    real = dedup_service_mod.calculate_entity_similarity

    def _spy(*args: Any, **kwargs: Any) -> float:
        threads.append(threading.get_ident())
        return real(*args, **kwargs)

    monkeypatch.setattr(dedup_service_mod, "calculate_entity_similarity", _spy)
    entities = [{"name": f"Entity{i}", "type": "Person"} for i in range(3)]

    unique, _mapping, _embeddings = await EntityProcessor().deduplicate_entities_semantic(
        entities,
        embedding_service=MagicMock(),
        precomputed_embeddings=[[1.0, 0.0], [0.0, 1.0], [0.7, 0.7]],
    )

    assert len(unique) == 3
    assert threads, "the pair loop never ran"
    assert loop_thread not in threads
