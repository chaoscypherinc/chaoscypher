# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""No unit test loads the local embedding model (``chaoscypher_core.testing.network_fixtures``).

Eleven tests did on 2026-10-26, each downloading Qwen3-Embedding-0.6B (1.2 GB) into its own
temporary data directory and holding it in memory; most of them through code that catches an
embedding failure and carries on, so the guard fails the test itself, not only the load.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from chaoscypher_core.adapters.embedding.local_provider import LocalEmbeddingProvider
from chaoscypher_core.testing import network_fixtures
from chaoscypher_core.testing.network_fixtures import TestLoadedEmbeddingModelError, ollama_offline


def _provider(cache_dir: Path) -> LocalEmbeddingProvider:
    return LocalEmbeddingProvider("Qwen/Qwen3-Embedding-0.6B", 1024, cache_dir)


def test_a_load_is_refused_and_names_the_test(tmp_path: Path) -> None:
    with pytest.raises(TestLoadedEmbeddingModelError, match="test_a_load_is_refused_and_names"):
        _provider(tmp_path / "models")._ensure_model()
    assert not (tmp_path / "models").exists(), "the guard let the download start"
    network_fixtures._state["model_loads"].clear()  # this test meant to load it


def test_a_swallowed_load_still_fails_the_test(tmp_path: Path) -> None:
    """Driven by hand around a body that catches the error, as the commit's template step does."""
    request = SimpleNamespace(
        node=SimpleNamespace(nodeid="probe::test_swallows", get_closest_marker=lambda _name: None)
    )
    gen = ollama_offline.__wrapped__(request)  # type: ignore[attr-defined]  # pytest.fixture sets __wrapped__ at runtime
    next(gen)
    try:
        _provider(tmp_path)._ensure_model()
    except TestLoadedEmbeddingModelError:
        pass
    with pytest.raises(pytest.fail.Exception, match="probe::test_swallows loaded the local"):
        next(gen)


def test_a_patched_sentence_transformer_is_let_through(tmp_path: Path) -> None:
    """The provider's own tests fake the class; the guard stands aside for them."""
    with patch("sentence_transformers.SentenceTransformer", return_value=MagicMock()):
        assert _provider(tmp_path)._ensure_model() is not None
