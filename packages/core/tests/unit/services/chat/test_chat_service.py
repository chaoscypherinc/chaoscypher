# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for ChatService."""

from unittest.mock import MagicMock

import pytest

from chaoscypher_core.services.chat.management.service import ChatService


@pytest.fixture
def mock_storage():
    """Create a mock ChatStorageProtocol."""
    return MagicMock()


@pytest.fixture
def service(mock_storage):
    """Create ChatService with mock storage."""
    return ChatService(storage=mock_storage, database_name="test_db")


# ============================================================================
# list_chats
# ============================================================================


class TestListChats:
    """Tests for ChatService.list_chats."""

    def test_delegates_to_storage(self, service, mock_storage) -> None:
        mock_storage.list_chats.return_value = [{"id": "c1"}]
        result = service.list_chats(limit=10)
        mock_storage.list_chats.assert_called_once_with(
            database_name="test_db",
            user_id=None,
            status=None,
            limit=10,
            offset=0,
            scoped=None,
            search=None,
        )
        assert result == [{"id": "c1"}]

    def test_passes_status_filter(self, service, mock_storage) -> None:
        mock_storage.list_chats.return_value = []
        service.list_chats(status="active")
        mock_storage.list_chats.assert_called_once_with(
            database_name="test_db",
            user_id=None,
            status="active",
            limit=50,
            offset=0,
            scoped=None,
            search=None,
        )

    def test_passes_scoped_filter(self, service, mock_storage) -> None:
        mock_storage.list_chats.return_value = []
        service.list_chats(scoped=True)
        mock_storage.list_chats.assert_called_once_with(
            database_name="test_db",
            user_id=None,
            status=None,
            limit=50,
            offset=0,
            scoped=True,
            search=None,
        )

    def test_passes_offset_to_storage(self, service, mock_storage) -> None:
        """Offset lives in SQL — Python slicing of a top-N fetch made every
        page after the first come back empty (2026-06-10 audit P1).
        """
        mock_storage.list_chats.return_value = [{"id": "c2"}]
        result = service.list_chats(limit=1, offset=1)
        mock_storage.list_chats.assert_called_once_with(
            database_name="test_db",
            user_id=None,
            status=None,
            limit=1,
            offset=1,
            scoped=None,
            search=None,
        )
        assert result == [{"id": "c2"}]


# ============================================================================
# get_chat
# ============================================================================


class TestGetChat:
    """Tests for ChatService.get_chat."""

    def test_returns_chat_with_messages(self, service, mock_storage) -> None:
        mock_storage.get_chat.return_value = {"id": "c1", "title": "Test"}
        mock_storage.get_messages.return_value = [{"id": "m1", "content": "hi"}]
        result = service.get_chat("c1")
        assert result["id"] == "c1"
        assert result["messages"] == [{"id": "m1", "content": "hi"}]
        mock_storage.get_chat.assert_called_once_with("c1", "test_db")
        mock_storage.get_messages.assert_called_once_with("c1")

    def test_returns_none_when_not_found(self, service, mock_storage) -> None:
        mock_storage.get_chat.return_value = None
        assert service.get_chat("missing") is None
        mock_storage.get_messages.assert_not_called()

    def test_returns_empty_messages_list(self, service, mock_storage) -> None:
        mock_storage.get_chat.return_value = {"id": "c1"}
        mock_storage.get_messages.return_value = []
        result = service.get_chat("c1")
        assert result["messages"] == []


# ============================================================================
# create_chat
# ============================================================================


class TestCreateChat:
    """Tests for ChatService.create_chat."""

    def test_creates_chat_with_defaults(self, service, mock_storage) -> None:
        mock_storage.create_chat.return_value = {
            "id": "c1",
            "title": "New Chat",
            "status": "active",
        }
        service.create_chat(chat_id="c1")
        call_args = mock_storage.create_chat.call_args[0][0]
        assert call_args["id"] == "c1"
        assert call_args["database_name"] == "test_db"
        assert call_args["title"] == "New Chat"
        assert call_args["status"] == "active"
        assert "created_at" in call_args
        assert "updated_at" in call_args

    def test_creates_chat_with_custom_title(self, service, mock_storage) -> None:
        mock_storage.create_chat.return_value = {"id": "c1", "title": "My Chat"}
        service.create_chat(chat_id="c1", title="My Chat")
        call_args = mock_storage.create_chat.call_args[0][0]
        assert call_args["title"] == "My Chat"

    def test_creates_scoped_chat(self, service, mock_storage) -> None:
        mock_storage.create_chat.return_value = {"id": "c1", "source_ids": ["s1", "s2"]}
        service.create_chat(chat_id="c1", source_ids=["s1", "s2"])
        call_args = mock_storage.create_chat.call_args[0][0]
        assert call_args["source_ids"] == ["s1", "s2"]

    def test_returns_chat_with_empty_messages(self, service, mock_storage) -> None:
        mock_storage.create_chat.return_value = {"id": "c1"}
        result = service.create_chat(chat_id="c1")
        assert result["messages"] == []

    def test_unscoped_chat_has_no_source_ids(self, service, mock_storage) -> None:
        mock_storage.create_chat.return_value = {"id": "c1"}
        service.create_chat(chat_id="c1")
        call_args = mock_storage.create_chat.call_args[0][0]
        assert "source_ids" not in call_args


