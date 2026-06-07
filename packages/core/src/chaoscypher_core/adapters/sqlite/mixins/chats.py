# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Chat Storage Protocol Mixin for SqliteAdapter."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import load_only
from sqlmodel import col, delete, func, select

from chaoscypher_core.adapters.sqlite.mixin_base import SqliteMixinBase
from chaoscypher_core.adapters.sqlite.models import Chat, ChatMessage
from chaoscypher_core.exceptions import NotFoundError
from chaoscypher_core.ports.storage_chats import ChatStorageProtocol


class ChatsMixin(SqliteMixinBase, ChatStorageProtocol):
    """Mixin implementing ChatStorageProtocol for SQLite storage.

    Implements operations for:
    - Chats
    - Messages
    """

    def get_chat(self, chat_id: str, database_name: str) -> dict[str, Any] | None:
        """Get chat by ID and database."""
        self._ensure_connected()
        chat = self.session.get(Chat, chat_id)
        if chat and chat.database_name == database_name:
            return self._entity_to_dict(chat)
        return None

    def create_chat(self, chat_data: dict[str, Any]) -> dict[str, Any]:
        """Create chat."""
        self._ensure_connected()
        chat = Chat(**chat_data)
        self.session.add(chat)
        self._maybe_commit()
        self.session.refresh(chat)
        return self._entity_to_dict(chat)

    def update_chat(self, chat_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        """Update chat."""
        self._ensure_connected()
        chat = self.session.get(Chat, chat_id)
        if not chat:
            msg = "Chat"
            raise NotFoundError(msg, chat_id)

        for key, value in updates.items():
            setattr(chat, key, value)

        chat.updated_at = datetime.now(UTC)
        self.session.add(chat)
        self._maybe_commit()
        self.session.refresh(chat)
        return self._entity_to_dict(chat)

    def delete_chat(self, chat_id: str) -> bool:
        """Delete chat."""
        self._ensure_connected()
        chat = self.session.get(Chat, chat_id)
        if not chat:
            return False

        self.session.delete(chat)
        self._maybe_commit()
        return True

    def list_chats(
        self,
        database_name: str,
        user_id: int | None = None,
        status: str | None = None,
        limit: int = 100,
        scoped: bool | None = None,
    ) -> list[dict[str, Any]]:
        """List chats for database with optional filters."""
        self._ensure_connected()
        stmt = (
            select(Chat)
            .options(
                load_only(
                    Chat.id,
                    Chat.database_name,
                    Chat.user_id,
                    Chat.title,
                    Chat.status,
                    Chat.created_at,
                    Chat.updated_at,
                    Chat.message_count,
                    Chat.source_ids,
                )
            )
            .where(Chat.database_name == database_name)
        )

        if user_id is not None:
            stmt = stmt.where(Chat.user_id == user_id)
        if status is not None:
            stmt = stmt.where(Chat.status == status)
        if scoped is True:
            stmt = stmt.where(Chat.source_ids.is_not(None))
        elif scoped is False:
            stmt = stmt.where(Chat.source_ids.is_(None))

        stmt = stmt.order_by(Chat.created_at.desc()).limit(limit)

        results = self.session.exec(stmt)
        return self._entities_to_dicts(results.all())

    def count_chats(
        self,
        database_name: str,
        status: str | None = None,
    ) -> int:
        """Count chats for database with optional status filter.

        Args:
            database_name: Database name to filter by
            status: Optional status filter

        Returns:
            Number of matching chats

        """
        self._ensure_connected()
        stmt = select(func.count()).select_from(Chat).where(Chat.database_name == database_name)
        if status is not None:
            stmt = stmt.where(Chat.status == status)
        return self.session.exec(stmt).one()

    def create_message(self, message_data: dict[str, Any]) -> dict[str, Any]:
        """Create chat message."""
        self._ensure_connected()
        message = ChatMessage(**message_data)
        self.session.add(message)
        self._maybe_commit()
        self.session.refresh(message)
        return self._entity_to_dict(message)

    def get_messages(self, chat_id: str, limit: int = 500) -> list[dict[str, Any]]:
        """Get messages for a chat, ordered by timestamp.

        When a chat has more messages than *limit*, only the most recent
        *limit* messages are returned (in chronological order).

        Args:
            chat_id: Chat ID to retrieve messages for.
            limit: Maximum number of messages to return (most recent).
        """
        self._ensure_connected()
        # Sub-select the most recent `limit` messages (descending), then
        # re-order chronologically so callers get oldest-first ordering.
        subq = (
            select(ChatMessage.id)
            .where(ChatMessage.chat_id == chat_id)
            .order_by(ChatMessage.timestamp.desc())
            .limit(limit)
            .subquery()
        )
        stmt = (
            select(ChatMessage)
            .options(
                load_only(
                    ChatMessage.id,
                    ChatMessage.chat_id,
                    ChatMessage.role,
                    ChatMessage.content,
                    ChatMessage.timestamp,
                    ChatMessage.extra_metadata,
                )
            )
            .where(ChatMessage.id.in_(select(subq.c.id)))
            .order_by(ChatMessage.timestamp)
        )
        results = self.session.exec(stmt)
        return self._entities_to_dicts(results.all())

    # ------------------------------------------------------------------
    # Bulk / reset operations (PR2a Task 7).
    # Consumed by the reset services moving into core in PR2b.
    # ------------------------------------------------------------------

    def delete_messages_by_chat_ids(self, *, chat_ids: list[str]) -> int:
        """Delete ChatMessage rows whose chat_id is in the given list."""
        self._ensure_connected()
        if not chat_ids:
            return 0
        stmt = delete(ChatMessage).where(col(ChatMessage.chat_id).in_(chat_ids))
        result = self.session.exec(stmt)
        self._maybe_commit()
        return int(result.rowcount or 0)

    def delete_all_chats(self, *, database_name: str) -> int:
        """Delete every Chat row in one database."""
        self._ensure_connected()
        stmt = delete(Chat).where(Chat.database_name == database_name)
        result = self.session.exec(stmt)
        self._maybe_commit()
        return int(result.rowcount or 0)
