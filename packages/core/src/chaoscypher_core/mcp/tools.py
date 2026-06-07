# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""MCP tool definitions for ChaosCypher.

Defines the 31 tools exposed via MCP, each with a JSON Schema for input
validation and a ``write_only`` flag used by ``get_tools_for_mode()`` to
filter tools based on server access mode.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class ToolDefinition:
    """Immutable description of an MCP tool.

    Attributes:
        name: Unique tool identifier (snake_case).
        description: Human-readable description for LLM hosts.
        input_schema: JSON Schema dict describing accepted parameters.
        write_only: If True the tool is hidden in read-only mode.
    """

    name: str
    description: str
    input_schema: dict = field(default_factory=dict)
    write_only: bool = False


# ============================================================================
# GraphRAG (1 read tool)
# ============================================================================

_graphrag_search = ToolDefinition(
    name="graphrag_search",
    description=(
        "Graph-enhanced RAG search. Fuses Personalized PageRank over the "
        "knowledge graph with hybrid vector/keyword search to find the most "
        "relevant information for a query."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Natural-language search query.",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of results to return.",
                "default": 10,
            },
            "seed_limit": {
                "type": "integer",
                "description": "Maximum number of seed nodes for PageRank.",
                "default": 10,
            },
        },
        "required": ["query"],
    },
)

# ============================================================================
# Node Operations (5 read, 3 write)
# ============================================================================

_search_nodes = ToolDefinition(
    name="search_nodes",
    description=(
        "Hybrid search for graph nodes by name, property values, or semantic "
        "similarity. Returns ranked node matches."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query for node matching.",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of results.",
                "default": 10,
            },
            "template_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Filter results to specific template types.",
            },
        },
        "required": ["query"],
    },
)

_search_chunks = ToolDefinition(
    name="search_chunks",
    description=(
        "Hybrid search for document chunks using vector and keyword matching. "
        "Returns the most relevant text passages from indexed documents."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query for chunk matching.",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of chunks to return.",
                "default": 5,
            },
            "source_id": {
                "type": "string",
                "description": "Filter to chunks from a specific source document.",
            },
        },
        "required": ["query"],
    },
)

_get_node = ToolDefinition(
    name="get_node",
    description=(
        "Get a single graph node by its ID or by search query. Returns the "
        "full node with all properties."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "node_id": {
                "type": "string",
                "description": "Exact node ID to retrieve.",
            },
            "query": {
                "type": "string",
                "description": "Search query to find a node (used if node_id is not provided).",
            },
        },
        "required": [],
    },
)

_get_node_context = ToolDefinition(
    name="get_node_context",
    description=(
        "Get a node with its immediate neighborhood: 1-hop edges and "
        "optionally related document chunks. Useful for understanding "
        "a node's connections and supporting evidence."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "node_id": {
                "type": "string",
                "description": "ID of the node to get context for.",
            },
            "include_edges": {
                "type": "boolean",
                "description": "Include connected edges in the response.",
                "default": True,
            },
            "include_chunks": {
                "type": "boolean",
                "description": "Include related document chunks.",
                "default": False,
            },
            "edge_limit": {
                "type": "integer",
                "description": "Maximum number of edges to return.",
                "default": 20,
            },
            "chunk_limit": {
                "type": "integer",
                "description": "Maximum number of chunks to return.",
                "default": 5,
            },
        },
        "required": ["node_id"],
    },
)

_resolve_node = ToolDefinition(
    name="resolve_node",
    description=(
        "Resolve an alias, nickname, or alternative name to a canonical "
        "graph node. Useful when the exact node label is unknown."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Alias or name to resolve.",
            },
            "include_alternatives": {
                "type": "boolean",
                "description": "Include alternative candidate matches.",
                "default": True,
            },
            "max_alternatives": {
                "type": "integer",
                "description": "Maximum number of alternative candidates.",
                "default": 3,
            },
        },
        "required": ["query"],
    },
)

