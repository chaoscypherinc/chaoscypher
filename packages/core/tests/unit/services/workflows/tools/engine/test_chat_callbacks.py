# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""The chat callbacks every tool-executor producer hands over honour ``ChatCallback``: a ``{"content"}`` dict."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_core.models import LLMChatResponse
from chaoscypher_core.ports.llm import TaskType
from chaoscypher_core.services.workflows.engine.tool_executor_adapter import (
    BackendToolExecutorAdapter,
)
from chaoscypher_core.services.workflows.tools.engine.chat_callbacks import (
    provider_chat_callback,
    queued_chat_callback,
)


@pytest.mark.asyncio
async def test_the_provider_callback_answers_a_content_dict_and_passes_only_what_was_set() -> None:
    provider = MagicMock()
    provider.chat = AsyncMock(return_value=LLMChatResponse(content="hi", provider="ollama"))
    chat = provider_chat_callback(provider)
    assert await chat([{"role": "user", "content": "q"}], temperature=0.0) == {"content": "hi"}
    assert provider.chat.await_args.kwargs == {
        "messages": [{"role": "user", "content": "q"}],
        "stream": False,
        "temperature": 0.0,
    }


@pytest.mark.asyncio
async def test_the_queued_callback_enqueues_a_chat_and_answers_its_content() -> None:
    queue = MagicMock()
    queue.queue_operation = AsyncMock(return_value="t1")
    queue.wait_for_result = AsyncMock(return_value={"content": "hi", "provider": "ollama"})
    chat = queued_chat_callback(queue, priority=50)
    assert await chat([{"role": "user", "content": "q"}], max_tokens=8) == {"content": "hi"}
    kwargs: dict[str, Any] = queue.queue_operation.await_args.kwargs
    assert (kwargs["task_type"], kwargs["operation_name"], kwargs["priority"]) == (
        TaskType.CHAT,
        "chat_completion",
        50,
    )
    assert (kwargs["temperature"], kwargs["max_tokens"]) == (None, 8)
    queue.wait_for_result.assert_awaited_once_with("t1")


def test_the_workflow_adapter_hands_the_executor_a_callback_over_the_llm_queue() -> None:
    """It passed the queue service itself, which is no callable chat model."""
    llm_service = MagicMock()
    llm_service.settings.priorities.background = 50
    adapter = BackendToolExecutorAdapter(MagicMock(), MagicMock(), llm_provider=llm_service)
    chat = adapter.tool_executor.llm_chat_callback
    assert chat is not None and chat is not llm_service
    assert adapter.tool_executor.graphrag_handlers.chat_callback is chat
    none = BackendToolExecutorAdapter(MagicMock(), MagicMock(), llm_provider=None)
    assert none.tool_executor.llm_chat_callback is None
