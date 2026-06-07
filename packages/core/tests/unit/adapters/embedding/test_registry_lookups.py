# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Coverage for the curated embedding model registry.

Exercises ``get_default_model``, ``resolve_model_name`` (local / ollama /
openai / gemini, plus the None misses), and ``get_curated_dimensions`` across
curated, cloud, and display-name identifiers.
"""

from __future__ import annotations

from chaoscypher_core.adapters.embedding.registry import (
    get_curated_dimensions,
    get_default_model,
    resolve_model_name,
)


# ---------------------------------------------------------------------------
# get_default_model
# ---------------------------------------------------------------------------


def test_get_default_model_returns_flagged_default() -> None:
    """The default model is the curated entry flagged default=True."""
    model = get_default_model()
    assert model.default is True
    assert model.name == "Qwen3 Embedding 0.6B"
    assert model.local == "Qwen/Qwen3-Embedding-0.6B"


# ---------------------------------------------------------------------------
# resolve_model_name
# ---------------------------------------------------------------------------


def test_resolve_local_by_display_name() -> None:
    """A curated display name resolves to the local HF id for provider 'local'."""
    assert resolve_model_name("Qwen3 Embedding 0.6B", "local") == "Qwen/Qwen3-Embedding-0.6B"


def test_resolve_local_by_ollama_tag_returns_local_id() -> None:
    """Matching on the ollama tag still returns the local id for provider 'local'."""
    assert resolve_model_name("qwen3-embedding:0.6b", "local") == "Qwen/Qwen3-Embedding-0.6B"


def test_resolve_ollama_returns_ollama_tag() -> None:
    """For provider 'ollama' the ollama tag is returned."""
    assert resolve_model_name("Qwen3 Embedding 0.6B", "ollama") == "qwen3-embedding:0.6b"


def test_resolve_curated_miss_returns_none() -> None:
    """An unknown curated identifier resolves to None."""
    assert resolve_model_name("does-not-exist", "local") is None


def test_resolve_openai_by_model_id() -> None:
    """OpenAI cloud models resolve by their model id."""
    assert resolve_model_name("text-embedding-3-large", "openai") == "text-embedding-3-large"


def test_resolve_gemini_by_display_name() -> None:
    """Gemini cloud models resolve by display name to their model id."""
    assert resolve_model_name("Gemini Embedding 001", "gemini") == "gemini-embedding-001"


def test_resolve_cloud_miss_returns_none() -> None:
    """An unknown cloud identifier resolves to None."""
    assert resolve_model_name("nonexistent-cloud-model", "openai") is None


def test_resolve_unknown_provider_returns_none() -> None:
    """A provider with no curated/cloud entries yields None."""
    assert resolve_model_name("anything", "huggingface") is None


# ---------------------------------------------------------------------------
# get_curated_dimensions
# ---------------------------------------------------------------------------


def test_dimensions_curated_by_local_id() -> None:
    """Dimensions are found by a curated local id."""
    assert get_curated_dimensions("Qwen/Qwen3-Embedding-4B") == 2560


def test_dimensions_curated_by_ollama_tag() -> None:
    """Dimensions are found by a curated ollama tag."""
    assert get_curated_dimensions("bge-m3") == 1024


def test_dimensions_cloud_by_model_id() -> None:
    """Dimensions are found by a cloud model id."""
    assert get_curated_dimensions("text-embedding-3-small") == 1536


def test_dimensions_by_display_name() -> None:
    """Dimensions are found by a display name (cloud)."""
    assert get_curated_dimensions("Gemini Embedding 2 Preview") == 3072


def test_dimensions_unknown_returns_none() -> None:
    """An unknown identifier yields None dimensions."""
    assert get_curated_dimensions("mystery-model") is None
