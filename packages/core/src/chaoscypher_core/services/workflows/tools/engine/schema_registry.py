# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tool Schema Registry.

Defines JSON schemas for all chat tools in OpenAI function calling format.
Used to populate the tools list for LLM chat requests.
"""

from typing import Any

import structlog

from chaoscypher_core.settings import GraphSettings


logger = structlog.get_logger(__name__)

# Load graph settings for default template IDs and relationship type
_GRAPH = GraphSettings()
_DEFAULT_EDGE_TEMPLATE = _GRAPH.default_edge_template
_DEFAULT_RELATIONSHIP_TYPE = _GRAPH.default_relationship_type


# Tool schema definitions in OpenAI function calling format
TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    # ========================================================================
    # GraphRAG Search (Primary)
    # ========================================================================
    "graphrag_search": {
        "type": "function",
        "function": {
            "name": "graphrag_search",
            "description": (
                "Primary search tool. Searches both the knowledge graph and document "
                "text simultaneously. Uses graph structure to find multi-hop connections "
                "and related entities that pure text search would miss. Returns graph "
                "context (entities, relationships) and relevant document chunks with "
                "citations. Use this as your FIRST search tool for any question."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of document chunks to return",
                        "default": 10,
                    },
                    "seed_limit": {
                        "type": "integer",
                        "description": "Maximum seed entities to match from query",
                        "default": 10,
                    },
                },
                "required": ["query"],
            },
        },
    },
    # ========================================================================
    # Node Operations
    # ========================================================================
    "search_nodes": {
        "type": "function",
        "function": {
            "name": "search_nodes",
            "description": "Search for ENTITIES in the knowledge graph by name or type. Prefer graphrag_search for general questions — it searches both graph and documents. Use search_nodes only when you need to find a specific entity by exact name/ID or filter by template type.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query text to find matching nodes",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of results to return",
                        "default": 10,
                    },
                    "template_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional: filter results to specific template types (e.g., ['person', 'character', 'organization']). Useful when searching for a specific entity type.",
                    },
                    "source_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional: filter to nodes from specific source documents",
                    },
                },
                "required": ["query"],
            },
        },
    },
    "search_chunks": {
        "type": "function",
        "function": {
            "name": "search_chunks",
            "description": "Search document text for specific content from a particular source. Prefer graphrag_search for general questions — it searches both graph and documents. Use search_chunks only when you need to search within a specific source document by ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Text to search for in document chunks",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of chunks to return",
                        "default": 5,
                    },
                    "source_id": {
                        "type": "string",
                        "description": "Optional: filter to a specific source document",
                    },
                    "source_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional: filter to specific source documents by ID",
                    },
                },
                "required": ["query"],
            },
        },
    },
    "get_node": {
        "type": "function",
        "function": {
            "name": "get_node",
            "description": "Get detailed information about a specific node by ID or search query.",
            "parameters": {
                "type": "object",
                "properties": {
                    "node_id": {
                        "type": "string",
                        "description": "The unique ID of the node to retrieve",
                    },
                    "query": {
                        "type": "string",
                        "description": "Search query to find the node (used if node_id not provided)",
                    },
                },
                "required": [],
            },
        },
    },
    "create_node": {
        "type": "function",
        "function": {
            "name": "create_node",
            "description": "Create a new node in the knowledge graph with the specified template, label, and properties.",
            "parameters": {
                "type": "object",
                "properties": {
                    "template_id": {
                        "type": "string",
                        "description": "The template ID that defines the node type and properties",
                    },
                    "label": {
                        "type": "string",
                        "description": "The display label for the node",
                    },
                    "properties": {
                        "type": "object",
                        "description": "Key-value pairs of node properties",
                    },
                },
                "required": ["template_id", "label", "properties"],
            },
        },
    },
    "update_node": {
        "type": "function",
        "function": {
            "name": "update_node",
            "description": "Update an existing node's label or properties.",
            "parameters": {
                "type": "object",
                "properties": {
                    "node_id": {
                        "type": "string",
                        "description": "The unique ID of the node to update",
                    },
                    "label": {
                        "type": "string",
                        "description": "New label for the node (optional)",
                    },
                    "properties": {
                        "type": "object",
                        "description": "Updated properties for the node (optional)",
                    },
                },
                "required": ["node_id"],
            },
        },
    },
    "delete_node": {
        "type": "function",
        "function": {
            "name": "delete_node",
            "description": "Delete a node from the knowledge graph. Also removes all edges connected to it.",
            "parameters": {
                "type": "object",
                "properties": {
                    "node_id": {
                        "type": "string",
                        "description": "The unique ID of the node to delete",
                    },
                },
                "required": ["node_id"],
            },
        },
    },
    "get_node_context": {
        "type": "function",
        "function": {
            "name": "get_node_context",
            "description": "Get comprehensive context for a node including relationships, connected nodes, and optionally document chunks mentioning it. Use for understanding an entity's full context.",
            "parameters": {
                "type": "object",
                "properties": {
                    "node_id": {
                        "type": "string",
                        "description": "The ID of the node to get context for",
                    },
                    "include_edges": {
                        "type": "boolean",
                        "description": "Whether to include edge/relationship information",
                        "default": True,
                    },
                    "include_chunks": {
                        "type": "boolean",
                        "description": "Whether to include document chunks mentioning this node",
                        "default": False,
                    },
                    "edge_limit": {
                        "type": "integer",
                        "description": "Maximum number of edges to return",
                        "default": 20,
                    },
                    "chunk_limit": {
                        "type": "integer",
                        "description": "Maximum number of chunks to return (if include_chunks is true)",
                        "default": 5,
                    },
                },
                "required": ["node_id"],
            },
        },
    },
    "resolve_node": {
        "type": "function",
        "function": {
            "name": "resolve_node",
            "description": "Resolve an alias, nickname, title, or descriptive phrase to a canonical node. Use this when the user refers to an entity by a nickname (e.g., 'The Little Princess'), title (e.g., 'Prince Vasili'), or description (e.g., 'Natasha's suitor'). Returns the best matching node with confidence score and alternatives.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The alias, nickname, or description to resolve to a node",
                    },
                    "include_alternatives": {
                        "type": "boolean",
                        "description": "Whether to include alternative possible matches",
                        "default": True,
                    },
                    "max_alternatives": {
                        "type": "integer",
                        "description": "Maximum number of alternative matches to return",
                        "default": 3,
                    },
                },
                "required": ["query"],
            },
        },
    },
    # ========================================================================
    # Edge Operations
    # ========================================================================
    "create_edge": {
        "type": "function",
        "function": {
            "name": "create_edge",
            "description": "Create a relationship (edge) between two nodes in the knowledge graph.",
            "parameters": {
                "type": "object",
                "properties": {
                    "source_node_id": {
                        "type": "string",
                        "description": "The ID of the source node",
                    },
                    "target_node_id": {
                        "type": "string",
                        "description": "The ID of the target node",
                    },
                    "template_id": {
                        "type": "string",
                        "description": "The template ID for the edge type",
                        "default": _DEFAULT_EDGE_TEMPLATE,
                    },
                    "label": {
                        "type": "string",
                        "description": "The relationship label (e.g., 'related_to', 'parent_of')",
                        "default": _DEFAULT_RELATIONSHIP_TYPE,
                    },
                    "properties": {
                        "type": "object",
                        "description": "Additional properties for the edge",
                    },
                },
                "required": ["source_node_id", "target_node_id"],
            },
        },
    },
    "list_edges": {
        "type": "function",
        "function": {
            "name": "list_edges",
            "description": "List edges in the graph, optionally filtered by a specific node.",
            "parameters": {
                "type": "object",
                "properties": {
                    "node_id": {
                        "type": "string",
                        "description": "Filter edges connected to this node ID",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of edges to return",
                        "default": 100,
                    },
                },
                "required": [],
            },
        },
    },
    "get_node_edges": {
        "type": "function",
        "function": {
            "name": "get_node_edges",
            "description": "Get all edges connected to a specific node with direction and type filtering. Returns edges with full details of the connected nodes. Use this for traversing relationships like 'children of X' or 'parents of Y'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "node_id": {
                        "type": "string",
                        "description": "The ID of the node to get edges for",
                    },
                    "direction": {
                        "type": "string",
                        "description": "Edge direction filter",
                        "enum": ["outgoing", "incoming", "both"],
                        "default": "both",
                    },
                    "edge_type": {
                        "type": "string",
                        "description": "Filter by edge label or template (e.g., 'parent_of', 'spouse_of')",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum edges to return",
                        "default": 50,
                    },
                },
                "required": ["node_id"],
            },
        },
    },
    # ========================================================================
    # Template Operations
    # ========================================================================
    "list_templates": {
        "type": "function",
        "function": {
            "name": "list_templates",
            "description": "List available templates that define node and edge types.",
            "parameters": {
                "type": "object",
                "properties": {
                    "template_type": {
                        "type": "string",
                        "description": "Filter by template type ('node' or 'edge')",
                        "enum": ["node", "edge"],
                    },
                },
                "required": [],
            },
        },
    },
    "search_templates": {
        "type": "function",
        "function": {
            "name": "search_templates",
            "description": "Search for templates by semantic similarity. Returns templates with entity_count showing how many entities use each template. IMPORTANT: Only use templates with entity_count > 0 - templates with 0 entities have no data in the graph. Results are sorted by entity_count (highest first). Use returned template IDs with search_nodes or analyze_graph_structure.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Concept or description to search for (e.g., 'people', 'places', 'relationships')",
                    },
                    "template_type": {
                        "type": "string",
                        "description": "Filter by template type",
                        "enum": ["node", "edge"],
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of results",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
        },
    },
    "create_template": {
        "type": "function",
        "function": {
            "name": "create_template",
            "description": "Create a new template for defining node or edge types.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "The name of the template",
                    },
                    "template_type": {
                        "type": "string",
                        "description": "The type of template ('node' or 'edge')",
                        "enum": ["node", "edge"],
                    },
                    "description": {
                        "type": "string",
                        "description": "Description of the template",
                        "default": "",
                    },
                    "properties": {
                        "type": "array",
                        "description": "Property definitions for the template",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "display_name": {"type": "string"},
                                "property_type": {
                                    "type": "string",
                                    "enum": ["string", "text", "number", "boolean", "date", "enum"],
                                },
                                "required": {"type": "boolean", "default": False},
                            },
                        },
                    },
                },
                "required": ["name", "template_type"],
            },
        },
    },
    "delete_template": {
        "type": "function",
        "function": {
            "name": "delete_template",
            "description": "Delete a template by ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "template_id": {
                        "type": "string",
                        "description": "The unique ID of the template to delete",
                    },
                },
                "required": ["template_id"],
            },
        },
    },
    # ========================================================================
    # Graph Analytics
    # ========================================================================
    "analyze_graph_structure": {
        "type": "function",
        "function": {
            "name": "analyze_graph_structure",
            "description": "Analyze the overall structure of the knowledge graph. Returns statistics like node count, edge count, communities, and top nodes by PageRank. Use template_ids to filter analysis to specific entity types (e.g., get top 10 people by PageRank).",
            "parameters": {
                "type": "object",
                "properties": {
                    "template_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional: filter analysis to specific template types (e.g., ['character']). Returns stats and top nodes only for these types.",
                    },
                },
                "required": [],
            },
        },
    },
    "find_shortest_path": {
        "type": "function",
        "function": {
            "name": "find_shortest_path",
            "description": "Find the shortest path between two nodes in the graph.",
            "parameters": {
                "type": "object",
                "properties": {
                    "source_id": {
                        "type": "string",
                        "description": "The ID of the starting node",
                    },
                    "target_id": {
                        "type": "string",
                        "description": "The ID of the destination node",
                    },
                },
                "required": ["source_id", "target_id"],
            },
        },
    },
    "find_similar_nodes": {
        "type": "function",
        "function": {
            "name": "find_similar_nodes",
            "description": "Find nodes similar to a given node using vector similarity search.",
            "parameters": {
                "type": "object",
                "properties": {
                    "node_id": {
                        "type": "string",
                        "description": "The ID of the node to find similar nodes for",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of similar nodes to return",
                        "default": 10,
                    },
                },
                "required": ["node_id"],
            },
        },
    },
    "traverse_path": {
        "type": "function",
        "function": {
            "name": "traverse_path",
            "description": "Traverse the graph from a starting node following specified edge types. Use this for multi-hop queries like 'who are X's spouse's siblings' or 'find all descendants of Y'. Returns discovered nodes with the path taken to reach them.",
            "parameters": {
                "type": "object",
                "properties": {
                    "start_node_id": {
                        "type": "string",
                        "description": "The ID of the node to start traversal from",
                    },
                    "edge_types": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional list of edge types/labels to follow (e.g., ['spouse_of', 'sibling_of']). If not specified, follows all edge types.",
                    },
                    "max_depth": {
                        "type": "integer",
                        "description": "Maximum traversal depth (number of hops)",
                        "default": 2,
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of nodes to return",
                        "default": 50,
                    },
                },
                "required": ["start_node_id"],
            },
        },
    },
    # ========================================================================
    # External Tools
    # ========================================================================
    "research_topic": {
        "type": "function",
        "function": {
            "name": "research_topic",
            "description": "Research a topic in depth and return findings.",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": "The topic to research",
                    },
                    "depth": {
                        "type": "string",
                        "description": "Research depth level",
                        "enum": ["quick", "full"],
                        "default": "full",
                    },
                },
                "required": ["topic"],
            },
        },
    },
    # ── Summarization ──────────────────────────────────────────────
    "summarize": {
        "type": "function",
        "function": {
            "name": "summarize",
            "description": (
                "Summarize large amounts of content from documents. Use when the user "
                "asks to summarize a document, topic, character, or compare multiple "
                "sources. NOT for simple factual lookups — use search_chunks instead."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "What to summarize. Examples: 'full document', "
                            "'the character Anna', 'climate policy recommendations', "
                            "'compare research methodologies'"
                        ),
                    },
                    "source_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Source document IDs to summarize from. "
                            "If omitted, searches across all available sources."
                        ),
                    },
                },
                "required": ["query"],
            },
        },
    },
}


def get_tool_schemas() -> list[dict[str, Any]]:
    """Get all tool schemas as a list.

    Returns:
        List of tool schemas in OpenAI function calling format

    """
    return list(TOOL_SCHEMAS.values())


def get_tool_schema(tool_name: str) -> dict[str, Any] | None:
    """Get schema for a specific tool.

    Args:
        tool_name: Name of the tool

    Returns:
        Tool schema dict or None if not found

    """
    return TOOL_SCHEMAS.get(tool_name)


def get_essential_tool_schemas(essential_names: list[str]) -> list[dict[str, Any]]:
    """Get schemas for a list of essential tools.

    Args:
        essential_names: List of tool names to include

    Returns:
        List of tool schemas for the specified tools

    """
    schemas = []
    for name in essential_names:
        schema = TOOL_SCHEMAS.get(name)
        if schema:
            schemas.append(schema)
        else:
            logger.warning("tool_schema_not_found", tool_name=name)
    return schemas


__all__ = [
    "TOOL_SCHEMAS",
    "get_essential_tool_schemas",
    "get_tool_schema",
    "get_tool_schemas",
]
