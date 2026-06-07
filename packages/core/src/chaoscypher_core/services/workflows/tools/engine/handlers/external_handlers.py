# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""External Tool Handlers.

Handles entity extraction and research operations via callbacks.

Extracted from tool_executor.py for SRP compliance.
"""

from collections.abc import Callable
from typing import Any, cast

import structlog

from chaoscypher_core.services.workflows.tools.engine.handlers.decorators import tool_handler


logger = structlog.get_logger(__name__)


class ExternalToolHandlers:
    """Handles external tool operations (research)."""

    def __init__(
        self,
        research_agent_callback: Callable | None = None,
    ):
        """Initialize the instance.

        Args:
            research_agent_callback: Callback function for research operations.

        """
        self.research_callback = research_agent_callback

    @tool_handler("extract_entities_failed")
    async def extract_entities_from_text(self, text: str) -> dict:
        """Extract entities from text (optional - requires research agent)."""
        if not self.research_callback:
            return {
                "success": False,
                "error": "Entity extraction not available (no research agent)",
            }

        result = await self.research_callback("extract_entities", text=text)
        return cast("dict[Any, Any]", result)

    @tool_handler("research_topic_failed")
    async def research_topic(self, topic: str, depth: str = "full") -> dict:
        """Research a topic (optional - requires research agent)."""
        if not self.research_callback:
            return {"success": False, "error": "Topic research not available (no research agent)"}

        result = await self.research_callback("research_topic", topic=topic, depth=depth)
        return cast("dict[Any, Any]", result)

    @tool_handler("build_topic_hierarchy_failed")
    async def build_topic_hierarchy(self, root_topic: str) -> dict:
        """Build topic hierarchy (optional - requires research agent)."""
        if not self.research_callback:
            return {"success": False, "error": "Topic hierarchy not available (no research agent)"}

        result = await self.research_callback("build_hierarchy", root_topic=root_topic)
        return cast("dict[Any, Any]", result)

    @tool_handler("identify_knowledge_gaps_failed")
    async def identify_knowledge_gaps(self) -> dict:
        """Identify knowledge gaps (optional - requires research agent)."""
        if not self.research_callback:
            return {
                "success": False,
                "error": "Gap identification not available (no research agent)",
            }

        result = await self.research_callback("identify_gaps")
        return cast("dict[Any, Any]", result)
