# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The one classifier of LLM failures, for raised exceptions and stream error chunks alike.

Until audit wave 5 there were four: the cloud providers' indicator cascade
(``providers/error_classifier.py``), the Ollama provider's non-streaming
``except`` ladder (a 429 or 503 there became a non-retryable ``LLMError``),
the Ollama stream's ``stream_error_code`` (a crashed runner was fatal) and the
extraction consumer's ``"not found" in error`` test. Each consumer retried a
different set of failures.

Now every path reduces a failure to the same facts (its message, HTTP status
and whether it was a timeout or a dropped transport) and runs one rule list:

1. 404, or a model the server says it does not have: :class:`LLMModelError`,
   fatal (asked again it fails the same way).
2. 429: :class:`LLMRateLimitError`, retryable unless the provider says the
   quota is spent; 502/503/504, a timeout, a refused or dropped connection:
   :class:`LLMServiceError` (``is_retryable=True``).
3. 500 or a crashed model runner ("llama runner process has terminated"): a
   :class:`LLMServiceError` that may be retried **once**
   (:data:`RUNNER_CRASH_RETRY_LIMIT`, read by :func:`retry_budget`): a runner
   that died loading or mid-answer usually comes back, one that dies twice on
   the same request will die a third time.
4. The provider's own indicator words (auth, content filter, context length).
5. Anything else: a plain :class:`LLMError`.

A provider raises :func:`classify_llm_exception`'s result and streams
:func:`error_chunk` of it; a consumer turns the chunk back into the same
error with :func:`error_from_stream_chunk`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from chaoscypher_core.exceptions import (
    LLMAuthenticationError,
    LLMContentFilterError,
    LLMContextLengthError,
    LLMError,
    LLMModelError,
    LLMRateLimitError,
    LLMServiceError,
    ToolCallingNotSupportedError,
)


RUNNER_CRASH_RETRY_LIMIT = 1
"""How many times a request whose model runner crashed (HTTP 500) is asked again."""

RETRY_LIMIT_KEY = "retry_limit"
"""The ``details`` key of an error that caps its own retries (:func:`retry_budget`)."""

MODEL_MISSING_STATUS = 404
"""The server does not have the model (Ollama: ``model 'x' not found``)."""

RATE_LIMIT_STATUS = 429
"""Too many requests: the server will take the request later."""

TRANSIENT_STATUS: frozenset[int] = frozenset({502, 503, 504, 529})
"""The server is busy or a gateway in front of it is (Ollama answers 503 with a full queue)."""

RUNNER_CRASH_STATUS = 500
"""Ollama's answer when the model runner died (out of memory loading it, a crash mid-answer)."""

_RUNNER_CRASH_TEXT = ("runner process has terminated", "llama runner", "runner process no longer")
"""Words of a crashed Ollama runner, for an error that lost its status."""

_MODEL_MISSING_TEXT = re.compile(r"model\b.*\bnot found|not found.*\bmodel\b|pull the model")
_TEXT_RULE_STATUS: frozenset[int | None] = frozenset({None, 400, 404})
"""Statuses under which the model-missing text rule may decide: a server error with that text is
transient (the runner reloading), never fatal."""
"""A model the server does not have, worded by Ollama (``model "x" not found, try pulling it``)."""


@dataclass(frozen=True)
class ProviderErrorPatterns:
    """A provider's indicator words, checked after the shared status rules.

    Each field is a sequence of lowercase substrings matched against the
    lowercased error text, in a fixed order (rate limit, auth, model, content
    filter, context length, server error, timeout, connection). Two shapes
    are not plain substrings (:func:`_mentions`): a bare status number
    (``"403"``) matches the failure's own status when it carries one, and
    otherwise only a number standing alone in the text — a context-length 400
    reporting "142901 tokens" is not a rate limit (2026-10-19 audit) — and an indicator
    with ``.*`` in it is a regular expression (``"max.*token"``).

    Attributes:
        provider: Provider name used in error messages (e.g. "openai").
        rate_limit: Indicators for 429 / rate-limit / quota errors.
        auth: Indicators for 401/403 / invalid API key errors.
        model_not_found: Indicators for 404 / unknown model errors.
        content_filter: Indicators for safety / moderation blocks.
        content_filter_type: Label for the filter (e.g. "SAFETY", "MODERATION").
        context_length: Indicators for token-limit / context-length errors.
        server_error: Indicators for 5xx / service-unavailable errors.
        timeout: Indicators for timeout errors.
        connection: Indicators for network / DNS / connection-refused errors.
        extra_content_filter_rules: Keyword to filter-type overrides.
        unknown_retryable: Whether an error no rule matched may be retried
            (the cloud providers' default; Ollama's is False).

    """

    provider: str
    rate_limit: tuple[str, ...] = ()
    auth: tuple[str, ...] = ()
    model_not_found: tuple[str, ...] = ()
    content_filter: tuple[str, ...] = ()
    content_filter_type: str = "SAFETY"
    context_length: tuple[str, ...] = ()
    server_error: tuple[str, ...] = ()
    timeout: tuple[str, ...] = ("timeout", "timed out")
    connection: tuple[str, ...] = ("connection", "network", "dns", "unreachable", "refused")
    extra_content_filter_rules: dict[str, str] = field(default_factory=dict)
    unknown_retryable: bool = True


