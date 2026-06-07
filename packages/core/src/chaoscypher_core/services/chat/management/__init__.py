# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Chat Management - CRUD Operations.

Provides CRUD operations for chats and messages.

Components:
- ChatService: Manage chats and messages

Example:
    from chaoscypher_core.services.chat.management import ChatService

    service = ChatService(storage, database_name)
    chat = service.create_chat(chat_id="chat_123", title="Research")

"""

from chaoscypher_core.services.chat.management.service import ChatService


__all__ = [
    "ChatService",
]
