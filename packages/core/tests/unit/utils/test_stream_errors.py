# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""One classifier for every LLM failure (audit wave 5, 2026-10-06).

Before it there were four: the Ollama non-streaming ``except`` ladder (a 429
or 503 became a non-retryable ``LLMError``), the Ollama stream's code (a
crashed runner was fatal), the cloud indicator cascade and the extraction
consumer's ``"not found"`` test. These tests drive both Ollama paths and both
chat stream consumers (``consume_llm_stream`` and the tool loop's
``_process_iteration_stream``) with the same failures and pin one verdict.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from ollama._types import ResponseError

from chaoscypher_core.adapters.llm.providers.ollama_provider import OllamaProvider
from chaoscypher_core.exceptions import (
    LLMAuthenticationError,
    LLMContextLengthError,
    LLMError,
    LLMModelError,
    LLMRateLimitError,
    LLMServiceError,
)
from chaoscypher_core.streaming.chat.loop import consume_llm_stream
from chaoscypher_core.streaming.chat.sinks import CollectingSink
from chaoscypher_core.streaming.chat.tools import _process_iteration_stream
from chaoscypher_core.utils.stream_errors import (
    OLLAMA_PATTERNS,
    RUNNER_CRASH_RETRY_LIMIT,
    classify_llm_exception,
    classify_llm_failure,
    classify_provider_error,
    error_from_stream_chunk,
    retry_budget,
)


_CONFIG: dict[str, Any] = {
    "chat_provider": "ollama",
    "llm_max_concurrent": 1,
    "llm_reserved_interactive": 0,
    "llm_enable_priority": False,
    "llm_request_timeout": 30,
    "base_url": "http://localhost:11434",
    "ollama_chat_model": "qwen3:0.6b",
    "stream_chunk_timeout": 30.0,
    "ollama_health_check_timeout": 5.0,
    "ollama_recovery_delay": 0.0,
}

# (raised, the class every path reports, retries allowed under a budget of 3)
_CASES = [
    (ResponseError("model 'qwen3:0.6b' not found, try pulling it first", 404), LLMModelError, 0),
    (ResponseError("too many requests", 429), LLMRateLimitError, 3),
    (ResponseError("server busy, please try again", 503), LLMServiceError, 3),
    (
        ResponseError("llama runner process has terminated: exit status 2", 500),
        LLMServiceError,
        RUNNER_CRASH_RETRY_LIMIT,
    ),
    (httpx.ConnectError("All connection attempts failed"), LLMServiceError, 3),
    (httpx.ReadTimeout("timed out"), LLMServiceError, 3),
    (ResponseError("invalid option 'foo'", 400), LLMError, 0),
]


def _provider() -> OllamaProvider:
    with patch(
        "chaoscypher_core.adapters.llm.providers.ollama_provider.ChatOllama",
        return_value=MagicMock(),
    ):
        return OllamaProvider(_CONFIG)


async def _failing_stream(provider: OllamaProvider, raised: Exception) -> list[dict[str, Any]]:
    """The provider's real ``_stream_chat`` over a transport that raises ``raised``."""

    async def _raising(*_a: Any, **_kw: Any) -> Any:
        raise raised
        yield  # pragma: no cover - makes this an async generator

    with (
        patch.object(provider, "_get_llm_for_request", return_value=MagicMock()),
        patch.object(provider, "_astream_with_timeout", _raising),
    ):
        return [
            chunk
            async for chunk in provider._stream_chat(
                lc_messages=[MagicMock()], tools=None, enable_thinking=False
            )
        ]


async def _replay(chunks: list[dict[str, Any]]) -> Any:
    for chunk in chunks:
        yield chunk


def _exact(error: LLMError, kind: type[LLMError]) -> None:
    """``error`` is ``kind`` itself (LLMError cases must not be a subclass that retries)."""
    assert isinstance(error, kind)
    if kind is LLMError:
        assert type(error) is LLMError


