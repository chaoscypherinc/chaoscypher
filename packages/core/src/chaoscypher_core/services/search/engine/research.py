# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""RAG Research Operations - Topic Research with Knowledge Graph Synthesis.

Handles comprehensive topic research:
1. Knowledge graph search for existing knowledge
2. LLM synthesis and analysis
3. Source citation and formatting

Extracted from research_agent.py for Single Responsibility Principle (SRP).
"""

import json
from typing import Any

import structlog

from chaoscypher_core.ports.llm import TaskType
from chaoscypher_core.utils.llm_response import extract_content_with_fallback


logger = structlog.get_logger(__name__)


class TopicResearcher:
    """Handles comprehensive topic research using the knowledge graph.

    Combines knowledge graph queries and LLM synthesis
    to provide comprehensive research on any topic.

    Args:
        graph_manager: Graph manager for knowledge graph queries
        search_manager: Search manager for vector/fulltext search
        config_manager: Configuration manager for settings
        llm_manager: LLM manager for provider access
        llm: Optional LLM service for queue operations

    Example:
        >>> researcher = TopicResearcher(graph, search, config, llm_mgr)
        >>> result = await researcher.research_topic(
        ...     topic="Artificial Intelligence",
        ...     depth="medium"
        ... )
        >>> print(result["analysis"])

    """

    def __init__(
        self,
        graph_manager: Any,
        search_manager: Any,
        config_manager: Any,
        llm_manager: Any,
        llm: Any = None,
    ) -> None:
        """Initialize the instance.

        Args:
            graph_manager: Graph manager for knowledge graph queries.
            search_manager: Search manager for vector/fulltext search.
            config_manager: Configuration manager for settings.
            llm_manager: LLM manager for provider access.
            llm: Optional LLM service for queue operations.

        """
        self.graph = graph_manager
        self.search = search_manager
        self.config = config_manager
        self.llm_manager = llm_manager
        self.llm = llm or llm_manager

    async def research_topic(self, topic: str, depth: str = "medium") -> dict[str, Any]:
        """Perform comprehensive research on a topic.

        Combines knowledge graph queries and LLM analysis
        to provide comprehensive research findings.

        Workflow:
        1. Search existing knowledge graph
        2. LLM synthesis of graph sources
        3. Return findings with citations

        Args:
            topic: Topic to research
            depth: Research depth - "shallow" (3 results), "medium" (5), "deep" (10)

        Returns:
            Dictionary containing:
                - success: Whether research succeeded
                - topic: Research topic
                - depth: Research depth used
                - existing_knowledge: List of related graph nodes
                - analysis: LLM-generated analysis text

        Raises:
            Exception: If research fails (logged and returned in result)

        Example:
            >>> result = await researcher.research_topic(
            ...     topic="Machine Learning",
            ...     depth="deep"
            ... )
            >>> print(result["analysis"])

        Note:
            Uses low priority LLM calls since research is typically background work.

        """
        try:
            # Search existing knowledge graph (use default page size from settings)
            settings = self.config.get_settings()
            graph_results = self.search.search(topic, limit=settings.pagination.default_page_size)

            # Analyze with LLM
            provider = self.llm_manager.get_chat_provider()

            # Build context from graph (batch fetch to avoid N+1 queries)
            top_result_ids = [r["id"] for r in graph_results[:5]]
            graph_nodes = self.graph.get_nodes_batch(top_result_ids) if top_result_ids else []
            graph_node_map = {node.id: node for node in graph_nodes}
            graph_context = "\n".join(
                [
                    f"- {graph_node_map[rid].label}"
                    for rid in top_result_ids
                    if rid in graph_node_map
                ]
            )

            analysis_prompt = f"""Analyze the topic in <topic> tags.

<topic>{topic}</topic>

<existing_knowledge>
{graph_context}
</existing_knowledge>

Think through the topic, then provide a comprehensive analysis covering:
1. A brief summary of the topic
2. Key concepts and subtopics (5-10 items)
3. Important entities (people, organizations, locations, etc.)
4. Suggested structure for organizing this knowledge

You can respond with JSON or clear prose."""

            # Use TOOL queue if available, otherwise direct call
            if self.llm and getattr(settings, "enable_llm_queueing", False):
                logger.info(
                    "research_queueing_analysis", queue="llm.inference", enable_llm_queueing=True
                )
                # Queue through INFERENCE queue (consolidated architecture)
                task_id = await self.llm.queue_operation(
                    task_type=TaskType.TOOL,
                    operation_name="chat_completion",
                    messages=[{"role": "user", "content": analysis_prompt}],
                    enable_thinking=settings.llm.thinking_for_tools,  # Keep thinking for analysis (not strict JSON)
                    metadata={},
                )

                result = await self.llm.wait_for_result(task_id)
                # Queue layer strips 'success' key, so check for 'response' key instead
                response = result.get("response", {})

                # Handle case where response might be JSON string instead of dict
                if isinstance(response, str):
                    try:
                        response = json.loads(response)
                    except json.JSONDecodeError:
                        logger.exception(
                            "research_json_parse_failed", response_preview=response[:200]
                        )
                        response = {}
            else:
                # Direct call (fallback) - LOW PRIORITY for background research
                logger.info("research_direct_llm_call", enable_llm_queueing=False, priority="LOW")
                response = await provider.chat(
                    [{"role": "user", "content": analysis_prompt}],
                    stream=False,
                    high_priority=False,
                    enable_thinking=settings.llm.thinking_for_tools,
                )

            # Extract analysis with intelligent fallback
            analysis_text = extract_content_with_fallback(
                response,
                expected_format="text",
                fallback_message="Analysis unavailable. The LLM did not generate research analysis content.",
            )

            return {
                "success": True,
                "topic": topic,
                "depth": depth,
                "existing_knowledge": [
                    {"id": rid, "label": graph_node_map[rid].label}
                    for rid in top_result_ids
                    if rid in graph_node_map
                ],
                "analysis": analysis_text,
            }

        except Exception as e:
            logger.exception(
                "research_topic_failed",
                error_type=type(e).__name__,
                error_message=str(e),
            )
            return {"success": False, "error": "Search operation failed"}
