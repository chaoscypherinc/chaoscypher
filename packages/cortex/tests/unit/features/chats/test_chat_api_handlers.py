# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Handler-level tests for the Chats API routes.

FastAPI DI is bypassed — each async route function is called directly with
a MagicMock ``ChatService`` / ``ChatFeatureService`` and ``_="test-user"``.
The response_model is not applied (we call the coroutine directly), so the
assertions target delegation, the raw dict / response object returned, and
the 404 / 202 / enqueue branches.

Mirrors the handler-level pattern in
tests/unit/features/nodes/test_node_handlers.py.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from chaoscypher_cortex.features.chats.api import (
    add_message,
    clear_chat_scope,
    create_chat,
    delete_all_chats,
    delete_chat,
    generate_title,
    get_chat,
    get_chat_count,
    get_chat_messages,
    list_chats,
    send_message,
    update_chat,
    update_chat_scope,
    update_chat_status,
)
from chaoscypher_cortex.features.chats.models import (
    ChatCountResponse,
    ChatCreate,
    ChatMessageCreate,
    ChatScopeUpdate,
    ChatSendRequest,
    ChatStatusUpdate,
    ChatTitleUpdate,
    PaginatedChatsResponse,
)


_NOW = datetime.now(UTC)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _settings() -> MagicMock:
    """Return a minimal settings mock for chat handlers."""
    settings = MagicMock()
    settings.current_database = "default"
    settings.priorities.interactive = 10
    return settings


def _chat_dict(chat_id: str = "chat-1", status: str = "active") -> dict[str, Any]:
    """Return a chat dict resembling engine ChatService output."""
    return {
        "id": chat_id,
        "title": "My Chat",
        "status": status,
        "created_at": _NOW,
        "updated_at": _NOW,
        "message_count": 0,
        "source_ids": None,
        "messages": [],
    }


def _list_row(chat_id: str) -> dict[str, Any]:
    """Return a chat list-row dict (no messages)."""
    return {
        "id": chat_id,
        "title": "Row",
        "status": "active",
        "created_at": _NOW,
        "updated_at": _NOW,
        "message_count": 0,
        "source_ids": None,
    }


# ---------------------------------------------------------------------------
# list_chats — pagination metadata
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestListChatsHandler:
    """Tests for list_chats pagination-metadata assembly."""

    @pytest.mark.asyncio
    async def test_builds_pagination_metadata(self) -> None:
        """Handler translates (page, page_size) → (offset, limit) + metadata."""
        service = MagicMock()
        service.list_chats.return_value = [_list_row("c1"), _list_row("c2")]
        service.count_chats.return_value = 30

        result = await list_chats(
            chat_service=service,
            pagination=(2, 10),
            _="test-user",
            scoped=None,
        )

        kwargs = service.list_chats.call_args.kwargs
        assert kwargs["offset"] == 10
        assert kwargs["limit"] == 10
        assert isinstance(result, PaginatedChatsResponse)
        assert result.pagination.total == 30
        assert result.pagination.total_pages == 3
        assert result.pagination.has_next is True
        assert result.pagination.has_prev is True


# ---------------------------------------------------------------------------
# create_chat — delegates to feature service
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreateChatHandler:
    """Tests for create_chat delegation to the feature service."""

    @pytest.mark.asyncio
    async def test_delegates_to_feature_service(self) -> None:
        """Handler forwards the ChatCreate body to create_chat_with_scope."""
        feature_service = MagicMock()
        feature_service.create_chat_with_scope.return_value = _chat_dict("new-chat")
        body = ChatCreate(title="Hello", source_ids=["s1"])

        result = await create_chat(
            chat_create=body,
            feature_service=feature_service,
            _="test-user",
        )

        feature_service.create_chat_with_scope.assert_called_once_with(body)
        assert result["id"] == "new-chat"


