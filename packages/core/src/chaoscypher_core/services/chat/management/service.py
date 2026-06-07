# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Chat Service - Hexagonal Architecture chat management.

Uses storage protocol for backend-independent data access.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog


if TYPE_CHECKING:
    from chaoscypher_core.ports.storage_chats import ChatStorageProtocol
    from chaoscypher_core.ports.types import ChatDict, MessageDict

logger = structlog.get_logger(__name__)


class ChatService:
    """Service for managing chats and messages.

    Example:
        >>> from chaoscypher_core.services.chat.api import get_chat_service
        >>> from chaoscypher_core.adapters.sqlite import get_db_session
        >>> from chaoscypher_core.settings import EngineSettings
        >>>
        >>> # Get service instance via factory
        >>> settings = EngineSettings()
        >>> with get_db_session("my_database") as session:
        ...     service = get_chat_service(session, settings)
        ...
        ...     # Create a new chat
        ...     chat = service.create_chat(
        ...         chat_id="chat_abc123",
        ...         title="Research Discussion"
        ...     )
        ...     print(chat["id"])
        ...     "chat_abc123"
        ...
        ...     # Add messages to chat
        ...     msg = service.add_message(
        ...         chat_id="chat_abc123",
        ...         role="user",
        ...         content="What is knowledge graph?"
        ...     )
        ...     print(msg["role"])
        ...     "user"
        ...
        ...     # Get chat with all messages
        ...     chat = service.get_chat("chat_abc123")
        ...     print(len(chat["messages"]))
        ...     1
        ...
        ...     # List all chats
        ...     chats = service.list_chats(limit=10, status="active")
        ...     print(len(chats))
        ...     5

    """

    def __init__(self, storage: ChatStorageProtocol, database_name: str):
        """Initialize chat service.

        Args:
            storage: ChatStorageProtocol instance
            database_name: Database name for filtering

        """
        self.storage = storage
        self.database_name = database_name

    # ========================================================================
    # Chat CRUD
    # ========================================================================

    def list_chats(
        self,
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
        scoped: bool | None = None,
    ) -> list[ChatDict]:
        """List chats with pagination.

        Args:
            limit: Maximum number of chats to return
            offset: Number of chats to skip
            status: Filter by status
            scoped: Filter by scope status (True=scoped, False=unscoped, None=all)

        Returns:
            List of chat dictionaries

        """
        chats = self.storage.list_chats(
            database_name=self.database_name,
            user_id=None,
            status=status,
            limit=limit,
            scoped=scoped,
        )
        # Apply offset in service layer
        if offset > 0:
            chats = chats[offset:]
        return chats

    def get_chat(self, chat_id: str) -> dict[str, Any] | None:
        """Get chat by ID with all messages.

        Args:
            chat_id: Chat ID

        Returns:
            Chat dictionary with messages or None

        """
        # Get chat via storage
        chat = self.storage.get_chat(chat_id, self.database_name)
        if not chat:
            return None

        # Get messages via storage
        messages = self.storage.get_messages(chat_id)

        # Add messages to chat dict (extends beyond ChatDict schema)
        result: dict[str, Any] = dict(chat)
        result["messages"] = messages

        return result

    def create_chat(
        self,
        chat_id: str,
        title: str = "New Chat",
        source_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create a new chat.

        Args:
            chat_id: Unique chat ID
            title: Chat title
            source_ids: Optional list of source IDs to scope this chat to

        Returns:
            Created chat dictionary with empty messages list

        """
        chat_dict: dict[str, Any] = {
            "id": chat_id,
            "database_name": self.database_name,
            "title": title,
            "status": "active",
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
        }

        if source_ids:
            chat_dict["source_ids"] = source_ids

        chat = self.storage.create_chat(chat_dict)

        logger.info("chat_created", chat_id=chat_id, title=title, scoped=bool(source_ids))

        # Add empty messages list (extends beyond ChatDict schema)
        result: dict[str, Any] = dict(chat)
        result["messages"] = []

        return result

    def update_chat(self, chat_id: str, updates: dict[str, Any]) -> bool:
        """Update chat.

        Args:
            chat_id: Chat ID
            updates: Dictionary of fields to update

        Returns:
            True if updated, False if not found

        """
        # Get chat via storage
        chat = self.storage.get_chat(chat_id, self.database_name)
        if not chat:
            return False

        # Prepare updates with timestamp
        update_dict = {k: v for k, v in updates.items() if k in ["title", "status"]}
        update_dict["updated_at"] = datetime.now(UTC)

        self.storage.update_chat(chat_id, update_dict)

        logger.info("chat_updated", chat_id=chat_id, updated_fields=list(updates.keys()))
        return True

    def update_chat_status(self, chat_id: str, status: str) -> bool:
        """Update chat status.

        Args:
            chat_id: Chat ID
            status: New status ('active', 'processing', 'completed', 'error')

        Returns:
            True if updated, False if not found

        """
        return self.update_chat(chat_id, {"status": status})

    def delete_chat(self, chat_id: str) -> bool:
        """Delete chat and all its messages.

        Args:
            chat_id: Chat ID

        Returns:
            True if deleted, False if not found

        """
        # Storage handles deletion of chat and messages
        success = self.storage.delete_chat(chat_id)

        if success:
            logger.info("chat_deleted", chat_id=chat_id)

        return success

    def delete_all_chats(self, user_id: int | None = None) -> int:
        """Delete all chats for the current database.

        Args:
            user_id: Optional user ID filter (for auth-enabled mode)

        Returns:
            Number of chats deleted

        """
        chats = self.storage.list_chats(
            database_name=self.database_name,
            user_id=user_id,
        )

        deleted = 0
        for chat in chats:
            if self.storage.delete_chat(chat["id"]):
                deleted += 1

        logger.info("all_chats_deleted", count=deleted, database=self.database_name)
        return deleted

    def update_scope(self, chat_id: str, source_ids: list[str] | None) -> ChatDict | None:
        """Update the source scope of a chat.

        Args:
            chat_id: Chat ID
            source_ids: New source IDs (None to clear scope)

        Returns:
            Updated chat dict or None if not found

        """
        chat = self.storage.get_chat(chat_id, self.database_name)
        if not chat:
            return None

        updates: dict[str, Any] = {"source_ids": source_ids}
        updated = self.storage.update_chat(chat_id, updates)

        logger.info(
            "chat_scope_updated",
            chat_id=chat_id,
            source_count=len(source_ids) if source_ids else 0,
        )

        return updated

    # ========================================================================
    # Message CRUD
    # ========================================================================

    def add_message(
        self,
        chat_id: str,
        role: str,
        content: str,
        extra_metadata: dict[str, Any] | None = None,
    ) -> MessageDict | None:
        """Add a message to a chat.

        Args:
            chat_id: Chat ID
            role: Message role ('user' or 'assistant')
            content: Message content
            extra_metadata: Optional message metadata

        Returns:
            Created message dictionary or None if chat not found

        """
        message_dict = self.build_message(chat_id, role, content, extra_metadata)

        message = self.storage.create_message(message_dict)

        if not message:
            return None

        logger.debug(
            "chat_message_added", chat_id=chat_id, message_id=message_dict["id"], role=role
        )
        return message

    def build_message(
        self,
        chat_id: str,
        role: str,
        content: str,
        extra_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Construct a message dict (id + timestamp fixed now) without persisting.

        Lets a caller accumulate every message produced during a long
        operation and write them all at the end via ``persist_messages`` —
        the basis for idempotent chat completion. Capturing the id and
        timestamp here (rather than at flush time) keeps message ordering
        faithful to production order and avoids the timestamp-PK collision a
        tight end-of-run write loop would otherwise risk.

        Args:
            chat_id: Owning chat ID.
            role: Message role ('user', 'assistant', 'tool').
            content: Message content.
            extra_metadata: Optional message metadata.

        Returns:
            A message dict ready to hand to ``storage.create_message`` /
            ``persist_messages``.
        """
        return {
            "id": f"{chat_id}_msg_{datetime.now(UTC).timestamp()}",
            "chat_id": chat_id,
            "role": role,
            "content": content,
            "extra_metadata": extra_metadata,
            "timestamp": datetime.now(UTC),
        }

    def persist_messages(self, messages: list[dict[str, Any]]) -> None:
        """Write a list of pre-built message dicts to storage, in order.

        The companion to ``build_message``: a handler buffers messages as it
        produces them and calls this once it has succeeded, so a transient
        failure earlier in the run leaves nothing half-written and a retry
        that re-runs from the top cannot duplicate already-saved rows.

        Args:
            messages: Message dicts produced by ``build_message``.
        """
        for message in messages:
            self.storage.create_message(message)

    def get_chat_messages(
        self,
        chat_id: str,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[MessageDict]:
        """Get messages for a chat.

        Args:
            chat_id: Chat ID
            limit: Maximum number of messages to return
            offset: Number of messages to skip

        Returns:
            List of message dictionaries

        """
        # Get messages via storage
        messages = self.storage.get_messages(chat_id)

        # Apply pagination in service layer
        if offset > 0:
            messages = messages[offset:]
        if limit is not None:
            messages = messages[:limit]

        return messages

    # ========================================================================
    # Utility Methods
    # ========================================================================

    def count_chats(self, status: str | None = None) -> int:
        """Count total chats.

        Args:
            status: Optional status filter

        Returns:
            Number of chats

        """
        return self.storage.count_chats(
            database_name=self.database_name,
            status=status,
        )