@pytest.mark.asyncio
@pytest.mark.parametrize(("raised", "kind", "budget"), _CASES)
async def test_the_non_streaming_path_raises_the_one_verdict(
    raised: Exception, kind: type[LLMError], budget: int
) -> None:
    """A 429/503 on ``chat(stream=False)`` was a non-retryable LLMError before wave 5."""
    provider = _provider()
    with (
        patch.object(provider, "_make_sync_request", new=AsyncMock(side_effect=raised)),
        pytest.raises(LLMError) as excinfo,
    ):
        await provider.chat(messages=[{"role": "user", "content": "hi"}])
    _exact(excinfo.value, kind)
    assert retry_budget(excinfo.value, 3) == budget


@pytest.mark.asyncio
@pytest.mark.parametrize(("raised", "kind", "budget"), _CASES)
async def test_the_chat_loop_reads_the_same_verdict_off_the_stream(
    raised: Exception, kind: type[LLMError], budget: int
) -> None:
    """``consume_llm_stream`` emits the classified event; reading it back gives the same error."""
    provider = _provider()
    chunks = await _failing_stream(provider, raised)
    sink = CollectingSink()
    *_, failed, _done = await consume_llm_stream(_replay(chunks), sink, "chat-1")
    assert failed is True
    (event,) = sink.of_type("error")
    error = error_from_stream_chunk(event)
    _exact(error, kind)
    assert retry_budget(error, 3) == budget
    assert error.model == "qwen3:0.6b"


@pytest.mark.asyncio
@pytest.mark.parametrize(("raised", "kind", "budget"), _CASES)
async def test_the_tool_loop_reads_the_same_verdict_off_the_stream(
    raised: Exception, kind: type[LLMError], budget: int
) -> None:
    """The tool loop's per-iteration consumer classifies with the same function."""
    provider = _provider()
    chunks = await _failing_stream(provider, raised)
    out = [c async for c in _process_iteration_stream(_replay(chunks), "chat-1", 1)]
    assert out[-1]["_internal_type"] == "error"
    error = error_from_stream_chunk(out[-1])
    _exact(error, kind)
    assert retry_budget(error, 3) == budget


@pytest.mark.parametrize(
    ("chunk", "kind", "retryable"),
    [
        # a producer that classifies nothing: the text and the status decide
        ({"type": "error", "error": "model 'x' not found"}, LLMModelError, False),
        ({"type": "error", "error": "boom", "status_code": 503}, LLMServiceError, True),
        ({"type": "error", "error": "connection refused"}, LLMServiceError, True),
        ({"type": "error", "error": "boom"}, LLMError, False),
        # an older code with no details still names the class
        (
            {"type": "error", "error": "LLM streaming failed", "error_code": "LLM_MODEL_ERROR"},
            LLMModelError,
            False,
        ),
        (
            {"type": "error", "error": "LLM streaming failed", "error_code": "LLM_SERVICE_ERROR"},
            LLMServiceError,
            True,
        ),
    ],
)
def test_an_unclassified_chunk_goes_through_the_same_rules(
    chunk: dict[str, Any], kind: type[LLMError], retryable: bool
) -> None:
    error = error_from_stream_chunk(chunk)
    _exact(error, kind)
    assert error.is_retryable is retryable
    assert error.message == chunk["error"]


def test_a_crashed_runner_is_retried_once_whatever_the_loop_allows() -> None:
    """The "once" is the error's own retry limit: ``retry_budget`` caps any configured count."""
    crash = classify_llm_exception(
        ResponseError("llama runner process has terminated", 500),
        provider="ollama",
        model="m",
        patterns=OLLAMA_PATTERNS,
    )
    assert crash.is_retryable is True
    assert [retry_budget(crash, n) for n in (0, 1, 2, 5)] == [0, 1, 1, 1]
    assert retry_budget(LLMModelError(provider="ollama", model="m"), 5) == 0


