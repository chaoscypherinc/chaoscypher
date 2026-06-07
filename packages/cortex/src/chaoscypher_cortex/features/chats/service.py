# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Chat feature orchestration service.

Wraps the engine ``ChatService`` and the new :class:`ChatScopeRepository`
so the Cortex route handlers can stay thin. The engine service owns
chat / message persistence; this service owns the orchestration around
scope resolution, id generation, and the "scope changed" system
message that the handlers previously inlined.

Three operations live here:

- :meth:`ChatFeatureService.create_chat_with_scope` — generate a chat
  id, resolve the scope, call the engine create_chat.
- :meth:`ChatFeatureService.update_scope_with_message` — resolve the
  new scope, update the chat, inject a human-readable system message,
  return the refreshed chat.
- :meth:`ChatFeatureService.clear_scope_with_message` — clear the
  scope, inject the "scope removed" system message, return the chat.

The streaming endpoint and the other chat CRUD handlers continue to
talk to the engine ``ChatService`` directly; this service only covers
the three handlers that previously had inline scope / session logic.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.utils.id import generate_id


if TYPE_CHECKING:
    from chaoscypher_core.services.chat import ChatService
    from chaoscypher_cortex.features.chats.models import ChatCreate, ChatScopeUpdate
    from chaoscypher_cortex.features.chats.repository import ChatScopeRepository


logger = structlog.get_logger(__name__)


_SCOPE_CLEARED_MESSAGE = "Source scope removed. All sources are now accessible."


class ChatFeatureService:
    """Orchestrates the handful of chat endpoints that need scope logic.

    Attributes:
        engine_chat_service: The framework-agnostic core ``ChatService``
            responsible for chat / message persistence.
        scope_repository: Cortex repository that resolves source-id +
            tag-id inputs into a final scope and fetches display titles.

    """

    def __init__(
        self,
        engine_chat_service: ChatService,
        scope_repository: ChatScopeRepository,
    ) -> None:
        """Initialize with the engine chat service and the scope repo."""
        self.engine_chat_service = engine_chat_service
        self.scope_repository = scope_repository

    def create_chat_with_scope(self, body: ChatCreate) -> dict[str, Any]:
        """Create a chat with its initial source scope.

        Generates a new chat id, resolves the scope (explicit
        ``source_ids`` + tag-derived ``tag_ids`` deduped), and delegates
        the row insert to the engine ``ChatService``.

        Args:
            body: The ``ChatCreate`` DTO from the request.

        Returns:
            The created chat dict (with an empty messages list).

        """
        chat_id = generate_id()
        source_ids = self.scope_repository.resolve_scope(body.source_ids, body.tag_ids)
        return self.engine_chat_service.create_chat(
            chat_id=chat_id,
            title=body.title,
            source_ids=source_ids,
        )

    def update_scope_with_message(
        self,
        chat_id: str,
        body: ChatScopeUpdate,
    ) -> dict[str, Any] | None:
        """Update a chat's scope and record it as a system message.

        Returns ``None`` if the chat does not exist at any stage (the
        engine ``update_scope`` returns ``None`` in that case). The HTTP
        handler translates ``None`` to a 404 via ``raise_if_not_found``.

        Args:
            chat_id: Target chat id.
            body: The ``ChatScopeUpdate`` DTO from the request.

        Returns:
            The refreshed chat dict after the update, or ``None`` if the
            chat does not exist.

        """
        final_source_ids = self.scope_repository.resolve_scope(body.source_ids, body.tag_ids)

        updated = self.engine_chat_service.update_scope(chat_id, final_source_ids)
        if updated is None:
            return None

        if final_source_ids:
            titles = self.scope_repository.get_source_titles(final_source_ids)
            scope_msg = f"Source scope updated. Now scoped to: {', '.join(titles)}"
        else:
            scope_msg = _SCOPE_CLEARED_MESSAGE

        self.engine_chat_service.add_message(chat_id, role="system", content=scope_msg)

        return self.engine_chat_service.get_chat(chat_id)

    def clear_scope_with_message(self, chat_id: str) -> dict[str, Any] | None:
        """Clear a chat's scope and record it as a system message.

        Returns ``None`` if the chat does not exist (the HTTP handler
        maps that to a 404).

        Args:
            chat_id: Target chat id.

        Returns:
            The refreshed chat dict after the clear, or ``None`` if the
            chat does not exist.

        """
        updated = self.engine_chat_service.update_scope(chat_id, None)
        if updated is None:
            return None

        self.engine_chat_service.add_message(chat_id, role="system", content=_SCOPE_CLEARED_MESSAGE)

        return self.engine_chat_service.get_chat(chat_id)
