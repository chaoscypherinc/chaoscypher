# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""LLMHealth.missing_models for the ollama provider."""

from __future__ import annotations

import pytest

from chaoscypher_core.services.llm.health import LLMHealth, get_llm_health


@pytest.mark.asyncio
async def test_missing_models_lists_configured_models_not_in_tags(
    monkeypatch,
    ollama_settings_factory,
):
    """If ollama_extraction_model is set to a name not in /api/tags, it
    must appear in missing_models. Chat and vision behave the same.
    """
    settings = ollama_settings_factory(
        chat_model="qwen3:30b-instruct",
        extraction_model="qwen3:30b-instruct",
        vision_model="qwen2-vl:7b",
    )

    async def fake_pulled(_settings):
        return {"qwen3:30b-instruct"}

    monkeypatch.setattr(
        "chaoscypher_core.services.llm.health._ollama_pulled_models",
        fake_pulled,
    )

    health = await get_llm_health(settings)
    assert isinstance(health, LLMHealth)
    # Extraction model is set explicitly and equals chat model — present.
    # Vision model qwen2-vl:7b is configured but not pulled — missing.
    assert "qwen2-vl:7b" in health.missing_models
    assert "qwen3:30b-instruct" not in health.missing_models


@pytest.mark.asyncio
async def test_missing_models_empty_for_cloud_providers(openai_settings):
    """missing_models is ollama-only; cloud providers return empty."""
    health = await get_llm_health(openai_settings)
    assert health.missing_models == ()


@pytest.mark.asyncio
async def test_missing_models_empty_when_ollama_unreachable(
    monkeypatch,
    ollama_settings_factory,
):
    """If /api/tags can't be reached, we can't claim models are missing —
    keep verified=False (existing behavior) and missing_models=() so the
    UI surfaces the upstream connection problem, not a false-positive
    "pull this model" prompt.
    """
    settings = ollama_settings_factory(extraction_model="qwen3:30b-instruct")

    async def fake_unreachable(_settings):
        return None  # Sentinel for "could not reach"

    monkeypatch.setattr(
        "chaoscypher_core.services.llm.health._ollama_pulled_models",
        fake_unreachable,
    )

    health = await get_llm_health(settings)
    assert health.missing_models == ()
