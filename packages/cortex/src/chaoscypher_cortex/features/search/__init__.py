# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Search Feature.

Full-text, vector, and AI research search capabilities.

This feature provides comprehensive search functionality including RAG-based
document search and AI-powered research agents.
All search repositories moved to engine for CLI reusability. Backend feature
now serves as a barrel export for engine search services.

Components:
- ResearchAgent: Multi-step AI research with source synthesis

Architecture:
Minimal backend wrapper - all business logic in chaoscypher. For direct search access,
use chaoscypher_core.adapters.sqlite.repos.SearchRepository. This feature exports
high-level services for AI research capabilities that complement local RAG.

Example:
    from chaoscypher_cortex.features.search import ResearchAgent
    from chaoscypher_core.adapters.sqlite.repos import SearchRepository

    # AI-powered research with local sources
    agent = ResearchAgent(search_repo, llm_provider)
    result = await agent.research("quantum computing applications")

"""

from chaoscypher_core.services.chat.engine.research import ResearchAgent


__all__ = [
    "ResearchAgent",
]
