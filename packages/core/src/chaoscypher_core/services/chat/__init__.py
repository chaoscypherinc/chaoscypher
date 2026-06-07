# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Chat System - Chat Management and AI Chat Processing.

Provides chat management and AI-powered chat processing.

Architecture:
- management/: Chat CRUD (ChatService)
- engine/: Chat processing and research (ChatExecutor, ResearchAgent)
- engine/constants: Shared system prompt and tool config for Cortex and CLI

Example:
    from chaoscypher_core.services.chat import ChatService, ChatExecutor, ResearchAgent
    from chaoscypher_core.services.chat import SYSTEM_PROMPT, ESSENTIAL_TOOL_NAMES

    # Manage chats
    service = ChatService(storage, database_name)
    chat = service.create_chat(chat_id="chat_123", title="Research")

    # Process chat messages
    executor = ChatExecutor(storage, llm_service, settings)
    result = await executor.process_user_message(chat_id, message)

    # AI research
    agent = ResearchAgent(llm_provider, discovery_svc, graph_repo)
    result = await agent.research_topic(topic="Knowledge Graphs")

"""

# Constants: Shared chat configuration
# Engine: Chat processing and research
from chaoscypher_core.services.chat.engine import (
    ESSENTIAL_TOOL_NAMES,
    MAX_TOOL_ITERATIONS,
    MAX_TOOLS,
    MAX_TOTAL_TOOL_CALLS,
    SYSTEM_PROMPT,
    ChatExecutor,
    ResearchAgent,
)

# Management: Chat CRUD
from chaoscypher_core.services.chat.management import ChatService


__all__ = [
    "ESSENTIAL_TOOL_NAMES",
    "MAX_TOOLS",
    "MAX_TOOL_ITERATIONS",
    "MAX_TOTAL_TOOL_CALLS",
    "SYSTEM_PROMPT",
    "ChatExecutor",
    "ChatService",
    "ResearchAgent",
]
