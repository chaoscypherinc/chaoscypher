# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tool Executor Adapter.

Adapts backend services to implement engine's ToolExecutor Protocol.
Bridges backend (SQLModel services) to engine (Protocol-based workflow execution).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.services.workflows.tools.engine import ToolExecutorService


if TYPE_CHECKING:
    from chaoscypher_core.ports.graph import GraphRepositoryProtocol

logger = structlog.get_logger(__name__)


class BackendToolExecutorAdapter:
    """Adapter implementing ToolExecutor Protocol for backend workflows.

    Wraps ToolExecutorService with backend-specific service initialization.
    This allows engine's workflow builder to work with backend services.
    """

    def __init__(
        self,
        graph_repository: GraphRepositoryProtocol,
        search_repository: Any,
        llm_provider: Any,
        tool_service: Any = None,
        discovery_service: Any = None,
    ) -> None:
        """Initialize adapter with backend services.

        Args:
            graph_repository: GraphRepository instance
            search_repository: SearchRepository instance
            llm_provider: LLM provider instance
            tool_service: Optional ToolService for user tool resolution
            discovery_service: Optional DiscoveryService for research tools

        """
        self.tool_service = tool_service
        self.discovery_service = discovery_service

        # Create research agent callback if discovery service available
        research_agent_callback = None
        if discovery_service:

            async def research_agent_callback(operation: str, **kwargs: Any) -> dict[str, Any]:
                """Research agent callback for AI tools."""
                # Map operations to discovery service methods
                if operation == "extract_entities":
                    # Not implemented in discovery service yet
                    return {"success": False, "error": "Entity extraction not available"}
                if operation == "research_topic":
                    # Not implemented in discovery service yet
                    return {"success": False, "error": "Topic research not available"}
                if operation == "build_hierarchy":
                    return {"success": False, "error": "Topic hierarchy not available"}
                if operation == "identify_gaps":
                    return {"success": False, "error": "Gap identification not available"}
                return {"success": False, "error": f"Unknown operation: {operation}"}

        # Initialize engine ToolExecutorService
        self.tool_executor = ToolExecutorService(
            graph_repository=graph_repository,
            search_repository=search_repository,
            llm_chat_callback=llm_provider,
            research_agent_callback=research_agent_callback,
        )

    async def execute_tool(
        self, tool_id: str, inputs: dict[str, Any], thinking_mode: str | None = None
    ) -> dict[str, Any]:
        """Execute a tool (implements ToolExecutor Protocol).

        Args:
            tool_id: System tool ID (e.g., 'search_nodes', 'create_node')
            inputs: Tool parameters
            thinking_mode: Optional AI thinking mode

        Returns:
            Tool execution result

        """
        logger.debug(
            "backend_tool_executor_executing", tool_id=tool_id, thinking_mode=thinking_mode
        )

        # Delegate to engine ToolExecutorService
        return await self.tool_executor.execute_tool(tool_id, inputs)

        # Note: thinking_mode is passed but not currently used by ToolExecutorService
        # This is for future enhancement when tools support different thinking modes

    def get_user_tool(self, tool_id: str) -> dict[str, Any] | None:
        """Resolve user tool configuration.

        Used by workflow executor for user tool steps.

        Args:
            tool_id: User tool ID

        Returns:
            User tool dict or None if not found

        """
        from typing import cast

        if not self.tool_service:
            return None

        return cast("dict[str, Any] | None", self.tool_service.get_user_tool(tool_id))


__all__ = ["BackendToolExecutorAdapter"]
