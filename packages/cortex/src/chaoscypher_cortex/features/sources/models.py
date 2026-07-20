# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Pydantic DTOs for Sources API."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from chaoscypher_core.models import SourceStatus
from chaoscypher_core.ports.types import FilteringMode
from chaoscypher_cortex.features.sources.progress import SourceProgress, map_status_to_progress
from chaoscypher_cortex.shared.api.models import PaginationMetadata


# ================================
# Stage Progress DTO
# ================================


class StageProgressRecord(BaseModel):
    """Progress for one LLM stage on a source.

    One record per (source_id, stage_name) row in llm_stage_progress.
    The dict-keyed shape on SourceResponse lets consumers iterate stages
    without knowing the full set in advance.
    """

    total: int = Field(ge=0, description="Total units of work; 0 means no work needed.")
    processed: int = Field(ge=0, description="Units completed so far.")
    avg_ms: int | None = Field(
        default=None,
        description=(
            "EMA of wall-clock duration per completed unit, in ms. "
            "Null until the first tick reports timing."
        ),
    )
    started_at: datetime
    last_activity: datetime
    completed_at: datetime | None = None
    extras: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Stage-specific extension data. MCP extraction populates "
            "{entities_preview, relationships_preview}. Vision and "
            "embedding leave this null."
        ),
    )


# ================================
# Source DTOs
# ================================


class UrlImportRequest(BaseModel):
    """Request model for importing a source from a URL.

    ``enable_vision`` gives URL imports parity with the file-upload
    routes; every upload-time choice is persisted on the source row
    and surfaced via ``SourceResponse.upload_options``.
    """

    url: str
    extract_entities: bool = True
    analysis_depth: str = "full"
    enable_normalization: bool | None = None
    enable_vision: bool = True
    domain: str | None = None
    content_filtering: bool = True
    skip_duplicates: bool = False
    auto_confirm: bool = False
    filtering_mode: FilteringMode | None = None
    enable_direction_correction: bool | None = None
    protect_orphans: bool | None = None
    # Phase 6 (2026-05-08): per-source inverse-relationships toggle and degree cap.
    enable_inverse_relationships: bool | None = None
    max_entity_degree_override: int | None = None

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        """Validate that URL starts with http:// or https://."""
        if not v.startswith(("http://", "https://")):
            msg = "URL must start with http:// or https://"
            raise ValueError(msg)
        return v


class UrlImportResponse(BaseModel):
    """Response for ``POST /sources/url`` — returned when the fetch is queued.

    URL fetches run on the operations worker so the route can return
    immediately. The caller polls the source list (or the task id) to
    discover the resulting source once the fetch + indexing pipeline
    completes.
    """

    task_id: str
    url: str
    status: str = "queued"


class SourceUpdate(BaseModel):
    """Request model for updating a source."""

    title: str | None = None
    processing_status: str | None = None  # "ready" | "error"
    enabled: bool | None = None
    user_metadata: dict[str, Any] | None = None


class UploadOptions(BaseModel):
    """The user's choices at upload time, persisted on the source row.

    Every entry path (file upload, URL, CLI) sets these on the row so
    recovery / retry / re-extract use exactly what the user picked.
    The API surfaces them under ``SourceResponse.upload_options`` so
    the UI can show "you uploaded this with vision off,
    filtering=strict" etc., and so source export can preserve the
    choices.
    """

    auto_analyze: bool = True
    extraction_depth: str = "full"
    forced_domain: str | None = None
    enable_normalization: bool | None = None
    enable_vision: bool = True
    content_filtering: bool = True
    filtering_mode: str = "balanced"
    enable_direction_correction: bool | None = None
    protect_orphans: bool | None = None
    # Phase 6 (2026-05-08): per-source inverse-relationships toggle and degree cap.
    enable_inverse_relationships: bool | None = None
    max_entity_degree_override: int | None = None


class QualityMetrics(BaseModel):
    """Per-stage drop / merge counters for a single source.

    Every stage of the import pipeline increments one of these
    counters when it filters, drops, merges, truncates, or replaces
    content.  The "Data Quality" tab on the source detail page renders
    the counters grouped by stage with plain-English explanations so
    the operator can see exactly what happened to their data.

    This model only exposes the columns - it does NOT itself change
    pipeline behavior.

    Field order mirrors the pipeline order: loader -> cleanup ->
    chunking -> AI extraction -> post-extraction -> commit -> search.
    """

    # --- Loader stage ---
    loader_encoding_used: str | None = None
    loader_warnings_count: int = 0
    loader_files_skipped: int = 0
    loader_replacement_chars_count: int = 0
    loader_pdf_pages_failed: int = 0
    loader_docx_paragraphs_skipped: int = 0
    loader_xlsx_rows_skipped: int = 0
    loader_csv_rows_truncated: int = 0
    # Phase 7 audit-remediation (2026-05-09): JSON-shaped per-key breakdowns
    # — dict[tag/shape -> count], or None if the loader never ran.  Rendered
    # in the Data Quality tab as a one-line summary of the top keys.
    loader_html_dropped_tags: dict[str, int] | None = None
    loader_pptx_shapes_skipped: dict[str, int] | None = None

    # --- Cleanup stage ---
    cleaner_lines_removed: int = 0
    cleaner_paragraphs_deduplicated: int = 0
    cleaner_chars_removed: int = 0
    cleaner_plugin_load_failures: int = 0
    ocr_cleaner_skipped_by_predicate: int = 0

    # --- Chunking stage ---
    # ``chunks_coalesced_count`` counts COALESCE / merge events, not drops:
    # a sub-threshold chunk being folded into a neighbor so all content
    # still reaches extraction. Phase 7 (2026-05-09): renamed from
    # ``chunks_filtered_count`` (see QualityCounter.CHUNKS_COALESCED,
    # Alembic 0029).
    chunks_coalesced_count: int = 0
    chunker_normalize_drops: int = 0
    chunker_prestrip_lines_removed: int = 0
    chunks_skipped_by_depth: int = 0
    standalone_chunk_failures: int = 0
    user_regex_timeout_hits: int = 0

    # --- AI extraction stage ---
    llm_chunks_truncated: int = 0
    llm_chunks_aborted_by_loop: int = 0
    llm_chunks_timed_out: int = 0
    llm_chunks_failed_permanent: int = 0
    parser_lines_dropped: int = 0
    semantic_dedup_fallbacks: int = 0
    chunks_rerun_total: int = 0

    # --- Post-extraction stage ---
    dedup_entities_merged: int = 0
    structural_entities_filtered: int = 0
    orphan_entities_filtered: int = 0
    relationships_dropped_invalid: int = 0
    relationships_dropped_capped: int = 0
    relationships_dropped_type_unmatched: int = 0
    relationships_direction_corrected: int = 0
    relationships_type_fuzzy_matched: int = 0
    relationships_type_fell_through: int = 0
    evidence_entities_dropped: int = 0
    evidence_relationships_dropped: int = 0
    aggregator_relationships_dropped: int = 0

    # --- Commit stage ---
    citations_skipped_no_chunk_index: int = 0
    citations_skipped_index_not_mapped: int = 0

    # --- Embedding stage ---
    embedding_chunk_failures: int = 0
    embedding_dimension_mismatches: int = 0

    # --- Vision stage ---
    vision_pages_truncated: int = 0
    # Wave 4-5 (2026-05-23): pages the work-queue builder skipped when
    # ``extraction_depth='quick'``. Renders in the Processing tab vision
    # tile as "Quick mode: <sampled> of <total> pages processed (<skipped>
    # skipped by Quick mode)" so a Quick run does not read as a partial
    # vision failure. Stays at 0 for ``extraction_depth='full'``.
    vision_pages_sampled_quick_mode: int = 0

    # --- Search stage ---
    vector_indexed_at: datetime | None = None
    # pending | indexed | degraded | failed
    vector_indexing_status: str = "pending"


