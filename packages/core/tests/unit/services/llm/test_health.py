# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Contract tests for the LLM health predicate.

The same predicate drives the frontend banner and the import / chat
action gates — drift between the two would let the banner go green
while the gate still 409s (or vice versa).

The 2026-05-22 reshape made the predicate real-time: ``verified``
reflects current reachability (Ollama) or API-key format validity
(cloud), not a sticky gesture-based tracker.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from chaoscypher_core.app_config import Settings
from chaoscypher_core.exceptions import LLMNotVerifiedError
from chaoscypher_core.services.llm.health import (
    get_llm_health,
    require_llm_verified,
)


def _make_settings(**overrides) -> Settings:
    """Build a minimal Settings instance from defaults + overrides."""
    return Settings(**overrides)


@pytest.mark.asyncio
async def test_ollama_unreachable_is_unverified() -> None:
    """When /api/tags is unreachable, verified=False."""
    settings = _make_settings()
    with patch(
        "chaoscypher_core.services.llm.health._ollama_pulled_models",
        return_value=None,
    ):
        health = await get_llm_health(settings)
    assert health.provider == "ollama"
    assert health.configured is True  # ollama_instances has a seeded entry
    assert health.verified is False
    assert health.last_verified_at_iso is None


@pytest.mark.asyncio
async def test_ollama_reachable_flips_verified_true() -> None:
    """Reaching /api/tags is sufficient — no manual Verify gesture needed."""
    settings = _make_settings()
    with patch(
        "chaoscypher_core.services.llm.health._ollama_pulled_models",
        return_value={"qwen3:30b-instruct"},
    ):
        health = await get_llm_health(settings)
    assert health.verified is True
    assert health.last_verified_at_iso is not None


@pytest.mark.asyncio
async def test_openai_unconfigured_when_no_key() -> None:
    settings = _make_settings(llm={"chat_provider": "openai"})
    health = await get_llm_health(settings)
    assert health.provider == "openai"
    assert health.configured is False
    assert health.verified is False


@pytest.mark.asyncio
async def test_openai_format_valid_key_flips_verified_true() -> None:
    """A format-valid OpenAI key (sk- prefix + length) → verified=True."""
    settings = _make_settings(
        llm={
            "chat_provider": "openai",
            "openai_api_key": "sk-fake1234567890abcdefghij",
        }
    )
    health = await get_llm_health(settings)
    assert health.provider == "openai"
    assert health.configured is True
    assert health.verified is True


@pytest.mark.asyncio
async def test_openai_too_short_key_stays_unverified() -> None:
    """A placeholder-shaped key fails the length floor."""
    settings = _make_settings(
        llm={
            "chat_provider": "openai",
            "openai_api_key": "sk-x",  # too short
        }
    )
    health = await get_llm_health(settings)
    assert health.verified is False


@pytest.mark.asyncio
async def test_anthropic_requires_sk_ant_prefix() -> None:
    settings = _make_settings(
        llm={
            "chat_provider": "anthropic",
            "anthropic_api_key": "sk-ant-fake1234567890abcdef",
        }
    )
    health = await get_llm_health(settings)
    assert health.verified is True


@pytest.mark.asyncio
async def test_anthropic_wrong_prefix_unverified() -> None:
    settings = _make_settings(
        llm={
            "chat_provider": "anthropic",
            "anthropic_api_key": "sk-fakekey1234567890abcdef",  # missing -ant-
        }
    )
    health = await get_llm_health(settings)
    assert health.verified is False


@pytest.mark.asyncio
async def test_gemini_format_valid_key_flips_verified_true() -> None:
    settings = _make_settings(
        llm={
            "chat_provider": "gemini",
            "gemini_api_key": "AIzaSyFakeKeyExampleForTestingPurposes12345",
        }
    )
    health = await get_llm_health(settings)
    assert health.verified is True


@pytest.mark.asyncio
async def test_require_llm_verified_raises_when_ollama_unreachable() -> None:
    settings = _make_settings()
    with patch(
        "chaoscypher_core.services.llm.health._ollama_pulled_models",
        return_value=None,
    ):
        with pytest.raises(LLMNotVerifiedError) as exc_info:
            await require_llm_verified(settings)
    assert exc_info.value.code == "LLM_NOT_VERIFIED"
    assert exc_info.value.provider == "ollama"


@pytest.mark.asyncio
async def test_require_llm_verified_passes_when_ollama_reachable() -> None:
    settings = _make_settings()
    with patch(
        "chaoscypher_core.services.llm.health._ollama_pulled_models",
        return_value=set(),
    ):
        await require_llm_verified(settings)  # does not raise


@pytest.mark.asyncio
async def test_health_predicate_matches_gate_predicate() -> None:
    """The banner and gate must agree on the same verified state for a given snapshot."""
    settings = _make_settings()
    with patch(
        "chaoscypher_core.services.llm.health._ollama_pulled_models",
        return_value=None,
    ):
        health = await get_llm_health(settings)
        if health.verified:
            await require_llm_verified(settings)
        else:
            with pytest.raises(LLMNotVerifiedError):
                await require_llm_verified(settings)
