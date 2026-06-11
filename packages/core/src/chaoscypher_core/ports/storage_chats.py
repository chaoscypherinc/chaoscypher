# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""ChatStorageProtocol — storage contract for chat history and messages.

Split from the legacy ``ports/storage.py`` god file on 2026-04-23.
Implemented by ``chaoscypher_core.adapters.sqlite.mixins.chats.ChatsMixin``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable


if TYPE_CHECKING:
    from chaoscypher_core.ports.types import ChatDict, MessageDict


@runtime_checkable
class ChatStorageProtocol(Protocol):
    """Storage protocol for chat operations.

    Handles CRUD for:
    - Chats (chat history)
    - Chat messages
    """

    # Chats
    def get_chat(self, chat_id: str, database_name: str) -> ChatDict | None:
        """Get chat by ID and database."""
        ...

    def create_chat(self, chat: dict[str, Any]) -> ChatDict:
        """Create chat."""
        ...

    def update_chat(self, chat_id: str, updates: dict[str, Any]) -> ChatDict:
        """Update chat."""
        ...

    def delete_chat(self, chat_id: str) -> bool:
        """Delete chat."""
        ...

    def list_chats(
        self,
        database_name: str,
        user_id: int | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
        scoped: bool | None = None,
        search: str | None = None,
    ) -> list[ChatDict]:
        """List chats for database with optional filters (newest first).

        ``search`` filters by case-insensitive title substring.
        """
        ...

    def count_chats(
        self,
        database_name: str,
        status: str | None = None,
        scoped: bool | None = None,
        search: str | None = None,
    ) -> int:
        """Count chats for database with optional filters."""
        ...

    # Messages
    def create_message(self, message: dict[str, Any]) -> MessageDict:
        """Create chat message."""
        ...

    def get_messages(self, chat_id: str, limit: int = 500) -> list[MessageDict]:
        """Get messages for a chat, ordered by timestamp.

        Args:
            chat_id: Chat ID to retrieve messages for.
            limit: Maximum number of messages to return (most recent).
        """
        ...

    def delete_messages_after(
        self,
        chat_id: str,
        message_id: str,
        *,
        inclusive: bool = False,
    ) -> int:
        """Delete the tail of a chat's history starting at/after a message.

        Backs regenerate (exclusive: keep the last user message, drop the
        answer) and edit-and-resend (inclusive: the edited message itself is
        replaced). Ordering matches ``get_messages`` (timestamp, id tiebreak).

        Args:
            chat_id: Chat whose tail to remove.
            message_id: Anchor message; must belong to ``chat_id``.
            inclusive: Also delete the anchor itself.

        Returns:
            Number of rows deleted (0 when the anchor is unknown or belongs
            to a different chat).
        """
        ...

    # ------------------------------------------------------------------
    # Bulk / reset operations (PR2a Task 7).
    # Consumed by the reset services moving into core in PR2b.
    # ------------------------------------------------------------------

    def delete_messages_by_chat_ids(self, *, chat_ids: list[str]) -> int:
        """Delete ChatMessage rows whose chat_id is in the given list.

        Args:
            chat_ids: Chat IDs whose messages should be deleted.

        Returns:
            Number of rows deleted.
        """
        ...

    def delete_all_chats(self, *, database_name: str) -> int:
        """Delete every Chat row in one database.

        Args:
            database_name: Database to scope the delete to.

        Returns:
            Number of rows deleted.
        """
        ...