class SourceResponse(BaseModel):
    """Response model for a unified source (upload through committed).

    Single response model for all source lifecycle stages:
    pending → indexing → indexed → extracting → extracted → committing → committed.
    """

    id: str
    database_name: str

    # File metadata
    filename: str
    filepath: str | None = None
    file_type: str | None = None
    file_size: int | None = None

    # Source metadata
    title: str | None = None
    source_type: str | None = None
    origin_url: str | None = None

    # Lifecycle status
    status: SourceStatus
    enabled: bool = True
    error_message: str | None = None
    error_stage: str | None = None

    # Resumability observability
    last_activity_at: datetime | None = None
    recovery_attempts: int = 0

    # Per-source pause state
    is_paused: bool = False
    paused_at: datetime | None = None
    paused_reason: str | None = None

    # Indexing stage
    chunk_count: int = 0
    total_content_length: int = 0
    embedding_model: str | None = None
    embedding_dimensions: int | None = None
    indexing_started_at: datetime | None = None
    indexing_completed_at: datetime | None = None
    indexing_duration_seconds: float | None = None
    # Extraction stage
    extraction_depth: str | None = None
    extraction_entities_count: int = 0
    extraction_relationships_count: int = 0
    extraction_domain: str | None = None  # Domain used (e.g., 'technical', 'generic')
    extraction_domain_auto: bool = True  # True if auto-detected, False if user-selected
    extraction_domain_icon: str | None = None  # MUI icon name for the domain
    domain_version: str | None = None  # Plugin version this source extracted under
    domain_changed_since_extraction: bool = False  # Live plugin hash differs from stored
    extraction_started_at: datetime | None = None
    extraction_completed_at: datetime | None = None
    extraction_duration_seconds: float | None = None
    current_extraction_job_id: str | None = None

    # Domain-confirmation gate (Phase 4, 2026-05-28). The raw proposal blob
    # hydrates from the source row (detection_proposal JSON column) but is
    # excluded from JSON; the three public fields below are the surface the
    # UI/CLI/MCP read — mirroring the upload_options / quality_metrics pattern.
    detection_proposal: dict[str, Any] | None = Field(default=None, exclude=True)
    confirmation_required: bool = Field(default=False)
    extraction_confirmed_at: datetime | None = Field(default=None)
    detection_ranking: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "Ranked detection candidates [{domain, score}] (best first) from the "
            "fast heuristic. Empty when detection wasn't confident (generic fallback)."
        ),
    )
    detection_confidence: float | None = Field(
        default=None,
        description="Winning candidate score (ranking[0].score) or None when low-confidence.",
    )
    detection_low_confidence: bool | None = Field(
        default=None,
        description=(
            "True when the fast heuristic fell back to generic (low_confidence flag "
            "from the detection_proposal blob). None for non-parked sources."
        ),
    )
    proposed_extraction_options: dict[str, Any] | None = Field(
        default=None,
        description=(
            "The full detection proposal blob (ranking, confidence, detected_domain, "
            "low_confidence) the UI seeds the confirm dialog from."
        ),
    )

    # MCP extraction progress
    extraction_mode: str | None = None

    # Per-stage LLM progress (stage-progress facility, 2026-05-09).
    stage_progress: dict[str, StageProgressRecord] = Field(
        default_factory=dict,
        description=(
            "Per-stage LLM progress for in-flight or completed stages. "
            "Keys are stage names ('vision', 'embedding', 'mcp_extraction', "
            "or any future stage). Empty dict when no stages have started. "
            "UI consumers iterate this to render progress tiles."
        ),
    )

    # Commit stage
    commit_started_at: datetime | None = None
    commit_completed_at: datetime | None = None
    commit_duration_seconds: float | None = None
    commit_nodes_created: int = 0
    commit_edges_created: int = 0
    commit_templates_created: int = 0

    # Progress tracking (UI)
    current_step: int | None = None
    total_steps: int | None = None
    step_description: str | None = None

    # LLM Metrics
    llm_total_calls: int = 0
    llm_successful_calls: int = 0
    llm_failed_calls: int = 0
    llm_retry_calls: int = 0
    llm_first_try_successes: int = 0
    llm_retry_successes: int = 0
    llm_permanent_failures: int = 0
    llm_total_input_tokens: int = 0
    llm_total_output_tokens: int = 0
    llm_wasted_tokens: int = 0
    llm_avg_call_duration_ms: int | None = None
    llm_total_duration_ms: int = 0
    llm_estimated_cost_usd: float | None = None
    llm_error_counts: dict[str, int] | None = None
    llm_model: str | None = None

    # Timestamps
    created_at: datetime
    updated_at: datetime

    # User metadata
    user_metadata: dict[str, Any] | None = None

    # Duplicate-skip metadata (populated only when skip_duplicates=True matched an existing row)
    skipped_duplicate: bool | None = Field(
        default=None,
        description="True if this upload was skipped because content hash matched an existing source.",
    )
    existing_status: SourceStatus | None = Field(
        default=None,
        description="When skipped_duplicate is True, status of the existing source ('error' to prompt retry).",
    )

    # Derived loader metadata — not a DB column; populated by model_validator.
    indexing_extraction_method: str | None = Field(
        default=None,
        description=(
            "Text-extraction method used during loading. Derived from source_type: "
            "PDF sources always use 'pypdf' (ADR-0003); other types return None."
        ),
    )

    # Public 5-phase progress — not a DB column; populated by model_validator.
    progress: SourceProgress | None = Field(
        default=None,
        description=(
            "User-facing 5-phase progress summary derived from ``status``. "
            "Phases: waiting_to_index | indexing | awaiting_input | extracting | ready. "
            "``is_searchable`` is True once indexing completes."
        ),
    )

    # Upload-time user choices (Workstream 1, 2026-05-07). The row
    # carries these as discrete columns; the validator below assembles
    # them into the nested ``upload_options`` object so the public API
    # contract is exactly one nested object rather than seven sibling
    # fields. Each sibling is marked ``exclude=True`` so it hydrates
    # the model from ``from_attributes`` but never appears in the
    # serialized JSON — ``upload_options`` is the sole public surface.
    forced_domain: str | None = Field(
        default=None,
        exclude=True,
        description="Domain forced at upload time (None means auto-detect).",
    )
    auto_analyze: bool = Field(
        default=True,
        exclude=True,
        description="Whether the upload flow should auto-queue analysis.",
    )
    enable_normalization: bool | None = Field(
        default=None,
        exclude=True,
        description=(
            "Normalization choice at upload (``None`` defers to the "
            "file-type default; ``True`` / ``False`` is an explicit "
            "override)."
        ),
    )
    enable_vision: bool = Field(
        default=True,
        exclude=True,
        description="Vision-LLM toggle for images and scanned PDFs.",
    )
    content_filtering: bool = Field(
        default=True,
        exclude=True,
        description="Domain content-exclusion toggle at upload time.",
    )
    filtering_mode: str = Field(
        default="balanced",
        exclude=True,
        description="Strictness preset for post-extraction filters.",
    )
    enable_direction_correction: bool | None = Field(
        default=None,
        exclude=True,
        description=(
            "Phase 4 (2026-05-08): per-source direction-correction toggle. "
            "None defers to domain config / ExtractionSettings default."
        ),
    )
    protect_orphans: bool | None = Field(
        default=None,
        exclude=True,
        description=(
            "Phase 4 (2026-05-08): per-source orphan-protection toggle. "
            "True keeps orphan entities; False drops them. "
            "None defers to domain config / ExtractionSettings default."
        ),
    )
    # Phase 6 (2026-05-08): per-source inverse-relationships toggle and degree cap.
    # Both are nullable (NULL = use global default). Excluded from JSON; surfaced
    # via upload_options.enable_inverse_relationships and
    # upload_options.max_entity_degree_override instead.
    enable_inverse_relationships: bool | None = Field(
        default=None,
        exclude=True,
        description=(
            "Phase 6 (2026-05-08): per-source inverse-relationships toggle. "
            "None defers to ExtractionSettings.enable_inverse_relationships (True)."
        ),
    )
    max_entity_degree_override: int | None = Field(
        default=None,
        exclude=True,
        description=(
            "Phase 6 (2026-05-08): per-source hard cap on relationships per entity. "
            "None defers to ExtractionSettings.max_entity_degree."
        ),
    )
    upload_options: UploadOptions | None = Field(
        default=None,
        description=(
            "User's choices at upload time (vision, filtering mode, "
            "content filtering, normalization, depth, domain). "
            "Persisted on the source row so recovery / retry / "
            "re-extract preserve user choice."
        ),
    )

    # Per-stage quality counters (Workstream 2, 2026-05-07; Phase 7
    # audit-remediation 2026-05-09 expanded the surface from 18 to 40+).
    # These columns hydrate the model via ``from_attributes`` but never
    # appear in the serialized JSON — ``quality_metrics`` is the sole
    # public surface, mirroring the ``upload_options`` pattern from W1.
    loader_encoding_used: str | None = Field(default=None, exclude=True)
    loader_warnings_count: int = Field(default=0, exclude=True)
    loader_files_skipped: int = Field(default=0, exclude=True)
    loader_replacement_chars_count: int = Field(default=0, exclude=True)
    loader_pdf_pages_failed: int = Field(default=0, exclude=True)
    loader_docx_paragraphs_skipped: int = Field(default=0, exclude=True)
    loader_xlsx_rows_skipped: int = Field(default=0, exclude=True)
    loader_csv_rows_truncated: int = Field(default=0, exclude=True)
    loader_html_dropped_tags: dict[str, int] | None = Field(default=None, exclude=True)
    loader_pptx_shapes_skipped: dict[str, int] | None = Field(default=None, exclude=True)
    cleaner_lines_removed: int = Field(default=0, exclude=True)
    cleaner_paragraphs_deduplicated: int = Field(default=0, exclude=True)
    cleaner_chars_removed: int = Field(default=0, exclude=True)
    cleaner_plugin_load_failures: int = Field(default=0, exclude=True)
    ocr_cleaner_skipped_by_predicate: int = Field(default=0, exclude=True)
    chunks_coalesced_count: int = Field(default=0, exclude=True)
    chunker_normalize_drops: int = Field(default=0, exclude=True)
    chunker_prestrip_lines_removed: int = Field(default=0, exclude=True)
    chunks_skipped_by_depth: int = Field(default=0, exclude=True)
    standalone_chunk_failures: int = Field(default=0, exclude=True)
    user_regex_timeout_hits: int = Field(default=0, exclude=True)
    llm_chunks_truncated: int = Field(default=0, exclude=True)
    llm_chunks_aborted_by_loop: int = Field(default=0, exclude=True)
    llm_chunks_timed_out: int = Field(default=0, exclude=True)
    llm_chunks_failed_permanent: int = Field(default=0, exclude=True)
    parser_lines_dropped: int = Field(default=0, exclude=True)
    semantic_dedup_fallbacks: int = Field(default=0, exclude=True)
    chunks_rerun_total: int = Field(default=0, exclude=True)
    dedup_entities_merged: int = Field(default=0, exclude=True)
    structural_entities_filtered: int = Field(default=0, exclude=True)
    orphan_entities_filtered: int = Field(default=0, exclude=True)
    relationships_dropped_invalid: int = Field(default=0, exclude=True)
    relationships_dropped_capped: int = Field(default=0, exclude=True)
    relationships_dropped_type_unmatched: int = Field(default=0, exclude=True)
    relationships_direction_corrected: int = Field(default=0, exclude=True)
    relationships_type_fuzzy_matched: int = Field(default=0, exclude=True)
    relationships_type_fell_through: int = Field(default=0, exclude=True)
    evidence_entities_dropped: int = Field(default=0, exclude=True)
    evidence_relationships_dropped: int = Field(default=0, exclude=True)
    aggregator_relationships_dropped: int = Field(default=0, exclude=True)
    citations_skipped_no_chunk_index: int = Field(default=0, exclude=True)
    citations_skipped_index_not_mapped: int = Field(default=0, exclude=True)
    embedding_chunk_failures: int = Field(default=0, exclude=True)
    embedding_dimension_mismatches: int = Field(default=0, exclude=True)
    vision_pages_truncated: int = Field(default=0, exclude=True)
    vision_pages_sampled_quick_mode: int = Field(default=0, exclude=True)
    vector_indexed_at: datetime | None = Field(default=None, exclude=True)
    vector_indexing_status: str = Field(default="pending", exclude=True)

    quality_metrics: QualityMetrics | None = Field(
        default=None,
        description=(
            "Per-stage drop / merge / warning counters recorded as the "
            "source moved through the import pipeline. Aggregated from "
            "the row-level columns by a model validator; the UI's 'Data "
            "Quality' tab consumes this object directly."
        ),
    )

    @model_validator(mode="after")
    def _derive_computed_fields(self) -> SourceResponse:
        """Populate derived fields after model initialisation.

        - ``indexing_extraction_method``: PDF sources always use pypdf
          (ADR-0003); other types leave the field as None.
        - ``progress``: 5-phase public progress derived from ``status``.
        - ``upload_options``: nested object assembled from the per-row
          columns above.
        """
        if self.indexing_extraction_method is None and self.source_type == "pdf":
            self.indexing_extraction_method = "pypdf"
        if self.progress is None:
            self.progress = map_status_to_progress(str(self.status))
        if self.upload_options is None:
            self.upload_options = UploadOptions(
                auto_analyze=self.auto_analyze,
                extraction_depth=self.extraction_depth or "full",
                forced_domain=self.forced_domain,
                enable_normalization=self.enable_normalization,
                enable_vision=self.enable_vision,
                content_filtering=self.content_filtering,
                filtering_mode=self.filtering_mode,
                enable_direction_correction=self.enable_direction_correction,
                protect_orphans=self.protect_orphans,
                enable_inverse_relationships=self.enable_inverse_relationships,
                max_entity_degree_override=self.max_entity_degree_override,
            )
        if self.quality_metrics is None:
            # Workstream 2 (2026-05-07) / Phase 7 audit-remediation
            # (2026-05-09): assemble the nested ``quality_metrics`` object
            # from every row-level counter column so the API contract is
            # one well-named object rather than 40+ scattered top-level
            # fields. Field order mirrors the pipeline: loader → cleanup
            # → chunking → AI extraction → post-extraction → commit →
            # embedding → search.
            self.quality_metrics = QualityMetrics(
                loader_encoding_used=self.loader_encoding_used,
                loader_warnings_count=self.loader_warnings_count,
                loader_files_skipped=self.loader_files_skipped,
                loader_replacement_chars_count=self.loader_replacement_chars_count,
                loader_pdf_pages_failed=self.loader_pdf_pages_failed,
                loader_docx_paragraphs_skipped=self.loader_docx_paragraphs_skipped,
                loader_xlsx_rows_skipped=self.loader_xlsx_rows_skipped,
                loader_csv_rows_truncated=self.loader_csv_rows_truncated,
                loader_html_dropped_tags=self.loader_html_dropped_tags,
                loader_pptx_shapes_skipped=self.loader_pptx_shapes_skipped,
                cleaner_lines_removed=self.cleaner_lines_removed,
                cleaner_paragraphs_deduplicated=self.cleaner_paragraphs_deduplicated,
                cleaner_chars_removed=self.cleaner_chars_removed,
                cleaner_plugin_load_failures=self.cleaner_plugin_load_failures,
                ocr_cleaner_skipped_by_predicate=self.ocr_cleaner_skipped_by_predicate,
                chunks_coalesced_count=self.chunks_coalesced_count,
                chunker_normalize_drops=self.chunker_normalize_drops,
                chunker_prestrip_lines_removed=self.chunker_prestrip_lines_removed,
                chunks_skipped_by_depth=self.chunks_skipped_by_depth,
                standalone_chunk_failures=self.standalone_chunk_failures,
                user_regex_timeout_hits=self.user_regex_timeout_hits,
                llm_chunks_truncated=self.llm_chunks_truncated,
                llm_chunks_aborted_by_loop=self.llm_chunks_aborted_by_loop,
                llm_chunks_timed_out=self.llm_chunks_timed_out,
                llm_chunks_failed_permanent=self.llm_chunks_failed_permanent,
                parser_lines_dropped=self.parser_lines_dropped,
                semantic_dedup_fallbacks=self.semantic_dedup_fallbacks,
                chunks_rerun_total=self.chunks_rerun_total,
                dedup_entities_merged=self.dedup_entities_merged,
                structural_entities_filtered=self.structural_entities_filtered,
                orphan_entities_filtered=self.orphan_entities_filtered,
                relationships_dropped_invalid=self.relationships_dropped_invalid,
                relationships_dropped_capped=self.relationships_dropped_capped,
                relationships_dropped_type_unmatched=self.relationships_dropped_type_unmatched,
                relationships_direction_corrected=self.relationships_direction_corrected,
                relationships_type_fuzzy_matched=self.relationships_type_fuzzy_matched,
                relationships_type_fell_through=self.relationships_type_fell_through,
                evidence_entities_dropped=self.evidence_entities_dropped,
                evidence_relationships_dropped=self.evidence_relationships_dropped,
                aggregator_relationships_dropped=self.aggregator_relationships_dropped,
                citations_skipped_no_chunk_index=self.citations_skipped_no_chunk_index,
                citations_skipped_index_not_mapped=self.citations_skipped_index_not_mapped,
                embedding_chunk_failures=self.embedding_chunk_failures,
                embedding_dimension_mismatches=self.embedding_dimension_mismatches,
                vision_pages_truncated=self.vision_pages_truncated,
                vision_pages_sampled_quick_mode=self.vision_pages_sampled_quick_mode,
                vector_indexed_at=self.vector_indexed_at,
                vector_indexing_status=self.vector_indexing_status,
            )
        # Domain-confirmation gate (Phase 4, 2026-05-28): unpack the persisted
        # proposal blob onto the public detection_* fields. Sole source is the
        # row's detection_proposal JSON; absent for non-parked sources.
        if self.detection_proposal:
            self.proposed_extraction_options = self.detection_proposal
            ranking = self.detection_proposal.get("ranking") or []
            if isinstance(ranking, list):
                self.detection_ranking = ranking
            conf = self.detection_proposal.get("confidence")
            if isinstance(conf, (int, float)):
                self.detection_confidence = float(conf)
            low_conf = self.detection_proposal.get("low_confidence")
            if isinstance(low_conf, bool):
                self.detection_low_confidence = low_conf
        return self

    model_config = ConfigDict(from_attributes=True)


