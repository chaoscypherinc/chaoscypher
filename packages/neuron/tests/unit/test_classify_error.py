# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for chaoscypher_core.queue.service.classify_error.

classify_error is the central retry-classification function used by the
queue worker. It returns either "transient" (retry) or "permanent" (do
not retry). These tests pin the 4 canonical paths.

Note on RuntimeError classification:
  A generic RuntimeError with a message that contains no transient keywords
  (e.g. "connection", "timeout", "network", "reset") defaults to "permanent".
  The function only classifies as transient when the exception type is in
  TRANSIENT_ERROR_TYPES or the message contains a transient keyword.
  RuntimeError itself is not in TRANSIENT_ERROR_TYPES.
"""

from __future__ import annotations

import chaoscypher_neuron.worker  # noqa: F401
from chaoscypher_core.exceptions import LLMError
from chaoscypher_core.queue.service import classify_error


def test_classify_error_timeout_is_transient() -> None:
    """asyncio.TimeoutError is classified as transient (retryable).

    TimeoutError (and asyncio.TimeoutError which is an alias) is in
    TRANSIENT_ERROR_TYPES, so it's always retried.
    """
    exc = TimeoutError("simulated timeout")
    assert classify_error(exc) == "transient"


def test_classify_error_runtime_error_is_permanent() -> None:
    """A generic RuntimeError with no transient keywords is classified as permanent.

    RuntimeError is not in TRANSIENT_ERROR_TYPES, and the message
    "simulated failure" contains no transient keywords (connection, timeout,
    network, etc.) so the function falls through to the permanent default.
    """
    exc = RuntimeError("simulated failure")
    assert classify_error(exc) == "permanent"


def test_classify_error_llm_error_retryable_is_transient() -> None:
    """An LLMError with is_retryable=True is classified as transient.

    Example: rate-limit / model warmup. Retry after cooldown.
    """
    exc = LLMError("rate limited", is_retryable=True)
    assert classify_error(exc) == "transient"


def test_classify_error_llm_error_non_retryable_is_permanent() -> None:
    """An LLMError with is_retryable=False is classified as permanent.

    Example: quota exceeded, auth failure. No point retrying.
    """
    exc = LLMError("quota exceeded", is_retryable=False)
    assert classify_error(exc) == "permanent"