OLLAMA_PATTERNS = ProviderErrorPatterns(
    provider="ollama",
    rate_limit=("too many requests",),
    server_error=("server busy", "service unavailable"),
    timeout=("timed out", "timeout"),
    connection=("connect", "connection"),
    unknown_retryable=False,
)
"""Ollama's words: a local server has no auth, filter or quota; an unknown error is not retried."""


def _mentions(text: str, status: int | None, indicators: tuple[str, ...]) -> bool:
    """Whether any of a provider's indicator words describes ``text``.

    A digit-only indicator is a status number: when the failure carries a
    status it matches that status (a 403 is an auth failure whatever its
    text says), and otherwise only a whole number in the text, never one
    inside a token count. An indicator containing ``.*`` is a regular
    expression; the rest are substrings.
    """
    for indicator in indicators:
        if indicator.isdigit():
            if status is not None:
                if status == int(indicator):
                    return True
            elif re.search(rf"(?<!\d){indicator}(?!\d)", text):
                return True
        elif ".*" in indicator:
            if re.search(indicator, text):
                return True
        elif indicator in text:
            return True
    return False


def _status_of(error: BaseException) -> int | None:
    """The HTTP status an exception carries (``status_code``, or its ``response``'s), else None."""
    for source in (error, getattr(error, "response", None)):
        status = getattr(source, "status_code", None)
        if isinstance(status, int) and not isinstance(status, bool) and status > 0:
            return status
    return None


def _kind_of(error: BaseException) -> str | None:
    """``"timeout"`` or ``"transport"`` when the exception's type says so, else None."""
    try:
        import httpx
    except ImportError:  # pragma: no cover - httpx ships with every provider
        httpx = None  # type: ignore[assignment]
    timeouts: tuple[type[BaseException], ...] = (TimeoutError,)
    transports: tuple[type[BaseException], ...] = (ConnectionError,)
    if httpx is not None:
        timeouts = (*timeouts, httpx.TimeoutException)
        transports = (*transports, httpx.TransportError)
    if isinstance(error, timeouts):
        return "timeout"
    if isinstance(error, transports):
        return "transport"
    return None


