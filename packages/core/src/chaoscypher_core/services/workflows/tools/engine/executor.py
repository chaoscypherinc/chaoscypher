# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tool Executor - Tool Execution Engine.

Executes AI tool calls against the knowledge graph using handler delegation.

High-level execution service with strategy pattern for different tool categories.
"""

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, ClassVar

import structlog

from chaoscypher_core.exceptions import NotFoundError
from chaoscypher_core.services.graph.engine.analytics import GraphAnalyticsService
from chaoscypher_core.services.workflows.tools.engine.handlers.analytics_handlers import (
    AnalyticsToolHandlers,
)
from chaoscypher_core.services.workflows.tools.engine.handlers.edge_handlers import EdgeToolHandlers
from chaoscypher_core.services.workflows.tools.engine.handlers.external_handlers import (
    ExternalToolHandlers,
)
from chaoscypher_core.services.workflows.tools.engine.handlers.graphrag_handlers import (
    GraphRAGToolHandlers,
)
from chaoscypher_core.services.workflows.tools.engine.handlers.node_handlers import NodeToolHandlers
from chaoscypher_core.services.workflows.tools.engine.handlers.summarize_handlers import (
    SummarizeToolHandlers,
)
from chaoscypher_core.services.workflows.tools.engine.handlers.template_handlers import (
    TemplateToolHandlers,
)


if TYPE_CHECKING:
    from chaoscypher_core.ports.graph import GraphRepositoryProtocol
    from chaoscypher_core.ports.index import IndexingProtocol
    from chaoscypher_core.ports.search import SearchRepositoryProtocol
    from chaoscypher_core.services.workflows.tools.engine.registry import ToolRegistry
    from chaoscypher_core.settings import EngineSettings

logger = structlog.get_logger(__name__)


class ToolExecutorService:
    """Executes AI tool calls against the knowledge graph.

    Uses strategy pattern with handler delegation for clean separation of concerns.
    """

    _SOURCE_SCOPED_TOOLS: ClassVar[set[str]] = {
        "graphrag_search",
        "search_chunks",
        "search_nodes",
        "get_node",
        "get_node_context",
        "get_node_edges",
        "traverse_path",
        "resolve_node",
        "create_node",
        "update_node",
        "create_edge",
        "delete_node",
        "list_edges",
        "analyze_graph_structure",
        "find_shortest_path",
        "find_similar_nodes",
        "summarize",
        "get_summary_context",
    }

    def __init__(
        self,
        graph_repository: GraphRepositoryProtocol,
        search_repository: SearchRepositoryProtocol,
        indexing_repository: IndexingProtocol | None = None,
        embedding_callback: Callable | None = None,
        llm_chat_callback: Callable | None = None,
        research_agent_callback: Callable | None = None,
        search_settings: Any | None = None,
        engine_settings: EngineSettings | None = None,
        scope: dict[str, Any] | None = None,
        source_storage: Any | None = None,
    ):
        """Initialize tool executor service.

        Args:
            graph_repository: GraphRepository implementation
            search_repository: SearchRepository implementation
            indexing_repository: Optional IndexingProtocol for chunk operations
            embedding_callback: Optional async callback for generating embeddings
            llm_chat_callback: Optional async callback for LLM chat completions
            research_agent_callback: Optional callback for research agent (backend-specific)
            search_settings: Optional SearchSettings for reranking configuration
            engine_settings: Optional full engine settings for summarize handler
            scope: Optional scope constraints (e.g. {"source_ids": [...]})
            source_storage: Optional SourceStorageProtocol for citation lookups

        """
        self.scope = scope or {}
        self.llm_chat_callback = llm_chat_callback
        # Initialize handler classes
        analytics_service = GraphAnalyticsService(graph_repository)

        self.node_handlers = NodeToolHandlers(
            graph_repository,
            search_repository,
            indexing_repository,
            embedding_callback=embedding_callback,
            search_settings=search_settings,
        )
        self.edge_handlers = EdgeToolHandlers(graph_repository)

        self.template_handlers = TemplateToolHandlers(
            graph_repository,
            search_repository=search_repository,
            embedding_callback=embedding_callback,
        )
        self.analytics_handlers = AnalyticsToolHandlers(
            graph_repository, search_repository, analytics_service, settings=engine_settings
        )
        self.external_handlers = ExternalToolHandlers(research_agent_callback)
        # SummarizeToolHandlers requires concrete settings — fall back to
        # default EngineSettings when none provided so the executor still
        # boots in lightweight contexts (tests, headless tools).
        if engine_settings is None:
            from chaoscypher_core.settings import EngineSettings as _EngineSettings

            summarize_settings: EngineSettings = _EngineSettings()
        else:
            summarize_settings = engine_settings
        self.summarize_handlers = SummarizeToolHandlers(
            indexing_repository=indexing_repository,
            search_repository=search_repository,
            llm_chat_callback=llm_chat_callback,
            embedding_callback=embedding_callback,
            settings=summarize_settings,
            scope=scope,
        )
        self.graphrag_handlers = GraphRAGToolHandlers(
            graph_repository=graph_repository,
            search_repository=search_repository,
            indexing_repository=indexing_repository,
            source_storage=source_storage,
            embedding_callback=embedding_callback,
            settings=engine_settings,
            database_name=scope.get("database_name", "default") if scope else "default",
        )

        # Strategy pattern: Tool name -> handler method (O(1) lookup)
        self._tool_handlers = {
            # GraphRAG search (primary)
            "graphrag_search": self.graphrag_handlers.graphrag_search,
            # Node operations
            "search_nodes": self.node_handlers.search_nodes,
            "search_chunks": self.node_handlers.search_chunks,
            "get_node": self.node_handlers.get_node,
            "get_node_context": self.node_handlers.get_node_context,
            "resolve_node": self.node_handlers.resolve_node,
            "create_node": self.node_handlers.create_node,
            "update_node": self.node_handlers.update_node,
            "delete_node": self.node_handlers.delete_node,
            # Edge operations
            "create_edge": self.edge_handlers.create_edge,
            "list_edges": self.edge_handlers.list_edges,
            "get_node_edges": self.edge_handlers.get_node_edges,
            # Template operations
            "list_templates": self.template_handlers.list_templates,
            "search_templates": self.template_handlers.search_templates,
            "create_template": self.template_handlers.create_template,
            "delete_template": self.template_handlers.delete_template,
            # Graph analytics
            "analyze_graph_structure": self.analytics_handlers.analyze_graph_structure,
            "find_shortest_path": self.analytics_handlers.find_shortest_path,
            "find_similar_nodes": self.analytics_handlers.find_similar_nodes,
            "traverse_path": self.analytics_handlers.traverse_path,
            # Summarization
            "summarize": self.summarize_handlers.summarize,
            "get_summary_context": self.summarize_handlers.get_summary_context,
            # Research tools
            "extract_entities_from_text": self.external_handlers.extract_entities_from_text,
            "research_topic": self.external_handlers.research_topic,
            "build_topic_hierarchy": self.external_handlers.build_topic_hierarchy,
            "identify_knowledge_gaps": self.external_handlers.identify_knowledge_gaps,
        }

    def _apply_scope(self, tool_name: str, parameters: dict[str, Any]) -> dict[str, Any]:
        """Inject scope constraints into tool parameters.

        Args:
            tool_name: Name of the tool being called
            parameters: Original parameters from LLM

        Returns:
            Parameters with scope constraints injected

        """
        source_ids = self.scope.get("source_ids")
        if not source_ids:
            return parameters

        if tool_name in self._SOURCE_SCOPED_TOOLS:
            parameters = {**parameters, "source_ids": source_ids}

        return parameters

    async def execute_tool(self, tool_name: str, parameters: dict[str, Any]) -> dict[str, Any]:
        """Execute a tool using dictionary dispatch (O(1) lookup).

        Args:
            tool_name: Name of the tool to execute
            parameters: Tool parameters

        Returns:
            Dict with execution results

        """
        try:
            logger.info("tool_executing", tool_name=tool_name, parameters=parameters)

            # Apply scope constraints
            if self.scope:
                parameters = self._apply_scope(tool_name, parameters)

            from typing import cast

            # Look up handler in dictionary
            handler = self._tool_handlers.get(tool_name)
            if not handler or not callable(handler):
                return {"error": f"Unknown tool: {tool_name}"}

            # Execute handler with parameters
            result = await handler(**parameters)
            return cast("dict[str, Any]", result)

        except TypeError as e:
            logger.exception(
                "tool_invalid_parameters",
                tool_name=tool_name,
                error_type=type(e).__name__,
                error_message=str(e),
            )
            return {"error": f"Invalid parameters for {tool_name}"}
        except Exception as e:
            logger.exception(
                "tool_execution_failed",
                tool_name=tool_name,
                error_type=type(e).__name__,
                error_message=str(e),
            )
            return {"error": "Tool execution failed"}


# Global registry instance (initialized on first use)
_registry: ToolRegistry | None = None


def get_tool_discovery() -> ToolRegistry:
    """Get or create the global tool registry instance.

    Returns:
        ToolRegistry instance with all plugins discovered.

    """
    global _registry
    if _registry is None:
        from pathlib import Path

        from chaoscypher_core.services.workflows.tools.engine.registry import ToolRegistry

        # Point to plugins directory (sibling of engine directory)
        plugins_dir = Path(__file__).parent.parent / "plugins"
        _registry = ToolRegistry(plugins_dir=plugins_dir)
        logger.debug("tool_registry_initialized", plugin_count=_registry.count())
    return _registry


async def execute_tool(
    tool_id: str,
    inputs: dict[str, Any],
    graph_manager: Any,
    llm_service: Any | None = None,
    discovery_service: Any | None = None,
    search_repository: Any | None = None,
    **context_kwargs: Any,
) -> dict[str, Any]:
    """Execute a tool plugin by ID.

    Convenience wrapper for executing tools outside of workflows.
    Creates a ToolExecutionContext and calls the plugin.

    Args:
        tool_id: Tool plugin ID (e.g., "ai.prompt", "templates.list")
        inputs: Tool input parameters
        graph_manager: GraphRepository instance (required)
        llm_service: LLM service (optional)
        discovery_service: Discovery service (optional)
        search_repository: Search repository (optional)
        **context_kwargs: Additional context parameters (thinking_mode, database_name, etc.)

    Returns:
        Tool execution result dictionary

    Raises:
        NotFoundError: If tool_id not found in the registry

    Example:
        result = await execute_tool(
            tool_id='ai.prompt',
            inputs={'prompt': 'Analyze...'},
            graph_manager=graph_repo,
            llm_service=llm_svc,
            thinking_mode='extended'
        )

    """
    from chaoscypher_core.services.workflows.tools.engine.context import ToolExecutionContext

    registry = get_tool_discovery()

    # Get plugin
    plugin = registry.get(tool_id)
    if not plugin:
        available_tools = list(registry.list_all().keys())
        logger.warning("tool_not_found", tool_id=tool_id, available=available_tools)
        raise NotFoundError("Tool", tool_id)

    # Create execution context
    context = ToolExecutionContext(
        graph_manager=graph_manager,
        llm_service=llm_service,
        discovery_service=discovery_service,
        search_repository=search_repository,
        **context_kwargs,
    )

    # Execute plugin
    logger.debug("executing_tool", tool_id=tool_id, plugin_name=plugin.name)

    result = await plugin.execute(inputs, context)

    logger.debug("tool_executed", tool_id=tool_id, success=True)

    return result


__all__ = ["ToolExecutorService", "execute_tool", "get_tool_discovery"]