# ---------------------------------------------------------------------------
# get_chat / delete_chat / delete_all_chats
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetDeleteChatHandlers:
    """Tests for get_chat (+404), delete_chat, and delete_all_chats."""

    @pytest.mark.asyncio
    async def test_get_chat_returns_chat(self) -> None:
        """get_chat returns the chat dict when present."""
        service = MagicMock()
        service.get_chat.return_value = _chat_dict("chat-1")

        result = await get_chat(chat_id="chat-1", chat_service=service, _="test-user")

        service.get_chat.assert_called_once_with("chat-1")
        assert result["id"] == "chat-1"

    @pytest.mark.asyncio
    async def test_get_chat_raises_404_when_missing(self) -> None:
        """get_chat raises 404 via raise_if_not_found when the chat is absent."""
        service = MagicMock()
        service.get_chat.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            await get_chat(chat_id="missing", chat_service=service, _="test-user")

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_chat_delegates(self) -> None:
        """delete_chat forwards the id to the service and returns None."""
        service = MagicMock()

        result = await delete_chat(chat_id="chat-1", chat_service=service, _="test-user")

        service.delete_chat.assert_called_once_with("chat-1")
        assert result is None

    @pytest.mark.asyncio
    async def test_delete_all_chats_delegates(self) -> None:
        """delete_all_chats forwards to the service."""
        service = MagicMock()

        result = await delete_all_chats(_="test-user", service=service)

        service.delete_all_chats.assert_called_once_with()
        assert result is None


# ---------------------------------------------------------------------------
# update_chat / update_chat_status
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUpdateChatHandlers:
    """Tests for update_chat (title) and update_chat_status."""

    @pytest.mark.asyncio
    async def test_update_title_then_returns_refreshed_chat(self) -> None:
        """update_chat updates the title and returns the refreshed chat."""
        service = MagicMock()
        service.get_chat.return_value = _chat_dict("chat-1")

        result = await update_chat(
            chat_id="chat-1",
            title_update=ChatTitleUpdate(title="Renamed"),
            chat_service=service,
            _="test-user",
        )

        service.update_chat.assert_called_once_with(chat_id="chat-1", updates={"title": "Renamed"})
        service.get_chat.assert_called_once_with("chat-1")
        assert result["id"] == "chat-1"

    @pytest.mark.asyncio
    async def test_update_status_then_returns_refreshed_chat(self) -> None:
        """update_chat_status updates the status and returns the refreshed chat."""
        service = MagicMock()
        service.get_chat.return_value = _chat_dict("chat-1", status="completed")

        result = await update_chat_status(
            chat_id="chat-1",
            status_update=ChatStatusUpdate(status="completed"),
            chat_service=service,
            _="test-user",
        )

        service.update_chat_status.assert_called_once_with(chat_id="chat-1", status="completed")
        assert result["status"] == "completed"

    @pytest.mark.asyncio
    async def test_update_title_missing_chat_404(self) -> None:
        """A missing chat must 404, not 500 via response_model on None."""
        service = MagicMock()
        service.update_chat.return_value = False

        with pytest.raises(HTTPException) as exc:
            await update_chat(
                chat_id="nope",
                title_update=ChatTitleUpdate(title="x"),
                chat_service=service,
                _="test-user",
            )
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_update_status_missing_chat_404(self) -> None:
        """A missing chat must 404, not 500 via response_model on None."""
        service = MagicMock()
        service.update_chat_status.return_value = False

        with pytest.raises(HTTPException) as exc:
            await update_chat_status(
                chat_id="nope",
                status_update=ChatStatusUpdate(status="active"),
                chat_service=service,
                _="test-user",
            )
        assert exc.value.status_code == 404