class TagSummary(BaseModel):
    """Minimal tag info for source list views."""

    id: str
    name: str
    color: str | None = None


class SourceSummaryResponse(BaseModel):
    """Lightweight response model for source list views.

    Contains only the fields used by the UI list/table view (~25 fields),
    excluding large payload fields like user_metadata, detailed timestamps,
    and fields only needed in detail views.
    """

    id: str
    database_name: str

    # File metadata
    filename: str
    file_type: str | None = None
    file_size: int | None = None

    # Source metadata
    title: str | None = None
    source_type: str | None = None

    # Lifecycle status
    status: SourceStatus
    enabled: bool = True
    error_message: str | None = None
    error_stage: str | None = None

    # Recovery state (for D1 indicators in list view)
    recovery_attempts: int = 0

    # Per-source pause state
    is_paused: bool = False
    paused_at: datetime | None = None
    paused_reason: str | None = None

    # Indexing
    chunk_count: int = 0
    embedding_model: str | None = None
    embedding_dimensions: int | None = None

    # Extraction
    extraction_depth: str | None = None
    extraction_entities_count: int = 0
    extraction_relationships_count: int = 0
    extraction_domain: str | None = None
    extraction_domain_auto: bool = True
    extraction_domain_icon: str | None = None
    domain_version: str | None = None  # Plugin version this source extracted under
    domain_changed_since_extraction: bool = False  # Live plugin hash differs from stored

    # Commit
    commit_nodes_created: int = 0
    commit_edges_created: int = 0
    commit_templates_created: int = 0

    # Durations (calculated by mapper)
    indexing_duration_seconds: float | None = None
    extraction_duration_seconds: float | None = None
    commit_duration_seconds: float | None = None

    # Progress tracking (UI)
    current_step: int | None = None
    total_steps: int | None = None
    step_description: str | None = None

    # LLM Metrics (subset used in list view)
    llm_total_calls: int = 0
    llm_first_try_successes: int = 0
    llm_retry_successes: int = 0
    llm_permanent_failures: int = 0
    llm_total_input_tokens: int = 0
    llm_total_output_tokens: int = 0
    llm_model: str | None = None

    # MCP extraction progress
    extraction_mode: str | None = None

    # Per-stage LLM progress (stage-progress facility, 2026-05-09).
    stage_progress: dict[str, StageProgressRecord] = Field(
        default_factory=dict,
        description=(
            "Per-stage LLM progress for in-flight or completed stages. "
            "Keys are stage names ('vision', 'embedding', 'mcp_extraction', "
            "or any future stage). Empty dict when no stages have started. "
            "UI consumers iterate this to render progress tiles."
        ),
    )

    # Quality
    cached_quality_grade: str | None = None
    cached_quality_label: str | None = None

    # Vector-search visibility (Workstream 10).  Surfaced flat on the
    # summary so the source list can render the SearchStatusBadge
    # without loading the full QualityMetrics object.
    vector_indexed_at: datetime | None = None
    vector_indexing_status: str = "pending"

    # Tags (enriched by API endpoint)
    tags: list[TagSummary] | None = None

    # Timestamps
    created_at: datetime
    updated_at: datetime

    # Domain-confirmation gate (Phase 4, 2026-05-28). The raw proposal blob
    # is hidden from JSON; the five public fields below are the same surface
    # exposed on SourceResponse so the frontend UnifiedSource mapping is
    # uniform across list and detail endpoints. Task 2.3 widened the
    # list_sources load_only() projection to include detection_proposal,
    # confirmation_required, and extraction_confirmed_at so all data is
    # available for the list view. For non-parked rows detection_proposal is
    # None and the derived fields remain at their defaults.
    detection_proposal: dict[str, Any] | None = Field(default=None, exclude=True)
    confirmation_required: bool = Field(default=False)
    extraction_confirmed_at: datetime | None = Field(default=None)
    detection_ranking: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "Ranked detection candidates [{domain, score}] (best first) from the "
            "fast heuristic. Empty when detection wasn't confident (generic fallback)."
        ),
    )
    detection_confidence: float | None = Field(
        default=None,
        description="Winning candidate score (ranking[0].score) or None when low-confidence.",
    )
    detection_low_confidence: bool | None = Field(
        default=None,
        description=(
            "True when the fast heuristic fell back to generic (low_confidence flag "
            "from the detection_proposal blob). None for non-parked sources."
        ),
    )
    proposed_extraction_options: dict[str, Any] | None = Field(
        default=None,
        description=(
            "The full detection proposal blob (ranking, confidence, detected_domain, "
            "low_confidence) the UI seeds the confirm dialog from."
        ),
    )

    @model_validator(mode="after")
    def _derive_detection_fields(self) -> SourceSummaryResponse:
        """Unpack detection_proposal onto the public detection_* fields.

        Mirrors the derivation in SourceResponse._derive_computed_fields so the
        frontend UnifiedSource mapping is uniform for list and detail responses.
        For non-parked sources detection_proposal is None and all derived fields
        remain at their defaults (empty list / None).
        """
        if self.detection_proposal:
            self.proposed_extraction_options = self.detection_proposal
            ranking = self.detection_proposal.get("ranking") or []
            if isinstance(ranking, list):
                self.detection_ranking = ranking
            conf = self.detection_proposal.get("confidence")
            if isinstance(conf, (int, float)):
                self.detection_confidence = float(conf)
            low_conf = self.detection_proposal.get("low_confidence")
            if isinstance(low_conf, bool):
                self.detection_low_confidence = low_conf
        return self

    model_config = ConfigDict(from_attributes=True)


