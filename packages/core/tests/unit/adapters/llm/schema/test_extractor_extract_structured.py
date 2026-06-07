# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""End-to-end tests for StructuredExtractor.extract_structured and helpers.

These drive the async retry loop with a fake provider whose ``chat`` is an
async function returning a provider-style response dict (tool_calls /
content / usage / model). They cover:

- ``_extract_from_response`` (tool_calls dict / stringified / malformed;
  fallthrough to content parsing).
- ``_extract_from_content`` (parse; content-as-dict unwrap; empty ->
  ValueError; repair; truncated item recovery; was_truncated attr).
- ``extract_structured`` happy path; schema-validation retry then success;
  quality retry then accept; final return_with_errors; all-retries-exhausted
  -> RuntimeError; ModelCapabilityError re-raised; empty-response backoff
  invokes check_health (asyncio.sleep patched).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from chaoscypher_core.adapters.llm.schema.extractor import StructuredExtractor
from chaoscypher_core.exceptions import ModelCapabilityError


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeLLMSettings:
    """Minimal stand-in for settings.llm used by extract_structured."""

    def __init__(self) -> None:
        self.extraction_temperature = 0.1
        self.llm_max_retries = 3
        self.extraction_max_tokens = 1024
        self.chat_provider = "openai"
        self.thinking_for_extraction = False


class _FakeSettings:
    def __init__(self) -> None:
        self.llm = _FakeLLMSettings()


class _FakeProvider:
    """Fake chat provider.

    ``responses`` is a list of dicts returned in order from ``chat``; the
    last one is reused once exhausted. ``check_health`` returns ``healthy``.
    """

    def __init__(self, responses: list[Any], healthy: bool = True) -> None:
        self._responses = responses
        self._idx = 0
        self.healthy = healthy
        self.chat_calls: list[dict[str, Any]] = []
        self.health_calls = 0

    async def chat(self, **kwargs: Any) -> dict[str, Any]:
        self.chat_calls.append(kwargs)
        resp = self._responses[min(self._idx, len(self._responses) - 1)]
        self._idx += 1
        if isinstance(resp, Exception):
            raise resp
        return resp

    async def check_health(self) -> bool:
        self.health_calls += 1
        return self.healthy


class _FakeFactory:
    def __init__(self, provider: _FakeProvider, max_retries: int = 3) -> None:
        self.settings = _FakeSettings()
        self.settings.llm.llm_max_retries = max_retries
        self._provider = provider

    def get_chat_provider(self) -> _FakeProvider:
        return self._provider


def _tool_response(data: dict[str, Any], model: str = "test-model") -> dict[str, Any]:
    """Build a provider response carrying a tool call with dict arguments."""
    return {
        "tool_calls": [{"function": {"name": "ExtractStructuredData", "arguments": data}}],
        "content": "",
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        "model": model,
    }


def _make_extractor(provider: _FakeProvider, max_retries: int = 3) -> StructuredExtractor:
    return StructuredExtractor(_FakeFactory(provider, max_retries))


# A permissive schema that accepts entity/relationship style payloads.
_OPEN_SCHEMA: dict[str, Any] = {"type": "object"}

# A schema requiring a "name" string property at the root.
_REQUIRED_NAME_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"name": {"type": "string"}},
    "required": ["name"],
}


# ---------------------------------------------------------------------------
# _extract_from_response
# ---------------------------------------------------------------------------


def test_extract_from_response_tool_call_dict() -> None:
    ext = _make_extractor(_FakeProvider([]))
    result = _tool_response({"entities": [{"name": "A"}]})
    out = ext._extract_from_response(result, "ExtractStructuredData", "openai")
    assert out == {"entities": [{"name": "A"}]}


def test_extract_from_response_tool_call_unwraps_data_key() -> None:
    ext = _make_extractor(_FakeProvider([]))
    result = _tool_response({"data": {"entities": []}})
    out = ext._extract_from_response(result, "ExtractStructuredData", "openai")
    assert out == {"entities": []}


def test_extract_from_response_stringified_arguments() -> None:
    ext = _make_extractor(_FakeProvider([]))
    result = {
        "tool_calls": [{"function": {"arguments": '{"entities": [{"name": "B"}]}'}}],
        "content": "",
    }
    out = ext._extract_from_response(result, "ExtractStructuredData", "openai")
    assert out == {"entities": [{"name": "B"}]}


def test_extract_from_response_malformed_string_args_repaired() -> None:
    ext = _make_extractor(_FakeProvider([]))
    # Trailing comma -> initial json.loads fails -> repaired to valid JSON.
    result = {
        "tool_calls": [{"function": {"arguments": '{"entities": [],}'}}],
        "content": "",
    }
    out = ext._extract_from_response(result, "ExtractStructuredData", "openai")
    assert out == {"entities": []}


