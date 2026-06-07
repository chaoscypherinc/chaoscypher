# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Shared Constants.

System-wide constants used across features.
"""

# Queue names
QUEUE_LLM = "llm"
QUEUE_OPERATIONS = "operations"

# Queue operation types
OP_IMPORT_INDEXING = "import_indexing"
OP_IMPORT_ANALYSIS = "import_analysis"
OP_EXTRACT_CHUNK = "extract_chunk"
OP_FINALIZE_EXTRACTION = "finalize_extraction"
OP_VISION_PAGE = "vision_page"
OP_VISION_FINALIZE = "vision_finalize"
OP_IMPORT_COMMIT = "import_commit"
OP_IMPORT_CCX = "import_ccx"
OP_INDEX_DOCUMENT = "index_document"
OP_EMBED_CHUNKS = "embed_chunks"
# URL imports moved to the queue 2026-04-28: the /sources/url route used to
# block its connection on a synchronous WebScraper fetch. Now the route
# enqueues OP_FETCH_URL and returns 202 immediately; the worker fetches and
# feeds the bytes through the standard upload_file pipeline.
OP_FETCH_URL = "fetch_url"
OP_REBUILD_SEARCH_INDEXES = "rebuild_search_indexes"
OP_CHAT_BACKGROUND = "chat_background"
# Reset / cleanup operations — moved to queue + 202 per 2026-04-18 decision 3.
# These used to block the API synchronously (could take 30s+ for knowledge-
# base wipes); now they dispatch to a worker handler and return a task id.
OP_RESET_KNOWLEDGE_BASE = "reset_knowledge_base"
OP_RESET_ALL = "reset_all"
OP_GRAPH_CLEANUP = "graph_cleanup"
OP_CLEANUP_ORPHANS = "cleanup_orphans"
OP_BUILD_GRAPH_SNAPSHOT = "build_graph_snapshot"

# System template IDs
SYSTEM_TEMPLATE_IDS = [
    "system_workflow",
    "system_workflow_step",
    "system_lens",
]


# -----------------------------------------------------------------------------
# Queue routing — single source of truth for op-name → queue mapping.
#
# Every handler registered via queue_client.register_handlers(queue, {op: fn})
# MUST appear here, and the queue in the call MUST match the value below.
# Enforced by CC044 in scripts/lint_claude_rules.py.
#
# Decision rule: if the handler calls an LLM provider, generates embeddings,
# or otherwise consumes GPU/LLM capacity, it goes on QUEUE_LLM. Everything
# else goes on QUEUE_OPERATIONS. See the queue routing constants and tests.
# -----------------------------------------------------------------------------
OPERATION_QUEUE_ROUTING: dict[str, str] = {
    # QUEUE_LLM — LLM/embedding work, 1 concurrent worker, blocking.
    "chat_completion": QUEUE_LLM,
    "tool_execution": QUEUE_LLM,
    OP_EXTRACT_CHUNK: QUEUE_LLM,
    OP_FINALIZE_EXTRACTION: QUEUE_LLM,
    OP_VISION_PAGE: QUEUE_LLM,
    OP_EMBED_CHUNKS: QUEUE_LLM,
    "chat_background": QUEUE_LLM,
    "regenerate_template_embeddings": QUEUE_LLM,
    # QUEUE_OPERATIONS — I/O-bound work, 8 concurrent workers, parallel.
    "bulk_nodes": QUEUE_OPERATIONS,
    "bulk_edges": QUEUE_OPERATIONS,
    "bulk_templates": QUEUE_OPERATIONS,
    "export_graph": QUEUE_OPERATIONS,
    "export_by_sources": QUEUE_OPERATIONS,
    OP_IMPORT_CCX: QUEUE_OPERATIONS,
    OP_IMPORT_COMMIT: QUEUE_OPERATIONS,
    OP_IMPORT_ANALYSIS: QUEUE_OPERATIONS,
    OP_INDEX_DOCUMENT: QUEUE_OPERATIONS,
    OP_FETCH_URL: QUEUE_OPERATIONS,
    "lexicon_import": QUEUE_OPERATIONS,
    "execute_workflow": QUEUE_OPERATIONS,
    "execute_step": QUEUE_OPERATIONS,
    "recalculate_quality_scores": QUEUE_OPERATIONS,
    OP_REBUILD_SEARCH_INDEXES: QUEUE_OPERATIONS,
    OP_RESET_KNOWLEDGE_BASE: QUEUE_OPERATIONS,
    OP_RESET_ALL: QUEUE_OPERATIONS,
    OP_GRAPH_CLEANUP: QUEUE_OPERATIONS,
    OP_CLEANUP_ORPHANS: QUEUE_OPERATIONS,
    OP_BUILD_GRAPH_SNAPSHOT: QUEUE_OPERATIONS,
    OP_VISION_FINALIZE: QUEUE_OPERATIONS,
}