@pytest.mark.unit
class TestMissingChat404s:
    """add_message / generate_title must 404 on a missing chat (audit P2)."""

    @pytest.mark.asyncio
    async def test_add_message_missing_chat_404(self) -> None:
        """No existence check previously meant an FK IntegrityError 500."""
        service = MagicMock()
        service.get_chat.return_value = None

        with pytest.raises(HTTPException) as exc:
            await add_message(
                chat_id="nope",
                message_create=ChatMessageCreate(role="user", content="hi"),
                chat_service=service,
                _="test-user",
            )
        assert exc.value.status_code == 404
        service.add_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_generate_title_missing_chat_404(self) -> None:
        """Previously returned None -> ResponseValidationError 500."""
        service = MagicMock()
        service.get_chat.return_value = None

        with pytest.raises(HTTPException) as exc:
            await generate_title(
                chat_id="nope",
                chat_service=service,
                settings=_settings(),
                _="test-user",
            )
        assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# scope handlers
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestScopeHandlers:
    """Tests for update_chat_scope and clear_chat_scope (delegation + 404)."""

    @pytest.mark.asyncio
    async def test_update_scope_returns_chat(self) -> None:
        """update_chat_scope returns the feature-service result when present."""
        feature_service = MagicMock()
        feature_service.update_scope_with_message.return_value = _chat_dict("chat-1")
        body = ChatScopeUpdate(source_ids=["s1", "s2"])

        result = await update_chat_scope(
            chat_id="chat-1",
            scope_update=body,
            feature_service=feature_service,
            _="test-user",
        )

        feature_service.update_scope_with_message.assert_called_once_with("chat-1", body)
        assert result["id"] == "chat-1"

    @pytest.mark.asyncio
    async def test_update_scope_raises_404_when_missing(self) -> None:
        """update_chat_scope raises 404 when the feature service returns None."""
        feature_service = MagicMock()
        feature_service.update_scope_with_message.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            await update_chat_scope(
                chat_id="missing",
                scope_update=ChatScopeUpdate(),
                feature_service=feature_service,
                _="test-user",
            )

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_clear_scope_returns_chat(self) -> None:
        """clear_chat_scope returns the feature-service result when present."""
        feature_service = MagicMock()
        feature_service.clear_scope_with_message.return_value = _chat_dict("chat-1")

        result = await clear_chat_scope(
            chat_id="chat-1",
            feature_service=feature_service,
            _="test-user",
        )

        feature_service.clear_scope_with_message.assert_called_once_with("chat-1")
        assert result["id"] == "chat-1"

    @pytest.mark.asyncio
    async def test_clear_scope_raises_404_when_missing(self) -> None:
        """clear_chat_scope raises 404 when the feature service returns None."""
        feature_service = MagicMock()
        feature_service.clear_scope_with_message.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            await clear_chat_scope(
                chat_id="missing",
                feature_service=feature_service,
                _="test-user",
            )

        assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# message handlers
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMessageHandlers:
    """Tests for add_message and get_chat_messages."""

    @pytest.mark.asyncio
    async def test_add_message_forwards_fields(self) -> None:
        """add_message forwards role/content/extra_metadata to the service."""
        service = MagicMock()
        service.add_message.return_value = {"id": "m1", "role": "user", "content": "hi"}
        body = ChatMessageCreate(role="user", content="hi", extra_metadata={"k": "v"})

        result = await add_message(
            chat_id="chat-1",
            message_create=body,
            chat_service=service,
            _="test-user",
        )

        service.add_message.assert_called_once_with(
            chat_id="chat-1",
            role="user",
            content="hi",
            extra_metadata={"k": "v"},
        )
        assert result["id"] == "m1"

    @pytest.mark.asyncio
    async def test_get_chat_messages_delegates(self) -> None:
        """get_chat_messages returns the service's message list."""
        service = MagicMock()
        service.get_chat_messages.return_value = [{"id": "m1"}, {"id": "m2"}]

        result = await get_chat_messages(
            chat_id="chat-1",
            chat_service=service,
            _="test-user",
        )

        service.get_chat_messages.assert_called_once_with("chat-1")
        assert len(result) == 2


# ---------------------------------------------------------------------------
# count handler
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestChatCountHandler:
    """Tests for get_chat_count."""

    @pytest.mark.asyncio
    async def test_returns_count_response(self) -> None:
        """get_chat_count wraps the service count in ChatCountResponse."""
        service = MagicMock()
        service.count_chats.return_value = 7

        result = await get_chat_count(chat_service=service, _="test-user")

        assert isinstance(result, ChatCountResponse)
        assert result.count == 7