_create_node = ToolDefinition(
    name="create_node",
    description="Create a new graph node with a template, label, and properties.",
    input_schema={
        "type": "object",
        "properties": {
            "template_id": {
                "type": "string",
                "description": "ID of the template to use for the node.",
            },
            "label": {
                "type": "string",
                "description": "Display label for the node.",
            },
            "properties": {
                "type": "object",
                "description": "Key-value properties for the node.",
            },
        },
        "required": ["template_id", "label", "properties"],
    },
    write_only=True,
)

_update_node = ToolDefinition(
    name="update_node",
    description="Update a node's label or properties.",
    input_schema={
        "type": "object",
        "properties": {
            "node_id": {
                "type": "string",
                "description": "ID of the node to update.",
            },
            "label": {
                "type": "string",
                "description": "New label for the node.",
            },
            "properties": {
                "type": "object",
                "description": "Properties to update (merged with existing).",
            },
        },
        "required": ["node_id"],
    },
    write_only=True,
)

_delete_node = ToolDefinition(
    name="delete_node",
    description="Delete a graph node and its connected edges.",
    input_schema={
        "type": "object",
        "properties": {
            "node_id": {
                "type": "string",
                "description": "ID of the node to delete.",
            },
        },
        "required": ["node_id"],
    },
    write_only=True,
)

# ============================================================================
# Edge Operations (2 read, 1 write)
# ============================================================================

_list_edges = ToolDefinition(
    name="list_edges",
    description=(
        "List edges (relationships) in the graph with optional filtering "
        "by node. Returns edge metadata including source, target, and type."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "node_id": {
                "type": "string",
                "description": "Filter edges connected to this node.",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of edges to return.",
                "default": 100,
            },
        },
        "required": [],
    },
)

_get_node_edges = ToolDefinition(
    name="get_node_edges",
    description=(
        "Get edges for a specific node with direction and type filters. "
        "More granular than list_edges for exploring a node's relationships."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "node_id": {
                "type": "string",
                "description": "ID of the node to get edges for.",
            },
            "direction": {
                "type": "string",
                "enum": ["both", "outgoing", "incoming"],
                "description": "Filter by edge direction relative to the node.",
                "default": "both",
            },
            "edge_type": {
                "type": "string",
                "description": "Filter by edge type/label.",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of edges to return.",
                "default": 50,
            },
        },
        "required": ["node_id"],
    },
)

_create_edge = ToolDefinition(
    name="create_edge",
    description="Create a relationship (edge) between two graph nodes.",
    input_schema={
        "type": "object",
        "properties": {
            "source_node_id": {
                "type": "string",
                "description": "ID of the source (from) node.",
            },
            "target_node_id": {
                "type": "string",
                "description": "ID of the target (to) node.",
            },
            "template_id": {
                "type": "string",
                "description": "Edge template ID.",
                "default": "system_template_link",
            },
            "label": {
                "type": "string",
                "description": "Relationship label.",
                "default": "related_to",
            },
            "properties": {
                "type": "object",
                "description": "Key-value properties for the edge.",
            },
        },
        "required": ["source_node_id", "target_node_id"],
    },
    write_only=True,
)

# ============================================================================
# Template Operations (2 read, 2 write)
# ============================================================================

_list_templates = ToolDefinition(
    name="list_templates",
    description=(
        "List available templates with optional type filter. Templates "
        "define the schema for nodes and edges in the knowledge graph."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "template_type": {
                "type": "string",
                "enum": ["node", "edge"],
                "description": "Filter by template type.",
            },
        },
        "required": [],
    },
)

_search_templates = ToolDefinition(
    name="search_templates",
    description="Search templates by name or description.",
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query for template matching.",
            },
            "template_type": {
                "type": "string",
                "enum": ["node", "edge"],
                "description": "Filter by template type.",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of results.",
                "default": 5,
            },
        },
        "required": ["query"],
    },
)

_create_template = ToolDefinition(
    name="create_template",
    description="Create a new node or edge template that defines a schema for graph elements.",
    input_schema={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Template name.",
            },
            "template_type": {
                "type": "string",
                "enum": ["node", "edge"],
                "description": "Whether this is a node or edge template.",
            },
            "description": {
                "type": "string",
                "description": "Human-readable template description.",
                "default": "",
            },
            "properties": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "data_type": {"type": "string"},
                        "required": {"type": "boolean"},
                    },
                },
                "description": "Property definitions for the template.",
            },
        },
        "required": ["name", "template_type"],
    },
    write_only=True,
)

