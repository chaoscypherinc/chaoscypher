# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""A deliberate re-ask resamples by seed, never by temperature (audit wave 5, 2026-10-06).

Decoding is pinned by default (temperature 0, seed 42), so the chat's
regenerate returned the same answer and the schema extractor's validation
retries repeated the same failed output. One helper, ``resample_decoding``,
gives every re-ask its seed; ``seed_override`` carries it to the Ollama
provider, which these tests drive for real (``ChatOllama`` captured).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_core.adapters.llm.providers.ollama_provider import OllamaProvider
from chaoscypher_core.adapters.llm.schema.extractor import StructuredExtractor
from chaoscypher_core.app_config import Settings
from chaoscypher_core.streaming.chat.finalize import (
    DECODING_METADATA_KEY,
    build_optional_payload,
    next_regeneration_attempt,
)
from chaoscypher_core.streaming.chat.loop import ChatLoopDeps, run_chat_tool_loop
from chaoscypher_core.streaming.chat.sinks import CollectingSink
from chaoscypher_core.utils.decoding import (
    decoding_override,
    effective_seed,
    effective_temperature,
    resample_decoding,
    seed_override,
    turn_decoding,
)


def test_attempt_zero_is_the_configured_seed_and_each_re_ask_the_next() -> None:
    assert [resample_decoding(42, n) for n in range(4)] == [42, 43, 44, 45]
    # no configured seed: the provider already picks one per request
    assert resample_decoding(None, 3) is None
    with pytest.raises(ValueError, match="attempt"):
        resample_decoding(42, -1)


def test_the_override_is_scoped_to_the_block() -> None:
    assert effective_seed(42) == 42
    with seed_override(43):
        assert effective_seed(42) == 43
        with seed_override(None):
            assert effective_seed(42) is None
        assert effective_seed(42) == 43
    assert effective_seed(42) == 42


_CONFIG: dict[str, Any] = {
    "chat_provider": "ollama",
    "llm_max_concurrent": 1,
    "llm_reserved_interactive": 0,
    "llm_enable_priority": False,
    "llm_request_timeout": 30,
    "base_url": "http://localhost:11434",
    "ollama_chat_model": "qwen3:0.6b",
    "ai_temperature": 0.0,
    "seed": 42,
    "stream_chunk_timeout": 30.0,
    "ollama_health_check_timeout": 5.0,
    "ollama_recovery_delay": 0.0,
}


def test_the_ollama_provider_sends_the_overridden_seed_at_the_same_temperature() -> None:
    """The real provider: the override reaches ChatOllama, the temperature does not move."""
    built: list[dict[str, Any]] = []

    def _capture(**kwargs: Any) -> MagicMock:
        built.append(kwargs)
        return MagicMock()

    with patch(
        "chaoscypher_core.adapters.llm.providers.ollama_provider.ChatOllama", side_effect=_capture
    ):
        provider = OllamaProvider(_CONFIG)
        default = provider._get_llm_for_request(None, None, enable_thinking=True)
        with seed_override(resample_decoding(42, 2)):
            resampled = provider._get_llm_for_request(None, None, enable_thinking=True)
        again = provider._get_llm_for_request(None, None, enable_thinking=True)
    assert resampled is not default and again is default
    assert built[0]["seed"] == 42 and built[-1]["seed"] == 44
    assert built[0].get("temperature") == built[-1].get("temperature")


# ---------------------------------------------------------------------------
# The schema extractor's validation retries
# ---------------------------------------------------------------------------


