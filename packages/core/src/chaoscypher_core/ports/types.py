# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Typed dictionaries for storage protocol return values.

These TypedDicts replace raw ``dict[str, Any]`` in the most commonly
used storage protocol methods.  They serve two purposes:

1. **IDE autocomplete** — editors show available keys when accessing
   the result of a storage protocol call.
2. **Static analysis** — mypy (and similar) catch ``result.name``
   (attribute access on a dict) as a type error, because TypedDict
   only supports subscript access (``result["name"]``).

Usage:
    from chaoscypher_core.ports.types import WorkflowDict

    wf: WorkflowDict = storage.get_workflow(wf_id)
    print(wf["name"])  # OK — IDE shows all valid keys
    print(wf.name)     # type error — caught by mypy

Note:
    Adapter implementations can return plain dicts — TypedDict is
    structurally compatible. The type annotations only affect callers.

    Fields are derived from SQLModel entity ``model_dump(mode="json")``
    output.  ``total=False`` is used because list endpoints may omit
    large columns via ``load_only()``.
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict


# ============================================================================
# Boundary type aliases (cross-package)
# ============================================================================


# Canonical filtering-mode preset names (most → least restrictive).
# Source of truth for valid preset names is
# ``chaoscypher_core.services.sources.engine.extraction.utils.filtering_config._PRESET_OVERRIDES``
# — the keys of that dict and this Literal must stay in sync (regression
# enforced by ``test_filtering_mode_matches_preset_overrides``). Used at
# every public boundary that accepts a filtering preset: Cortex request
# models (``UrlImportRequest``, multipart Form params), CLI ``click.Choice``
# (derived via ``get_args(FilteringMode)``), and the engine's
# ``resolve_filtering_config(mode=...)`` parameter.
FilteringMode = Literal[
    "maximum",
    "strict",
    "balanced",
    "lenient",
    "minimal",
    "unfiltered",
]


# ============================================================================
# Workflow Dicts
# ============================================================================


class WorkflowDict(TypedDict, total=False):
    """Dict shape returned by WorkflowStorageProtocol methods.

    Source entity: ``adapters/sqlite/models.py::Workflow``
    """

    id: str
    database_name: str
    name: str
    description: str | None
    category: str | None
    is_system: bool
    is_active: bool
    expose_as_ai_tool: bool
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None
    allow_parallel_execution: bool
    timeout_seconds: int | None
    max_retries: int
    tags: list[str] | None
    icon: str | None
    version: str
    created_by: str | None
    created_at: str
    updated_at: str
    last_executed_at: str | None


class WorkflowStepDict(TypedDict, total=False):
    """Dict shape returned by workflow step storage methods.

    Source entity: ``adapters/sqlite/models.py::WorkflowStep``
    """

    id: str
    workflow_id: str
    step_number: int
    name: str
    description: str | None
    tool_type: str
    tool_id: str
    configuration: dict[str, Any]
    condition: dict[str, Any] | None
    retry_on_failure: bool
    timeout_seconds: int | None
    depends_on: list[str] | None
    continue_on_error: bool
    thinking_mode: str | None
    created_at: str
    updated_at: str


# ============================================================================
# Source Dicts
# ============================================================================


class SourceDict(TypedDict, total=False):
    """Dict shape returned by SourceStorageProtocol methods.

    Source entity: ``adapters/sqlite/models.py::Source``

    Note: This entity has 80+ fields. Only the most commonly accessed
    fields are listed here. The full dict may contain additional keys
    (LLM metrics, quality cache, extraction state, etc.).

    Use this TypedDict for autocomplete/light type-checking only. Callers
    must not assume the dict is closed-shape — additional keys produced by
    the SQL projection (or future model fields) will pass through silently.
    For a strict shape, project explicitly via ``load_only(...)`` and
    access by string key.
    """

    id: str
    database_name: str
    filename: str
    filepath: str
    file_type: str | None
    file_size: int | None
    content_hash: str | None
    title: str | None
    source_type: str | None
    origin_url: str | None
    status: str
    enabled: bool
    error_message: str | None
    error_stage: str | None
    chunk_count: int
    embedding_model: str | None
    embedding_dimensions: int | None
    extraction_depth: str
    extraction_domain: str | None
    forced_domain: str | None
    commit_nodes_created: int
    commit_edges_created: int
    commit_templates_created: int
    user_metadata: dict[str, Any] | None
    created_at: str
    updated_at: str


# ============================================================================
# Stage Progress Dicts
# ============================================================================


class StageProgressDict(TypedDict, total=False):
    """Dict shape returned by StageProgressStorageProtocol._fetch_stage_progress().

    Backed by the ``llm_stage_progress`` table (Alembic migration 0030).
    ``total=False`` because list endpoints may omit columns and because
    all fields except ``total`` and ``processed`` can be NULL at various
    lifecycle stages.
    """

    total: int
    processed: int
    avg_ms: int | None
    started_at: str | None
    last_activity: str | None
    completed_at: str | None
    extras: dict[str, Any] | None


# ============================================================================
# Chat Dicts
# ============================================================================


class ChatDict(TypedDict, total=False):
    """Dict shape returned by ChatStorageProtocol methods.

    Source entity: ``adapters/sqlite/models.py::Chat``
    """

    id: str
    database_name: str
    user_id: int | None
    title: str
    status: str
    message_count: int
    source_ids: list[str] | None
    created_at: str
    updated_at: str


class MessageDict(TypedDict, total=False):
    """Dict shape returned by ChatStorageProtocol.get_messages().

    Source entity: ``adapters/sqlite/models.py::ChatMessage``
    """

    id: str
    chat_id: str
    role: str
    content: str
    timestamp: str
    extra_metadata: dict[str, Any] | None


# ============================================================================
# Trigger Dicts
# ============================================================================


class TriggerDict(TypedDict, total=False):
    """Dict shape returned by TriggerStorageProtocol methods.

    Source entity: ``adapters/sqlite/models.py::Trigger``
    """

    id: str
    database_name: str
    user_id: int | None
    name: str
    event_source: str
    filters: dict[str, Any]
    workflow_id: str
    workflow_inputs: dict[str, Any] | None
    enabled: bool
    priority: int
    created_at: str
    updated_at: str


# ============================================================================
# Tool Dicts
# ============================================================================


class SystemToolDict(TypedDict, total=False):
    """Dict shape returned by ToolStorageProtocol system tool methods.

    Source entity: ``adapters/sqlite/models.py::SystemTool``
    """

    id: str
    category: str
    icon: str | None
    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    version: str
    is_active: bool
    created_at: str
    updated_at: str


class UserToolDict(TypedDict, total=False):
    """Dict shape returned by ToolStorageProtocol user tool methods.

    Source entity: ``adapters/sqlite/models.py::UserTool``
    """

    id: str
    database_name: str
    user_id: int | None
    name: str
    description: str | None
    system_tool_id: str
    configuration: dict[str, Any]
    tags: list[str] | None
    is_active: bool
    created_by: str | None
    created_at: str
    updated_at: str


# Unified alias for protocol methods that return either tool type
ToolDict = SystemToolDict | UserToolDict


__all__ = [
    "ChatDict",
    "FilteringMode",
    "MessageDict",
    "SourceDict",
    "StageProgressDict",
    "SystemToolDict",
    "ToolDict",
    "TriggerDict",
    "UserToolDict",
    "WorkflowDict",
    "WorkflowStepDict",
]