class PaginatedSourcesResponse(BaseModel):
    """Paginated response for listing sources."""

    data: list[SourceSummaryResponse]
    pagination: PaginationMetadata


# ================================
# Chunk DTOs
# ================================


class ChunkResponse(BaseModel):
    """Response model for a document chunk."""

    id: str
    source_id: str | None = None
    chunk_index: int
    content: str
    page_number: int | None = None
    section: str | None = None
    group_index: int | None = None
    # Phase 5a: char offsets into original upload text (NULL when method is 'none').
    char_start: int | None = None
    char_end: int | None = None
    # How char_start/char_end were computed:
    #   'exact'  — substring match against original upload text.
    #   'fuzzy'  — rapidfuzz partial_ratio_alignment fallback (≥80 score).
    #   'none'   — unlocatable; char_start/char_end are NULL.
    # Default 'exact' covers legacy rows and callers that don't supply original_text.
    citation_offset_method: Literal["exact", "fuzzy", "none"] = "exact"
    status: str
    created_at: datetime
    raw_content: str | None = Field(
        default=None,
        description=(
            "Pre-cleanup text slice for this chunk. None for sources extracted "
            "before migration 0040. Omitted from list responses for payload "
            "size reasons (the list service uses load_only() to exclude this "
            "column); only returned by the single-chunk detail endpoint."
        ),
    )
    chunk_metadata: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Chunk-level metadata, notably ``sentence_offsets`` (per-sentence "
            "char ranges) used to highlight the exact cited sentence when the "
            "source page is opened from a citation. Like raw_content it is "
            "omitted from list responses (load_only() excludes the JSON column) "
            "and only returned by the single-chunk detail endpoint."
        ),
    )

    model_config = ConfigDict(from_attributes=True)


