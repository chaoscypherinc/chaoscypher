# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Research Agent - Advanced AI capabilities for knowledge discovery and analysis.

Main orchestrator that delegates to specialized modules:
- research.py: Topic research with knowledge graph synthesis
- Built-in methods for hierarchy, gap analysis, and entity extraction

Simplified version that delegates research to dedicated module.
"""

import json
import re
from typing import Any

import structlog

from chaoscypher_core.app_config import get_settings
from chaoscypher_core.ports.llm import TaskType
from chaoscypher_core.services.search.engine.research import TopicResearcher
from chaoscypher_core.utils.llm_response import extract_content_with_fallback


logger = structlog.get_logger(__name__)


class ResearchAgent:
    """Advanced research capabilities for AI assistant.

    Delegates topic research to TopicResearcher module while maintaining
    other research operations (hierarchy, gap analysis, entity extraction).

    Args:
        graph_manager: Graph manager for knowledge graph operations
        search_manager: Search manager for vector/fulltext search
        config_manager: Configuration manager for settings
        llm_manager: LLM manager for provider access
        llm: Optional LLM service for queue operations

    Example:
        >>> agent = ResearchAgent(graph, search, config, llm_mgr)
        >>> result = await agent.research_topic("AI", depth="medium")

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
            graph_manager: Graph manager for knowledge graph operations.
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

        # Delegate to specialized researcher
        self.topic_researcher = TopicResearcher(
            graph_manager, search_manager, config_manager, llm_manager, llm
        )

    async def research_topic(self, topic: str, depth: str = "medium") -> dict[str, Any]:
        """Perform comprehensive research on a topic.

        Delegates to TopicResearcher for implementation.

        Args:
            topic: Topic to research
            depth: Research depth - "shallow", "medium", "deep"

        Returns:
            Research findings with sources, key concepts, and suggested nodes

        """
        return await self.topic_researcher.research_topic(topic, depth)

    async def build_topic_hierarchy(self, topic: str, max_levels: int = 3) -> dict[str, Any]:
        """Build a hierarchical structure for a topic.

        Args:
            topic: Main topic
            max_levels: Maximum hierarchy depth

        Returns:
            Hierarchical structure with suggested nodes and relationships

        """
        try:
            provider = self.llm_manager.get_chat_provider()

            prompt = f"""Create a hierarchical knowledge structure for the topic in <topic> tags.

<topic>{topic}</topic>

Build a hierarchy with up to {max_levels} levels showing how this topic breaks down into subtopics.

Your response should be valid JSON following this structure:
{{
  "main_topic": "the topic",
  "hierarchy": [
    {{
      "level": 1,
      "name": "Subtopic name",
      "description": "Brief description",
      "children": [
        {{
          "level": 2,
          "name": "Sub-subtopic",
          "description": "Description"
        }}
      ]
    }}
  ]
}}

Include at least 3-5 level-1 topics with relevant subtopics. Think through the best way to organize this knowledge, then provide the JSON structure."""

            # Use TOOL queue if available, otherwise direct call
            settings = self.config.get_settings()
            if self.llm and getattr(settings, "enable_llm_queueing", False):
                task_id = await self.llm.queue_operation(
                    task_type=TaskType.TOOL,
                    operation_name="chat_completion",
                    messages=[{"role": "user", "content": prompt}],
                    enable_thinking=settings.llm.thinking_for_tools,
                    metadata={},
                )

                result = await self.llm.wait_for_result(task_id)
                response = result.get("response", {})

                if isinstance(response, str):
                    try:
                        response = json.loads(response)
                        logger.info("hierarchy_json_parsed", response_keys=list(response.keys()))
                    except json.JSONDecodeError:
                        logger.exception(
                            "hierarchy_json_parse_failed", response_preview=response[:200]
                        )
                        response = {}

                logger.info(
                    "hierarchy_response_received",
                    response_type=type(response).__name__,
                    response_keys=list(response.keys()) if isinstance(response, dict) else None,
                )
            else:
                response = await provider.chat(
                    [{"role": "user", "content": prompt}],
                    stream=False,
                    high_priority=False,
                    enable_thinking=settings.llm.thinking_for_tools,
                )

            # Extract hierarchy with intelligent fallback
            if isinstance(response, dict) and "main_topic" in response and "hierarchy" in response:
                logger.info("hierarchy_response_is_direct_json")
                hierarchy_content = json.dumps(response)
            else:
                hierarchy_content = extract_content_with_fallback(
                    response,
                    expected_format="json",
                    fallback_message=json.dumps(
                        {
                            "main_topic": topic,
                            "hierarchy": [],
                            "error": "The LLM did not generate a hierarchy. This may be due to model limitations or unclear topic.",
                        }
                    ),
                )

            return {
                "success": True,
                "topic": topic,
                "max_levels": max_levels,
                "hierarchy": hierarchy_content,
            }

        except Exception as e:
            logger.exception(
                "build_hierarchy_failed",
                error_type=type(e).__name__,
                error_message=str(e),
            )
            return {"success": False, "error": "Research operation failed"}

    async def identify_knowledge_gaps(self, topic: str) -> dict[str, Any]:
        """Identify gaps in knowledge graph coverage for a topic.

        Args:
            topic: Topic to analyze

        Returns:
            Analysis of what's missing or incomplete

        """
        try:
            # 1. Get existing knowledge
            graph_results = self.search.search(topic, limit=20)

            existing_nodes = []
            for result in graph_results:
                node = self.graph.get_node(result["id"])
                if node:
                    edges = self.graph.list_edges(node_id=node.id)
                    existing_nodes.append(
                        {
                            "id": node.id,
                            "label": node.label,
                            "template_id": node.template_id,
                            "num_connections": len(edges),
                        }
                    )

            # 2. Analyze with LLM
            provider = self.llm_manager.get_chat_provider()

            existing_knowledge = "\n".join(
                [
                    f"- {node['label']} ({node['template_id']}, {node['num_connections']} connections)"
                    for node in existing_nodes
                ]
            )

            prompt = f"""Analyze knowledge gaps for the topic in <topic> tags.

<topic>{topic}</topic>

Current knowledge graph contains:
<existing_knowledge>
{existing_knowledge if existing_knowledge else "No existing nodes found"}
</existing_knowledge>

Provide a detailed analysis covering:
1. What key concepts are missing?
2. What relationships should be added?
3. What areas need more detail?
4. What templates/structures would help organize this better?

Think through the gaps, then provide specific, actionable recommendations in clear prose or bullet points."""

            # Use TOOL queue if available, otherwise direct call
            settings = self.config.get_settings()
            if self.llm and getattr(settings, "enable_llm_queueing", False):
                task_id = await self.llm.queue_operation(
                    task_type=TaskType.TOOL,
                    operation_name="chat_completion",
                    messages=[{"role": "user", "content": prompt}],
                    enable_thinking=settings.llm.thinking_for_tools,
                    metadata={},
                )

                result = await self.llm.wait_for_result(task_id)
                response = result.get("response", {})

                if isinstance(response, str):
                    try:
                        response = json.loads(response)
                    except json.JSONDecodeError:
                        logger.exception(
                            "gap_analysis_json_parse_failed", response_preview=response[:200]
                        )
                        response = {}
            else:
                response = await provider.chat(
                    [{"role": "user", "content": prompt}],
                    stream=False,
                    high_priority=False,
                    enable_thinking=settings.llm.thinking_for_tools,
                )

            gap_analysis_content = extract_content_with_fallback(
                response,
                expected_format="text",
                fallback_message="Analysis unavailable. The LLM did not generate gap analysis content.",
            )

            return {
                "success": True,
                "topic": topic,
                "existing_nodes_count": len(existing_nodes),
                "existing_nodes": existing_nodes[:10],
                "gap_analysis": gap_analysis_content,
            }

        except Exception as e:
            logger.exception(
                "knowledge_gap_analysis_failed",
                error_type=type(e).__name__,
                error_message=str(e),
            )
            return {"success": False, "error": "Research operation failed"}

    async def extract_entities_from_text(
        self, text: str, template_id: str | None = None, create_nodes: bool = False
    ) -> dict[str, Any]:
        """Extract entities from text using LLM.

        Args:
            text: Text to analyze
            template_id: Template to use for created nodes (optional)
            create_nodes: Whether to automatically create nodes

        Returns:
            Extracted entities and optionally created nodes

        """
        try:
            provider = self.llm_manager.get_chat_provider()

            # Get available templates if template_id not specified
            templates_info = ""
            if not template_id:
                templates = self.graph.list_templates()
                templates_info = "\n".join(
                    [f"- {t.id}: {t.name} ({t.template_type})" for t in templates[:10]]
                )

            research_window = get_settings().extraction.research_context_window_chars
            prompt = f"""Extract entities from the text inside <document> tags.

<document>
{text[:research_window]}
</document>

Identify these types of entities:
1. People (names, roles)
2. Organizations (companies, institutions)
3. Locations (places, regions)
4. Concepts (key ideas, topics)
5. Events (significant occurrences)
6. Dates/Times

Available templates:
{templates_info if templates_info else "Use generic 'entity' template"}

Think through what entities are present, then provide a JSON array following this format:
[
  {{
    "entity": "Entity name",
    "type": "person|organization|location|concept|event|date",
    "description": "Brief description",
    "template_id": "suggested_template_id",
    "properties": {{}}
  }}
]

Include at least 3-5 relevant entities."""

            # Use TOOL queue if available, otherwise direct call
            settings = self.config.get_settings()
            if self.llm and getattr(settings, "enable_llm_queueing", False):
                task_id = await self.llm.queue_operation(
                    task_type=TaskType.TOOL,
                    operation_name="chat_completion",
                    messages=[{"role": "user", "content": prompt}],
                    enable_thinking=settings.llm.thinking_for_tools,
                    metadata={},
                )

                result = await self.llm.wait_for_result(task_id)
                response = result.get("response", {})

                if isinstance(response, str):
                    try:
                        response = json.loads(response)
                    except json.JSONDecodeError:
                        logger.exception(
                            "entity_extraction_json_parse_failed", response_preview=response[:200]
                        )
                        response = {}
            else:
                response = await provider.chat(
                    [{"role": "user", "content": prompt}],
                    stream=False,
                    high_priority=False,
                    enable_thinking=settings.llm.thinking_for_tools,
                )

            # Extract entities with intelligent JSON fallback
            entities_text = extract_content_with_fallback(
                response, expected_format="json", fallback_message="[]"
            )

            # Try to parse JSON from response
            entities = []
            try:
                entities = json.loads(entities_text)
            except json.JSONDecodeError as e:
                logger.warning("entities_json_parse_direct_failed", error=str(e))
                try:
                    json_match = re.search(r"\[[\s\S]*\]", entities_text)
                    if json_match:
                        entities = json.loads(json_match.group())
                except json.JSONDecodeError as e2:
                    logger.warning("entities_json_parse_array_failed", error=str(e2))
            except re.error as e:
                logger.exception(
                    "entity_extraction_regex_error",
                    error_type=type(e).__name__,
                    error_message=str(e),
                )

            created_nodes = []
            if create_nodes and entities:
                from chaoscypher_core.models import NodeCreate

                for entity in entities:
                    try:
                        node = self.graph.create_node(
                            NodeCreate(
                                template_id=entity.get("template_id", template_id or "entity"),
                                label=entity["entity"],
                                properties={
                                    "description": entity.get("description", ""),
                                    "entity_type": entity.get("type", "unknown"),
                                    **entity.get("properties", {}),
                                },
                            )
                        )
                        created_nodes.append({"id": node.id, "label": node.label})
                    except Exception as e:
                        logger.warning(
                            "entity_node_creation_failed",
                            entity_name=entity.get("entity"),
                            error=str(e),
                        )

            return {
                "success": True,
                "num_entities": len(entities),
                "entities": entities,
                "created_nodes": created_nodes if create_nodes else [],
                "raw_response": entities_text,
            }

        except Exception as e:
            logger.exception(
                "entity_extraction_failed",
                error_type=type(e).__name__,
                error_message=str(e),
            )
            return {"success": False, "error": "Research operation failed"}