# ============================================================================
# update_chat
# ============================================================================


class TestUpdateChat:
    """Tests for ChatService.update_chat."""

    def test_updates_title(self, service, mock_storage) -> None:
        mock_storage.get_chat.return_value = {"id": "c1"}
        result = service.update_chat("c1", {"title": "New Title"})
        assert result is True
        call_args = mock_storage.update_chat.call_args[0]
        assert call_args[0] == "c1"
        assert call_args[1]["title"] == "New Title"
        assert "updated_at" in call_args[1]

    def test_whitelists_fields(self, service, mock_storage) -> None:
        mock_storage.get_chat.return_value = {"id": "c1"}
        service.update_chat("c1", {"title": "New", "hacked_field": "bad"})
        call_args = mock_storage.update_chat.call_args[0][1]
        assert "title" in call_args
        assert "hacked_field" not in call_args

    def test_allows_status_field(self, service, mock_storage) -> None:
        mock_storage.get_chat.return_value = {"id": "c1"}
        service.update_chat("c1", {"status": "completed"})
        call_args = mock_storage.update_chat.call_args[0][1]
        assert call_args["status"] == "completed"

    def test_returns_false_when_not_found(self, service, mock_storage) -> None:
        mock_storage.get_chat.return_value = None
        result = service.update_chat("missing", {"title": "X"})
        assert result is False
        mock_storage.update_chat.assert_not_called()


# ============================================================================
# update_chat_status
# ============================================================================


class TestUpdateChatStatus:
    """Tests for ChatService.update_chat_status."""

    def test_delegates_to_update_chat(self, service, mock_storage) -> None:
        mock_storage.get_chat.return_value = {"id": "c1"}
        result = service.update_chat_status("c1", "completed")
        assert result is True
        call_args = mock_storage.update_chat.call_args[0][1]
        assert call_args["status"] == "completed"

    def test_returns_false_when_not_found(self, service, mock_storage) -> None:
        mock_storage.get_chat.return_value = None
        result = service.update_chat_status("missing", "active")
        assert result is False


# ============================================================================
# delete_chat
# ============================================================================


class TestDeleteChat:
    """Tests for ChatService.delete_chat."""

    def test_delegates_to_storage(self, service, mock_storage) -> None:
        mock_storage.delete_chat.return_value = True
        result = service.delete_chat("c1")
        assert result is True
        mock_storage.delete_chat.assert_called_once_with("c1")

    def test_returns_false_on_failure(self, service, mock_storage) -> None:
        mock_storage.delete_chat.return_value = False
        result = service.delete_chat("missing")
        assert result is False


# ============================================================================
# update_scope
# ============================================================================


class TestUpdateScope:
    """Tests for ChatService.update_scope."""

    def test_sets_source_ids(self, service, mock_storage) -> None:
        mock_storage.get_chat.return_value = {"id": "c1"}
        mock_storage.update_chat.return_value = {"id": "c1", "source_ids": ["s1"]}
        result = service.update_scope("c1", ["s1"])
        mock_storage.update_chat.assert_called_once_with("c1", {"source_ids": ["s1"]})
        assert result is not None

    def test_clears_scope_with_none(self, service, mock_storage) -> None:
        mock_storage.get_chat.return_value = {"id": "c1"}
        mock_storage.update_chat.return_value = {"id": "c1", "source_ids": None}
        service.update_scope("c1", None)
        mock_storage.update_chat.assert_called_once_with("c1", {"source_ids": None})

    def test_returns_none_when_not_found(self, service, mock_storage) -> None:
        mock_storage.get_chat.return_value = None
        result = service.update_scope("missing", ["s1"])
        assert result is None
        mock_storage.update_chat.assert_not_called()


# ============================================================================
# add_message
# ============================================================================


class TestAddMessage:
    """Tests for ChatService.add_message."""

    def test_creates_message(self, service, mock_storage) -> None:
        mock_storage.create_message.return_value = {"id": "m1", "role": "user", "content": "hello"}
        result = service.add_message("c1", role="user", content="hello")
        assert result["role"] == "user"
        call_args = mock_storage.create_message.call_args[0][0]
        assert call_args["chat_id"] == "c1"
        assert call_args["role"] == "user"
        assert call_args["content"] == "hello"
        assert "id" in call_args
        assert "timestamp" in call_args

    def test_passes_extra_metadata(self, service, mock_storage) -> None:
        mock_storage.create_message.return_value = {"id": "m1"}
        service.add_message("c1", role="user", content="hi", extra_metadata={"key": "val"})
        call_args = mock_storage.create_message.call_args[0][0]
        assert call_args["extra_metadata"] == {"key": "val"}