class ChunkListResponse(BaseModel):
    """Paginated chunks for a source.

    Uses the house-standard {data, pagination} envelope consistent with
    all other paginated list endpoints (e.g. PaginatedSourcesResponse).
    """

    data: list[ChunkResponse]
    pagination: PaginationMetadata


# ================================
# Extraction Task DTOs
# ================================


class ExtractionTaskResponse(BaseModel):
    """Response model for a single chunk extraction task."""

    id: str
    job_id: str
    chunk_index: int
    hierarchical_group_id: str | None = None
    small_chunk_ids: list[str] | None = None
    status: str  # pending|queued|running|completed|failed

    # Timing
    created_at: datetime
    queued_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    llm_duration_ms: int | None = None

    # Results
    retry_count: int = 0
    entity_count: int = 0
    relationship_count: int = 0
    invalid_relationship_count: int = 0

    # Chunk numbers for UI display (chunk_index + 1 for each small chunk)
    small_chunk_numbers: list[int] | None = None

    # Input/Output lengths (for charts without loading full text)
    input_text_length: int | None = None
    llm_response_length: int | None = None

    # Token tracking (actual counts from LLM API)
    input_tokens: int | None = None
    output_tokens: int | None = None
    context_window_available: int | None = None

    # Input/Output content (optional, for detail views)
    input_text: str | None = None
    llm_response_json: str | None = None

    # Pipeline filtering diagnostics (optional, for detail views)
    filtering_log: dict | None = None

    # Error info
    error_message: str | None = None
    error_type: str | None = None

    model_config = ConfigDict(from_attributes=True)


class ExtractionTaskListResponse(BaseModel):
    """Response model for listing extraction tasks."""

    tasks: list[ExtractionTaskResponse]
    total: int
    page: int
    page_size: int


class ExtractionTaskStatsResponse(BaseModel):
    """Response model for extraction task aggregate statistics.

    Provides min/avg/max statistics computed via SQL aggregates,
    allowing charts to display accurate data for ALL tasks without
    loading every row.
    """

    total_tasks: int
    context_window: int | None = None
    # Input tokens
    min_input_tokens: int | None = None
    max_input_tokens: int | None = None
    avg_input_tokens: int | None = None
    # Output tokens
    min_output_tokens: int | None = None
    max_output_tokens: int | None = None
    avg_output_tokens: int | None = None
    # Total tokens (input + output)
    min_total_tokens: int | None = None
    max_total_tokens: int | None = None
    avg_total_tokens: int | None = None
    # Utilization percentages
    min_utilization: float | None = None
    max_utilization: float | None = None
    avg_utilization: float | None = None
    # Duration
    min_duration_ms: int | None = None
    max_duration_ms: int | None = None
    avg_duration_ms: int | None = None
    # Entity counts
    total_entities: int = 0
    avg_entities_per_task: float = 0
    # Relationship counts
    total_relationships: int = 0
    avg_relationships_per_task: float = 0
    # Retry stats
    total_retries: int = 0
    max_retries_single_task: int = 0
    # Invalid relationship stats
    total_invalid_relationships: int = 0
    avg_invalid_per_task: float = 0
    # Pipeline filtering aggregates
    total_entities_filtered: int = 0
    total_relationships_filtered: int = 0
    filtering_stage_summary: list[dict] | None = None
    # Shared LLM prompts (from job, same for all chunks)
    system_prompt: str | None = None
    # Separate parts for distinct UI display
    extraction_rules_template: str | None = None
    entity_templates: str | None = None
    relationship_templates: str | None = None
    domain_guidance: str | None = None
    domain_examples: str | None = None
    # User-provided instructions
    user_instructions: str | None = None
    user_instructions_template: str | None = None
    # Pass-2 relationship prompt template, shown next to user_instructions
    # (the pass-1 entity prompt template) on the Processing tab.
    relationship_instructions: str | None = None


# ================================
# Citation DTOs
# ================================


class SourceCitationResponse(BaseModel):
    """Response model for a source citation."""

    id: str
    entity_uri: str
    entity_label: str
    entity_type: str | None = None
    source_id: str
    chunk_id: str
    confidence: float
    extraction_method: str
    context_snippet: str | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SourceCitationListResponse(BaseModel):
    """Response model for listing citations."""

    citations: list[SourceCitationResponse]
    total: int
    page: int
    page_size: int


# ================================
# Admin / Maintenance DTOs
# ================================


class RecoveryEventResponse(BaseModel):
    """One row of the source recovery audit trail.

    Surfaces in the source detail page so operators can diagnose the
    "auto-recovered N times" warning without grepping logs.
    """

    id: str = Field(description="Event row identifier.")
    source_id: str = Field(description="Source whose recovery this event records.")
    database_name: str = Field(description="Database scope.")
    attempt_at: datetime = Field(description="When the recovery dispatch fired.")
    from_status: str = Field(description="SourceRow.status when the classifier fired.")
    action_taken: str = Field(
        description=(
            'Operation dispatched. One of "extract_chunk", "import_commit", '
            '"index_document", "import_analysis", "finalize_extraction", '
            'or "compound" (multi-task dispatch).'
        ),
    )
    reason: str = Field(
        description=(
            'Why the classifier fired. Today: "stalled" (default bulk '
            'reconcile path) or "compound" (multi-chunk dispatch).'
        ),
    )
    enqueued_count: int = Field(
        description=(
            "Number of queue tasks actually enqueued by this dispatch. 1 "
            "for single-task actions; >1 for compound."
        ),
    )


