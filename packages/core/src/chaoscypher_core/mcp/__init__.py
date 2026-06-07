# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""MCP (Model Context Protocol) server for ChaosCypher.

Provides tool definitions, a bridge to the ToolExecutorService, background
document processing, and a server factory for stdio and Streamable HTTP
transports.
"""

# Bridge
from chaoscypher_core.mcp.bridge import BridgeResult, MCPToolBridge

# Extraction orchestrator
from chaoscypher_core.mcp.extraction import ExtractionOrchestrator

# Document processor
from chaoscypher_core.mcp.processor import (
    CompletedFile,
    DocumentProcessor,
    ProcessingStatus,
    QueuedFile,
)

# Server factory
from chaoscypher_core.mcp.server import create_mcp_server

# Tool definitions
from chaoscypher_core.mcp.tools import TOOL_DEFINITIONS, ToolDefinition, get_tools_for_mode


__all__ = [
    "TOOL_DEFINITIONS",
    "BridgeResult",
    "CompletedFile",
    "DocumentProcessor",
    "ExtractionOrchestrator",
    "MCPToolBridge",
    "ProcessingStatus",
    "QueuedFile",
    "ToolDefinition",
    "create_mcp_server",
    "get_tools_for_mode",
]