def classify_llm_failure(  # noqa: PLR0911, C901 - one ordered rule list, each return a distinct outcome
    message: str,
    *,
    provider: str,
    model: str | None,
    patterns: ProviderErrorPatterns | None = None,
    status: int | None = None,
    kind: str | None = None,
    error_type: str | None = None,
    connection_reason: str | None = None,
) -> LLMError:
    """The :class:`LLMError` for a failure described by its text, status and kind.

    The rule list in the module docstring, applied to the facts a raised
    exception (:func:`classify_llm_exception`) and a stream error chunk
    (:func:`error_from_stream_chunk`) both reduce to.

    Args:
        message: The failure's text, as the provider worded it.
        provider: The provider that failed (``ollama``, ``openai`` ...).
        model: The model that was asked.
        patterns: The provider's indicator words; Ollama's when None.
        status: The HTTP status, when the failure carried one.
        kind: ``"timeout"`` or ``"transport"`` when the failure's type said so.
        error_type: The original exception's type name, kept in ``details``.
        connection_reason: The text a connection failure reports instead of
            the generic one (the Ollama provider's Docker hint).

    Returns:
        The classified error (never raised here).

    """
    patterns = patterns or OLLAMA_PATTERNS
    text = (message or "").lower()
    details: dict[str, Any] = {"original_error": message}
    if error_type:
        details["error_type"] = error_type
    if status is not None:
        details["status_code"] = status
    model_name = model or "unknown"

    # 1. shared status rules. The model-missing TEXT rule applies only without a
    # status or with a client-error one: "error loading model: file not found"
    # under a 503 is the runner mid-restart, not a missing model (audit wave 6).
    if status == MODEL_MISSING_STATUS or (
        status in _TEXT_RULE_STATUS and _MODEL_MISSING_TEXT.search(text)
    ):
        return LLMModelError(
            provider=provider,
            model=model_name,
            reason=message or "Model not found or not available",
            details=details,
        )
    if status == RATE_LIMIT_STATUS or _mentions(text, status, patterns.rate_limit):
        retry_match = re.search(r"retry.?after[:\s]*(\d+)", text)
        return LLMRateLimitError(
            provider=provider,
            model=model,
            retry_after=int(retry_match.group(1)) if retry_match else None,
            quota_exceeded="quota" in text or "billing" in text,
            details=details,
        )
    if status in TRANSIENT_STATUS:
        return LLMServiceError(
            provider=provider,
            reason=message or "Service temporarily unavailable",
            model=model,
            is_timeout=status == 504,
            details=details,
        )
    if status == RUNNER_CRASH_STATUS or any(ind in text for ind in _RUNNER_CRASH_TEXT):
        details[RETRY_LIMIT_KEY] = RUNNER_CRASH_RETRY_LIMIT
        return LLMServiceError(
            provider=provider,
            reason=f"the model runner failed: {message}",
            model=model,
            details=details,
        )

    # 2. the provider's words
    if _mentions(text, status, patterns.auth):
        return LLMAuthenticationError(
            provider=provider, reason="Invalid or expired API key", model=model, details=details
        )
    if _mentions(text, status, patterns.model_not_found):
        return LLMModelError(
            provider=provider,
            model=model_name,
            reason="Model not found or not available",
            details=details,
        )
    if _mentions(text, status, patterns.content_filter):
        filter_type = patterns.content_filter_type
        for keyword, override_type in patterns.extra_content_filter_rules.items():
            if keyword in text:
                filter_type = override_type
        return LLMContentFilterError(
            provider=provider,
            filter_type=filter_type,
            reason="Response blocked by content filter",
            model=model,
            details=details,
        )
    if _mentions(text, status, patterns.context_length):
        return LLMContextLengthError(provider=provider, model=model_name, details=details)
    if _mentions(text, status, patterns.server_error):
        return LLMServiceError(
            provider=provider,
            reason="Service temporarily unavailable",
            model=model,
            is_timeout="timeout" in text or _mentions(text, status, ("504",)),
            details=details,
        )

    # 3. timeouts and dropped connections
    if kind == "timeout" or _mentions(text, status, patterns.timeout):
        return LLMServiceError(
            provider=provider,
            reason=f"Request timed out [{error_type or 'timeout'}]: {message}",
            model=model,
            is_timeout=True,
            details=details,
        )
    if kind == "transport" or _mentions(text, status, patterns.connection):
        return LLMServiceError(
            provider=provider,
            reason=connection_reason or f"Unable to connect to {provider.title()} API: {message}",
            model=model,
            details=details,
        )

    return LLMError(
        message=f"{provider.title()} error [{error_type or 'error'}]: {message}",
        code="LLM_ERROR",
        provider=provider,
        model=model,
        is_retryable=patterns.unknown_retryable,
        suggested_action=f"Try again or check the {provider.title()} API status.",
        details=details,
    )


def classify_llm_exception(
    error: BaseException,
    *,
    provider: str,
    model: str | None,
    patterns: ProviderErrorPatterns | None = None,
    connection_reason: str | None = None,
) -> LLMError:
    """The :class:`LLMError` a provider raises (or streams) for ``error``.

    An :class:`LLMError` is returned unchanged (already classified, possibly
    by a provider's own fatal check such as ``ToolCallingNotSupportedError``);
    anything else goes through :func:`classify_llm_failure` with the status
    and kind its type carries.
    """
    if isinstance(error, LLMError):
        return error
    message = str(error) or repr(error)
    return classify_llm_failure(
        message,
        provider=provider,
        model=model,
        patterns=patterns,
        status=_status_of(error),
        kind=_kind_of(error),
        error_type=type(error).__name__,
        connection_reason=connection_reason,
    )


def classify_provider_error(
    error: Exception, model: str, patterns: ProviderErrorPatterns
) -> LLMError:
    """A cloud provider's classifier: :func:`classify_llm_exception` with its indicator words."""
    return classify_llm_exception(error, provider=patterns.provider, model=model, patterns=patterns)


def stream_error_event(error: LLMError) -> dict[str, Any]:
    """The ``error`` event payload for a classified failure: message, code and details.

    The details carry ``is_retryable``, ``retry_limit``, ``status_code``,
    ``is_timeout``, the provider and the model, so
    :func:`error_from_stream_chunk` rebuilds the same error from the event a
    sink received as from the chunk a provider yielded.
    """
    return {"error": error.message, "error_code": error.code, "error_details": dict(error.details)}


