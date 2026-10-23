# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The chat callbacks the tool executor's producers hand it, each honouring ``ChatCallback``.

A tool's chat model (the shape router, the summarize tool) is awaited as
``chat(messages, temperature=..., max_tokens=...)`` and answers a
``{"content": str}`` dict
(:data:`~chaoscypher_core.services.workflows.tools.engine.handlers.question_shape.ChatCallback`).
Two ways to reach a model, one builder each, so no producer hands over a
provider response or a service object instead:

- :func:`provider_chat_callback`: the provider directly (the CLI, and the
  Neuron worker, which runs on the LLM queue and would deadlock enqueuing on it);
- :func:`queued_chat_callback`: through the LLM queue (the app's chat stream,
  workflows).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from chaoscypher_core.ports.llm import TaskType
from chaoscypher_core.services.workflows.tools.engine.handlers.question_shape import (
    reply_content,
)


if TYPE_CHECKING:
    from chaoscypher_core.services.workflows.tools.engine.handlers.question_shape import (
        ChatCallback,
    )


def provider_chat_callback(provider: Any) -> ChatCallback:
    """A chat callback over a provider's ``chat`` (no queue), answering ``{"content": ...}``.

    ``temperature`` and ``max_tokens`` are passed only when set, so the
    provider's own defaults hold otherwise.

    Args:
        provider: An object with ``async chat(messages=..., stream=..., **kwargs)``
            returning a response with a text ``content`` (``LLMChatResponse``).
    """

    async def chat(
        messages: list[dict[str, Any]],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        """One non-streaming chat completion on the provider."""
        kwargs: dict[str, Any] = {"messages": messages, "stream": False}
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        return {"content": reply_content(await provider.chat(**kwargs))}

    return chat


def queued_chat_callback(queue_service: Any, *, priority: int) -> ChatCallback:
    """A chat callback through the LLM queue at ``priority``, answering ``{"content": ...}``.

    Args:
        queue_service: The ``LLMQueueService`` (``queue_operation`` and
            ``wait_for_result``).
        priority: The queue priority (``settings.priorities``: interactive for
            chat, background for workflows).
    """

    async def chat(
        messages: list[dict[str, Any]],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        """One chat completion, enqueued and awaited."""
        task_id = await queue_service.queue_operation(
            task_type=TaskType.CHAT,
            operation_name="chat_completion",
            messages=messages,
            priority=priority,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return {"content": reply_content(await queue_service.wait_for_result(task_id))}

    return chat


__all__ = ["provider_chat_callback", "queued_chat_callback"]
