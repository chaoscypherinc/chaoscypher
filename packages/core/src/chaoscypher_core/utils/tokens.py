# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Token estimation utilities.

Provides simple heuristics for estimating token counts when exact counts
are not available (e.g., during streaming responses).

The 4 characters per token ratio is a commonly used approximation that
works reasonably well for English text across different tokenizers.
"""

from typing import Any


def estimate_tokens(text: str) -> int:
    """Estimate tokens using simple heuristic (4 chars per token).

    This approximation is based on the observation that most English text
    averages about 4 characters per token across common tokenizers (GPT,
    Claude, etc.).

    Args:
        text: Text to estimate tokens for

    Returns:
        Estimated token count (minimum 1 for non-empty text)

    Example:
        >>> estimate_tokens("Hello world")
        2
        >>> estimate_tokens("")
        0

    """
    if not text:
        return 0
    return max(1, len(text) // 4)


def estimate_message_tokens(messages: list[dict[str, Any]]) -> int:
    """Estimate tokens for a list of chat messages.

    Includes overhead for message structure (role, formatting).

    Args:
        messages: List of message dicts with 'content' field

    Returns:
        Estimated total token count

    Example:
        >>> msgs = [{"role": "user", "content": "Hello"}]
        >>> estimate_message_tokens(msgs)
        5

    """
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total += estimate_tokens(content)
        # Add overhead for role and message formatting
        total += 4
    return total


__all__ = ["estimate_message_tokens", "estimate_tokens"]