def error_chunk(error: LLMError) -> dict[str, Any]:
    """The stream chunk a provider yields for a classified failure (:func:`stream_error_event`)."""
    return {"type": "error", **stream_error_event(error)}


def _rebuilt(
    code: str, message: str, details: dict[str, Any], provider: str, model: str | None
) -> LLMError | None:
    """The error a classified chunk names by its code, with its message kept; None for an unknown code."""
    error: LLMError
    if code == "LLM_MODEL_ERROR":
        error = LLMModelError(provider=provider, model=model or "unknown", details=dict(details))
    elif code == "LLM_SERVICE_ERROR":
        error = LLMServiceError(
            provider=provider,
            model=model,
            is_timeout=bool(details.get("is_timeout")),
            details=dict(details),
        )
    elif code == "LLM_RATE_LIMIT_ERROR":
        error = LLMRateLimitError(
            provider=provider,
            model=model,
            retry_after=details.get("retry_after"),
            quota_exceeded=bool(details.get("quota_exceeded")),
            details=dict(details),
        )
    elif code == "MODEL_CAPABILITY_ERROR" and details.get("capability") == "tool_calling":
        error = ToolCallingNotSupportedError(model=model or "unknown", provider=provider)
    elif code.startswith("LLM_"):
        error = LLMError(
            message,
            code=code,
            provider=provider,
            model=model,
            is_retryable=bool(details.get("is_retryable")),
            details=dict(details),
        )
    else:
        return None
    error.message = message
    error.args = (message,)
    return error


def error_from_stream_chunk(chunk: dict[str, Any]) -> LLMError:
    """The :class:`LLMError` a stream's error chunk stands for: the one stream classifier.

    A chunk a provider built with :func:`error_chunk` (its details say
    whether it is retryable) is rebuilt as the same class with the same
    message, code and retry limit. A chunk that carries only text, a status or
    an older ``error_code`` (a provider that classifies nothing, a test
    double) goes through :func:`classify_llm_failure`, so a 404 or "model not
    found" is fatal, a 429/503/timeout/dropped connection retryable, a crashed
    runner retryable once and the rest a plain :class:`LLMError`.

    Every consumer of a stream calls this: the chat loop
    (``streaming/chat/loop.py``), the extraction stream and the benchmark's
    chat stage, so a failure is retried or not the same way everywhere.
    """
    message = str(chunk.get("error") or "Unknown LLM error")
    code = str(chunk.get("error_code") or "")
    details = chunk.get("error_details")
    details = details if isinstance(details, dict) else {}
    provider = str(details.get("provider") or "unknown")
    model = details.get("model")
    if code and "is_retryable" in details:
        rebuilt = _rebuilt(code, message, details, provider, model)
        if rebuilt is not None:
            return rebuilt
    status = chunk.get("status_code", details.get("status_code"))
    classified = classify_llm_failure(
        message,
        provider=provider,
        model=model,
        status=status if isinstance(status, int) else None,
        error_type=str(details.get("error_type") or "") or None,
    )
    if classified.code == "LLM_ERROR" and code:
        # the text said nothing; a code the producer set still names the class
        rebuilt = _rebuilt(code, message, details, provider, model)
        if rebuilt is not None:
            return rebuilt
    # the producer already worded it: the chunk's message is the error's
    classified.message = message
    classified.args = (message,)
    return classified


def retry_budget(error: BaseException, configured: int) -> int:
    """How many retries ``error`` allows under a loop configured for ``configured``.

    Zero for an error that is not retryable; the error's own limit
    (``details["retry_limit"]``, :data:`RUNNER_CRASH_RETRY_LIMIT` for a
    crashed runner) when it sets one below ``configured``; else
    ``configured``. A retry loop asks this on every failure and stops when
    its attempt count reaches it.
    """
    if not getattr(error, "is_retryable", False) or isinstance(error, LLMModelError):
        return 0
    limit = (getattr(error, "details", None) or {}).get(RETRY_LIMIT_KEY)
    if isinstance(limit, int) and not isinstance(limit, bool):
        return max(0, min(configured, limit))
    return configured


__all__ = [
    "OLLAMA_PATTERNS",
    "RETRY_LIMIT_KEY",
    "RUNNER_CRASH_RETRY_LIMIT",
    "ProviderErrorPatterns",
    "classify_llm_exception",
    "classify_llm_failure",
    "classify_provider_error",
    "error_chunk",
    "error_from_stream_chunk",
    "retry_budget",
    "stream_error_event",
]
