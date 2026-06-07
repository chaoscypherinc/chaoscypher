# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""System Tools Execution - Workflow Service Layer.

Provides the execute_system_tool function that bridges LLMProvider
with the workflow tool execution engine.

Tool execution is business logic, not an LLM-adapter concern, which is why
this module lives under ``services/workflows/tools/`` rather than the
``adapters/llm/`` tree.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.exceptions import ValidationError

from .engine import execute_tool


if TYPE_CHECKING:
    from chaoscypher_core.settings import EngineSettings


logger = structlog.get_logger(__name__)


async def execute_system_tool(
    tool_name: str,
    tool_input: dict[str, Any],
    managers: dict[str, Any],
    settings: EngineSettings,
) -> dict[str, Any]:
    """Execute a system tool via the workflow tool engine.

    This function adapts the LLMProvider interface to the underlying
    tool execution engine, extracting required services from the managers dict.

    Args:
        tool_name: Name/ID of the tool to execute (e.g., "ai.prompt", "templates.list")
        tool_input: Tool input parameters
        managers: Dict containing service instances:
            - graph_manager: GraphRepository instance (required)
            - search_manager: SearchRepository instance (optional)
            - config_manager: Configuration manager (optional)
        settings: Application settings (passed to context)

    Returns:
        Tool execution result dictionary

    Raises:
        ValidationError: If required managers are missing

    Example:
        >>> managers = {
        ...     "graph_manager": graph_repo,
        ...     "search_manager": search_repo,
        ... }
        >>> result = await execute_system_tool(
        ...     tool_name="templates.list",
        ...     tool_input={"category": "research"},
        ...     managers=managers,
        ...     settings=app_settings,
        ... )
        >>> print(result)
        {"templates": [...]}

    """
    logger.debug(
        "execute_system_tool_called",
        tool_name=tool_name,
        has_managers=bool(managers),
    )

    # Extract managers (graph_manager is required)
    graph_manager = managers.get("graph_manager")
    if graph_manager is None:
        raise ValidationError(
            "graph_manager is required in managers dict",
            field="graph_manager",
        )

    search_repository = managers.get("search_manager")
    discovery_service = managers.get("discovery_service")

    # Get LLM service if available (for AI tools)
    llm_service = managers.get("llm_service")

    # Get database name from settings if available
    database_name = None
    if settings and hasattr(settings, "current_database"):
        database_name = settings.current_database

    # Execute via the tool engine
    result = await execute_tool(
        tool_id=tool_name,
        inputs=tool_input,
        graph_manager=graph_manager,
        llm_service=llm_service,
        discovery_service=discovery_service,
        search_repository=search_repository,
        database_name=database_name,
    )

    logger.debug(
        "execute_system_tool_completed",
        tool_name=tool_name,
        success="error" not in result,
    )

    return result


__all__ = ["execute_system_tool"]