class _SeedRecordingProvider:
    """Answers wrong twice then right; records the seed each call would send."""

    def __init__(self) -> None:
        self.calls: list[tuple[int | None, Any]] = []
        self._answers = [{"wrong": 1}, {"wrong": 2}, {"name": "Pierre"}]

    async def chat(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append((effective_seed(42), kwargs.get("temperature")))
        data = self._answers[min(len(self.calls) - 1, len(self._answers) - 1)]
        return {
            "tool_calls": [{"function": {"name": "ExtractStructuredData", "arguments": data}}],
            "content": "",
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            "model": "m",
        }


@pytest.mark.asyncio
async def test_schema_validation_retries_vary_the_seed_never_the_temperature() -> None:
    provider = _SeedRecordingProvider()
    factory = MagicMock()
    factory.settings.llm.extraction_temperature = 0.0
    factory.settings.llm.llm_max_retries = 3
    factory.settings.llm.extraction_max_tokens = 256
    factory.settings.llm.chat_provider = "ollama"
    factory.settings.llm.thinking_for_extraction = False
    factory.settings.llm.seed = 42
    factory.get_chat_provider.return_value = provider
    schema = {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}

    result = await StructuredExtractor(factory).extract_structured(
        text="Pierre arrived.",
        json_schema=schema,
        system_prompt="extract",
        enable_quality_check=False,
    )

    assert result["name"] == "Pierre"
    assert provider.calls == [(42, 0.0), (43, 0.0), (44, 0.0)]


@pytest.mark.asyncio
async def test_a_regenerates_tool_call_into_ai_extract_json_keeps_the_regenerates_seed() -> None:
    """Audit wave 6: attempt 0 overrode the seed with the configured one, cancelling the outer one.

    A chat regenerate (attempt 2, seed 44) whose tool call runs ``ai.extract_json``:
    the extractor's first try asks at 44, its validation retries at 45 and 46.
    """
    from chaoscypher_core.services.workflows.tools.engine.context import ToolExecutionContext
    from chaoscypher_core.services.workflows.tools.plugins.ai_extract_json_plugin import (
        ExtractJsonPlugin,
    )

    provider = _SeedRecordingProvider()
    settings = Settings()
    settings.llm.seed = 42
    settings.llm.extraction_temperature = 0.0
    settings.llm.llm_max_retries = 3
    factory = MagicMock()
    factory.settings = settings
    factory.get_chat_provider.return_value = provider
    context = ToolExecutionContext(
        graph_manager=MagicMock(),
        settings=settings,
        llm_service=MagicMock(),
        structured_extractor=StructuredExtractor(factory),
    )
    schema = {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}
    regenerate = turn_decoding(
        base_seed=42, configured_temperature=0.0, regenerate_temperature=0.7, attempt=2
    )

    with decoding_override(regenerate):
        result = await ExtractJsonPlugin().execute(
            {"text": "Pierre arrived.", "json_schema": schema, "enable_quality_check": False},
            context,
        )

    assert result["name"] == "Pierre"
    assert [seed for seed, _ in provider.calls] == [44, 45, 46]


# ---------------------------------------------------------------------------
# The chat loop and regenerate
# ---------------------------------------------------------------------------


async def _done_stream(text: str) -> Any:
    yield {"type": "done", "content": text, "thinking": None, "tool_calls": None}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("attempt", "seed", "temperature"), [(0, 42, 0.0), (1, 43, 0.7), (3, 45, 0.7)]
)
async def test_the_chat_loop_asks_with_the_attempts_decoding_and_records_it(
    attempt: int, seed: int, temperature: float
) -> None:
    """A plain turn sends 0 / 42; the n-th regenerate 0.7 / 42+n (Denis, 2026-10-06), recorded."""
    seen: list[tuple[int | None, float | None]] = []
    answer = "A substantive answer about the graph, long enough to be a final answer here."

    async def _chat(**kw: Any) -> Any:
        seen.append((effective_seed(42), effective_temperature(kw.get("temperature"))))
        return _done_stream(answer)

    provider = MagicMock()
    provider.chat = AsyncMock(side_effect=_chat)
    settings = Settings()
    settings.llm.seed = 42
    settings.llm.ai_temperature = 0.0
    assert settings.chat.regenerate_temperature == 0.7  # the default
    deps = ChatLoopDeps(
        chat_id="chat-1",
        provider=provider,
        tool_executor=MagicMock(),
        chat_service=MagicMock(),
        settings=settings,
        sink=CollectingSink(),
        decoding_attempt=attempt,
    )

    result = await run_chat_tool_loop([{"role": "user", "content": "q"}], deps)

    assert seen == [(seed, temperature if attempt else None)]  # a plain turn: the configured 0
    assert result.decoding == {"seed": seed, "temperature": temperature, "attempt": attempt}
    # outside the turn the configured decoding is back
    assert effective_seed(42) == 42 and effective_temperature(None) is None


def test_a_regenerate_never_lowers_a_configured_temperature() -> None:
    """max(configured, regenerate_temperature): a user's 1.0 stays 1.0."""
    hot = turn_decoding(
        base_seed=7, configured_temperature=1.0, regenerate_temperature=0.7, attempt=2
    )
    assert (hot.seed, hot.temperature) == (9, 1.0)
    first = turn_decoding(
        base_seed=7, configured_temperature=1.0, regenerate_temperature=0.7, attempt=0
    )
    assert (first.seed, first.temperature) == (7, 1.0)


@pytest.mark.asyncio
@pytest.mark.parametrize(("attempt", "seed", "temperature"), [(0, 42, 0.0), (2, 44, 0.7)])
async def test_the_ollama_provider_sends_a_regenerates_temperature_and_seed(
    attempt: int, seed: int, temperature: float
) -> None:
    """The real provider's chat(): the override reaches ChatOllama; a plain call stays 0 / 42."""
    from langchain_core.messages import AIMessage

    built: list[dict[str, Any]] = []

    def _capture(**kwargs: Any) -> MagicMock:
        built.append(kwargs)
        llm = MagicMock()
        llm.ainvoke = AsyncMock(return_value=AIMessage(content="an answer"))
        return llm

    decoding = turn_decoding(
        base_seed=42, configured_temperature=0.0, regenerate_temperature=0.7, attempt=attempt
    )
    with patch(
        "chaoscypher_core.adapters.llm.providers.ollama_provider.ChatOllama", side_effect=_capture
    ):
        provider = OllamaProvider({**_CONFIG, "llm_enable_priority": False})
        with decoding_override(decoding):
            out = await provider.chat([{"role": "user", "content": "q"}], stream=False)
    assert out["content"] == "an answer"
    assert (built[-1]["seed"], built[-1].get("temperature")) == (seed, temperature)


def test_each_regenerate_counts_one_past_the_answer_it_drops() -> None:
    """The answer records its attempt; the next regenerate asks with the one after."""
    answer = MagicMock(thinking=None, all_tool_calls=[], chunk_citations={}, entity_refs={})
    decoding = {"seed": 43, "temperature": 0.7, "attempt": 1}
    recorded = build_optional_payload(answer, None, decoding)
    assert recorded[DECODING_METADATA_KEY] == decoding
    messages = [
        {"id": "u1", "role": "user"},
        {"id": "a1", "role": "assistant", "extra_metadata": recorded},
    ]
    assert next_regeneration_attempt(messages, "u1") == 2
    # an answer from before the record, or none at all: the first regenerate is attempt 1
    assert next_regeneration_attempt([{"id": "u1", "role": "user"}], "u1") == 1
    assert next_regeneration_attempt([*messages[:1], {"id": "a1", "role": "assistant"}], "u1") == 1