# ---------------------------------------------------------------------------
# send_message — background queue path
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSendMessageHandler:
    """Tests for the send_message background-processing handler."""

    @pytest.mark.asyncio
    async def test_enqueues_and_returns_202_payload(self) -> None:
        """Handler saves the message, sets processing, enqueues, returns task id."""
        service = MagicMock()
        service.get_chat.return_value = _chat_dict("chat-1")
        settings = _settings()
        body = ChatSendRequest(content="hello there")

        with (
            patch(
                "chaoscypher_core.services.llm.require_extraction_ready",
                new=AsyncMock(),
            ),
            patch(
                "chaoscypher_cortex.features.chats.api.queue_client.enqueue_task",
                new=AsyncMock(return_value="task-9"),
            ) as mock_enqueue,
        ):
            result = await send_message(
                chat_id="chat-1",
                message=body,
                chat_service=service,
                settings=settings,
                _="test-user",
            )

        service.add_message.assert_called_once_with("chat-1", role="user", content="hello there")
        service.update_chat_status.assert_called_once_with("chat-1", "processing")
        mock_enqueue.assert_awaited_once()
        assert result.task_id == "task-9"
        assert result.status == "processing"

    @pytest.mark.asyncio
    async def test_raises_404_when_chat_missing(self) -> None:
        """Handler raises 404 (no enqueue) when the chat does not exist."""
        service = MagicMock()
        service.get_chat.return_value = None
        settings = _settings()

        with (
            patch(
                "chaoscypher_core.services.llm.require_extraction_ready",
                new=AsyncMock(),
            ),
            patch(
                "chaoscypher_cortex.features.chats.api.queue_client.enqueue_task",
                new=AsyncMock(),
            ) as mock_enqueue,
        ):
            with pytest.raises(HTTPException) as exc_info:
                await send_message(
                    chat_id="missing",
                    message=ChatSendRequest(content="hi"),
                    chat_service=service,
                    settings=settings,
                    _="test-user",
                )

        assert exc_info.value.status_code == 404
        mock_enqueue.assert_not_called()

    @pytest.mark.asyncio
    async def test_propagates_extraction_not_ready(self) -> None:
        """A require_extraction_ready failure propagates before any enqueue."""
        service = MagicMock()
        settings = _settings()

        with (
            patch(
                "chaoscypher_core.services.llm.require_extraction_ready",
                new=AsyncMock(side_effect=HTTPException(status_code=409, detail="not verified")),
            ),
            patch(
                "chaoscypher_cortex.features.chats.api.queue_client.enqueue_task",
                new=AsyncMock(),
            ) as mock_enqueue,
        ):
            with pytest.raises(HTTPException) as exc_info:
                await send_message(
                    chat_id="chat-1",
                    message=ChatSendRequest(content="hi"),
                    chat_service=service,
                    settings=settings,
                    _="test-user",
                )

        assert exc_info.value.status_code == 409
        service.get_chat.assert_not_called()
        mock_enqueue.assert_not_called()


