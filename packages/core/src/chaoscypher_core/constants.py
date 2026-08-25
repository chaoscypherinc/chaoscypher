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
# Make an already-committed IMPORTED source searchable: re-embed its chunks
# (CCX bundles carry no chunk vectors) and push node + chunk vectors into the
# search index — without re-running extraction or regressing its committed
# status (the reason OP_EMBED_CHUNKS can't be reused here). LLM-bound (embeds).
OP_INDEX_IMPORTED_SOURCE = "index_imported_source"
# Make a KNOWLEDGE-ONLY import's nodes searchable: re-embed + index the imported
# nodes directly off an id list. Knowledge-only imports (lexicon, CLI) land a
# graph with no source/chunks, so OP_INDEX_IMPORTED_SOURCE doesn't apply. Same
# LLM queue (re-embeds node text).
OP_INDEX_IMPORTED_NODES = "index_imported_nodes"
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
    OP_INDEX_IMPORTED_SOURCE: QUEUE_LLM,
    OP_INDEX_IMPORTED_NODES: QUEUE_LLM,
    OP_CHAT_BACKGROUND: QUEUE_LLM,
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


# -----------------------------------------------------------------------------
# Retry-on-crash policy — single source of truth for op-name -> retry_on_crash,
# correct in ANY process, including one that never registered a single
# handler.
#
# QueueClient._retry_policy was historically populated only as a side effect
# of register_handlers(), which only Neuron ever calls (see
# chaoscypher_neuron.setup). Cortex holds the same shared queue_client
# singleton and drives POST /queue/reconcile from it, but never registers
# handlers, so QueueClient.get_retry_policy() always answered False in the
# Cortex process — permanently failing every task abandoned by a crashed
# worker regardless of the handler's actual policy (entry 566, fixed
# 2026-08-15). QueueClient.get_retry_policy() now falls back to this table
# for any (queue, operation) it has no in-process registration for.
#
# QueueClient.register_handlers() also validates every incoming HandlerSpec's
# retry_on_crash against this table whenever the operation is present here:
# a contradicting value raises TypeError at registration time, so drift
# between a service's HandlerSpec and this table fails loudly at Neuron
# startup instead of silently corrupting reconcile decisions later.
#
# Decision rule: True only for handlers whose work is safe to re-run —
# idempotent via a DB checkpoint, an id-keyed upsert, or a terminal-status
# guard. See each True handler's own HandlerSpec call site for its specific
# idempotency argument. Every operation in OPERATION_QUEUE_ROUTING must have
# an entry here (enforced by
# test_retry_policy_canonical_table.test_canonical_table_covers_every_routed_operation);
# operations absent from both tables (ad hoc/test op names) keep the safe
# False default.
# -----------------------------------------------------------------------------
OPERATION_RETRY_ON_CRASH: dict[str, bool] = {
    # QUEUE_LLM
    "chat_completion": False,  # user-facing chat turn, not idempotent
    "tool_execution": False,  # LLM tool-call dispatch, not idempotent
    OP_EXTRACT_CHUNK: True,  # DB short-circuit makes re-running safe
    OP_FINALIZE_EXTRACTION: True,  # status short-circuit makes re-running safe
    OP_VISION_PAGE: True,  # row-status guard + single-terminal-observation guarantee
    OP_EMBED_CHUNKS: True,  # embedded_at checkpoint
    OP_INDEX_IMPORTED_SOURCE: True,  # embedded_at checkpoint + id-keyed vector upserts
    OP_INDEX_IMPORTED_NODES: True,  # right-dim-vector skip + id-keyed upserts
    OP_CHAT_BACKGROUND: False,  # not idempotent
    "regenerate_template_embeddings": False,  # not idempotent
    # QUEUE_OPERATIONS
    "bulk_nodes": False,  # bare-callable registration; no idempotency guard declared
    "bulk_edges": False,  # bare-callable registration; no idempotency guard declared
    "bulk_templates": False,  # bare-callable registration; no idempotency guard declared
    "export_graph": True,  # pure read, no database writes or side effects
    "export_by_sources": True,  # pure read, no database writes or side effects
    OP_IMPORT_CCX: False,  # bare-callable registration; no idempotency guard declared
    OP_IMPORT_COMMIT: True,  # resumable commit pipeline
    OP_IMPORT_ANALYSIS: True,  # resumable analysis pipeline
    OP_INDEX_DOCUMENT: True,  # resumable indexing pipeline
    "lexicon_import": False,  # bare-callable registration; no idempotency guard declared
    OP_FETCH_URL: False,  # bare-callable registration; no idempotency guard declared
    "execute_workflow": True,  # completed/failed executions skip re-execution
    "execute_step": True,  # stateless step handler, safe to re-run
    "recalculate_quality_scores": False,  # bare-callable registration; no idempotency guard declared
    OP_REBUILD_SEARCH_INDEXES: False,  # bare-callable registration; no idempotency guard declared
    OP_RESET_KNOWLEDGE_BASE: False,  # bare-callable registration; no idempotency guard declared
    OP_RESET_ALL: False,  # bare-callable registration; no idempotency guard declared
    OP_GRAPH_CLEANUP: False,  # bare-callable registration; no idempotency guard declared
    OP_CLEANUP_ORPHANS: False,  # bare-callable registration; no idempotency guard declared
    OP_BUILD_GRAPH_SNAPSHOT: False,  # bare-callable registration; no idempotency guard declared
    OP_VISION_FINALIZE: True,  # aggregation + terminal state transition only
}
