# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for ModelConfig + cost calculation + filtering.

Note: model lists are no longer parsed standalone - they're embedded in
named benchmark configs. Config-loading tests live in test_config.py;
this file covers the underlying ModelConfig / compute_cost / filter_models
primitives used by both.
"""

from __future__ import annotations

from chaoscypher_cli.benchmark.models import (
    ModelConfig,
    assert_registry_coverage,
    compute_cost,
    filter_models,
)


def test_filter_models_local_only_strips_commercial():
    models = [
        ModelConfig(provider="ollama", model="x", label="X"),
        ModelConfig(provider="openai", model="gpt-4o", label="GPT-4o"),
        ModelConfig(provider="anthropic", model="claude", label="C"),
    ]
    local_only = filter_models(models, local_only=True)
    assert [m.provider for m in local_only] == ["ollama"]


def test_filter_models_by_kind_keeps_unscoped_models():
    """Models with kinds=None are unscoped and run on every kind."""
    models = [
        ModelConfig(provider="ollama", model="big", label="Big"),
        ModelConfig(provider="ollama", model="small", label="Small", kinds=["extraction"]),
        ModelConfig(provider="ollama", model="chat-only", label="C", kinds=["chat"]),
    ]
    extraction_models = filter_models(models, kind="extraction")
    assert [m.model for m in extraction_models] == ["big", "small"]


def test_compute_cost_for_known_commercial_model():
    """Use a known model from the price registry (gpt-4o)."""
    m = ModelConfig(provider="openai", model="gpt-4o", label="GPT-4o")
    cost = compute_cost(m, input_tokens=1_000_000, output_tokens=1_000_000)
    # Just assert positive - exact prices live in the registry and may shift.
    assert cost is not None
    assert cost > 0


def test_model_id_combines_provider_and_model():
    m = ModelConfig(provider="ollama", model="llama3.1:8b", label="L")
    assert m.model_id == "ollama/llama3.1:8b"


def test_compute_cost_uses_registry_opus_price():
    m = ModelConfig(provider="anthropic", model="claude-opus-4-8", label="Opus")
    cost = compute_cost(m, input_tokens=1_000_000, output_tokens=1_000_000)
    assert cost == 5.00 + 25.00  # corrected registry price


def test_compute_cost_local_is_zero():
    m = ModelConfig(provider="ollama", model="llama3.1:8b", label="L")
    assert compute_cost(m, input_tokens=1000, output_tokens=1000) == 0.0


def test_compute_cost_unknown_commercial_is_none():
    m = ModelConfig(provider="openai", model="not-in-registry", label="X")
    assert compute_cost(m, input_tokens=1000, output_tokens=1000) is None


def test_assert_registry_coverage_flags_unpriced_commercial():
    good = ModelConfig(provider="anthropic", model="claude-opus-4-8", label="Opus")
    bad = ModelConfig(provider="openai", model="ghost", label="Ghost")
    local = ModelConfig(provider="ollama", model="m", label="M")
    assert assert_registry_coverage([good, bad, local]) == ["openai/ghost"]