def test_extract_from_response_falls_through_to_content() -> None:
    ext = _make_extractor(_FakeProvider([]))
    # Unparseable+unrepairable string args -> fall through to content parsing.
    result = {
        "tool_calls": [{"function": {"arguments": "not json at all !!!"}}],
        "content": '{"entities": [{"name": "C"}]}',
    }
    out = ext._extract_from_response(result, "ExtractStructuredData", "openai")
    assert out == {"entities": [{"name": "C"}]}


def test_extract_from_response_no_tool_calls_uses_content() -> None:
    ext = _make_extractor(_FakeProvider([]))
    result = {"tool_calls": [], "content": '{"x": 1}'}
    out = ext._extract_from_response(result, "ExtractStructuredData", "openai")
    assert out == {"x": 1}


# ---------------------------------------------------------------------------
# _extract_from_content
# ---------------------------------------------------------------------------


def test_extract_from_content_parses_json() -> None:
    ext = _make_extractor(_FakeProvider([]))
    out = ext._extract_from_content({"content": '{"a": 1}'})
    assert out == {"a": 1}


def test_extract_from_content_unwraps_data() -> None:
    ext = _make_extractor(_FakeProvider([]))
    out = ext._extract_from_content({"content": '{"data": {"a": 1}}'})
    assert out == {"a": 1}


def test_extract_from_content_content_as_dict_unwrap() -> None:
    ext = _make_extractor(_FakeProvider([]))
    out = ext._extract_from_content({"content": {"content": '{"a": 9}'}})
    assert out == {"a": 9}


def test_extract_from_content_empty_raises_value_error() -> None:
    ext = _make_extractor(_FakeProvider([]))
    with pytest.raises(ValueError, match="empty response"):
        ext._extract_from_content({"content": ""})


def test_extract_from_content_repairs_malformed() -> None:
    ext = _make_extractor(_FakeProvider([]))
    # Trailing comma -> initial parse fails -> repair succeeds.
    out = ext._extract_from_content({"content": '{"a": 1,}'})
    assert out == {"a": 1}


def test_extract_from_content_truncated_item_recovery() -> None:
    ext = _make_extractor(_FakeProvider([]))
    # Truncated items array that standard repair cannot fully fix -> item
    # recovery extracts the complete items before the cut.
    content = '{"items": [{"a": 1}, {"b": 2}, {"c": "incomplete'
    out = ext._extract_from_content({"content": content})
    assert out == {"items": [{"a": 1}, {"b": 2}]}


def test_extract_from_content_was_truncated_attr_on_error() -> None:
    ext = _make_extractor(_FakeProvider([]))
    # Garbage that triggers truncation detection (unbalanced) and stays
    # invalid after repair; no "items" key so no recovery.
    content = '{"k": "v" "broken'  # odd quotes + unbalanced brace
    with pytest.raises(ValueError) as exc_info:
        ext._extract_from_content({"content": content})
    assert getattr(exc_info.value, "was_truncated", None) is True


# ---------------------------------------------------------------------------
# extract_structured - async retry loop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_structured_happy_path() -> None:
    provider = _FakeProvider([_tool_response({"name": "Alice"})])
    ext = _make_extractor(provider)
    out = await ext.extract_structured(
        text="some text",
        json_schema=_REQUIRED_NAME_SCHEMA,
        enable_quality_check=False,
    )
    assert out["name"] == "Alice"
    assert out["_metadata"]["success"] is True
    assert out["_metadata"]["model"] == "test-model"
    assert provider.chat_calls  # provider was called


@pytest.mark.asyncio
async def test_extract_structured_schema_retry_then_success() -> None:
    # First response violates required "name", second is valid.
    provider = _FakeProvider(
        [
            _tool_response({"wrong": "field"}),
            _tool_response({"name": "Bob"}),
        ]
    )
    ext = _make_extractor(provider)
    out = await ext.extract_structured(
        text="t",
        json_schema=_REQUIRED_NAME_SCHEMA,
        enable_quality_check=False,
    )
    assert out["name"] == "Bob"
    assert out["_metadata"]["success"] is True
    assert out["_metadata"]["attempts"] == 2
    assert len(provider.chat_calls) == 2