class RecoveryEventListResponse(BaseModel):
    """Response from GET /api/v1/sources/{source_id}/recovery_events."""

    events: list[RecoveryEventResponse] = Field(
        default_factory=list,
        description="Events ordered newest first.",
    )


class OrphanTaskCleanupResponse(BaseModel):
    """Response from POST /api/v1/sources/cleanup/orphan_tasks.

    Reports how many orphaned chunk tasks were deleted. Orphaned tasks
    are created by BE-7's cascade update when an ExtractionJob fails
    (non-terminal tasks become status='orphaned'). The periodic cleanup
    job runs every 24h by default; this endpoint lets operators trigger
    an immediate pass (e.g. after a bulk failure).
    """

    deleted_count: int = Field(
        description="Number of orphaned chunk task rows deleted.",
    )
    retention_days: int = Field(
        description=(
            "Retention threshold used for this run (from "
            "SourceRecoverySettings.orphan_task_retention_days)."
        ),
    )


# ================================
# Tag DTOs
# ================================


class TagCreate(BaseModel):
    """Request model for creating a tag."""

    name: str
    color: str | None = None
    description: str | None = None


class TagUpdate(BaseModel):
    """Request model for updating a tag."""

    name: str | None = None
    color: str | None = None
    description: str | None = None


class TagResponse(BaseModel):
    """Response model for a tag."""

    id: str
    database_name: str
    name: str
    color: str | None = None
    description: str | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ================================
# Domain DTOs
# ================================


class DomainInfo(BaseModel):
    """A single extraction domain's UI-facing info.

    Shape mirrors ``DomainRegistry._domain_info[name]`` in
    ``chaoscypher_core.services.sources.engine.extraction.domains.registry``.
    """

    name: str = Field(description="Domain plugin name (e.g. 'technical', 'generic').")
    description: str = Field(description="Human-readable domain description.")
    icon: str | None = Field(default=None, description="Optional MUI icon name.")
    version: str = Field(description="Domain version string, or 'unknown'.")
    builtin: bool = Field(description="True for shipped domains, False for user plugins.")
    has_examples: bool = Field(description="True if the domain provides few-shot examples.")
    prompt_tokens: int = Field(
        description="Estimated prompt token count for capacity calculations."
    )
    extraction_density: float = Field(
        description=(
            "Domain extraction density factor used when sizing chunk groups. "
            "Continuous multiplier in the [0.5, 2.0] range; higher means "
            "more entities expected per chunk, which scales token budgets. "
            "Defaults to 1.0 and may be overridden per domain."
        )
    )


class DomainListResponse(BaseModel):
    """Response for ``GET /api/v1/sources/domains``."""

    domains: list[DomainInfo] = Field(
        description="Available extraction domains for the current database."
    )


# ================================
# Processing Stats DTOs
# ================================


class ProcessingStatsResponse(BaseModel):
    """Response for ``GET /api/v1/sources/stats``.

    Aggregate counts over all sources in the current database, produced by
    ``SourceProcessingService.get_stats`` (backed by
    ``SqliteAdapter.get_stats``).
    """

    total_files: int = Field(description="Total number of sources in the database.")
    by_status: dict[str, int] = Field(
        description=(
            "Count of sources grouped by lifecycle status "
            "(pending|indexing|indexed|extracting|extracted|committing|committed|error)."
        )
    )
    total_size_bytes: int = Field(description="Sum of ``file_size`` across all sources in bytes.")


# ================================
# Source Image DTOs
# ================================


class SourceImageInfo(BaseModel):
    """Single rendered page image for a source document."""

    filename: str = Field(description="PNG filename (e.g. 'page_001.png').")
    url: str = Field(
        description="Relative URL to fetch the image via GET /sources/{id}/images/{filename}."
    )


# ================================
# Batch Upload DTOs
# ================================


class BatchUploadError(BaseModel):
    """A single failed file in a batch upload."""

    filename: str = Field(description="Original filename (or 'unknown' if missing).")
    error: str = Field(description="Short, user-safe error message for the failed upload.")


class BatchUploadResponse(BaseModel):
    """Response for ``POST /api/v1/sources/batch``.

    Reports per-file outcomes for a multi-file upload. Successful uploads land
    in ``files`` as full ``SourceResponse`` payloads; failures land in
    ``errors`` with a filename and short diagnostic string.
    """

    uploaded: int = Field(description="Number of files successfully accepted.")
    failed: int = Field(description="Number of files that failed upload.")
    files: list[SourceResponse] = Field(
        description="Successfully queued sources (one per uploaded file)."
    )
    errors: list[BatchUploadError] = Field(description="Per-file error details for failed uploads.")


# ================================
# Extraction Status DTOs
# ================================


class ExtractionTimingStats(BaseModel):
    """Timing statistics for an extraction job (derived from chunk tasks)."""

    model_config = ConfigDict(extra="allow")

    avg_chunk_duration_ms: float | None = Field(
        default=None, description="Average per-chunk LLM duration in milliseconds."
    )
    min_chunk_duration_ms: int | None = Field(
        default=None, description="Fastest chunk duration in milliseconds."
    )
    max_chunk_duration_ms: int | None = Field(
        default=None, description="Slowest chunk duration in milliseconds."
    )
    total_duration_ms: int | None = Field(
        default=None, description="Sum of chunk LLM durations in milliseconds."
    )
    eta_seconds: float | None = Field(
        default=None,
        description=(
            "Estimated seconds until the job finishes, computed from average duration "
            "and remaining chunks."
        ),
    )


class RunningChunkTask(BaseModel):
    """Currently-running chunk task summary (subset of ExtractionTaskResponse)."""

    model_config = ConfigDict(extra="allow")

    id: str | None = None
    chunk_index: int | None = None
    status: str | None = None
    started_at: datetime | None = None


class ExtractionStatusResponse(BaseModel):
    """Response for ``GET /api/v1/sources/{id}/extraction``.

    Matches ``SourceService.get_extraction_status`` which returns either a
    full progress payload (when a job exists) or a short "no job" payload.
    Optional fields cover both cases without a discriminated union.
    """

    source_id: str = Field(description="The source file ID.")
    status: str | None = Field(
        default=None,
        description=(
            "Job status when ``has_extraction_job`` is True, otherwise the source's "
            "current lifecycle status."
        ),
    )
    has_extraction_job: bool = Field(
        description="True when a ChunkExtractionJob is associated with this source."
    )
    message: str | None = Field(
        default=None,
        description="Human-readable note when there is no active job.",
    )

    # Present only when has_extraction_job is True
    job_id: str | None = Field(default=None, description="ChunkExtractionJob ID.")
    total_chunks: int | None = Field(default=None, description="Total chunks planned for the job.")
    completed_chunks: int | None = Field(
        default=None, description="Number of chunks that completed successfully."
    )
    failed_chunks: int | None = Field(
        default=None, description="Number of chunks that failed permanently."
    )
    progress_percent: float | None = Field(
        default=None,
        description="(completed + failed) / total * 100, rounded to one decimal.",
    )
    chunks_by_status: dict[str, int] | None = Field(
        default=None, description="Chunk count grouped by task status."
    )
    total_entities: int | None = Field(
        default=None, description="Cumulative entities extracted across completed chunks."
    )
    total_relationships: int | None = Field(
        default=None,
        description="Cumulative relationships extracted across completed chunks.",
    )
    extraction_depth: str | None = Field(
        default=None, description="Job extraction depth (quick/full)."
    )
    started_at: datetime | None = Field(default=None, description="When the job started.")
    completed_at: datetime | None = Field(
        default=None, description="When the job finished (success or failure)."
    )
    timing: ExtractionTimingStats | None = Field(
        default=None, description="Per-chunk timing aggregates."
    )
    current_chunk: RunningChunkTask | None = Field(
        default=None, description="The chunk task currently being processed, if any."
    )