_delete_template = ToolDefinition(
    name="delete_template",
    description="Delete a template from the knowledge graph schema.",
    input_schema={
        "type": "object",
        "properties": {
            "template_id": {
                "type": "string",
                "description": "ID of the template to delete.",
            },
        },
        "required": ["template_id"],
    },
    write_only=True,
)

# ============================================================================
# Analytics (4 read tools)
# ============================================================================

_analyze_graph_structure = ToolDefinition(
    name="analyze_graph_structure",
    description=(
        "Analyze the knowledge graph structure: node/edge counts, community "
        "detection, PageRank centrality, and degree distribution."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "template_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Filter analysis to specific template types.",
            },
        },
        "required": [],
    },
)

_find_shortest_path = ToolDefinition(
    name="find_shortest_path",
    description=(
        "Find the shortest path between two nodes using breadth-first "
        "search. Returns the sequence of nodes and edges connecting them."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "source_id": {
                "type": "string",
                "description": "ID of the starting node.",
            },
            "target_id": {
                "type": "string",
                "description": "ID of the destination node.",
            },
        },
        "required": ["source_id", "target_id"],
    },
)

_find_similar_nodes = ToolDefinition(
    name="find_similar_nodes",
    description=(
        "Find nodes that are semantically similar to a given node using "
        "vector similarity search on node embeddings."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "node_id": {
                "type": "string",
                "description": "ID of the reference node.",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of similar nodes to return.",
                "default": 10,
            },
        },
        "required": ["node_id"],
    },
)

_traverse_path = ToolDefinition(
    name="traverse_path",
    description=(
        "Multi-hop breadth-first traversal from a starting node. Explores "
        "the graph neighborhood up to a specified depth."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "start_node_id": {
                "type": "string",
                "description": "ID of the node to start traversal from.",
            },
            "edge_types": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Filter traversal to specific edge types.",
            },
            "max_depth": {
                "type": "integer",
                "description": "Maximum traversal depth (hops).",
                "default": 2,
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of nodes to return.",
                "default": 50,
            },
        },
        "required": ["start_node_id"],
    },
)

# ============================================================================
# New MCP-only tools (2 read, 2 write)
# ============================================================================

_get_summary_context = ToolDefinition(
    name="get_summary_context",
    description=(
        "Retrieve and cluster document chunks relevant to a query for "
        "summarization. Returns chunks without making an LLM call, letting "
        "the MCP host perform the summarization."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Topic or question to gather context for.",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of chunks to return.",
                "default": 20,
            },
        },
        "required": ["query"],
    },
)

_get_document_status = ToolDefinition(
    name="get_document_status",
    description=(
        "Get the processing status of all documents: queued, in-progress, "
        "and completed uploads with their current pipeline stage."
    ),
    input_schema={
        "type": "object",
        "properties": {},
        "required": [],
    },
)