# ---------------------------------------------------------------------------
# cancel_chat_turn — stop an in-flight background turn (Phase 2)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCancelChatTurnHandler:
    """Tests for POST /chats/{chat_id}/cancel."""

    @pytest.mark.asyncio
    async def test_sets_flag_and_returns_cancelling(self) -> None:
        """A processing chat gets its cancel flag set; handler returns 202 body."""
        from chaoscypher_cortex.features.chats.api import cancel_chat_turn

        service = MagicMock()
        service.get_chat.return_value = _chat_dict("chat-1", status="processing")

        with patch(
            "chaoscypher_core.streaming.chat.cancellation.request_cancel",
            new=AsyncMock(return_value=True),
        ) as mock_request:
            result = await cancel_chat_turn(chat_id="chat-1", chat_service=service, _="test-user")

        mock_request.assert_awaited_once_with("chat-1")
        assert result == {"status": "cancelling"}

    @pytest.mark.asyncio
    async def test_raises_404_when_chat_missing(self) -> None:
        """An unknown chat 404s without touching the cancel transport."""
        from chaoscypher_cortex.features.chats.api import cancel_chat_turn

        service = MagicMock()
        service.get_chat.return_value = None

        with patch(
            "chaoscypher_core.streaming.chat.cancellation.request_cancel",
            new=AsyncMock(),
        ) as mock_request:
            with pytest.raises(HTTPException) as exc_info:
                await cancel_chat_turn(chat_id="missing", chat_service=service, _="test-user")

        assert exc_info.value.status_code == 404
        mock_request.assert_not_called()

    @pytest.mark.asyncio
    async def test_raises_409_when_no_turn_in_progress(self) -> None:
        """Cancelling an idle chat is a 409 — there is nothing to stop."""
        from chaoscypher_cortex.features.chats.api import cancel_chat_turn

        service = MagicMock()
        service.get_chat.return_value = _chat_dict("chat-1", status="active")

        with pytest.raises(HTTPException) as exc_info:
            await cancel_chat_turn(chat_id="chat-1", chat_service=service, _="test-user")

        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_raises_503_when_transport_unavailable(self) -> None:
        """A failed flag write surfaces as 503 so the UI can tell the user."""
        from chaoscypher_cortex.features.chats.api import cancel_chat_turn

        service = MagicMock()
        service.get_chat.return_value = _chat_dict("chat-1", status="processing")

        with patch(
            "chaoscypher_core.streaming.chat.cancellation.request_cancel",
            new=AsyncMock(return_value=False),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await cancel_chat_turn(chat_id="chat-1", chat_service=service, _="test-user")

        assert exc_info.value.status_code == 503


# ---------------------------------------------------------------------------
# retry_chat_turn — re-run a failed turn without duplicating the message
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRetryChatTurnHandler:
    """Tests for POST /chats/{chat_id}/retry."""

    @staticmethod
    def _chat_with_history(status: str = "error") -> dict[str, Any]:
        chat = _chat_dict("chat-1", status=status)
        chat["messages"] = [{"role": "user", "content": "the question"}]
        return chat

    @pytest.mark.asyncio
    async def test_reenqueues_without_adding_a_message(self) -> None:
        """Retry flips to processing and enqueues — but never add_message."""
        from chaoscypher_cortex.features.chats.api import retry_chat_turn

        service = MagicMock()
        service.get_chat.return_value = self._chat_with_history()
        settings = _settings()

        with (
            patch(
                "chaoscypher_core.services.llm.require_extraction_ready",
                new=AsyncMock(),
            ),
            patch(
                "chaoscypher_cortex.features.chats.api.queue_client.enqueue_task",
                new=AsyncMock(return_value="task-7"),
            ) as mock_enqueue,
        ):
            result = await retry_chat_turn(
                chat_id="chat-1", chat_service=service, settings=settings, _="test-user"
            )

        service.add_message.assert_not_called()
        service.update_chat_status.assert_called_once_with("chat-1", "processing")
        mock_enqueue.assert_awaited_once()
        assert result.task_id == "task-7"
        assert result.status == "processing"

    @pytest.mark.asyncio
    async def test_raises_404_when_chat_missing(self) -> None:
        from chaoscypher_cortex.features.chats.api import retry_chat_turn

        service = MagicMock()
        service.get_chat.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            await retry_chat_turn(
                chat_id="missing", chat_service=service, settings=_settings(), _="test-user"
            )
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_raises_409_when_already_processing(self) -> None:
        from chaoscypher_cortex.features.chats.api import retry_chat_turn

        service = MagicMock()
        service.get_chat.return_value = self._chat_with_history(status="processing")

        with pytest.raises(HTTPException) as exc_info:
            await retry_chat_turn(
                chat_id="chat-1", chat_service=service, settings=_settings(), _="test-user"
            )
        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_raises_409_when_no_user_message(self) -> None:
        """Nothing to answer: an empty history cannot be retried."""
        from chaoscypher_cortex.features.chats.api import retry_chat_turn

        service = MagicMock()
        chat = _chat_dict("chat-1", status="error")
        chat["messages"] = []
        service.get_chat.return_value = chat

        with pytest.raises(HTTPException) as exc_info:
            await retry_chat_turn(
                chat_id="chat-1", chat_service=service, settings=_settings(), _="test-user"
            )
        assert exc_info.value.status_code == 409


# ---------------------------------------------------------------------------
# export_chat — JSON + Markdown export
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExportChatHandler:
    """Tests for GET /chats/{chat_id}/export."""

    @staticmethod
    def _chat_with_marked_history() -> dict[str, Any]:
        chat = _chat_dict("chat-1")
        chat["title"] = "War & Peace Q&A"
        chat["messages"] = [
            {"id": "m1", "role": "user", "content": "Who is Pierre?"},
            {"id": "m2", "role": "tool", "content": "{}"},
            {
                "id": "m3",
                "role": "assistant",
                "content": (
                    "Pierre is [[node:node_abc|Pierre Bezukhov]]. "
                    "He inherits a fortune [[cite:C0:S1|war.txt]]."
                ),
                "extra_metadata": {
                    "chunk_citations": {
                        "C0:S1": {
                            "filename": "war.txt",
                            "sentence_text": "Pierre inherited his father's fortune.",
                        }
                    }
                },
            },
        ]
        return chat

    @pytest.mark.asyncio
    async def test_json_export_wraps_chat_in_data(self) -> None:
        from chaoscypher_cortex.features.chats.api import export_chat

        service = MagicMock()
        service.get_chat.return_value = self._chat_with_marked_history()

        result = await export_chat(
            chat_id="chat-1", chat_service=service, _="test-user", export_format="json"
        )
        # The frontend unwraps response.data.data — body must be {"data": chat}.
        import json as _json

        body = _json.loads(result.body)
        assert body["data"]["id"] == "chat-1"
        assert len(body["data"]["messages"]) == 3

    @pytest.mark.asyncio
    async def test_markdown_export_renders_footnotes_and_strips_markers(self) -> None:
        from chaoscypher_cortex.features.chats.api import export_chat

        service = MagicMock()
        service.get_chat.return_value = self._chat_with_marked_history()

        result = await export_chat(
            chat_id="chat-1", chat_service=service, _="test-user", export_format="markdown"
        )
        text = result.body.decode("utf-8")
        assert "# War & Peace Q&A" in text
        assert "### You" in text
        assert "### Assistant" in text
        assert "**Pierre Bezukhov**" in text
        assert "[^1]" in text
        assert "war.txt" in text
        assert "Pierre inherited his father's fortune." in text
        # No raw markers, no tool rows.
        assert "[[cite:" not in text
        assert "[[node:" not in text
        assert "{}" not in text
        assert result.media_type == "text/markdown"
        assert "attachment" in result.headers["content-disposition"]

    @pytest.mark.asyncio
    async def test_export_unknown_chat_404s(self) -> None:
        from chaoscypher_cortex.features.chats.api import export_chat

        service = MagicMock()
        service.get_chat.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            await export_chat(
                chat_id="missing", chat_service=service, _="test-user", export_format="json"
            )
        assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# regenerate_chat_turn + /send replace_from_message_id (edit-and-resend)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRegenerateChatTurnHandler:
    """Tests for POST /chats/{chat_id}/regenerate."""

    @staticmethod
    def _chat(status: str = "active") -> dict[str, Any]:
        chat = _chat_dict("chat-1", status=status)
        chat["messages"] = [
            {"id": "m1", "role": "user", "content": "q1"},
            {"id": "m2", "role": "assistant", "content": "a1"},
            {"id": "m3", "role": "user", "content": "q2"},
            {"id": "m4", "role": "assistant", "content": "a2"},
        ]
        return chat

    @pytest.mark.asyncio
    async def test_truncates_after_last_user_message_and_enqueues(self) -> None:
        from chaoscypher_cortex.features.chats.api import regenerate_chat_turn

        service = MagicMock()
        service.get_chat.return_value = self._chat()

        with (
            patch(
                "chaoscypher_core.services.llm.require_extraction_ready",
                new=AsyncMock(),
            ),
            patch(
                "chaoscypher_cortex.features.chats.api.queue_client.enqueue_task",
                new=AsyncMock(return_value="task-r"),
            ) as mock_enqueue,
        ):
            result = await regenerate_chat_turn(
                chat_id="chat-1", chat_service=service, settings=_settings(), _="test-user"
            )

        # Trailing answer dropped, last user message kept.
        service.truncate_from_message.assert_called_once_with("chat-1", "m3", inclusive=False)
        service.add_message.assert_not_called()
        service.update_chat_status.assert_called_once_with("chat-1", "processing")
        mock_enqueue.assert_awaited_once()
        assert result.task_id == "task-r"

    @pytest.mark.asyncio
    async def test_409_when_processing_or_no_user_message(self) -> None:
        from chaoscypher_cortex.features.chats.api import regenerate_chat_turn

        service = MagicMock()
        service.get_chat.return_value = self._chat(status="processing")
        with pytest.raises(HTTPException) as exc_info:
            await regenerate_chat_turn(
                chat_id="chat-1", chat_service=service, settings=_settings(), _="test-user"
            )
        assert exc_info.value.status_code == 409

        empty = _chat_dict("chat-1")
        empty["messages"] = []
        service.get_chat.return_value = empty
        with pytest.raises(HTTPException) as exc_info:
            await regenerate_chat_turn(
                chat_id="chat-1", chat_service=service, settings=_settings(), _="test-user"
            )
        assert exc_info.value.status_code == 409


@pytest.mark.unit
class TestSendReplaceFromMessage:
    """Edit-and-resend: /send with replace_from_message_id truncates first."""

    @staticmethod
    def _chat() -> dict[str, Any]:
        chat = _chat_dict("chat-1")
        chat["messages"] = [
            {"id": "m1", "role": "user", "content": "original question"},
            {"id": "m2", "role": "assistant", "content": "old answer"},
        ]
        return chat

    @pytest.mark.asyncio
    async def test_truncates_inclusively_then_adds_new_message(self) -> None:
        service = MagicMock()
        service.get_chat.return_value = self._chat()
        body = ChatSendRequest(content="edited question", replace_from_message_id="m1")

        with (
            patch(
                "chaoscypher_core.services.llm.require_extraction_ready",
                new=AsyncMock(),
            ),
            patch(
                "chaoscypher_cortex.features.chats.api.queue_client.enqueue_task",
                new=AsyncMock(return_value="task-e"),
            ),
        ):
            result = await send_message(
                chat_id="chat-1",
                message=body,
                chat_service=service,
                settings=_settings(),
                _="test-user",
            )

        service.truncate_from_message.assert_called_once_with("chat-1", "m1", inclusive=True)
        service.add_message.assert_called_once_with(
            "chat-1", role="user", content="edited question"
        )
        assert result.task_id == "task-e"

    @pytest.mark.asyncio
    async def test_409_when_anchor_is_not_a_user_message(self) -> None:
        service = MagicMock()
        service.get_chat.return_value = self._chat()
        body = ChatSendRequest(content="x", replace_from_message_id="m2")  # assistant row

        with patch(
            "chaoscypher_core.services.llm.require_extraction_ready",
            new=AsyncMock(),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await send_message(
                    chat_id="chat-1",
                    message=body,
                    chat_service=service,
                    settings=_settings(),
                    _="test-user",
                )
        assert exc_info.value.status_code == 409
        service.truncate_from_message.assert_not_called()
        service.add_message.assert_not_called()


# ---------------------------------------------------------------------------
# list_chats — server-side title search
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestListChatsSearch:
    """The q param reaches the service as a title-substring filter."""

    @pytest.mark.asyncio
    async def test_q_forwarded_to_service(self) -> None:
        service = MagicMock()
        service.list_chats.return_value = []
        service.count_chats.return_value = 0

        await list_chats(
            chat_service=service, pagination=(1, 50), _="test-user", scoped=None, q="peace"
        )

        assert service.list_chats.call_args.kwargs["search"] == "peace"
        assert service.count_chats.call_args.kwargs["search"] == "peace"