@pytest.mark.parametrize(
    "raised",
    [
        ResponseError("quota exceeded for this account", 429),
        ResponseError("billing hard limit reached", 429),
    ],
)
def test_an_exceeded_quota_never_retries_on_any_path(raised: ResponseError) -> None:
    """Audit wave 6 (pinned): a 429 that names the quota or billing is not a rate limit to wait out.

    Every retry loop asks :func:`retry_budget` (the chat loop, the benchmark's question
    retries) or ``is_retryable_chunk_failure`` (extraction): zero, raised or streamed, whatever
    the configured budget (500 included), while a plain 429 keeps the configured budget.
    """
    from chaoscypher_core.services.sources.engine.extraction.chunk_failures import (
        is_retryable_chunk_failure,
    )
    from chaoscypher_core.utils.stream_errors import error_chunk

    raised_error = classify_llm_exception(
        raised, provider="ollama", model="m", patterns=OLLAMA_PATTERNS
    )
    streamed = error_from_stream_chunk(error_chunk(raised_error))
    for error in (raised_error, streamed):
        assert isinstance(error, LLMRateLimitError) and error.quota_exceeded is True
        assert error.is_retryable is False
        assert [retry_budget(error, n) for n in (0, 1, 3, 500)] == [0, 0, 0, 0]
        assert is_retryable_chunk_failure(error) is False
    plain = classify_llm_exception(
        ResponseError("too many requests", 429), provider="ollama", model="m"
    )
    assert isinstance(plain, LLMRateLimitError) and plain.quota_exceeded is False
    assert retry_budget(plain, 500) == 500


class _StatusError(Exception):
    """A cloud SDK error carrying its HTTP status (``openai.APIStatusError`` shape)."""

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


def test_the_cloud_providers_share_the_status_rules() -> None:
    """The old cloud cascade is the same function with the provider's words."""
    from chaoscypher_core.adapters.llm.providers.openai_provider import _PATTERNS

    assert isinstance(
        classify_provider_error(_StatusError("nope", 404), "gpt", _PATTERNS), LLMModelError
    )
    busy = classify_provider_error(_StatusError("Bad gateway", 502), "gpt", _PATTERNS)
    assert isinstance(busy, LLMServiceError) and busy.is_retryable
    quota = classify_provider_error(_StatusError("You exceeded your quota", 429), "gpt", _PATTERNS)
    assert isinstance(quota, LLMRateLimitError) and quota.is_retryable is False
    # the cloud default stays retryable, Ollama's does not
    assert classify_provider_error(ValueError("odd"), "gpt", _PATTERNS).is_retryable is True


@pytest.mark.parametrize(
    ("status", "kind", "retryable"),
    [
        (503, LLMServiceError, True),  # the runner reloading: transient, never fatal (wave 6)
        (500, LLMServiceError, True),
        (404, LLMModelError, False),
        (None, LLMModelError, False),
    ],
)
def test_model_missing_text_decides_only_without_a_server_error_status(
    status: int | None, kind: type[LLMError], retryable: bool
) -> None:
    """The model-missing text under a 5xx is the runner mid-restart, not a missing model."""
    error = classify_llm_failure(
        "error loading model: file not found",
        status=status,
        provider="ollama",
        model="m",
        patterns=OLLAMA_PATTERNS,
    )
    _exact(error, kind)
    assert error.is_retryable is retryable


_CONTEXT_400 = (
    "This model's maximum context length is 128000 tokens. However, your messages "
    "resulted in {n} tokens. Please reduce the length of the messages."
)