_add_document = ToolDefinition(
    name="add_document",
    description=(
        "Upload a file or URL for indexing into the knowledge graph. "
        "By default, only indexes the document for RAG search (fast, ~30s). "
        "After indexing completes, YOU should extract entities by calling: "
        "1) get_extraction_tasks (get chunk count and instructions), "
        "2) get_extraction_chunks (fetch text for each chunk group), "
        "3) submit_chunk_extraction (submit your E|/P|/R| extraction for each chunk), "
        "4) finalize_extraction (deduplicate and commit to graph). "
        "Set extract_entities=true ONLY if the server should use its own LLM instead."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": (
                    "Absolute path to the file to index, OR an HTTP/HTTPS "
                    "URL to fetch and index. URLs are scraped for content "
                    "automatically."
                ),
            },
            "extract_entities": {
                "type": "boolean",
                "description": (
                    "When false (default), only indexes for search — you drive "
                    "extraction yourself using the extraction tools. Set true to "
                    "have the server's LLM extract entities automatically."
                ),
                "default": False,
            },
            "extraction_depth": {
                "type": "string",
                "description": (
                    "Extraction depth: 'full' extracts all entities and "
                    "relationships, 'quick' extracts only key entities. "
                    "Defaults to 'full'."
                ),
                "enum": ["quick", "full"],
                "default": "full",
            },
            "domain": {
                "type": "string",
                "description": (
                    "Force a specific extraction domain. When omitted, "
                    "domain is auto-detected from content (recommended). "
                    "Only set this if the user explicitly requests a "
                    "specific domain."
                ),
            },
            "enable_normalization": {
                "type": "boolean",
                "description": (
                    "Clean OCR artifacts, fix encoding issues, and "
                    "normalize whitespace before chunking. Recommended "
                    "for PDFs and scanned documents. Defaults to true."
                ),
                "default": True,
            },
            "skip_duplicates": {
                "type": "boolean",
                "description": (
                    "Check content hash to skip uploading if identical "
                    "content already exists in the database."
                ),
                "default": False,
            },
            "wait": {
                "type": "boolean",
                "description": (
                    "Block until processing completes and return the "
                    "final result. Defaults to true. Set to false to "
                    "return immediately with a job ID for polling."
                ),
                "default": True,
            },
            "wait_timeout": {
                "type": "integer",
                "description": (
                    "Maximum seconds to wait for completion (only used "
                    "when wait=true). Defaults to 300 (5 minutes)."
                ),
                "default": 300,
            },
            "content": {
                "type": "string",
                "description": (
                    "Pre-processed text content. When provided, this text "
                    "is indexed directly — the file is not loaded or parsed. "
                    "Use when the MCP client has already extracted and "
                    "described the document content including images."
                ),
            },
            "enable_vision": {
                "type": "boolean",
                "description": (
                    "Enable vision processing for images in PDFs and image "
                    "files. Defaults to auto (uses vision if model configured). "
                    "Set false to skip. Only applies when using file_path."
                ),
            },
            "auto_confirm": {
                "type": "boolean",
                "description": (
                    "Skip the domain confirmation gate. When false (default) "
                    "an auto-detected domain parks the source as "
                    "'awaiting_confirmation' until you call confirm_extraction. "
                    "Set true to proceed immediately with the detected domain."
                ),
                "default": False,
            },
        },
        "required": [],
    },
    write_only=True,
)

_confirm_extraction = ToolDefinition(
    name="confirm_extraction",
    description=(
        "Confirm (and optionally override) the auto-detected extraction "
        "domain for a source parked at 'awaiting_confirmation', then start "
        "extraction. Omit 'domain' to accept the detected recommendation. "
        "Full extraction options may be overridden at confirmation time. "
        "Returns success and the resolved domain; a source not in "
        "'awaiting_confirmation' is reported as a conflict."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "file_id": {
                "type": "string",
                "description": "ID of the source parked at awaiting_confirmation.",
            },
            "domain": {
                "type": "string",
                "description": (
                    "Override the detected domain. Omit to accept the "
                    "recommended domain from detection."
                ),
            },
            "analysis_depth": {
                "type": "string",
                "description": "Extraction depth override.",
                "enum": ["quick", "full"],
            },
            "filtering_mode": {
                "type": "string",
                "description": (
                    "Cross-chunk filtering preset override (0-5 scale preset "
                    "name, e.g. 'balanced')."
                ),
            },
            "content_filtering": {
                "type": "boolean",
                "description": "Enable content-exclusion filtering override.",
            },
            "enable_direction_correction": {
                "type": "boolean",
                "description": "Enable relationship direction correction override.",
            },
            "protect_orphans": {
                "type": "boolean",
                "description": "Protect orphan entities from drop override.",
            },
            "enable_inverse_relationships": {
                "type": "boolean",
                "description": "Generate inverse relationships override.",
            },
            "max_entity_degree_override": {
                "type": "integer",
                "description": ("Cap on entity degree override. Must be a positive integer."),
            },
        },
        "required": ["file_id"],
    },
    write_only=True,
)

