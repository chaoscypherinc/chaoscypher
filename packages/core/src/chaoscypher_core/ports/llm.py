# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""LLM provider protocol and task-type enumeration.

Services depend on ``LLMProviderPort`` — the narrow contract covering only the
methods services actually call — not on the concrete ``LLMProvider`` in
``chaoscypher_core.adapters.llm``. The Engine injects a concrete provider at
construction time.

``TaskType`` lives here because it is part of the port's vocabulary: callers
pass it as a routing/metrics hint to the port's methods.

Phase 2 will migrate ~15 service files off direct ``chaoscypher_core.adapters.llm``
imports onto this port.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable


if TYPE_CHECKING:
    from chaoscypher_core.models import LLMChatResponse


class TaskType(StrEnum):
    """Categorization of LLM request types — routing and metrics hint.

    Used for categorizing and routing LLM tasks through the queue system.
    """

    CHAT = "chat"
    EMBEDDING = "embedding"
    TOOL = "tool"


@runtime_checkable
class LLMProviderPort(Protocol):
    """Minimal service-facing contract for LLM access.

    Keep narrow. Adapter-internal helpers (load balancing, semaphore management,
    cost tracking) stay on the concrete ``LLMProvider``; only methods that
    multiple services actually call belong here.

    The sole method exposed is ``chat`` — the only LLM operation called directly
    by service code. (Embedding access goes through ``EmbeddingProviderProtocol``.)
    """

    async def chat(
        self,
        messages: str | list[Any],
        tools: list[Any] | None = None,
        stream: bool = False,
        **kwargs: Any,
    ) -> LLMChatResponse:
        """Execute a chat completion.

        Args:
            messages: A string prompt (auto-wrapped as a user message) or a list
                of chat messages in OpenAI format (each a dict with ``role`` and
                ``content``).
            tools: Optional list of tool definitions in OpenAI function-calling
                format. Pass ``None`` when tool calling is not needed.
            stream: When ``True`` the returned ``LLMChatResponse`` carries an
                async generator in its ``stream`` attribute instead of a
                pre-populated ``content`` string.
            **kwargs: Provider-specific overrides forwarded verbatim, e.g.
                ``temperature``, ``max_tokens``, ``enable_thinking``,
                ``high_priority``.

        Returns:
            ``LLMChatResponse`` with ``content``, ``tool_calls``, ``thinking``,
            ``usage``, ``provider``, and ``is_stream`` fields.

        Raises:
            LLMError: If the completion request fails.

        """
        ...