@pytest.mark.parametrize("tokens", ["142901", "140412", "140112", "150312"])
def test_a_status_number_inside_a_token_count_is_not_a_status(tokens: str) -> None:
    """A context-length 400 is a context-length error whatever digits its count contains.

    Before 2026-10-19 the provider word lists' bare "429" / "404" / "401" / "403"
    were substring-matched against the whole text, so "142901 tokens" was a
    retryable rate limit and "140412 tokens" a fatal model error.
    """
    from chaoscypher_core.adapters.llm.providers.openai_provider import _PATTERNS

    err = classify_provider_error(
        _StatusError(_CONTEXT_400.format(n=tokens), 400), "gpt", _PATTERNS
    )
    assert isinstance(err, LLMContextLengthError), type(err)
    assert err.is_retryable is False


def test_a_bare_status_number_still_counts_without_a_status() -> None:
    """A stream error chunk carries no status; "429" standing alone in its text is one."""
    from chaoscypher_core.adapters.llm.providers.openai_provider import _PATTERNS

    rate = classify_llm_failure(
        "Error code: 429 - slow down", provider="openai", model="gpt", patterns=_PATTERNS
    )
    assert isinstance(rate, LLMRateLimitError)
    count = classify_llm_failure(
        "messages resulted in 142901 tokens", provider="openai", model="gpt", patterns=_PATTERNS
    )
    assert not isinstance(count, LLMRateLimitError)


@pytest.mark.parametrize(
    ("provider", "message", "status"),
    [
        ("openai", "Country, region, or territory not supported", 403),
        ("openai", "Project `proj_abc` does not have access to model `gpt-5`", 403),
        ("anthropic", "invalid x-api-key", 401),
    ],
)
def test_a_401_or_403_status_is_an_auth_failure_whatever_its_text(
    provider: str, message: str, status: int
) -> None:
    """The status names the failure when the text has no auth words (2026-10-26, #824 review).

    The shared status rules cover 404, 429 and the 5xx statuses only; a 401 or
    403 is decided by the provider's own "401" / "403" indicators. Skipping
    every digit indicator under a status made these an unknown ``LLMError``,
    which a cloud provider retries.
    """
    import importlib

    patterns = importlib.import_module(
        f"chaoscypher_core.adapters.llm.providers.{provider}_provider"
    )._PATTERNS
    err = classify_provider_error(_StatusError(message, status), "m", patterns)
    assert isinstance(err, LLMAuthenticationError), type(err)
    assert err.is_retryable is False


def test_a_400_naming_a_status_like_count_stays_a_context_length_error() -> None:
    """A status digit indicator compares to the status, so a 400 with "142901 tokens" is no 429."""
    from chaoscypher_core.adapters.llm.providers.openai_provider import _PATTERNS

    err = classify_provider_error(
        _StatusError(_CONTEXT_400.format(n="142901"), 400), "gpt", _PATTERNS
    )
    assert type(err) is LLMContextLengthError


def test_a_server_error_is_a_timeout_only_when_504_stands_alone() -> None:
    """``is_timeout`` read "504" as a substring, so a request id 85041 marked a timeout."""
    from chaoscypher_core.adapters.llm.providers.openai_provider import _PATTERNS

    plain = classify_llm_failure(
        "Internal server error (request 85041)", provider="openai", model="gpt", patterns=_PATTERNS
    )
    assert isinstance(plain, LLMServiceError)
    assert plain.is_timeout is False
    gateway = classify_llm_failure(
        "server error: 504 gateway", provider="openai", model="gpt", patterns=_PATTERNS
    )
    assert isinstance(gateway, LLMServiceError)
    assert gateway.is_timeout is True


def test_a_regex_indicator_is_a_regex() -> None:
    """Gemini's ``max.*token`` and ``exceeds.*limit`` never matched as substrings."""
    from chaoscypher_core.adapters.llm.providers.gemini_provider import _PATTERNS

    err = classify_llm_failure(
        "The input token count exceeds the maximum number of tokens allowed",
        provider="gemini",
        model="gemini-2.5-flash",
        patterns=_PATTERNS,
    )
    assert isinstance(err, LLMContextLengthError)