# ============================================================================
# build_message / persist_messages (deferred-write primitives)
# ============================================================================


class TestBuildAndPersistMessages:
    """Tests for the buffer-and-flush primitives behind idempotent chat completion.

    ``build_message`` constructs a message dict (id + timestamp captured now)
    without touching storage; ``persist_messages`` writes a list of pre-built
    dicts. Together they let a long-running handler accumulate every message
    it produces and persist them only once it succeeds — so a transient-error
    retry that re-runs from the top never duplicates already-saved rows.
    """

    def test_build_message_does_not_persist(self, service, mock_storage) -> None:
        msg = service.build_message("c1", role="tool", content="result", extra_metadata={"n": "x"})

        # The dict is fully formed...
        assert msg["chat_id"] == "c1"
        assert msg["role"] == "tool"
        assert msg["content"] == "result"
        assert msg["extra_metadata"] == {"n": "x"}
        assert "id" in msg
        assert "timestamp" in msg
        # ...but nothing was written.
        mock_storage.create_message.assert_not_called()

    def test_persist_messages_writes_each_in_order(self, service, mock_storage) -> None:
        m1 = service.build_message("c1", role="tool", content="t1")
        m2 = service.build_message("c1", role="assistant", content="answer")

        service.persist_messages([m1, m2])

        assert mock_storage.create_message.call_count == 2
        written = [call.args[0] for call in mock_storage.create_message.call_args_list]
        assert written[0]["content"] == "t1"
        assert written[1]["content"] == "answer"

    def test_persist_messages_empty_is_noop(self, service, mock_storage) -> None:
        service.persist_messages([])
        mock_storage.create_message.assert_not_called()


# ============================================================================
# get_chat_messages
# ============================================================================


class TestGetChatMessages:
    """Tests for ChatService.get_chat_messages."""

    def test_returns_all_messages(self, service, mock_storage) -> None:
        mock_storage.get_messages.return_value = [{"id": "m1"}, {"id": "m2"}]
        result = service.get_chat_messages("c1")
        assert len(result) == 2
        mock_storage.get_messages.assert_called_once_with("c1")

    def test_applies_offset(self, service, mock_storage) -> None:
        mock_storage.get_messages.return_value = [{"id": "m1"}, {"id": "m2"}, {"id": "m3"}]
        result = service.get_chat_messages("c1", offset=1)
        assert result == [{"id": "m2"}, {"id": "m3"}]

    def test_applies_limit(self, service, mock_storage) -> None:
        mock_storage.get_messages.return_value = [{"id": "m1"}, {"id": "m2"}, {"id": "m3"}]
        result = service.get_chat_messages("c1", limit=2)
        assert result == [{"id": "m1"}, {"id": "m2"}]


# ============================================================================
# count_chats
# ============================================================================


class TestCountChats:
    """Tests for ChatService.count_chats."""

    def test_delegates_to_storage(self, service, mock_storage) -> None:
        mock_storage.count_chats.return_value = 5
        result = service.count_chats()
        assert result == 5
        mock_storage.count_chats.assert_called_once_with(
            database_name="test_db", status=None, scoped=None, search=None
        )

    def test_passes_status_filter(self, service, mock_storage) -> None:
        mock_storage.count_chats.return_value = 2
        result = service.count_chats(status="active")
        assert result == 2
        mock_storage.count_chats.assert_called_once_with(
            database_name="test_db", status="active", scoped=None, search=None
        )

    def test_passes_scoped_filter(self, service, mock_storage) -> None:
        mock_storage.count_chats.return_value = 1
        result = service.count_chats(scoped=True)
        assert result == 1
        mock_storage.count_chats.assert_called_once_with(
            database_name="test_db", status=None, scoped=True, search=None
        )


# ============================================================================
# delete_all_chats
# ============================================================================


class TestDeleteAllChats:
    """Tests for ChatService.delete_all_chats."""

    def test_uses_bulk_delete(self, service, mock_storage) -> None:
        """The list+loop approach only ever deleted the first 100 chats
        (adapter default list limit); bulk delete removes them all
        (2026-06-10 audit P2).
        """
        mock_storage.delete_all_chats.return_value = 250
        assert service.delete_all_chats() == 250
        mock_storage.delete_all_chats.assert_called_once_with(database_name="test_db")
        mock_storage.list_chats.assert_not_called()
