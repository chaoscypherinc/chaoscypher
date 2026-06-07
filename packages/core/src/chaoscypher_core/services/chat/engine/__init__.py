# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Chat Execution Engine.

Chat processing, research agent execution, and shared chat constants.

Components:
- ChatExecutor: Executes chat message processing and AI response generation
- ResearchAgent: AI research agent for topic exploration
- Constants: Shared system prompt and tool configuration for Cortex and CLI

Example:
    from chaoscypher_core.services.chat.engine import ChatExecutor, ResearchAgent
    from chaoscypher_core.services.chat.engine import SYSTEM_PROMPT, ESSENTIAL_TOOL_NAMES

    # Process chat
    executor = ChatExecutor(storage, llm_service, settings)
    result = await executor.process_user_message(conversation_id, message)

    # Research
    agent = ResearchAgent(llm_provider, discovery_svc, graph_repo)
    result = await agent.research_topic(topic="Knowledge Graphs")

"""

# Constants: Shared chat configuration
from chaoscypher_core.services.chat.engine.constants import (
    ESSENTIAL_TOOL_NAMES,
    MAX_TOOL_ITERATIONS,
    MAX_TOOLS,
    MAX_TOTAL_TOOL_CALLS,
    SYSTEM_PROMPT,
)

# Engine: Chat processing and research
from chaoscypher_core.services.chat.engine.executor import ChatExecutor
from chaoscypher_core.services.chat.engine.research import ResearchAgent


__all__ = [
    "ESSENTIAL_TOOL_NAMES",
    "MAX_TOOLS",
    "MAX_TOOL_ITERATIONS",
    "MAX_TOTAL_TOOL_CALLS",
    "SYSTEM_PROMPT",
    "ChatExecutor",
    "ResearchAgent",
]