# ================================
# Extraction Chart DTOs
# ================================


class ExtractionTaskChartPoint(BaseModel):
    """One row of chart-ready extraction task data.

    Matches the ``load_only`` projection in
    ``SqliteAdapter.get_extraction_tasks_for_charts``.
    """

    id: str = Field(description="Chunk extraction task ID.")
    chunk_index: int = Field(description="Chunk ordinal within the source.")
    status: str = Field(description="Task status (pending|queued|running|completed|failed).")
    retry_count: int = Field(description="Number of retry attempts for this task.")
    entity_count: int = Field(description="Entities extracted for this chunk group.")
    relationship_count: int = Field(description="Relationships extracted for this chunk group.")
    invalid_relationship_count: int = Field(description="Relationships rejected during validation.")
    input_text_length: int | None = Field(
        default=None, description="Characters of input text sent to the LLM."
    )
    llm_duration_ms: int | None = Field(
        default=None, description="LLM call duration in milliseconds."
    )


# ================================
# Filtering Log DTOs
# ================================


FilteringLogResponse = dict[str, Any]
"""Response alias for ``GET /api/v1/sources/{id}/extraction/filteringlog``.

This endpoint returns the raw cross-chunk deduplication filtering log as
stored on ``sources.cross_chunk_filtering_log``. The log's shape is
pipeline-internal and evolves as new filtering stages are added, so it is
exposed as a free-form mapping. Each stage contributes a keyed entry whose
schema is owned by the stage itself; pinning the top-level shape would
couple the API contract to every pipeline change.
"""


# ================================
# Source Stats DTOs
# ================================


class TopEntityInfo(BaseModel):
    """One row of the per-source top-cited-entities list."""

    label: str = Field(description="Entity label as cited in this source.")
    type: str | None = Field(default=None, description="Entity template/type.")
    count: int = Field(description="Number of citations for this entity in the source.")


class SourceStatsResponse(BaseModel):
    """Response for ``GET /api/v1/sources/{id}/stats``.

    Shape mirrors ``SqliteAdapter.get_source_stats``.
    """

    total_chunks: int = Field(description="Total document chunks for this source.")
    total_content_length: int = Field(description="Sum of chunk content lengths in characters.")
    committed_chunks: int = Field(description="Chunks with status='committed'.")
    staged_chunks: int = Field(description="Chunks with status='staged'.")
    rejected_chunks: int = Field(description="Chunks with status='rejected'.")
    total_citations: int = Field(description="Total entity citations generated from this source.")
    entity_count: int = Field(description="Unique entities cited in this source.")
    relationship_count: int = Field(description="Total relationship citations from this source.")
    entity_type_distribution: dict[str, int] = Field(
        description="Citation count grouped by entity template/type."
    )
    relationship_type_distribution: dict[str, int] = Field(
        description="Relationship-citation count grouped by edge label."
    )
    top_entities: list[TopEntityInfo] = Field(
        description="Up to 10 most-cited entities in the source."
    )
    avg_confidence: float = Field(
        description="Average citation confidence (0.0-1.0), rounded to 2 decimals."
    )


# ================================
# Entities / Relationships / Templates / LLM Metrics Pagination
# ================================


class EntityPagination(BaseModel):
    """Pagination block used by /sources/{id}/entities, relationships, templates, llm_metrics/calls.

    Distinct from :class:`PaginationMetadata` because the service layer emits
    these routes' pagination dicts directly (matching the legacy frontend
    contract) and because they live alongside non-``data`` collection keys
    (``entities``, ``relationships``, ``templates``, ``calls``).
    """

    page: int = Field(description="Current page number (1-indexed).")
    page_size: int = Field(description="Items per page.")
    total: int = Field(description="Total items across all pages.")
    total_pages: int = Field(description="Total number of pages.")
    has_next: bool = Field(description="True when another page exists after this one.")
    has_prev: bool = Field(description="True when a page exists before this one.")


SourceEntity = dict[str, Any]
"""Per-entity payload alias used by ``SourceEntitiesResponse``.

Entity dicts are produced directly by domain extractors and evolve with the
domain definition (name, type, confidence, properties, source_chunks,
quality_score, plus any domain-specific keys). Pinning a concrete schema
here would lock every new domain to the same fields; instead we expose the
entity as a free-form mapping and keep the surrounding envelope typed.
"""


class SourceEntitiesResponse(BaseModel):
    """Response for ``GET /api/v1/sources/{id}/entities``."""

    entities: list[SourceEntity] = Field(
        description="Entities for this page (free-form per-domain shape)."
    )
    pagination: EntityPagination = Field(description="Pagination metadata.")


SourceRelationship = dict[str, Any]
"""Per-relationship payload alias used by ``SourceRelationshipsResponse``.

Relationship dicts carry domain-specific keys (source/target indices, type,
confidence, evidence) plus the service-enriched ``from`` and ``to`` labels.
Exposed as free-form for the same reasons as :data:`SourceEntity`.
"""


class SourceRelationshipsResponse(BaseModel):
    """Response for ``GET /api/v1/sources/{id}/relationships``."""

    relationships: list[SourceRelationship] = Field(
        description=(
            "Relationships for this page. Each dict includes the extractor's native "
            "fields plus service-enriched ``from``/``to`` entity labels."
        )
    )
    pagination: EntityPagination = Field(description="Pagination metadata.")


class SourceTemplateInfo(BaseModel):
    """One template row for ``GET /api/v1/sources/{id}/templates``.

    Mirrors ``TemplateService.list_templates`` output.
    """

    id: str = Field(description="Template ID.")
    name: str = Field(description="Template name.")
    description: str | None = Field(default=None, description="Template description.")
    template_type: str = Field(description="'node' or 'edge'.")
    properties: list[dict[str, Any]] = Field(
        description="Template property definitions (serialized PropertyDefinition models)."
    )
    is_system: bool = Field(description="True for system-provided templates.")
    icon: str | None = Field(default=None, description="Optional MUI icon name.")
    color: str | None = Field(default=None, description="Optional hex color.")
    source_id: str | None = Field(
        default=None, description="Source that produced this template (if any)."
    )
    node_count: int = Field(description="Nodes currently using this template.")
    edge_count: int = Field(description="Edges currently using this template.")
    created_at: datetime = Field(description="Template creation timestamp.")
    updated_at: datetime = Field(description="Last modification timestamp.")


class SourceTemplatesResponse(BaseModel):
    """Response for ``GET /api/v1/sources/{id}/templates``."""

    templates: list[SourceTemplateInfo] = Field(
        description="Templates owned/produced by this source for the requested page."
    )
    pagination: EntityPagination = Field(description="Pagination metadata.")


# ================================
# LLM Metrics DTOs
# ================================