@pytest.mark.asyncio
async def test_extract_structured_quality_retry_then_accept() -> None:
    # First response has many entities with empty descriptions (issue ratio
    # over the 0.3 threshold) -> quality retry. Second is clean -> accept.
    bad_entities = {
        "entities": [{"name": f"E{i}", "type": "T", "description": ""} for i in range(5)],
        "relationships": [],
    }
    good_entities = {
        "entities": [
            {
                "name": "Solid",
                "type": "Concept",
                "description": "A complete and well punctuated description here.",
            }
        ],
        "relationships": [],
    }
    provider = _FakeProvider([_tool_response(bad_entities), _tool_response(good_entities)])
    ext = _make_extractor(provider)
    out = await ext.extract_structured(
        text="t",
        json_schema=_OPEN_SCHEMA,
        enable_quality_check=True,
    )
    assert out["_metadata"]["success"] is True
    assert len(provider.chat_calls) == 2
    assert out["entities"][0]["name"] == "Solid"


@pytest.mark.asyncio
async def test_extract_structured_final_return_with_errors() -> None:
    # Always invalid against required schema; max_retries small so the final
    # attempt returns the data with success=False rather than raising.
    provider = _FakeProvider([_tool_response({"wrong": "x"})])
    ext = _make_extractor(provider, max_retries=1)
    out = await ext.extract_structured(
        text="t",
        json_schema=_REQUIRED_NAME_SCHEMA,
        enable_quality_check=False,
    )
    assert out["_metadata"]["success"] is False
    assert out["_metadata"]["validation_errors"]
    # initial attempt + 1 retry = 2 chat calls.
    assert len(provider.chat_calls) == 2


@pytest.mark.asyncio
async def test_extract_structured_all_retries_exhausted_raises_runtime() -> None:
    # Every attempt raises a non-capability, non-empty error -> after retries
    # exhausted, RuntimeError is raised.
    provider = _FakeProvider([ValueError("boom parse error")])
    ext = _make_extractor(provider, max_retries=1)
    with pytest.raises(RuntimeError, match="Failed to extract valid JSON"):
        await ext.extract_structured(
            text="t",
            json_schema=_OPEN_SCHEMA,
            enable_quality_check=False,
        )
    assert len(provider.chat_calls) == 2


@pytest.mark.asyncio
async def test_extract_structured_capability_error_reraised() -> None:
    err = ModelCapabilityError("model cannot do tools")
    provider = _FakeProvider([err])
    ext = _make_extractor(provider, max_retries=3)
    with pytest.raises(ModelCapabilityError):
        await ext.extract_structured(
            text="t",
            json_schema=_OPEN_SCHEMA,
            enable_quality_check=False,
        )
    # No retry on capability error -> exactly one chat call.
    assert len(provider.chat_calls) == 1


@pytest.mark.asyncio
async def test_extract_structured_empty_response_backoff_calls_check_health() -> None:
    # First response is empty content (no tool calls) -> ValueError("empty
    # response...") -> backoff path invokes check_health. Provider healthy ->
    # brief pause (asyncio.sleep patched). Second response succeeds.
    empty = {"tool_calls": [], "content": "", "usage": {}, "model": "m"}
    provider = _FakeProvider([empty, _tool_response({"name": "Recovered"})], healthy=True)
    ext = _make_extractor(provider)
    with patch(
        "chaoscypher_core.adapters.llm.schema.extractor.asyncio.sleep",
        new=AsyncMock(),
    ) as sleep_mock:
        out = await ext.extract_structured(
            text="t",
            json_schema=_REQUIRED_NAME_SCHEMA,
            enable_quality_check=False,
        )
    assert out["name"] == "Recovered"
    assert provider.health_calls == 1
    sleep_mock.assert_awaited()


@pytest.mark.asyncio
async def test_extract_structured_empty_response_unhealthy_backoff() -> None:
    # Provider reports unhealthy -> exponential backoff branch taken.
    empty = {"tool_calls": [], "content": "", "usage": {}, "model": "m"}
    provider = _FakeProvider([empty, _tool_response({"name": "Late"})], healthy=False)
    ext = _make_extractor(provider)
    with patch(
        "chaoscypher_core.adapters.llm.schema.extractor.asyncio.sleep",
        new=AsyncMock(),
    ) as sleep_mock:
        out = await ext.extract_structured(
            text="t",
            json_schema=_REQUIRED_NAME_SCHEMA,
            enable_quality_check=False,
        )
    assert out["name"] == "Late"
    assert provider.health_calls == 1
    sleep_mock.assert_awaited()


@pytest.mark.asyncio
async def test_extract_structured_resolves_settings_defaults() -> None:
    # temperature/max_retries/max_tokens default from settings.llm; verify the
    # chat call received the capped temperature and the configured max_tokens.
    provider = _FakeProvider([_tool_response({"name": "Z"})])
    ext = _make_extractor(provider)
    await ext.extract_structured(
        text="t",
        json_schema=_REQUIRED_NAME_SCHEMA,
        enable_quality_check=False,
    )
    call = provider.chat_calls[0]
    assert call["max_tokens"] == 1024
    assert call["temperature"] == 0.1
    assert call["stream"] is False


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
