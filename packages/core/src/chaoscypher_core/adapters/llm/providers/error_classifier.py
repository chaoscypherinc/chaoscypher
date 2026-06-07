# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Shared LLM Error Classifier.

Provides a unified error classification algorithm for cloud LLM providers
(Anthropic, OpenAI, Gemini). Each provider supplies its own indicator word
lists via ProviderErrorPatterns; the classification logic is shared.

Ollama uses a different error structure (connection/model focused) and is
intentionally excluded from this shared classifier.
"""

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
)


@dataclass(frozen=True)
class ProviderErrorPatterns:
    """Provider-specific indicator word lists for error classification.

    Each field is a sequence of lowercase substrings to match against
    ``str(error).lower()``.  The classification algorithm checks them
    in a fixed priority order (rate-limit → auth → model → content
    filter → context length → server → timeout → connection).

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


def classify_provider_error(  # noqa: PLR0911
    error: Exception,
    model: str,
    patterns: ProviderErrorPatterns,
) -> LLMError:
    """Classify an LLM API error into a specific LLMError subclass.

    Uses a priority-ordered cascade of substring checks against the
    lowercased error string.  Provider-specific indicator lists are
    supplied via *patterns*.

    Args:
        error: The original exception from the provider / LangChain.
        model: The model that was being used.
        patterns: Provider-specific indicator word lists.

    Returns:
        An appropriate LLMError subclass with user-friendly messaging.

    """
    error_str = str(error).lower()
    error_type = type(error).__name__
    provider = patterns.provider
    details: dict[str, Any] = {"original_error": str(error)}

    # Extract retry-after header value if present
    retry_after = None
    retry_match = re.search(r"retry.?after[:\s]*(\d+)", error_str)
    if retry_match:
        retry_after = int(retry_match.group(1))

    # 1. Rate limiting (429)
    if any(ind in error_str for ind in patterns.rate_limit):
        is_quota = "quota" in error_str or "billing" in error_str
        return LLMRateLimitError(
            provider=provider,
            model=model,
            retry_after=retry_after,
            quota_exceeded=is_quota,
            details=details,
        )

    # 2. Authentication (401, 403)
    if any(ind in error_str for ind in patterns.auth):
        return LLMAuthenticationError(
            provider=provider,
            reason="Invalid or expired API key",
            model=model,
            details=details,
        )

    # 3. Model not found (404)
    if any(ind in error_str for ind in patterns.model_not_found):
        return LLMModelError(
            provider=provider,
            model=model,
            reason="Model not found or not available",
            details=details,
        )

    # 4. Content filter / safety
    if any(ind in error_str for ind in patterns.content_filter):
        filter_type = patterns.content_filter_type
        for keyword, override_type in patterns.extra_content_filter_rules.items():
            if keyword in error_str:
                filter_type = override_type
        return LLMContentFilterError(
            provider=provider,
            filter_type=filter_type,
            reason="Response blocked by content filter",
            model=model,
            details=details,
        )

    # 5. Context length
    if any(ind in error_str for ind in patterns.context_length):
        return LLMContextLengthError(
            provider=provider,
            model=model,
            details=details,
        )

    # 6. Server errors (5xx)
    if any(ind in error_str for ind in patterns.server_error):
        is_timeout = "timeout" in error_str or "504" in error_str
        return LLMServiceError(
            provider=provider,
            reason="Service temporarily unavailable",
            model=model,
            is_timeout=is_timeout,
            details=details,
        )

    # 7. Timeout
    if any(ind in error_str for ind in patterns.timeout):
        return LLMServiceError(
            provider=provider,
            reason="Request timed out",
            model=model,
            is_timeout=True,
            details=details,
        )

    # 8. Connection
    if any(ind in error_str for ind in patterns.connection):
        return LLMServiceError(
            provider=provider,
            reason=f"Unable to connect to {provider.title()} API",
            model=model,
            details=details,
        )

    # Default: generic LLM error
    return LLMError(
        message=f"{provider.title()} error: {error!s}",
        code="LLM_ERROR",
        provider=provider,
        model=model,
        is_retryable=True,
        suggested_action=f"Try again or check the {provider.title()} API status.",
        details={**details, "error_type": error_type},
    )
