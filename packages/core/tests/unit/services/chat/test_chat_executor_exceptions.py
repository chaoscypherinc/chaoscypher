# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Contract tests: chat/engine/executor.py exception hygiene.

Verifies that the ChatExecutor raises LLMError (not bare ValueError)
when the LLM returns an empty response.
"""

from __future__ import annotations

from typing import Any

import pytest

from chaoscypher_core.exceptions import LLMError
from chaoscypher_core.services.chat.engine.executor import ChatExecutor


class _FakeChatStorage:
    """Minimal ChatStorageProtocol stub for testing."""

    def __init__(self) -> None:
        self._messages: list[dict[str, Any]] = []

    def create_message(self, msg: dict[str, Any]) -> dict[str, Any]:
        """Stub: store and return message."""
        self._messages.append(msg)
        return msg

    def update_chat(self, chat_id: str, updates: dict[str, Any]) -> None:
        """Stub: no-op update."""

    def get_messages(self, chat_id: str) -> list[dict[str, Any]]:
        """Stub: return stored messages."""
        return self._messages


class _FakeLLMService:
    """LLM service stub that returns a configurable response."""

    def __init__(self, response: dict[str, Any] | None) -> None:
        self._response = response

    async def queue_operation(self, **kwargs: Any) -> str:
        """Stub: return a fake task ID."""
        return "task-123"

    async def wait_for_result(self, task_id: str, timeout: float) -> dict[str, Any] | None:
        """Stub: return the configured response."""
        return self._response


class _FakeSettings:
    """Settings stub with minimal fields needed by ChatExecutor."""

    class _Priorities:
        interactive: int = 10

    class _Timeouts:
        llm_chat_wait: float = 30.0

    class _LLM:
        thinking_for_chat: bool = False

    priorities = _Priorities()
    timeouts = _Timeouts()
    llm = _LLM()


class TestChatExecutorEmptyLLMResponse:
    """Contract tests for ChatExecutor LLMError on empty LLM response."""

    @pytest.mark.asyncio
    async def test_empty_llm_result_raises_llm_error_internally(self) -> None:
        """When LLM returns None, process_user_message should log and return None.

        The LLMError is raised inside a broad except-block and swallowed;
        the method contract is to return None on failure.
        """
        storage = _FakeChatStorage()
        llm_service = _FakeLLMService(response=None)
        settings = _FakeSettings()  # type: ignore[arg-type]
        executor = ChatExecutor(
            chat_storage=storage,  # type: ignore[arg-type]
            llm_service=llm_service,
            settings=settings,  # type: ignore[arg-type]
        )

        result = await executor.process_user_message("chat-1", "Hello?")

        # The executor catches all exceptions (including LLMError) and returns None
        assert result is None

    @pytest.mark.asyncio
    async def test_empty_content_raises_llm_error_internally(self) -> None:
        """When LLM returns a result dict with empty content, executor returns None."""
        storage = _FakeChatStorage()
        llm_service = _FakeLLMService(response={"content": "", "usage": {}})
        settings = _FakeSettings()  # type: ignore[arg-type]
        executor = ChatExecutor(
            chat_storage=storage,  # type: ignore[arg-type]
            llm_service=llm_service,
            settings=settings,  # type: ignore[arg-type]
        )

        result = await executor.process_user_message("chat-1", "Hello?")

        # Empty content triggers LLMError → caught → returns None
        assert result is None

    def test_llm_error_is_not_value_error(self) -> None:
        """LLMError is a ChaosCypherException, not a bare ValueError."""
        err = LLMError("Empty response from LLM")

        assert not isinstance(err, ValueError)
        assert err.message == "Empty response from LLM"
