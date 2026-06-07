# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""MCP Tool Bridge.

Translates between MCP tool calls and the existing ToolExecutorService,
converting ToolExecutorService dict results to MCP-compatible result objects.
"""

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import structlog


if TYPE_CHECKING:
    from chaoscypher_core.services.workflows.tools.engine.executor import ToolExecutorService

logger = structlog.get_logger(__name__)


@dataclass
class BridgeResult:
    """Result from a bridge tool execution."""

    text: str
    is_error: bool


class MCPToolBridge:
    """Bridges MCP tool calls to the ToolExecutorService.

    Translates MCP JSON params to ToolExecutorService dict parameters and wraps
    the returned dict as a serialized JSON BridgeResult.
    """

    def __init__(self, tool_executor: ToolExecutorService) -> None:
        """Initialize with a wired ToolExecutorService instance."""
        self.executor = tool_executor

    async def execute(self, tool_name: str, arguments: dict[str, Any]) -> BridgeResult:
        """Execute a tool call and return a bridge result.

        Args:
            tool_name: Name of the tool to execute.
            arguments: Tool parameters as a dict.

        Returns:
            BridgeResult with serialized JSON text and error flag.

        """
        try:
            result = await self.executor.execute_tool(tool_name, arguments)
            is_error = not result.get("success", True)
            return BridgeResult(
                text=json.dumps(result, default=str),
                is_error=is_error,
            )
        except Exception as e:
            logger.warning("mcp_tool_execution_failed", tool=tool_name, error=str(e))
            error_result = {"success": False, "error": "Tool execution failed"}
            return BridgeResult(
                text=json.dumps(error_result),
                is_error=True,
            )
