# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""LLM Error Type Definitions.

Standardized error type constants and mapping functions for consistent
error reporting across the LLM extraction pipeline.

These error types are used for metrics collection and frontend display.
Each type maps to a user-friendly display name and description in the UI.
"""

from collections.abc import Callable
from enum import StrEnum


class LLMErrorType(StrEnum):
    """Standardized LLM error types.

    These error types are used for metrics collection and frontend display.
    Each type maps to a user-friendly display name and description.
    """

    # Schema/Validation errors
    VALIDATION_ERROR = "validation_error"
    QUALITY_ERROR = "quality_error"

    # Model capability errors
    CAPABILITY_ERROR = "capability_error"

    # API/Network errors
    CONNECTION_ERROR = "connection_error"
    TIMEOUT_ERROR = "timeout_error"
    RATE_LIMIT_ERROR = "rate_limit_error"

    # Response parsing errors
    JSON_PARSE_ERROR = "json_parse_error"
    TRUNCATION_ERROR = "truncation_error"
    EMPTY_RESPONSE_ERROR = "empty_response_error"

    # Generic/Unknown errors
    PROVIDER_ERROR = "provider_error"
    UNKNOWN_ERROR = "unknown_error"


# Mapping from Python exception names to standardized error types
_EXCEPTION_TO_ERROR_TYPE: dict[str, LLMErrorType] = {
    # JSON parsing
    "JSONDecodeError": LLMErrorType.JSON_PARSE_ERROR,
    # Timeout errors
    "TimeoutError": LLMErrorType.TIMEOUT_ERROR,
    "ReadTimeout": LLMErrorType.TIMEOUT_ERROR,
    "ConnectTimeout": LLMErrorType.TIMEOUT_ERROR,
    # Connection errors
    "ConnectionError": LLMErrorType.CONNECTION_ERROR,
    "ConnectError": LLMErrorType.CONNECTION_ERROR,
    # Rate limit errors (provider-specific)
    "RateLimitError": LLMErrorType.RATE_LIMIT_ERROR,
    # Ollama-specific
    "ResponseError": LLMErrorType.PROVIDER_ERROR,
    # Generic Python exceptions
    "ValueError": LLMErrorType.VALIDATION_ERROR,
    "RuntimeError": LLMErrorType.PROVIDER_ERROR,
    "Exception": LLMErrorType.PROVIDER_ERROR,
}


# Ordered list of (checker, error_type) tuples for message-based classification.
# Checked in priority order; first match wins.
_ERROR_PATTERNS: list[tuple[Callable[[str], bool], str]] = [
    (
        lambda e: "timeout" in e,
        LLMErrorType.TIMEOUT_ERROR.value,
    ),
    (
        lambda e: "rate limit" in e or "rate_limit" in e or "429" in e,
        LLMErrorType.RATE_LIMIT_ERROR.value,
    ),
    (
        lambda e: "connect" in e and ("refused" in e or "failed" in e),
        LLMErrorType.CONNECTION_ERROR.value,
    ),
    (
        lambda e: "truncat" in e or "incomplete" in e or "unexpected end" in e,
        LLMErrorType.TRUNCATION_ERROR.value,
    ),
    (
        lambda e: "empty response" in e or "no response" in e,
        LLMErrorType.EMPTY_RESPONSE_ERROR.value,
    ),
    (
        lambda e: "json" in e and ("parse" in e or "decode" in e or "invalid" in e),
        LLMErrorType.JSON_PARSE_ERROR.value,
    ),
]


def map_exception_to_error_type(exception: Exception) -> str:
    """Map a Python exception to a standardized error type.

    Uses a two-tier approach:
    1. First checks error message patterns for specific error indicators
    2. Falls back to exception type name mapping

    Args:
        exception: The exception that occurred

    Returns:
        Standardized error type string (matches LLMErrorType values)

    """
    error_str = str(exception).lower()

    # Check message patterns first (higher priority than exception type)
    for checker, error_type in _ERROR_PATTERNS:
        if checker(error_str):
            return error_type

    # Fall back to exception name mapping
    exception_name = type(exception).__name__
    if exception_name in _EXCEPTION_TO_ERROR_TYPE:
        return _EXCEPTION_TO_ERROR_TYPE[exception_name].value

    return LLMErrorType.UNKNOWN_ERROR.value
