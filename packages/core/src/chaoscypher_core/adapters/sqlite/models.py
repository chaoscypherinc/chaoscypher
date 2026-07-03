# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""SQLModel table definitions for ChaosCypher Knowledge Engine.

All tables use proper Python types (datetime, bool) instead of TEXT/INTEGER.
JSON fields use SQLModel's JSON type for structured data.
"""

from datetime import UTC, datetime
from typing import Any, Optional

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlmodel import JSON, Column, Field, Relationship, SQLModel


# pragma: ensure edit applied


# ================================
# Workflow System Models
# ================================


class Workflow(SQLModel, table=True):
    """Workflow definitions table."""

    __tablename__ = "workflows"
    __table_args__ = (
        UniqueConstraint("database_name", "name", name="uq_workflows_database_name_name"),
    )

    id: str = Field(primary_key=True)
    database_name: str = Field(index=True)
    name: str
    description: str | None = None
    category: str | None = None
    is_system: bool = Field(default=False)
    is_active: bool = Field(default=True)
    expose_as_ai_tool: bool = Field(default=False)
    input_schema: dict[str, Any] = Field(sa_column=Column(JSON))
    output_schema: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    allow_parallel_execution: bool = Field(default=True)
    timeout_seconds: int | None = None
    max_retries: int = Field(default=0)
    tags: list[str] | None = Field(default=None, sa_column=Column(JSON))
    icon: str | None = None
    version: str = Field(default="1.0.0")
    created_by: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_executed_at: datetime | None = None

    # Relationships
    steps: list["WorkflowStep"] = Relationship(
        back_populates="workflow", sa_relationship_kwargs={"cascade": "all, delete-orphan"}
    )
    statistics: Optional["WorkflowStatistics"] = Relationship(
        back_populates="workflow", sa_relationship_kwargs={"cascade": "all, delete-orphan"}
    )


class WorkflowStep(SQLModel, table=True):
    """Workflow steps table."""

    __tablename__ = "workflow_steps"

    id: str = Field(primary_key=True)
    workflow_id: str = Field(foreign_key="workflows.id", index=True)
    step_number: int
    name: str
    description: str | None = None
    tool_type: str  # 'system' or 'user'
    tool_id: str
    configuration: dict[str, Any] = Field(sa_column=Column(JSON))
    condition: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    retry_on_failure: bool = Field(default=False)
    timeout_seconds: int | None = None
    depends_on: list[str] | None = Field(default=None, sa_column=Column(JSON))
    continue_on_error: bool = Field(default=False)
    thinking_mode: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    # Relationships
    workflow: Workflow = Relationship(back_populates="steps")


class WorkflowStatistics(SQLModel, table=True):
    """Workflow execution statistics table."""

    __tablename__ = "workflow_statistics"

    workflow_id: str = Field(foreign_key="workflows.id", primary_key=True)
    total_executions: int = Field(default=0)
    successful_executions: int = Field(default=0)
    failed_executions: int = Field(default=0)
    cancelled_executions: int = Field(default=0)
    avg_duration_ms: int = Field(default=0)
    min_duration_ms: int | None = None
    max_duration_ms: int | None = None
    last_execution_at: datetime | None = None
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    # Relationships
    workflow: Workflow = Relationship(back_populates="statistics")


class WorkflowExecution(SQLModel, table=True):
    """Workflow execution records table."""

    __tablename__ = "workflow_executions"

    id: str = Field(primary_key=True)
    workflow_id: str = Field(foreign_key="workflows.id", index=True)
    triggered_by: str  # 'manual', 'trigger', 'api', 'parent_workflow'
    trigger_id: str | None = None
    parent_execution_id: str | None = None
    inputs: dict[str, Any] = Field(sa_column=Column(JSON))
    outputs: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    # Values validated via WorkflowExecutionStatus StrEnum at the service layer.
    status: str = Field(
        sa_column=Column(
            String,
            nullable=False,
        ),
    )  # 'pending', 'running', 'completed', 'failed', 'cancelled'
    current_step_id: str | None = None
    failed_step_id: str | None = None
    error_message: str | None = None
    duration_ms: int | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    started_at: datetime | None = None
    completed_at: datetime | None = None

    # Relationships
    step_executions: list["WorkflowStepExecution"] = Relationship(back_populates="execution")


class WorkflowStepExecution(SQLModel, table=True):
    """Workflow step execution records table."""

    __tablename__ = "workflow_step_executions"

    id: str = Field(primary_key=True)
    execution_id: str = Field(foreign_key="workflow_executions.id", index=True)
    step_id: str = Field(
        sa_column=Column(
            String,
            ForeignKey("workflow_steps.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
    )
    inputs: dict[str, Any] = Field(sa_column=Column(JSON))
    outputs: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    # Values: 'pending', 'running', 'completed', 'failed', 'skipped'.
    # Validated via a StrEnum at the service layer (not bound to a DB CheckConstraint).
    status: str = Field(
        sa_column=Column(
            String,
            nullable=False,
        ),
    )  # 'pending', 'running', 'completed', 'failed', 'skipped'
    error_message: str | None = None
    duration_ms: int | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    started_at: datetime | None = None
    completed_at: datetime | None = None

    # Relationships
    execution: WorkflowExecution = Relationship(back_populates="step_executions")


# ================================
# Tools System Models
# ================================


class SystemTool(SQLModel, table=True):
    """System tools registry table."""

    __tablename__ = "system_tools"

    id: str = Field(primary_key=True)
    category: str = Field(index=True)
    icon: str | None = Field(default=None)
    name: str
    description: str
    input_schema: dict[str, Any] = Field(sa_column=Column(JSON))
    output_schema: dict[str, Any] = Field(sa_column=Column(JSON))
    version: str = Field(default="1.0.0")
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    # Relationships
    user_tools: list["UserTool"] = Relationship(back_populates="system_tool")


class UserTool(SQLModel, table=True):
    """User-configured tools table."""

    __tablename__ = "user_tools"

    id: str = Field(primary_key=True)
    database_name: str = Field(index=True)
    user_id: int | None = Field(
        default=None, index=True
    )  # Multi-tenancy (no FK - users table removed)
    name: str
    description: str | None = None
    system_tool_id: str = Field(foreign_key="system_tools.id", index=True)
    configuration: dict[str, Any] = Field(sa_column=Column(JSON))
    tags: list[str] | None = Field(default=None, sa_column=Column(JSON))
    is_active: bool = Field(default=True)
    created_by: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    # Relationships
    system_tool: SystemTool = Relationship(back_populates="user_tools")


class ToolStatistics(SQLModel, table=True):
    """Tool execution statistics table."""

    __tablename__ = "tool_statistics"
    __table_args__ = (UniqueConstraint("tool_type", "tool_id", name="uix_tool_type_id"),)

    tool_type: str = Field(primary_key=True)  # 'system' or 'user'
    tool_id: str = Field(primary_key=True)
    total_calls: int = Field(default=0)
    successful_calls: int = Field(default=0)
    failed_calls: int = Field(default=0)
    avg_execution_ms: int = Field(default=0)
    last_called_at: datetime | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


# ================================
# Triggers System Models
# ================================


class Trigger(SQLModel, table=True):
    """Event triggers table."""

    __tablename__ = "triggers"

    id: str = Field(primary_key=True)
    database_name: str = Field(index=True)
    user_id: int | None = Field(
        default=None, index=True
    )  # Multi-tenancy (no FK - users table removed)
    name: str
    event_source: str = Field(index=True)  # 'node.created', 'node.updated', etc.
    filters: dict[str, Any] = Field(sa_column=Column(JSON))
    workflow_id: str = Field(
        sa_column=Column(
            String,
            ForeignKey("workflows.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
    )
    workflow_inputs: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    enabled: bool = Field(default=True)
    priority: int = Field(
        default=0,
        description=(
            "Dispatch order. Higher fires first; ties broken by created_at ASC. "
            "See TriggerService.list_triggers."
        ),
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class TriggerExecutionRow(SQLModel, table=True):
    """Trigger execution history table."""

    __tablename__ = "trigger_executions"

    id: str = Field(primary_key=True)
    trigger_id: str = Field(
        sa_column=Column(
            String,
            ForeignKey("triggers.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
    )
    workflow_execution_id: str = Field(
        sa_column=Column(
            String,
            ForeignKey("workflow_executions.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
    )
    event_data: dict[str, Any] = Field(sa_column=Column(JSON))
    # Values validated via TriggerExecutionStatus StrEnum at the service layer.
    status: str = Field(
        sa_column=Column(
            String,
            nullable=False,
        ),
    )  # 'success', 'failed'
    error_message: str | None = None
    executed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


# ================================
# Source Models (Unified)
# ================================


class SourceRow(SQLModel, table=True):
    """Unified source model - single record from upload through committed.

    One document = One ID = One record throughout lifecycle.

    Status Lifecycle (text-only):
        pending → indexing → indexed → extracting → extracted → committing → committed
                     ↓              ↓              ↓
                   error          error          error

    Status Lifecycle (image-bearing, vision pipeline enabled):
        pending → indexing → vision_pending → indexed → extracting → …

    Stage Completion Flags:
        - indexing_complete: RAG indexing done (chunks + embeddings)
        - extraction_complete: Entity/relationship extraction done
        - commit_complete: Graph commit done

    Visibility Control:
        - enabled: Controls visibility in knowledge graph and AI chats
    """

    __tablename__ = "sources"
    # Plain-ascending composite index. It still serves the paginated
    # ``WHERE database_name = ? ORDER BY created_at DESC`` read — SQLite
    # reverse-scans the index, so no filesort. Kept ascending (not a DESC
    # expression index) so it matches what migration 0044 creates AND so
    # Alembic autogenerate can reflect/compare it on SQLite (an expression
    # index would produce a perpetual false diff). Agreement pinned by
    # test_migration_fidelity.
    __table_args__ = (
        Index("ix_sources_database_name_created_at_desc", "database_name", "created_at"),
    )

    # ========== Core Identity ==========
    id: str = Field(primary_key=True)
    database_name: str = Field(index=True)

    # ========== File Metadata (set on upload) ==========
    filename: str
    filepath: str
    file_type: str | None = None
    file_size: int | None = None
    content_hash: str | None = Field(default=None, index=True)

    # ========== Source Metadata ==========
    title: str | None = None  # Human-readable name (defaults to filename)
    source_type: str | None = None  # "pdf", "text", "csv", "webpage", etc.
    origin_url: str | None = None  # Original location (URL, file path, etc.)

    # CCX 3.0 stable identity: the IRI anchoring this source to its identity in
    # an exported CCX package, so re-imports upsert by IRI. Added by migration
    # 0003. Nullable: legacy rows and not-yet-exported sources stay NULL.
    ccx_iri: str | None = Field(default=None, index=True)

    # CCX 3.0 full-text store: the extracted plain text of the source, so an
    # exported CCX package can carry it without re-deriving from chunks. Added
    # by migration 0004. Nullable: not yet populated for legacy rows.
    full_text: str | None = Field(default=None, sa_column=Column(Text))

    # Versioning (for future re-import support)
    version: int = Field(default=1)
    parent_id: str | None = Field(foreign_key="sources.id", default=None)

    # ========== Lifecycle Status ==========
    # Note: Field is 'status' in code but maps to 'processing_status' column in DB for backwards compatibility.
    # Values validated via SourceStatus StrEnum at the service layer.
    status: str = Field(
        default="pending",
        sa_column=Column(
            "processing_status",
            String,
            default="pending",
            index=True,
        ),
    )  # 'pending', 'indexing', 'vision_pending', 'indexed', 'extracting', 'mcp_extracting', 'extracted', 'committing', 'committed', 'error'

    # Stage Completion Flags
    indexing_complete: bool = Field(default=False)
    extraction_complete: bool = Field(default=False)
    commit_complete: bool = Field(default=False)

    # Visibility toggle (controls visibility in AI/search)
    enabled: bool = Field(default=True)

    # ========== Error Tracking ==========
    error_message: str | None = None
    error_stage: str | None = None  # 'indexing' | 'extraction' | 'commit'
    last_failed_stage: str | None = Field(
        default=None
    )  # prior error_stage before recovery_exhausted

    # ========== Resumability Observability ==========
    last_activity_at: datetime | None = None
    recovery_attempts: int = Field(default=0)

    # ========== User-Controlled Pause ==========
    # Orthogonal to `status`: a paused source retains its underlying
    # pipeline status so it can resume from the same checkpoint.
    # Handlers check this flag and return {"skipped": "paused"} without
    # consuming retry budget. See
    # source pause and graceful shutdown design notes
    is_paused: bool = Field(
        default=False,
        description="User-requested per-source pause flag.",
    )
    paused_at: datetime | None = Field(
        default=None,
        description="Timestamp when the source was paused.",
    )
    paused_reason: str | None = Field(
        default=None,
        description="Optional user-supplied pause reason.",
    )

    # ========== Indexing Stage (RAG - Document Chunking + Embeddings) ==========
    indexing_started_at: datetime | None = None
    indexing_completed_at: datetime | None = None
    chunk_count: int = Field(default=0)  # Number of chunks created
    total_content_length: int = Field(default=0)  # Sum of all chunk content lengths
    embedding_model: str | None = None
    embedding_dimensions: int | None = None
    # Vision pipeline (2026-05-13 PR 2): QualityCounter.VISION_PAGES_TRUNCATED.
    # Incremented by the per-page vision handler when finish_reason == 'length'
    # (i.e. the vision_max_output_tokens cap fired and the description was
    # truncated). Partial content is still saved — this counter surfaces the
    # truncation rate in the Data Quality UI tab.
    # NOTE: this column is added to the live DB by migration 0033 (Task 3
    # gap-fix). SQLModel.metadata.create_all() covers test databases. The
    # legacy vision columns (vision_pages_failed, vision_failed_pages,
    # loader_pdf_failed_pages) were dropped by migration 0034 (Task 14);
    # their replacement is vision_page_descriptions filtered by status.
    vision_pages_truncated: int = Field(default=0)

    # Vision sampling (Wave 4-5, 2026-05-23): QualityCounter.VISION_PAGES_SAMPLED_QUICK_MODE.
    # Incremented by the indexing handler when ``extraction_depth='quick'``
    # makes the work-queue builder skip image pages. The increment value is
    # ``total_image_pages - sampled``; sampling policy lives next to the
    # builder in indexing_handler.py and respects LoaderSettings.
    # vision_quick_sample_max_pages. The Processing tab vision tile reads
    # this column to display "Quick mode: 12 of 400 pages processed (388
    # skipped)" so a Quick run does not read as a partial vision failure.
    # Live DB column added by migration 0045 (companion to this field).
    vision_pages_sampled_quick_mode: int = Field(default=0)

    # Per-chunk rerun feature (migration 0037, 2026-05-15). Bumped once per
    # successful click on the "Rerun this chunk" button. Source-scoped
    # QualityCounter; surfaces in the Processing tab.
    chunks_rerun_total: int = Field(default=0)

    # ========== Extraction Stage (Entity/Relationship Analysis) ==========
    extraction_started_at: datetime | None = None
    extraction_completed_at: datetime | None = None
    extraction_depth: str = Field(default="full")  # 'quick' | 'full'
    extraction_entities_count: int = Field(default=0)
    extraction_relationships_count: int = Field(default=0)
    extraction_domain: str | None = (
        None  # Domain used for extraction (e.g., 'technical', 'generic')
    )
    extraction_domain_auto: bool = Field(
        default=True
    )  # True if auto-detected, False if user-selected

    # Domain plugin provenance (migration 0046, 2026-05-24). Stamped at
    # extraction finalize from the resolved domain's registry fingerprint.
    # NULL for pre-feature sources, generic/auto-no-match, and user domains
    # not discovered by the no-settings fingerprint lookup. domain_version is
    # shown next to the domain in the UI tooltip + CLI; domain_content_hash is
    # compared against the live plugin hash at read time to derive the
    # "plugin changed since extraction" flag (never shown raw).
    domain_version: str | None = Field(default=None)
    domain_content_hash: str | None = Field(default=None)

    # User-controlled extraction settings (set on upload, used during extraction)
    forced_domain: str | None = Field(default=None)  # User-selected domain (None = auto-detect)

    # Whether the upload flow should auto-queue analysis after indexing
    # completes. Also consulted by the source reconciler when
    # classifying an 'indexed' source — a source with auto_analyze=False
    # is treated as healthy (user wants manual control) and is not
    # re-dispatched to analysis.
    auto_analyze: bool = Field(default=True)

    # --- Domain confirmation gate (migration 0049, 2026-05-28) -------------
    # The pre-flight gate parks an auto-detected source at
    # AWAITING_CONFIRMATION before the expensive chunk extraction commits, so
    # a human can verify/override the detected domain. These three columns are
    # the durable gate state read by ``gate_decision`` (Phase 3); detection
    # never re-reads a queue payload, only this row.
    #
    # NOT NULL, set at upload: True means "auto domain + no bypass → must be
    # confirmed before extraction". Backfilled to False for pre-feature rows
    # by migration 0049 (Python-side default is the single source of truth;
    # the migration strips its transient server_default).
    confirmation_required: bool = Field(default=False)
    # Write-once stamp set by ``confirm_extraction`` on the winning CAS
    # (Phase 3). NULL until confirmed/bypassed. ``gate_decision`` short-circuits
    # to 'proceed' once this is set so a re-dispatch never re-parks a
    # confirmed source.
    extraction_confirmed_at: datetime | None = None

    # --- Upload-settings persistence (Workstream 1, 2026-05-07) ------------
    # Every choice the user makes at upload time becomes a column on
    # SourceRow so recovery / retry / re-extract preserve user choice.
    # Handlers read these from the row, not from the queue payload.
    enable_normalization: bool | None = Field(
        default=None,
        description="None = use file-type default; True/False = user override.",
    )
    enable_vision: bool = Field(
        default=True,
        description="Use vision model on images and scanned PDFs.",
    )
    content_filtering: bool = Field(
        default=True,
        description="Apply domain content-exclusion rules during extraction.",
    )
    filtering_mode: str = Field(
        default="balanced",
        description=(
            "Strictness of post-extraction filters: "
            "'unfiltered' / 'minimal' / 'lenient' / 'balanced' / "
            "'strict' / 'maximum'."
        ),
    )

    # --- Phase 4 toggle columns (2026-05-08) --------------------------------
    # Both are NULLABLE BOOLEAN: NULL means 'fall back to cascade default'
    # (domain config → ExtractionSettings chain). Non-NULL means the user
    # explicitly chose this value at upload time; the choice is preserved
    # across recovery / retry / re-extract runs.
    enable_direction_correction: bool | None = Field(
        default=None,
        description=(
            "Phase 4 (2026-05-08): per-source override for relationship "
            "direction correction. NULL means 'fall back to cascade default' "
            "(domain config → ExtractionSettings.enable_direction_correction)."
        ),
    )
    protect_orphans: bool | None = Field(
        default=None,
        description=(
            "Phase 4 (2026-05-08): per-source override for orphan-entity "
            "protection. NULL means 'fall back to cascade default' (domain "
            "config → filtering preset's protect_orphans value)."
        ),
    )
    # Phase 6 (2026-05-08): per-source toggle for inverse-relationship creation.
    # NULL = fall back to domain config → ExtractionSettings.enable_inverse_relationships.
    enable_inverse_relationships: bool | None = Field(
        default=None,
        description=(
            "Phase 6 (2026-05-08): per-source override for inverse-relationship "
            "creation. NULL means 'fall back to cascade default' (domain config → "
            "ExtractionSettings.enable_inverse_relationships)."
        ),
    )
    # Phase 6 (2026-05-08): per-source cap override for max entity degree.
    # NULL = use ExtractionSettings.max_entity_degree (no per-source override).
    max_entity_degree_override: int | None = Field(
        default=None,
        description=(
            "Phase 6 (2026-05-08): per-source hard cap on relationships per entity. "
            "When set, overrides ExtractionSettings.max_entity_degree for this "
            "source only. NULL = use global setting."
        ),
    )

    # Cross-chunk filtering diagnostics for the "Filtering" UI tab.
    # Small structured dict (typically a few KB) produced by
    # ``_apply_post_dedup_filters`` and persisted by the extraction finalizer.
    # Per-source entity / relationship rows live in the dedicated
    # ``source_entities`` / ``source_relationships`` tables.
    cross_chunk_filtering_log: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))

    # Pending commit payload — populated by the finalizer (or retry path)
    # before enqueueing OP_IMPORT_COMMIT, consumed by the commit handler,
    # and cleared atomically with the commit transaction. Stored as JSON
    # text (not SQLModel JSON) because the payload can contain arbitrary
    # nested entity/relationship dicts and we want a single blob the
    # commit handler can decode lazily without SQLAlchemy's structural
    # coercion. Keeping this on the source (rather than the extraction
    # job) means the manual "retry commit" path — which has no
    # ChunkExtractionJob — can still persist a payload.
    commit_payload: str | None = Field(default=None, sa_column=Column(Text))

    # Active extraction job reference
    current_extraction_job_id: str | None = Field(default=None, index=True)

    # Content filtering for extraction
    content_filtering_enabled: bool | None = Field(
        default=True,
        description="Whether content exclusion filtering is enabled for extraction",
    )

    # MCP extraction progress
    extraction_mode: str | None = Field(default=None)  # "internal" | "mcp" | None

    # Extraction queue gating (for serialized processing)
    # When set, this source is waiting for another source to finish extracting
    extraction_queued_at: datetime | None = None
    extraction_pending_file_info: dict[str, Any] | None = Field(
        default=None, sa_column=Column(JSON)
    )
    # Ranked-domain proposal blob persisted at park time by
    # ``park_for_confirmation`` (Phase 3). Shape:
    # ``{ranking: [{domain, score}], confidence: float,
    #     detected_domain: str, low_confidence: bool}``. Lives on SourceRow
    # (not the ChunkExtractionJob) so the CLI path — which never creates a job
    # row — participates and confirm re-resolves config rather than resuming.
    # Modeled on ``extraction_pending_file_info`` (sa.JSON via sqlmodel.JSON).
    detection_proposal: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))

    # Entity embeddings tracking
    embeddings_generated: bool = Field(default=False)
    embeddings_count: int = Field(default=0)
    embeddings_model: str | None = None
    embeddings_generated_at: datetime | None = None

    # ========== Commit Stage (Graph Import) ==========
    commit_started_at: datetime | None = None
    commit_completed_at: datetime | None = None
    commit_nodes_created: int = Field(default=0)
    commit_edges_created: int = Field(default=0)
    commit_templates_created: int = Field(default=0)

    # Reference to document node in graph (used by commit handlers)
    source_document_node_id: str | None = None

    # ========== Progress Tracking (UI) ==========
    current_step: int | None = None
    total_steps: int | None = None
    step_description: str | None = None

    # ========== LLM Metrics Summary ==========
    llm_total_calls: int = Field(default=0)
    llm_successful_calls: int = Field(default=0)
    llm_failed_calls: int = Field(default=0)
    llm_retry_calls: int = Field(default=0)
    llm_first_try_successes: int = Field(default=0)
    llm_retry_successes: int = Field(default=0)
    llm_permanent_failures: int = Field(default=0)
    llm_total_input_tokens: int = Field(default=0)
    llm_total_output_tokens: int = Field(default=0)
    llm_wasted_tokens: int = Field(default=0)
    llm_avg_call_duration_ms: int | None = None
    llm_total_duration_ms: int = Field(default=0)
    llm_estimated_cost_usd: float | None = None
    llm_error_counts: dict[str, int] | None = Field(default=None, sa_column=Column(JSON))
    llm_model: str | None = None

    # ========== User Metadata ==========
    user_metadata: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))

    # ========== Quality Score Cache ==========
    # Cached after extraction completes (see SCORING_VERSION in services/quality/scoring.py)
    cached_quality_grade: float | None = Field(default=None)  # 0-100
    cached_quality_label: str | None = Field(default=None)  # Outstanding/Excellent/Good/Fair/Low
    cached_richness_score: float | None = Field(default=None)  # Unbounded (total_score)
    cached_avg_entity_quality: float | None = Field(default=None)  # 0-100
    cached_avg_relationship_quality: float | None = Field(default=None)  # 0-100
    cached_connectivity_ratio: float | None = Field(default=None)  # 0-1
    cached_topology_score: float | None = Field(default=None)  # 0-100
    cached_density_ratio: float | None = Field(default=None)
    cached_density_score: float | None = Field(default=None)  # 0-100 (bell-shaped, v7+)
    cached_pollution_penalty: float | None = Field(default=None)  # 0-15 (low-quality items)
    cached_structural_penalty: float | None = Field(default=None)  # 0-15 (hub+reciprocal, v7+)
    cached_hub_skew: float | None = Field(default=None)  # max_deg / median_deg (v7+)
    cached_reciprocal_rate: float | None = Field(default=None)  # 0-1 (v7+)
    cached_low_quality_entity_count: int | None = Field(default=None)
    cached_low_quality_relationship_count: int | None = Field(default=None)
    cached_coverage_score: float | None = Field(default=None)  # 0-100 (entities per chunk)
    cached_scores_at: datetime | None = Field(default=None)  # When scores were calculated
    cached_scores_version: int | None = Field(default=None)  # Scoring algorithm version

    # ========== Quality Counters (Workstream 2, 2026-05-07) ==========
    # Every silent-drop / silent-merge site in the pipeline increments
    # one of these structured counters on the source row. Surfaces in
    # SourceResponse.quality_metrics and the "Data Quality" UI tab.
    # Counters are best-effort: failure to increment never blocks the
    # pipeline. They reset to zero on force_re_extract.
    loader_encoding_used: str | None = Field(default=None)
    loader_warnings_count: int = Field(default=0)
    loader_files_skipped: int = Field(default=0)
    cleaner_lines_removed: int = Field(default=0)
    cleaner_paragraphs_deduplicated: int = Field(default=0)
    cleaner_chars_removed: int = Field(default=0)
    # Phase 7 audit-remediation (2026-05-09): renamed (was chunks_filtered_count).
    chunks_coalesced_count: int = Field(default=0)
    llm_chunks_truncated: int = Field(default=0)
    llm_chunks_aborted_by_loop: int = Field(default=0)
    parser_lines_dropped: int = Field(default=0)
    dedup_entities_merged: int = Field(default=0)
    structural_entities_filtered: int = Field(default=0)
    orphan_entities_filtered: int = Field(default=0)
    relationships_dropped_invalid: int = Field(default=0)
    relationships_dropped_capped: int = Field(default=0)
    citations_skipped_no_chunk_index: int = Field(default=0)
    vector_indexed_at: datetime | None = Field(default=None)
    vector_indexing_status: str = Field(default="pending")

    # ========== Quality Counters Phase 2 (2026-05-08) ==========
    # 16 additional counters backing the Phase 2 observability tasks
    # (import-pipeline-phase2-observability plan, tasks 2-14).
    # All counters are additive, NOT NULL, and reset to 0 on
    # force_re_extract.  Increment sites are best-effort: failure never
    # blocks the pipeline.
    evidence_entities_dropped: int = Field(default=0)
    evidence_relationships_dropped: int = Field(default=0)
    aggregator_relationships_dropped: int = Field(default=0)
    llm_chunks_timed_out: int = Field(default=0)
    llm_chunks_failed_permanent: int = Field(default=0)
    standalone_chunk_failures: int = Field(default=0)
    semantic_dedup_fallbacks: int = Field(default=0)
    relationships_direction_corrected: int = Field(default=0)
    relationships_dropped_type_unmatched: int = Field(default=0)
    # _fuzzy_type_match audit (2026-05-20, migration 0041): rescue-rate
    # counters. _fuzzy_matched counts relationships that survived because
    # tier 2/3 of _fuzzy_type_match matched the LLM-emitted entity type
    # to an allowed type. _fell_through counts balanced-mode survivors
    # where the type didn't match any template at all. Together with
    # relationships_dropped_type_unmatched they cover every outcome of
    # the cross-chunk type-constraint check.
    relationships_type_fuzzy_matched: int = Field(default=0)
    relationships_type_fell_through: int = Field(default=0)
    user_regex_timeout_hits: int = Field(default=0)
    ocr_cleaner_skipped_by_predicate: int = Field(default=0)
    chunker_normalize_drops: int = Field(default=0)
    chunker_prestrip_lines_removed: int = Field(default=0)
    chunks_skipped_by_depth: int = Field(default=0)
    loader_replacement_chars_count: int = Field(default=0)
    citations_skipped_index_not_mapped: int = Field(default=0)
    # Phase 5b (2026-05-08): per-page failure tracking for PDF loader
    loader_pdf_pages_failed: int = Field(default=0)
    # Phase 6 (2026-05-08): loader observability completeness
    # Phase 7 audit-remediation (2026-05-09): JSON dict columns; renamed from loader_html_dropped_tags_count.
    loader_html_dropped_tags: dict[str, int] | None = Field(
        default=None,
        sa_column=Column(JSON),
        description="Per-tag count of HTML tags stripped by HTMLLoader.",
    )
    loader_docx_paragraphs_skipped: int = Field(default=0)
    loader_xlsx_rows_skipped: int = Field(default=0)
    # Phase 7 audit-remediation (2026-05-09): retyped to JSON dict (was INTEGER).
    loader_pptx_shapes_skipped: dict[str, int] | None = Field(
        default=None,
        sa_column=Column(JSON),
        description="Per-shape-type count of skipped shapes in PPTXLoader.",
    )
    loader_csv_rows_truncated: int = Field(default=0)
    cleaner_plugin_load_failures: int = Field(default=0)
    # Phase 7 audit-remediation (2026-05-09): new embedding counters.
    embedding_chunk_failures: int = Field(default=0)
    embedding_dimension_mismatches: int = Field(default=0)
    # Phase 7 audit-remediation (2026-05-09): "X of Y chunks succeeded"
    # answered directly from the source row, not by subtracting failure
    # counters from total_chunks.
    chunks_completed_count: int = Field(default=0)

    # ========== Timestamps ==========
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    # ========== Legacy/Internal Fields ==========
    # Analysis tracking ID (used by lens builder)
    analysis_id: str | None = None

    # ========== Helper Methods ==========
    def get_indexing_duration_seconds(self) -> float | None:
        """Calculate indexing duration in seconds."""
        if self.indexing_started_at and self.indexing_completed_at:
            delta = self.indexing_completed_at - self.indexing_started_at
            return delta.total_seconds()
        return None

    def get_extraction_duration_seconds(self) -> float | None:
        """Calculate extraction duration in seconds."""
        if self.extraction_started_at and self.extraction_completed_at:
            delta = self.extraction_completed_at - self.extraction_started_at
            return delta.total_seconds()
        return None

    def get_commit_duration_seconds(self) -> float | None:
        """Calculate commit duration in seconds."""
        if self.commit_started_at and self.commit_completed_at:
            delta = self.commit_completed_at - self.commit_started_at
            return delta.total_seconds()
        return None


class SourceEntityEmbedding(SQLModel, table=True):
    """Source entity embeddings table."""

    __tablename__ = "source_entity_embeddings"

    id: str = Field(primary_key=True)
    # CASCADE: deleting source also deletes associated embeddings
    source_id: str = Field(
        sa_column=Column(String, ForeignKey("sources.id", ondelete="CASCADE"), index=True)
    )
    entity_index: int
    entity_id: str | None = None
    embedding: bytes  # BLOB - stores base64-encoded numpy array (float32)
    embedding_model: str | None = None
    embedding_dimensions: int | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SourceEntity(SQLModel, table=True):
    """Per-source extracted entity.

    Replaces the old ``sources.extraction_results.entities`` JSON list with
    a relational row per entity so paginated entity-tab reads and
    confidence/name/type sorts run as indexed SQL queries rather than
    Python-side slicing of a 30+ MB JSON blob.

    ``ordinal`` preserves the original extraction order so the "default"
    sort matches the legacy in-list ordering.
    """

    __tablename__ = "source_entities"
    __table_args__ = (
        Index("ix_source_entities_source_ordinal", "source_id", "ordinal"),
        Index("ix_source_entities_source_confidence", "source_id", "confidence"),
        Index("ix_source_entities_source_name", "source_id", "name"),
        Index("ix_source_entities_source_type", "source_id", "type"),
    )

    id: str = Field(primary_key=True)
    # CASCADE: deleting the source also deletes its extracted entity rows
    source_id: str = Field(
        sa_column=Column(
            String,
            ForeignKey("sources.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    database_name: str = Field(index=True)
    ordinal: int
    name: str
    type: str | None = None
    confidence: float | None = None
    attributes: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class SourceRelationship(SQLModel, table=True):
    """Per-source extracted relationship.

    Replaces the old ``sources.extraction_results.relationships`` JSON
    list. ``source_entity_id`` and ``target_entity_id`` reference the
    new ``source_entities`` table; the legacy integer indices into the
    entities list are resolved to FKs by the extraction finalizer at
    write time.
    """

    __tablename__ = "source_relationships"
    __table_args__ = (Index("ix_source_relationships_source_ordinal", "source_id", "ordinal"),)

    id: str = Field(primary_key=True)
    # CASCADE: deleting the source also deletes its relationship rows
    source_id: str = Field(
        sa_column=Column(
            String,
            ForeignKey("sources.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    database_name: str = Field(index=True)
    ordinal: int
    source_entity_id: str = Field(
        sa_column=Column(
            String,
            ForeignKey("source_entities.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    target_entity_id: str = Field(
        sa_column=Column(
            String,
            ForeignKey("source_entities.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    predicate: str | None = None
    confidence: float | None = None
    attributes: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class ChunkExtractionJob(SQLModel, table=True):
    """Tracks a chunk-based extraction job for a document.

    Each job represents one extraction attempt for a source,
    with multiple chunk tasks running in parallel on the LLM queue.

    Status Lifecycle:
        pending → running → completed/failed/cancelled
    """

    __tablename__ = "chunk_extraction_jobs"

    id: str = Field(primary_key=True)
    # CASCADE: deleting source also deletes associated extraction jobs
    source_id: str = Field(
        sa_column=Column(String, ForeignKey("sources.id", ondelete="CASCADE"), index=True)
    )
    database_name: str = Field(index=True)

    # Configuration
    extraction_depth: str = Field(default="full")  # 'quick' | 'full'
    generate_embeddings: bool = Field(default=True)

    # Domain Detection
    forced_domain: str | None = Field(default=None)  # User-selected domain (e.g., 'technical')
    detected_domain: str | None = Field(default=None)  # Auto-detected domain name
    domain_guidance: str | None = Field(
        default=None, sa_column=Column(Text)
    )  # Domain-specific LLM guidance (can be long)

    # LLM Prompts (shared across all chunks - stored once for debugging/visibility)
    system_prompt: str | None = Field(default=None, sa_column=Column(Text))
    user_instructions: str | None = Field(default=None, sa_column=Column(Text))
    # Pass-2 relationship prompt template (placeholders for the chunk text and
    # the pass-1 entity list), shown alongside user_instructions (the pass-1
    # entity prompt template) on the Processing tab. Added 2026-05-26.
    relationship_instructions: str | None = Field(default=None, sa_column=Column(Text))
    # Separate template and domain parts for distinct UI display
    user_instructions_template: str | None = Field(default=None, sa_column=Column(Text))
    extraction_rules_template: str | None = Field(default=None, sa_column=Column(Text))
    entity_templates: str | None = Field(default=None, sa_column=Column(Text))
    relationship_templates: str | None = Field(default=None, sa_column=Column(Text))
    domain_examples: str | None = Field(default=None, sa_column=Column(Text))

    # Extraction Config (per-job template data, stored once instead of per-chunk in queue)
    extraction_config: str | None = Field(default=None, sa_column=Column(Text))

    # Progress Tracking
    # Values: 'pending' | 'running' | 'completed' | 'failed' | 'cancelled'.
    # Validated via ChunkExtractionJobStatus StrEnum at the service layer.
    status: str = Field(
        default="pending",
        sa_column=Column(
            String,
            default="pending",
            nullable=False,
        ),
    )  # 'pending' | 'running' | 'completed' | 'failed' | 'cancelled'
    total_chunks: int = Field(default=0)
    completed_chunks: int = Field(default=0)
    failed_chunks: int = Field(default=0)
    # Single-claim guard for the terminal transition: the worker whose
    # increment crosses (completed+failed >= total) atomically flips this
    # 0→1 and is the unique caller that enqueues OP_FINALIZE_EXTRACTION.
    # Prevents a double finalize-enqueue when two concurrent last-chunk
    # handlers both observe the terminal counts. Added by migration 0050.
    finalize_claimed: bool = Field(default=False)

    # Content filtering stats
    filtered_chunks: int | None = Field(
        default=0,
        description="Number of chunks excluded by content filtering",
    )
    filtered_content_ratio: float | None = Field(
        default=0.0,
        description="Average fraction of content stripped from kept chunks",
    )

    # Timestamps
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    started_at: datetime | None = None
    completed_at: datetime | None = None

    # Error Tracking
    error_message: str | None = None
    parent_task_id: str | None = None  # Queue task ID that created this job


class ChunkExtractionTask(SQLModel, table=True):
    """Tracks individual chunk extraction tasks within a job.

    Each task corresponds to one chunk being processed by the LLM queue.
    Results are stored here until finalization aggregates them.

    Status Lifecycle:
        pending → queued → running → completed/failed
    """

    __tablename__ = "chunk_extraction_tasks"
    __table_args__ = (
        # Covers F-2 cleanup: WHERE status='orphaned' AND created_at < cutoff
        Index("ix_chunk_tasks_status_created", "status", "created_at"),
        # Covers BE-7 cascade + BE-5 guard: WHERE job_id=? AND status IN (...)
        Index("ix_chunk_tasks_job_status", "job_id", "status"),
    )

    id: str = Field(primary_key=True)
    # CASCADE: deleting job also deletes associated tasks
    job_id: str = Field(
        sa_column=Column(
            String, ForeignKey("chunk_extraction_jobs.id", ondelete="CASCADE"), index=True
        )
    )
    database_name: str = Field(index=True)

    # Chunk Identification
    chunk_index: int
    hierarchical_group_id: str | None = None  # Reference to hierarchical chunk group
    small_chunk_ids: list[str] | None = Field(default=None, sa_column=Column(JSON))

    # Queue Tracking
    queue_task_id: str | None = None  # Queue task ID for this chunk

    # Status
    # Values: 'pending', 'queued', 'running', 'completed', 'failed', 'orphaned'.
    # Validated via ChunkExtractionTaskStatus StrEnum at the service layer.
    status: str = Field(
        default="pending",
        sa_column=Column(
            String,
            default="pending",
            index=True,
            nullable=False,
        ),
    )  # 'pending' | 'queued' | 'running' | 'completed' | 'failed' | 'orphaned'
    retry_count: int = Field(default=0)
    max_retries: int = Field(default=3)

    # Timestamps
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    queued_at: datetime | None = None
    started_at: datetime | None = Field(default=None, index=True)
    cancelled_at: datetime | None = Field(default=None)
    completed_at: datetime | None = None

    # Results (stored until finalization)
    raw_entities: list[dict] | None = Field(default=None, sa_column=Column(JSON))
    raw_relationships: list[dict] | None = Field(default=None, sa_column=Column(JSON))
    # Cached embeddings for raw_entities (parallel by index). Written
    # alongside raw_entities by the chunk handler; consumed by dedup at
    # finalize time so a finalize-handler crash doesn't trigger
    # re-embedding the aggregated entity set on retry. NULL means
    # "not yet computed" — finalize backfills it. Stored as a JSON
    # list-of-lists since chunk_extraction_tasks rows are short-lived
    # (cascade-deleted with the job post-commit).
    raw_entity_embeddings: list[list[float]] | None = Field(default=None, sa_column=Column(JSON))
    entity_count: int = Field(default=0)
    relationship_count: int = Field(default=0)
    invalid_relationship_count: int = Field(
        default=0
    )  # Relationships skipped due to invalid indices

    # Sentences (pre-split for reuse in finalization, avoids re-splitting)
    chunk_sentences: list[str] | None = Field(default=None, sa_column=Column(JSON))

    # Error Tracking
    error_message: str | None = None
    error_type: str | None = None  # 'llm_error' | 'validation_error' | 'timeout' | etc.

    # LLM Input/Output Tracking (for debugging and analytics)
    input_text: str | None = Field(default=None, sa_column=Column(Text))
    input_text_length: int | None = None
    llm_response_json: str | None = Field(default=None, sa_column=Column(Text))
    llm_response_length: int | None = None
    llm_duration_ms: int | None = None

    # Token tracking (actual counts from LLM API)
    input_tokens: int | None = None
    output_tokens: int | None = None

    # Context window at time of processing (for utilization chart)
    context_window_available: int | None = None  # = ai_context_window (full context window)

    # Pipeline filtering diagnostics (per-chunk stages)
    filtering_log: dict | None = Field(default=None, sa_column=Column(JSON))

    # LLM extraction observability (Workstream 8, 2026-05-07)
    # ``finish_reason`` is the normalized provider stop token
    # (``stop`` / ``length`` / ``content_filter`` / ``tool_calls`` /
    # ``error`` / ``unknown``). ``aborted_by_loop`` is set when the
    # streaming loop detector cut the stream short on a degenerate
    # pattern. Both default to NULL on legacy rows that pre-date the
    # 0022 migration.
    finish_reason: str | None = None
    aborted_by_loop: bool | None = None


class ChunkExtractionAttempt(SQLModel, table=True):
    """History of prior chunk_extraction_tasks results, snapshotted on rerun.

    One row per (chunk_task_id, attempt_number) — the chunk_task's live
    row always holds the latest attempt; this table preserves earlier
    attempts so the UI can show "did the rerun produce a different
    result?" without losing the prior data.

    Inserted inside the rerun reset transaction BEFORE the live row is
    wiped. raw_entity_embeddings is deliberately NOT snapshotted (large,
    re-derivable from raw_entities).
    """

    __tablename__ = "chunk_extraction_attempts"
    __table_args__ = (Index("ix_chunk_attempts_task_id", "chunk_task_id", "attempt_number"),)

    id: str = Field(primary_key=True)
    chunk_task_id: str = Field(
        sa_column=Column(
            String,
            ForeignKey("chunk_extraction_tasks.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    attempt_number: int = Field(nullable=False)
    snapshotted_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    # Snapshotted from ChunkExtractionTask at reset time
    started_at: datetime | None = None
    completed_at: datetime | None = None
    input_text: str | None = Field(default=None, sa_column=Column(Text))
    input_text_length: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    context_window_available: int | None = None
    llm_response_json: str | None = Field(default=None, sa_column=Column(Text))
    llm_response_length: int | None = None
    llm_duration_ms: int | None = None
    raw_entities: list[dict] | None = Field(default=None, sa_column=Column(JSON))
    raw_relationships: list[dict] | None = Field(default=None, sa_column=Column(JSON))
    entity_count: int = Field(default=0)
    relationship_count: int = Field(default=0)
    invalid_relationship_count: int = Field(default=0)
    filtering_log: dict | None = Field(default=None, sa_column=Column(JSON))
    finish_reason: str | None = None
    aborted_by_loop: bool | None = None
    chunk_sentences: list[str] | None = Field(default=None, sa_column=Column(JSON))
    # Text (not AutoString/VARCHAR): error messages can be multi-line LLM
    # stack traces. Migration 0037 created the live column as TEXT, so the
    # explicit Column(Text) here keeps SQLModel metadata aligned with the
    # baseline (otherwise ``test_baseline_columns_match_create_all`` reports
    # a TEXT-vs-VARCHAR drift on this column).
    error_message: str | None = Field(default=None, sa_column=Column(Text))
    error_type: str | None = None


# ================================
# Source Support Models
# ================================


class SourceTag(SQLModel, table=True):
    """Tags for organizing sources."""

    __tablename__ = "source_tags"

    id: str = Field(primary_key=True)
    database_name: str = Field(index=True)
    name: str = Field(index=True)  # "research", "legal", "technical", etc.
    color: str | None = None  # Hex color for UI
    description: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SourceTagAssignment(SQLModel, table=True):
    """Many-to-many relationship between sources and tags."""

    __tablename__ = "source_tag_assignments"
    __table_args__ = (UniqueConstraint("source_id", "tag_id", name="uq_source_tag_assignment"),)

    id: str = Field(primary_key=True)
    source_id: str = Field(foreign_key="sources.id", index=True)
    tag_id: str = Field(foreign_key="source_tags.id", index=True)
    database_name: str = Field(index=True)
    assigned_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class DocumentChunk(SQLModel, table=True):
    """Document chunks for RAG retrieval.

    Linked to source from creation. Status tracks indexing → committed lifecycle.
    """

    __tablename__ = "document_chunks"

    id: str = Field(primary_key=True)
    database_name: str = Field(index=True)

    # Source reference (CASCADE: deleting source also deletes chunks)
    source_id: str = Field(
        sa_column=Column(String, ForeignKey("sources.id", ondelete="CASCADE"), index=True)
    )

    # Chunk content & metadata
    chunk_index: int
    content: str
    # Pre-cleanup text for the three-way Chunks-tab view (added 0038).
    # NULL for sources extracted before the migration — UI surfaces a
    # "re-extract to repopulate" hint for those.
    raw_content: str | None = Field(default=None, sa_column=Column(Text))

    # Embeddings
    embedding: bytes | None = None  # BLOB - stores base64-encoded numpy array (float32)
    embedding_model: str | None = None
    embedding_dimensions: int | None = None

    # Location metadata
    page_number: int | None = None
    section: str | None = None
    group_index: int | None = None  # Hierarchical group index for UI grouping
    chunk_metadata: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))

    # Lifecycle status
    # Values: 'staged', 'indexed', 'committed'. Validated via
    # DocumentChunkStatus StrEnum at the service layer.
    status: str = Field(
        default="indexed",
        sa_column=Column(
            String,
            default="indexed",
            index=True,
            nullable=False,
        ),
    )  # 'staged' | 'indexed' | 'committed'
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), index=True)

    # Citation offset columns (Phase 5a, 2026-05-08)
    # char_start / char_end: character offsets into the *original* upload text.
    # Previously computed in-memory only; now persisted so the API can surface
    # accurate citation anchors. NULLABLE: 'none'-method chunks have no valid
    # offset (content was unlocatable in original text after cleaning).
    char_start: int | None = Field(default=None)
    char_end: int | None = Field(default=None)
    # How char_start/char_end were computed:
    # 'exact'  — substring match against original upload text (most accurate).
    # 'fuzzy'  — rapidfuzz partial_ratio_alignment fallback (approximate, ≥80 score).
    # 'none'   — chunk content unlocatable in original; offsets are NULL.
    # Default 'exact' covers: (a) legacy rows back-filled by migration, (b) new rows
    # from callers that don't supply original_text (pre-5a behaviour, positions are
    # relative to the cleaned/normalized text, not the original — slightly inaccurate
    # but consistent with what shipped before this phase).
    citation_offset_method: str = Field(
        default="exact",
        sa_column=Column(
            String(8),
            default="exact",
            nullable=False,
        ),
    )

    # Resumability: NULL means the chunk has been created but its
    # embedding has not yet been persisted. The embedding sub-stage of
    # indexing queries this column to resume after a crashed worker.
    embedded_at: datetime | None = Field(default=None, index=True)


class SourceCitation(SQLModel, table=True):
    """Links extracted entities to their source chunks.

    Created during commit stage, provides entity attribution.
    """

    __tablename__ = "source_citations"
    __table_args__ = (
        Index("ix_source_citations_db_entity", "database_name", "entity_uri"),
        Index("ix_source_citations_db_source", "database_name", "source_id"),
    )

    id: str = Field(primary_key=True)
    database_name: str = Field(index=True)

    # Entity reference (knowledge graph)
    entity_uri: str = Field(index=True)  # e.g., "chaoscypher:entity_123"
    entity_label: str
    entity_type: str | None = None  # "Person", "Organization", etc.

    # Source reference (permanent storage)
    # CASCADE: deleting source or chunk also deletes associated citations
    source_id: str = Field(
        sa_column=Column(String, ForeignKey("sources.id", ondelete="CASCADE"), index=True)
    )
    chunk_id: str = Field(
        sa_column=Column(String, ForeignKey("document_chunks.id", ondelete="CASCADE"), index=True)
    )

    # Citation metadata
    confidence: float = Field(default=1.0)
    extraction_method: str  # "ai_extraction" | "manual" | "imported"
    context_snippet: str | None = None  # Text around the entity mention

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    citation_metadata: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))


class RelationshipCitation(SQLModel, table=True):
    """Links extracted relationships (edges) to their source chunks.

    Created during commit stage, provides relationship attribution.
    Parallel to SourceCitation but for edges instead of entities.
    """

    __tablename__ = "relationship_citations"

    id: str = Field(primary_key=True)
    database_name: str = Field(index=True)

    # Edge reference (knowledge graph)
    edge_id: str = Field(index=True)  # e.g., "edge_abc123"
    edge_label: str  # e.g., "worked_at"
    edge_type: str | None = None  # Template name: "Employment", etc.

    # Entity labels (for display without graph lookup)
    source_entity_label: str  # e.g., "Albert Einstein"
    target_entity_label: str  # e.g., "Princeton University"

    # Source reference (permanent storage)
    # CASCADE: deleting source or chunk also deletes associated citations
    source_id: str = Field(
        sa_column=Column(String, ForeignKey("sources.id", ondelete="CASCADE"), index=True)
    )
    chunk_id: str = Field(
        sa_column=Column(String, ForeignKey("document_chunks.id", ondelete="CASCADE"), index=True)
    )

    # Citation metadata
    confidence: float = Field(default=1.0)
    extraction_method: str  # "ai_extraction" | "manual" | "imported"
    justification: str | None = None  # The LLM's justification for this relationship

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    citation_metadata: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))


# ================================
# Chat System Models
# ================================


class Chat(SQLModel, table=True):
    """Chats table (chat history)."""

    __tablename__ = "chats"
    __table_args__ = (Index("ix_chat_db_user", "database_name", "user_id"),)

    id: str = Field(primary_key=True)
    database_name: str = Field(index=True)
    user_id: int | None = Field(
        default=None, index=True
    )  # Multi-tenancy (no FK - users table removed)
    title: str
    # Values: 'active', 'processing', 'completed', 'error'.
    # Validated via ChatStatus StrEnum at the service layer.
    status: str = Field(
        default="active",
        sa_column=Column(
            String,
            default="active",
            nullable=False,
        ),
    )  # 'active', 'processing', 'completed', 'error'
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    message_count: int = Field(default=0)
    # Source scope for filtered chat (None = all sources)
    source_ids: list[str] | None = Field(default=None, sa_column=Column(JSON))

    # Relationships
    messages: list["ChatMessage"] = Relationship(
        back_populates="chat",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


class ChatMessage(SQLModel, table=True):
    """Chat messages table."""

    __tablename__ = "chat_messages"

    id: str = Field(primary_key=True)
    chat_id: str = Field(
        sa_column=Column(
            String,
            ForeignKey("chats.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
    )
    role: str  # 'user' or 'assistant'
    content: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    extra_metadata: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))

    # Relationships
    chat: Chat = Relationship(back_populates="messages")


# ================================
# Graph Storage Models (SQLite-backed RDF replacement)
# ================================


class GraphNode(SQLModel, table=True):
    """Knowledge graph nodes table.

    Stores nodes with their properties, position, and embeddings.
    Multi-database support via database_name field.
    """

    __tablename__ = "graph_nodes"
    __table_args__ = (
        Index("ix_graph_nodes_db_source", "database_name", "source_id"),
        Index("ix_graph_nodes_db_template", "database_name", "template_id"),
        # Composite index for Schema Insights aggregation queries that
        # group nodes by type within a database. Added by migration 0035
        # alongside the entity_type column.
        Index("ix_graph_nodes_db_entity_type", "database_name", "entity_type"),
    )

    id: str = Field(primary_key=True)
    database_name: str = Field(index=True)
    graph_name: str = Field(index=True)  # 'knowledge' or 'lenses'

    # Node data
    template_id: str = Field(
        sa_column=Column(
            String,
            ForeignKey("graph_templates.id", ondelete="RESTRICT"),
            index=True,
            nullable=False,
        ),
    )
    label: str
    # Extracted entity type (e.g., "Person", "Organization"). Backfilled
    # by migration 0035 from source_citations.entity_type via the
    # (database_name, label↔entity_label) join. Nullable: legacy nodes
    # without a matching citation stay NULL until a per-source cleanup
    # recovers them. New commit-path writes always populate.
    entity_type: str | None = None
    properties: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))

    # Position for graph canvas (optional)
    position_x: float | None = None
    position_y: float | None = None

    # Embedding for similarity search (stored as JSON array)
    embedding: list[float] | None = Field(default=None, sa_column=Column(JSON))

    # Timestamps
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    # Source tracking for enabled filtering (CASCADE delete with source)
    source_id: str | None = Field(
        sa_column=Column(String, ForeignKey("sources.id", ondelete="CASCADE"), index=True),
        default=None,
    )

    # CCX 3.0 stable identity: the IRI anchoring this node to its identity in
    # an exported CCX package, so re-imports upsert by IRI. Added by migration
    # 0003. Nullable: legacy rows and not-yet-exported nodes stay NULL.
    ccx_iri: str | None = Field(default=None, index=True)


class GraphEdge(SQLModel, table=True):
    """Knowledge graph edges table.

    Stores edges (relationships) between nodes.
    Includes proper indexes for both source and target node lookups.
    """

    __tablename__ = "graph_edges"
    __table_args__ = (
        Index("ix_graph_edges_db_source_node", "database_name", "source_node_id"),
        Index("ix_graph_edges_db_target_node", "database_name", "target_node_id"),
    )

    id: str = Field(primary_key=True)
    database_name: str = Field(index=True)
    graph_name: str = Field(default="knowledge", index=True)  # Always 'knowledge' for edges

    # Relationship data
    template_id: str = Field(
        sa_column=Column(
            String,
            ForeignKey("graph_templates.id", ondelete="RESTRICT"),
            index=True,
            nullable=False,
        ),
    )
    source_node_id: str = Field(
        sa_column=Column(String, ForeignKey("graph_nodes.id", ondelete="CASCADE"), index=True)
    )
    target_node_id: str = Field(
        sa_column=Column(String, ForeignKey("graph_nodes.id", ondelete="CASCADE"), index=True)
    )
    label: str
    properties: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))

    # Timestamps
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    # Source tracking for enabled filtering (CASCADE delete with source)
    source_id: str | None = Field(
        sa_column=Column(String, ForeignKey("sources.id", ondelete="CASCADE"), index=True),
        default=None,
    )

    # CCX 3.0 stable identity: the IRI anchoring this edge to its identity in
    # an exported CCX package, so re-imports upsert by IRI. Added by migration
    # 0003. Nullable: legacy rows and not-yet-exported edges stay NULL.
    ccx_iri: str | None = Field(default=None, index=True)


class GraphTemplate(SQLModel, table=True):
    """Graph templates table — node and edge type schemas.

    Stores node and edge type definitions with their property schemas.
    """

    __tablename__ = "graph_templates"
    __table_args__ = (
        UniqueConstraint(
            "database_name",
            "source_id",
            "template_type",
            "name",
            name="uq_graph_templates_per_source",
        ),
    )

    id: str = Field(primary_key=True)
    database_name: str = Field(index=True)

    # Template metadata
    name: str
    template_type: str = Field(index=True)  # 'node' or 'edge'
    description: str | None = None
    is_system: bool = Field(default=False, index=True)

    # Visual identity
    icon: str | None = Field(default=None)
    color: str | None = Field(default=None)

    # Property definitions (JSON array of PropertyDefinition objects)
    properties: list[dict[str, Any]] | None = Field(default=None, sa_column=Column(JSON))

    # Embedding fields for semantic search
    embedding: list[float] | None = Field(default=None, sa_column=Column(JSON))
    embedding_model: str | None = Field(default=None)
    embedding_dimensions: int | None = Field(default=None)

    # Timestamps
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    # Source tracking for enabled filtering (CASCADE delete with source)
    source_id: str | None = Field(
        sa_column=Column(String, ForeignKey("sources.id", ondelete="CASCADE"), index=True),
        default=None,
    )


# ================================
# LLM Metrics System Models
# ================================


class LLMCallMetric(SQLModel, table=True):
    """Individual LLM call metrics for retry/failure analysis.

    Tracks every LLM call with full context for:
    - Retry analysis (which calls needed retries, why)
    - Token waste analysis (tokens spent on failed calls)
    - Cost estimation (per-model pricing)
    - Performance patterns (duration, error types)

    Each record represents a single LLM API call attempt.
    Multiple records may exist per chunk if retries occurred.
    """

    __tablename__ = "llm_call_metrics"
    # Plain-ascending composite index (reverse-scanned for the paginated
    # ``ORDER BY started_at DESC`` read). See the note on Source.__table_args__.
    __table_args__ = (
        Index(
            "ix_llm_call_metrics_database_name_started_at_desc",
            "database_name",
            "started_at",
        ),
    )

    # Primary key
    id: str = Field(primary_key=True)
    database_name: str = Field(index=True)

    # Context linking (nullable - not all calls are source-related)
    source_id: str | None = Field(
        sa_column=Column(String, ForeignKey("sources.id", ondelete="CASCADE"), index=True),
        default=None,
    )
    chunk_task_id: str | None = Field(default=None, index=True)  # Links to ChunkExtractionTask

    # Call identification
    operation_type: str = Field(
        index=True
    )  # 'entity_extraction', 'chat', 'embedding', 'template_suggestion'
    call_sequence: int = Field(default=1)  # 1 = first attempt, 2+ = retry

    # Model info
    provider: str  # 'ollama', 'openai', 'anthropic', 'gemini'
    model: str  # 'llama3.1:8b', 'gpt-4o-mini', etc.

    # Token usage
    input_tokens: int = Field(default=0)
    output_tokens: int = Field(default=0)

    # Timing
    duration_ms: int = Field(default=0)
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None

    # Result
    success: bool = Field(default=False)
    error_type: str | None = (
        None  # 'validation_error', 'timeout', 'rate_limit', 'model_error', 'truncation'
    )
    error_message: str | None = Field(default=None, sa_column=Column(Text))

    # Retry context
    was_retry: bool = Field(default=False)
    retry_reason: str | None = (
        None  # 'schema_validation', 'quality_issues', 'truncation', 'exception'
    )

    # Extraction context (for entity extraction calls)
    chunk_index: int | None = None
    chunk_size_chars: int | None = None  # Input text length
    entities_extracted: int | None = None
    relationships_extracted: int | None = None

    # Timestamps
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


# ================================
# MCP Extraction Submission Models
# ================================


class ExtractionSubmission(SQLModel, table=True):
    """Stores partial extraction results submitted per chunk during MCP-driven extraction.

    Rows are transient: created during extraction, consumed during finalization,
    then deleted. Each row holds the raw entity/relationship text for one chunk group.
    """

    __tablename__ = "extraction_submissions"
    __table_args__ = (
        UniqueConstraint(
            "database_name",
            "source_id",
            "chunk_group_index",
            name="uq_extraction_sub_source_chunk",
        ),
        Index("ix_extraction_submissions_source", "database_name", "source_id"),
    )

    id: str = Field(primary_key=True)
    database_name: str = Field(index=True)
    source_id: str = Field(
        sa_column=Column(
            String,
            ForeignKey("sources.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
    )
    chunk_group_index: int
    entities_text: str = Field(default="")
    relationships_text: str = Field(default="")
    entity_count: int = Field(default=0)
    relationship_count: int = Field(default=0)
    submitted_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


# ================================
# System State (global pause)
# ================================


class SystemState(SQLModel, table=True):
    """Singleton table holding system-wide state flags.

    Only ever one row (id=1). Currently carries the global
    processing-pause flag. A source is effectively
    paused iff `SourceRow.is_paused` OR `SystemState.processing_paused`.

    See
    source pause and graceful shutdown design notes
    for the broader design rationale.
    """

    __tablename__ = "system_state"

    id: int = Field(default=1, primary_key=True)
    processing_paused: bool = Field(default=False)
    processing_paused_at: datetime | None = Field(default=None)
    processing_paused_reason: str | None = Field(default=None)
    paused_by: str | None = Field(default=None)  # "user" | "health_monitor" | None


class SystemEvent(SQLModel, table=True):
    """Audit trail for system-level events (pause, health, recovery, etc.).

    Every call to ``set_system_paused`` appends a row here, and other
    subsystems (health monitor, reconciler) can log events too.
    Old rows are pruned automatically when the table exceeds
    ``max_events`` (default 100).
    """

    __tablename__ = "system_events"

    id: int | None = Field(default=None, primary_key=True)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    type: str  # "pause" | "resume" | "health_change" | "task_failed" | "recovery"
    action: str  # human-readable action description
    source: str | None = None  # "user" | "health_monitor" | "reconciler" | "worker"
    reason: str | None = None
    details: str | None = None  # JSON string for extra context (probe names, task info)
    database_name: str | None = None


# ================================
# Search Index Support Models
# ================================


class PendingSearchIndex(SQLModel, table=True):
    """Items awaiting FTS5+vec (re-)indexing.

    Populated by the commit pipeline when post-transaction indexing fails,
    when the embedding model/dimension changes, or when an embedding
    service is temporarily unavailable. Drained by the search orphan-sweep
    worker.
    """

    __tablename__ = "pending_search_index"
    __table_args__ = (UniqueConstraint("kind", "item_id", name="uq_pending_search_index"),)
    # kind values: 'node', 'chunk', 'template'. Validated via
    # PendingSearchIndexKind StrEnum at the service layer.

    id: str = Field(primary_key=True)  # "{kind}:{item_id}"
    kind: str = Field(index=True)
    item_id: str = Field(index=True)
    source_id: str | None = Field(default=None, index=True)
    reason: str = Field(default="indexing_failed")
    attempts: int = Field(default=0)
    last_error: str | None = Field(default=None)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        index=True,
    )


# ================================
# Graph Snapshot Models
# ================================


class SourceRecoveryEvent(SQLModel, table=True):
    """One row per real recovery dispatch by ``SourceRecovery``.

    The audit trail surfaced in the source detail UI so operators can
    diagnose the "auto-recovered N times" warning without grepping
    logs. Only WRITTEN when ``_recover_one`` performed a real (non
    no-op) dispatch — debounced/healthy ticks do not produce rows.
    """

    __tablename__ = "source_recovery_events"
    __table_args__ = (
        # Per-source listing (newest first): WHERE source_id=? AND database_name=?
        # ORDER BY attempt_at DESC. The composite covers both the equality
        # filters and the order-by, so no separate single-column indexes
        # are needed on source_id or database_name.
        Index(
            "ix_source_recovery_events_source_db_time",
            "source_id",
            "database_name",
            "attempt_at",
        ),
    )

    id: str = Field(primary_key=True)
    source_id: str = Field(foreign_key="sources.id", ondelete="CASCADE", index=False)
    database_name: str
    attempt_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    from_status: str
    action_taken: str  # "extract_chunk" | "import_commit" | "compound" | …
    reason: str  # "stalled" | "compound" | "missing_queue_task" | …
    enqueued_count: int = Field(default=0)


class GraphSnapshot(SQLModel, table=True):
    """Latest pre-computed GraphBreakdown payload for a given database.

    One row per database (``database_name`` is the PK). Rebuilt by the
    ``OP_BUILD_GRAPH_SNAPSHOT`` operation handler after commits or on
    manual refresh. Consumers (dashboard, export bundle) read this row
    via ``GraphSnapshotRepository.get_current`` and deserialize ``payload_json``
    into a ``GraphBreakdown`` pydantic model.
    """

    __tablename__ = "graph_snapshots"

    database_name: str = Field(primary_key=True)
    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        index=True,
    )
    payload_json: str  # serialized GraphBreakdown (breakdown.model_dump_json())
    node_count: int = Field(default=0)
    edge_count: int = Field(default=0)


class LLMDailySpend(SQLModel, table=True):
    """Persisted per-database daily LLM token spend (UTC-day window).

    Backs the daily spend cap so a worker crash-loop cannot re-arm the daily
    budget on restart — the in-memory ``LLMSpendTracker`` daily counter zeroes
    on restart, this row does not. One row per ``(database_name, spend_date)``;
    ``spend_date`` is an ISO ``YYYY-MM-DD`` UTC date string, so midnight
    rollover is automatic (a new day reads a fresh row) and old rows are inert.
    Added by migration 0047.
    """

    __tablename__ = "llm_daily_spend"

    database_name: str = Field(primary_key=True)
    spend_date: str = Field(primary_key=True)  # ISO 'YYYY-MM-DD' (UTC)
    total_tokens: int = Field(default=0)


class LLMStageProgress(SQLModel, table=True):
    """Per-source, per-stage LLM progress tracking.

    Keyed by ``(source_id, stage_name)`` so the same table serves vision,
    embedding, and MCP extraction without any schema coupling between stages.
    The ``stage_name`` column is a free-form string; canonical names are
    defined in ``chaoscypher_core.constants`` (e.g. ``STAGE_MCP_EXTRACTION``).

    Created by migration 0030.  Replaces the six legacy
    ``extraction_chunks_*`` columns that were removed from ``SourceRow``.
    """

    __tablename__ = "llm_stage_progress"
    __table_args__ = (Index("ix_llm_stage_progress_active", "completed_at"),)

    source_id: str = Field(
        sa_column=Column(String, ForeignKey("sources.id", ondelete="CASCADE"), primary_key=True),
    )
    stage_name: str = Field(primary_key=True)

    total: int = Field(default=0)
    processed: int = Field(default=0)
    avg_ms: int | None = Field(default=None)

    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_activity: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = Field(default=None)
    extras_json: str | None = Field(default=None, sa_column=Column(Text))


class VisionJob(SQLModel, table=True):
    """Coordinator row for per-source vision processing.

    Mirrors ChunkExtractionJob's shape: total/completed/failed counters
    plus timestamps. One row per source that has image pages.

    Phase is not stored as a column — callers derive it from counters:
        * pending:     completed == 0 and failed == 0
        * in_progress: 0 < completed + failed < total_pages
        * terminal:    completed + failed >= total_pages
    The atomic ``increment_vision_job_completed_and_check`` adapter
    method returns ``is_terminal`` so handlers don't have to read this
    back themselves.
    """

    __tablename__ = "vision_jobs"

    id: str = Field(primary_key=True)
    source_id: str = Field(
        sa_column=Column(
            String,
            ForeignKey("sources.id", ondelete="CASCADE"),
            index=True,
            nullable=False,
        )
    )
    total_pages: int = Field(default=0, nullable=False)
    completed: int = Field(default=0, nullable=False)
    failed: int = Field(default=0, nullable=False)

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class VisionPageDescription(SQLModel, table=True):
    """Per-page durable record of a vision LLM result.

    One row per (source, page, region_index). v1 always writes
    region_index=0; v2 region-split will write 1, 2 when a page is
    split top/bottom on truncation. ``status`` is a VisionPageStatus
    StrEnum value (validated Python-side; no CheckConstraint per CC038).
    """

    __tablename__ = "vision_page_descriptions"
    __table_args__ = (
        Index("ix_vpd_source_status", "source_id", "status"),
        # UNIQUE(source_id, page_number, region_index) is enforced by a
        # named UniqueConstraint so the migration can DROP it cleanly on
        # downgrade. The constraint name is stable so Alembic autogenerate
        # doesn't churn it.
        UniqueConstraint(
            "source_id",
            "page_number",
            "region_index",
            name="uq_vpd_source_page_region",
        ),
    )

    id: str = Field(primary_key=True)
    source_id: str = Field(
        sa_column=Column(
            String,
            ForeignKey("sources.id", ondelete="CASCADE"),
            index=True,
            nullable=False,
        )
    )
    vision_job_id: str = Field(
        sa_column=Column(
            String,
            ForeignKey("vision_jobs.id", ondelete="CASCADE"),
            index=True,
            nullable=False,
        )
    )
    page_number: int = Field(nullable=False)
    region_index: int = Field(default=0, nullable=False)
    kind: str = Field(nullable=False)  # VisionPageKind StrEnum
    status: str = Field(default="pending", nullable=False)  # VisionPageStatus StrEnum
    description: str | None = Field(default=None, sa_column=Column(Text))
    image_path: str = Field(nullable=False)
    finish_reason: str | None = Field(default=None)
    error_message: str | None = Field(default=None, sa_column=Column(Text))
    attempts: int = Field(default=0, nullable=False)

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