_wait_for_document = ToolDefinition(
    name="wait_for_document",
    description=(
        "Block until a document finishes processing (indexing or full "
        "extraction). Returns the final status when complete. Use this "
        "after add_document instead of polling get_document_status. "
        "NOTE: wait_for_document only sees in-memory processor jobs — it "
        "CANNOT see a source parked at 'awaiting_confirmation' (that state "
        "lives in the database). For parked documents, poll "
        "get_document_status and call confirm_extraction to proceed."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "file_id": {
                "type": "string",
                "description": "File ID returned by add_document.",
            },
            "timeout": {
                "type": "integer",
                "description": (
                    "Maximum seconds to wait before timing out. Defaults to 300 (5 minutes)."
                ),
                "default": 300,
            },
        },
        "required": ["file_id"],
    },
    write_only=True,
)

_remove_document = ToolDefinition(
    name="remove_document",
    description=(
        "Delete a source document and cascade-remove all associated graph "
        "nodes, edges, templates, search indexes, embeddings, citations, "
        "and chunks."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "source_id": {
                "type": "string",
                "description": "ID of the source document to remove.",
            },
        },
        "required": ["source_id"],
    },
    write_only=True,
)

# ============================================================================
# MCP extraction tools (3 read, 2 write)
# ============================================================================

_get_extraction_tasks = ToolDefinition(
    name="get_extraction_tasks",
    description=(
        "Get extraction planning metadata for an indexed source. Returns "
        "total chunk count, extraction instructions, format spec, and "
        "existing templates. No chunk text — keeps response small."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "source_id": {
                "type": "string",
                "description": "ID of the indexed source document.",
            },
            "force": {
                "type": "boolean",
                "description": ("Force re-extraction even if already extracted."),
                "default": False,
            },
        },
        "required": ["source_id"],
    },
)

_get_extraction_chunks = ToolDefinition(
    name="get_extraction_chunks",
    description=(
        "Fetch chunk text for specific chunk group indices. Each sub-agent "
        "calls this once for its assigned chunks. Returns text with "
        "numbered sentences for evidence-gated extraction."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "source_id": {
                "type": "string",
                "description": "ID of the source document.",
            },
            "chunk_indices": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "Chunk group indices to fetch text for.",
            },
        },
        "required": ["source_id", "chunk_indices"],
    },
)

_submit_chunk_extraction = ToolDefinition(
    name="submit_chunk_extraction",
    description=(
        "Submit raw extraction results for a single chunk group. Accepts "
        "E|/P| and R| pipe-delimited lines. Idempotent — re-submitting "
        "the same chunk overwrites. Returns submission progress."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "source_id": {
                "type": "string",
                "description": "ID of the source document.",
            },
            "chunk_group_index": {
                "type": "integer",
                "description": "Index of the chunk group being submitted.",
            },
            "entities_text": {
                "type": "string",
                "description": ("Pipe-delimited entity lines (E| and P| format)."),
            },
            "relationships_text": {
                "type": "string",
                "description": "Pipe-delimited relationship lines (R| format).",
            },
        },
        "required": [
            "source_id",
            "chunk_group_index",
            "entities_text",
            "relationships_text",
        ],
    },
    write_only=True,
)

_get_extraction_progress = ToolDefinition(
    name="get_extraction_progress",
    description=(
        "Check extraction submission progress. Returns submitted and "
        "missing chunk indices. Use to verify completion before finalizing "
        "or to identify chunks that need retry."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "source_id": {
                "type": "string",
                "description": "ID of the source document.",
            },
        },
        "required": ["source_id"],
    },
)

_finalize_extraction = ToolDefinition(
    name="finalize_extraction",
    description=(
        "Finalize MCP extraction: parse all submitted chunks, deduplicate "
        "entities, match templates, create citations, and commit nodes and "
        "edges to the knowledge graph. Requires all chunks to be submitted. "
        "Returns nodes_created, edges_created, templates_created, status, "
        "and the v7 quality scoring — quality_grade (0-100), quality_label "
        "(Poor/Fair/Good/Excellent), and a quality_breakdown object with "
        "the component scores (richness, avg_entity_quality, "
        "avg_relationship_quality, topology_score, density_score, "
        "structural_penalty, hub_skew, reciprocal_rate, coverage_score, ...) "
        "so the client can show the user why the grade is what it is "
        "without an extra round trip."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "source_id": {
                "type": "string",
                "description": "ID of the source document to finalize.",
            },
            "model": {
                "type": "string",
                "description": (
                    "Model used for extraction (e.g. 'claude-sonnet-4-6'). "
                    "Stored as the LLM model name for provenance tracking."
                ),
            },
        },
        "required": ["source_id"],
    },
    write_only=True,
)