class LLMMetricsSummary(BaseModel):
    """Aggregate LLM metrics for a single source."""

    total_calls: int = Field(description="Total LLM calls made while processing this source.")
    successful_calls: int = Field(description="Calls that returned a usable response.")
    failed_calls: int = Field(description="Calls that failed permanently.")
    retry_calls: int = Field(description="Calls that were retries of an earlier attempt.")
    first_try_successes: int = Field(description="Calls that succeeded on the first attempt.")
    retry_successes: int = Field(description="Calls that succeeded after one or more retries.")
    permanent_failures: int = Field(description="Calls that exhausted retries without success.")
    total_input_tokens: int = Field(description="Sum of input tokens across all calls.")
    total_output_tokens: int = Field(description="Sum of output tokens across all calls.")
    wasted_tokens: int = Field(
        description="Tokens spent on failed attempts that produced no usable output."
    )
    avg_call_duration_ms: int | None = Field(
        default=None, description="Average LLM call duration in milliseconds."
    )
    total_duration_ms: int = Field(description="Sum of LLM call durations in milliseconds.")
    estimated_cost_usd: float | None = Field(
        default=None,
        description="Estimated USD cost based on the configured per-model pricing.",
    )
    error_counts: dict[str, int] = Field(description="Count of failures grouped by error_type.")
    model: str = Field(description="Display name of the model used for extraction.")
    success_rate: float = Field(
        description="successful_calls / total_calls (0.0-1.0); 0.0 when total is zero."
    )
    retry_rate: float = Field(
        description="retry_calls / total_calls (0.0-1.0); 0.0 when total is zero."
    )
    waste_percentage: float = Field(
        description="wasted_tokens / (input + output) (0.0-1.0); 0.0 when no tokens."
    )


class SourceLLMMetricsResponse(BaseModel):
    """Response for ``GET /api/v1/sources/{id}/llm_metrics``."""

    source_id: str = Field(description="Source file ID.")
    summary: LLMMetricsSummary = Field(description="Aggregate LLM metrics.")
    has_metrics: bool = Field(description="False when the source has zero recorded LLM calls.")


class LLMCallMetricRow(BaseModel):
    """One row from ``GET /api/v1/sources/{id}/llm_metrics/calls``.

    Mirrors the ``load_only`` projection in
    ``SqliteAdapter.list_llm_call_metrics``.
    """

    id: str = Field(description="LLM call metric ID.")
    database_name: str = Field(description="Database this call belongs to.")
    source_id: str | None = Field(
        default=None, description="Source file ID (null for non-source calls)."
    )
    chunk_task_id: str | None = Field(
        default=None, description="ChunkExtractionTask ID if this call was part of one."
    )
    operation_type: str = Field(
        description=(
            "Call category: 'entity_extraction' | 'chat' | 'embedding' | 'template_suggestion'."
        )
    )
    call_sequence: int = Field(
        description="1 for the first attempt, 2+ for retries within the same task."
    )
    provider: str = Field(description="LLM provider (ollama|openai|anthropic|gemini).")
    model: str = Field(description="Model identifier (e.g. 'llama3.1:8b', 'gpt-4o-mini').")
    input_tokens: int = Field(description="Input tokens consumed by the call.")
    output_tokens: int = Field(description="Output tokens produced by the call.")
    duration_ms: int = Field(description="Wall-clock duration of the call in milliseconds.")
    started_at: datetime = Field(description="When the call was issued.")
    completed_at: datetime | None = Field(
        default=None, description="When the call returned (null if still pending)."
    )
    success: bool = Field(description="True if the call returned a usable response.")
    error_type: str | None = Field(
        default=None,
        description=(
            "Error category when success=False (validation_error|timeout|"
            "rate_limit|model_error|truncation)."
        ),
    )
    was_retry: bool = Field(description="True if this attempt was triggered by a retry.")
    retry_reason: str | None = Field(
        default=None,
        description=(
            "Why the retry was triggered (schema_validation|quality_issues|truncation|exception)."
        ),
    )
    chunk_index: int | None = Field(
        default=None, description="Chunk index when this call extracted entities."
    )
    chunk_size_chars: int | None = Field(
        default=None, description="Input chunk size in characters."
    )
    entities_extracted: int | None = Field(
        default=None, description="Entities produced by this call."
    )
    relationships_extracted: int | None = Field(
        default=None, description="Relationships produced by this call."
    )
    created_at: datetime = Field(description="Row creation timestamp.")

    model_config = ConfigDict(from_attributes=True)


class SourceLLMCallsResponse(BaseModel):
    """Response for ``GET /api/v1/sources/{id}/llm_metrics/calls``."""

    calls: list[LLMCallMetricRow] = Field(description="Individual LLM call records for this page.")
    pagination: EntityPagination = Field(description="Pagination metadata.")


# ================================
# Vision Page DTOs
# ================================


class VisionPageResponse(BaseModel):
    """One vision_page_descriptions row, surfaced through the Cortex API.

    Mirrors the storage TypedDict shape with API-friendly typing
    (str-valued status enum, ISO-8601 datetimes). The ``status`` value
    is one of: ``pending`` | ``succeeded`` | ``failed`` | ``truncated``;
    ``kind`` is one of: ``pdf_page`` | ``standalone_image``. Both arrive
    as plain strings from the storage layer to keep the API forward-
    compatible with future status / kind variants.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    source_id: str
    job_id: str
    page_number: int
    region_index: int
    kind: str  # "pdf_page" | "standalone_image"
    status: str  # "pending" | "succeeded" | "failed" | "truncated"
    image_path: str
    description: str | None
    finish_reason: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime


class VisionJobSummary(BaseModel):
    """Aggregate of one vision_jobs row for the UI panel."""

    model_config = ConfigDict(extra="forbid")

    id: str
    total_pages: int
    completed: int
    failed: int
    is_terminal: bool
    created_at: datetime
    updated_at: datetime


class VisionPagesListResponse(BaseModel):
    """Envelope for ``GET /api/v1/sources/{id}/vision_pages``."""

    model_config = ConfigDict(extra="forbid")

    source_id: str
    job: VisionJobSummary | None
    pages: list[VisionPageResponse]


class VisionPageRetryResponse(BaseModel):
    """Response for single-page retry — confirms reset + re-enqueue."""

    model_config = ConfigDict(extra="forbid")

    source_id: str
    page_number: int
    region_index: int
    page_id: str
    status: str  # New status after reset — always "pending" on success.
    reset: bool  # True if the row was reset; False if already pending.


class VisionPagesBatchRetryResponse(BaseModel):
    """Response for batch retry-failed."""

    model_config = ConfigDict(extra="forbid")

    source_id: str
    retried_count: int
    skipped_count: int  # Pages skipped because already pending or non-FAILED.
    page_ids: list[str]  # Pages that were actually reset + re-enqueued.


# ============================================================================
# Chunk rerun feature (2026-05-15) - Q1-Q8 design decisions
# ============================================================================


class ChunkRerunResponse(BaseModel):
    """202 Accepted body for the per-chunk rerun endpoint."""

    model_config = ConfigDict(extra="forbid")

    chunk_task_id: str = Field(description="ID of the chunk_extraction_task that was reset.")
    queue_task_id: str = Field(description="Queue task ID for the re-enqueued OP_EXTRACT_CHUNK.")
    attempt_number: int = Field(description="Snapshotted attempt number (1-indexed).")
    source_status: str = Field(description="Source status after walk-back.")


class ChunkAttemptSummary(BaseModel):
    """One row in the list-attempts response — summary columns only."""

    model_config = ConfigDict(extra="forbid")

    id: str
    chunk_task_id: str
    attempt_number: int
    snapshotted_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    entity_count: int
    relationship_count: int
    invalid_relationship_count: int
    finish_reason: str | None = None
    aborted_by_loop: bool | None = None
    llm_duration_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    input_text_length: int | None = None
    llm_response_length: int | None = None
    error_message: str | None = None
    error_type: str | None = None


class ChunkAttemptDetail(ChunkAttemptSummary):
    """Full attempt body — adds heavy fields. Returned by GET /attempts/{id}."""

    input_text: str | None = None
    llm_response_json: str | None = None
    raw_entities: list[dict[str, Any]] | None = None
    raw_relationships: list[dict[str, Any]] | None = None
    filtering_log: dict[str, Any] | None = None
    chunk_sentences: list[str] | None = None


class ChunkAttemptsListResponse(BaseModel):
    """Listing response for /chunks/{chunk_index}/attempts."""

    model_config = ConfigDict(extra="forbid")

    data: list[ChunkAttemptSummary]
