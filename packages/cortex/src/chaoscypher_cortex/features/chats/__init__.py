# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Chats Feature.

Chat management with AI response generation.

This feature provides chat history tracking and AI-powered chat responses
with RAG integration. Manages chat threads, message persistence, and
context-aware responses using knowledge graph and document search. ChatExecutor
handles AI response generation while ChatService manages persistence.
Supports multi-turn chats with memory and source citations.

Components:
- ChatService: Chat and message storage (uses engine ChatService directly)
- ChatExecutor: AI response generation with RAG and tool calling

Architecture:
Simplified VSA - uses engine ChatService directly without wrapper layer.
Factory function in api.py provides dependency injection with storage adapter.
ChatExecutor (engine) performs RAG retrieval, graph queries, and LLM response
generation.

Example:
    from chaoscypher_core.services.chat import ChatService

    # Create service with storage adapter
    service = ChatService(storage=adapter, database_name="default")
    chat = service.create_chat(chat_id="chat_123", title="Research Session")

"""

from chaoscypher_core.services.chat import ChatService
from chaoscypher_core.services.chat.engine.executor import ChatExecutor
from chaoscypher_cortex.features.chats.api import router
from chaoscypher_cortex.features.chats.repository import ChatScopeRepository
from chaoscypher_cortex.features.chats.service import ChatFeatureService


__all__ = [
    "ChatExecutor",
    "ChatFeatureService",
    "ChatScopeRepository",
    "ChatService",
    "router",
]