# ============================================================================
# Tool registry
# ============================================================================

TOOL_DEFINITIONS: tuple[ToolDefinition, ...] = (
    # GraphRAG
    _graphrag_search,
    # Node operations (read)
    _search_nodes,
    _search_chunks,
    _get_node,
    _get_node_context,
    _resolve_node,
    # Node operations (write)
    _create_node,
    _update_node,
    _delete_node,
    # Edge operations (read)
    _list_edges,
    _get_node_edges,
    # Edge operations (write)
    _create_edge,
    # Template operations (read)
    _list_templates,
    _search_templates,
    # Template operations (write)
    _create_template,
    _delete_template,
    # Analytics
    _analyze_graph_structure,
    _find_shortest_path,
    _find_similar_nodes,
    _traverse_path,
    # MCP-specific read tools
    _get_summary_context,
    _get_document_status,
    # MCP-specific write tools
    _add_document,
    _wait_for_document,
    _remove_document,
    _confirm_extraction,
    # MCP extraction tools
    _get_extraction_tasks,
    _get_extraction_chunks,
    _submit_chunk_extraction,
    _get_extraction_progress,
    _finalize_extraction,
)


def get_tools_for_mode(mode: Literal["read", "write"]) -> tuple[ToolDefinition, ...]:
    """Return tool definitions filtered by access mode.

    Args:
        mode: ``"read"`` returns only read tools; ``"write"`` returns all tools.

    Returns:
        Filtered tuple of tool definitions.
    """
    if mode == "write":
        return TOOL_DEFINITIONS
    return tuple(t for t in TOOL_DEFINITIONS if not t.write_only)


# ============================================================================
# Maintenance-mode tools
#
# Advertised ONLY when the database is blocked on a pending schema upgrade
# (see chaoscypher_core.mcp.maintenance). These are deliberately NOT part of
# TOOL_DEFINITIONS, so they never appear in the normal read/write toolset and
# the normal server never routes them.
# ============================================================================

_upgrade_status = ToolDefinition(
    name="upgrade_status",
    description=(
        "Report the database's pending schema-upgrade state. Returns the "
        "migrations blocking normal use (each with its risk tier and a "
        "plain-language description), a human-readable message, the path of "
        "the most recent pre-upgrade backup, and the last-applied revisions. "
        "Call this first when a knowledge-graph tool says the database needs "
        "a one-time upgrade."
    ),
    input_schema={"type": "object", "properties": {}},
)

_apply_upgrade = ToolDefinition(
    name="apply_upgrade",
    description=(
        "Apply the pending schema migrations so the database can be used "
        "again. Safe and data-changing migrations apply automatically. "
        "Destructive (manual-tier) migrations — which may drop columns or "
        "force re-extraction — require confirm_destructive=true. A verified "
        "backup is taken before anything is applied. After a successful "
        "upgrade, reconnect the MCP server to access the knowledge-graph tools."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "confirm_destructive": {
                "type": "boolean",
                "description": (
                    "Set true to authorize applying destructive (manual-tier) "
                    "migrations that may drop data or force re-extraction. "
                    "Required only when a destructive migration is pending."
                ),
                "default": False,
            },
        },
    },
)

MAINTENANCE_TOOL_DEFINITIONS: tuple[ToolDefinition, ...] = (
    _upgrade_status,
    _apply_upgrade,
)


def get_maintenance_tools() -> tuple[ToolDefinition, ...]:
    """Return the degraded toolset advertised while the DB is blocked.

    Kept separate from :data:`TOOL_DEFINITIONS` so maintenance tools never
    leak into the normal read/write toolset.
    """
    return MAINTENANCE_TOOL_DEFINITIONS
