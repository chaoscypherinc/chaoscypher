# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Import Operations Service - orchestrates file import operations.

Main orchestrator for all import-related background operations including
CCX import, document analysis/extraction, document indexing, commit to
graph, and Lexicon package import. Delegates format-specific and indexing
logic to ``format_handler`` and ``indexing_handler`` sub-modules.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from chaoscypher_core.constants import (
    OP_BUILD_GRAPH_SNAPSHOT,
    OP_EMBED_CHUNKS,
    OP_EXTRACT_CHUNK,
    OP_IMPORT_ANALYSIS,
    OP_IMPORT_CCX,
    OP_IMPORT_COMMIT,
    OP_INDEX_DOCUMENT,
    QUEUE_LLM,
    QUEUE_OPERATIONS,
)
from chaoscypher_core.exceptions import OperationError, ValidationError
from chaoscypher_core.models import SourceStatus
from chaoscypher_core.operations.extraction.extraction_finalizer import (
    trigger_next_waiting_extraction,
)
from chaoscypher_core.operations.importing.embedding_handler import (
    handle_embed_chunks,
)
from chaoscypher_core.operations.importing.fanout_limits import (
    enforce_source_fanout_ceiling,
)
from chaoscypher_core.operations.importing.format_handler import (
    handle_import_ccx,
    handle_lexicon_import,
)
from chaoscypher_core.operations.importing.indexing_handler import (
    handle_index_document,
)
from chaoscypher_core.operations.queue_utils import (
    queue_import_commit as _queue_import_commit,
)
from chaoscypher_core.operations.queue_utils import (
    queue_import_indexing as _queue_import_indexing,
)
from chaoscypher_core.queue import queue_client
from chaoscypher_core.queue.handler_spec import HandlerSpec
from chaoscypher_core.services.events import event_bus
from chaoscypher_core.services.sources import source_heartbeat
from chaoscypher_core.utils.id import generate_id


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite import SqliteAdapter
    from chaoscypher_core.adapters.sqlite.repos import GraphRepository
    from chaoscypher_core.app_config import Settings
    from chaoscypher_core.settings import EngineSettings

logger = structlog.get_logger(__name__)


def _load_or_create_extraction_job(
    *,
    source_id: str,
    database_name: str,
    adapter: SqliteAdapter,
    **create_kwargs: Any,
) -> dict[str, Any]:
    """Reuse an existing non-terminal ChunkExtractionJob or create a new one.

    Idempotent: calling twice for the same source returns the same job.
    The "active" set is defined as jobs in ``pending`` or ``running``
    status — a completed or failed job is considered terminal and will
    not be reused.

    When creating, all kwargs are forwarded to
    ``adapter.create_extraction_job`` unchanged. The caller is
    responsible for supplying the new ``job_id``, ``extraction_depth``,
    ``extraction_config``, etc. — this helper doesn't know how to
    synthesize them.

    Args:
        source_id: Source being analyzed.
        database_name: Active database.
        adapter: SqliteAdapter exposing get_active_extraction_job and
            create_extraction_job.
        **create_kwargs: Forwarded to create_extraction_job when no
            active job is found.

    Returns:
        The existing or newly-created job dict.
    """
    existing = adapter.get_active_extraction_job(source_id=source_id, database_name=database_name)
    if existing is not None:
        logger.info(
            "extraction_job_reused",
            source_id=source_id,
            job_id=existing["id"],
            status=existing.get("status"),
            completed_chunks=existing.get("completed_chunks", 0),
            total_chunks=existing.get("total_chunks", 0),
        )
        return existing

    new_job = adapter.create_extraction_job(
        source_id=source_id,
        database_name=database_name,
        **create_kwargs,
    )
    logger.info(
        "extraction_job_created",
        source_id=source_id,
        job_id=new_job.get("id") if isinstance(new_job, dict) else None,
    )
    return new_job if isinstance(new_job, dict) else {}


def _upsert_extraction_tasks(
    *,
    job_id: str,
    groups: list[dict[str, Any]],
    database_name: str,
    adapter: SqliteAdapter,
) -> list[dict[str, Any]]:
    """Create ChunkExtractionTask rows for any groups that don't have one.

    Tasks for chunk_index values already represented (regardless of
    their status) are left alone. This preserves work done in a
    previous attempt so a completed task's results aren't trampled by
    a re-dispatch.

    Idempotency key is ``chunk_index``: the deterministic position of
    the group in the ``build_extraction_groups`` output. This relies on
    the grouping being deterministic under the same (chunks, filter,
    domain) inputs — which it is, because chunks are immutable after
    indexing and the domain config is pinned on the job.

    Stale-row cleanup: after deciding which indices are missing, the helper
    also calls ``adapter.orphan_chunk_tasks_outside_range`` with
    ``max_chunk_index=len(groups)``. A previous analysis pass that produced
    *more* groups than this one leaves task rows at indices ``>= len(groups)``;
    those rows must transition to ``orphaned`` so the SourceRecovery reconciler
    doesn't thrash on them. Terminal rows (completed/failed/cancelled/orphaned)
    are preserved by the adapter-side predicate. Root cause fix for source
    fa992140's recovery loop.

    Args:
        job_id: Parent extraction job.
        groups: Hierarchical groups (enumerate index becomes chunk_index).
        database_name: Active database.
        adapter: SqliteAdapter exposing list_extraction_tasks_for_job,
            create_chunk_tasks_batch, and orphan_chunk_tasks_outside_range.

    Returns:
        List of newly-created task dicts. Empty if nothing was missing.
    """
    existing = adapter.list_extraction_tasks_for_job(job_id=job_id, database_name=database_name)
    existing_by_index = {t["chunk_index"]: t for t in existing}

    # Cleanup pass: orphan non-terminal task rows whose chunk_index is beyond
    # the current group set. Runs unconditionally — the adapter's WHERE clause
    # filters so this is a no-op write when no stale rows exist. Doing it
    # unconditionally is cheaper than the service-side branch we would
    # otherwise need to maintain in lockstep with the terminal-status set.
    adapter.orphan_chunk_tasks_outside_range(
        job_id=job_id,
        database_name=database_name,
        max_chunk_index=len(groups),
    )

    missing: list[dict[str, Any]] = []
    for idx, group in enumerate(groups):
        if idx in existing_by_index:
            continue
        missing.append(
            {
                "task_id": generate_id(),
                "job_id": job_id,
                "database_name": database_name,
                "chunk_index": idx,
                "hierarchical_group_id": group.get("id"),
                "small_chunk_ids": group.get("small_chunk_ids"),
            }
        )

    if not missing:
        logger.info(
            "extraction_tasks_upsert_noop",
            job_id=job_id,
            total_groups=len(groups),
            existing_count=len(existing),
        )
        return []

    created = adapter.create_chunk_tasks_batch(missing)
    logger.info(
        "extraction_tasks_upserted",
        job_id=job_id,
        total_groups=len(groups),
        existing_count=len(existing),
        created_count=len(created),
    )
    return created


def _try_claim_or_wait(
    *,
    adapter: SqliteAdapter,
    file_id: str,
    database_name: str,
    analysis_depth: str,
    generate_embeddings: bool,
    file_info: dict[str, Any],
) -> dict[str, Any] | None:
    """Atomically claim the extraction slot or mark source as waiting.

    Only one source may extract at a time. If the slot is already
    held, this stores the file_info and marks the source as waiting
    so the reconciler can re-dispatch it later.

    Args:
        adapter: SqliteAdapter implementing extraction gating.
        file_id: Source file ID.
        database_name: Active database name.
        analysis_depth: Extraction depth (quick/full).
        generate_embeddings: Whether to generate embeddings.
        file_info: File metadata dict.

    Returns:
        A "waiting" result dict if the slot was not claimed, or None
        if the slot was claimed and extraction should proceed.
    """
    claimed = adapter.try_claim_extraction(file_id, database_name, depth=analysis_depth)
    if claimed:
        logger.info("extraction_starting", file_id=file_id, reason="extraction_slot_claimed")
        return None

    extracting_count = adapter.get_extracting_source_count(database_name)
    logger.info(
        "extraction_gated_waiting",
        file_id=file_id,
        extracting_count=extracting_count,
        reason="another_source_extracting",
    )

    waiting_file_info = {
        **file_info,
        "analysis_depth": analysis_depth,
        "generate_embeddings": generate_embeddings,
    }
    adapter.mark_extraction_waiting(file_id, waiting_file_info)

    return {
        "status": "waiting",
        "file_id": file_id,
        "message": "Queued behind active extraction",
        "extracting_count": extracting_count,
    }


def _resume_extraction_job(
    *,
    adapter: SqliteAdapter,
    existing_job: dict[str, Any],
    file_id: str,
    registry: Any,
    database_name: str,
) -> tuple[str, Any, list[dict[str, Any]]]:
    """Resume an existing extraction job and fetch its chunks.

    Restores domain context from the stored job record so the LLM
    domain-detection path is not re-run on crash recovery.

    Args:
        adapter: SqliteAdapter implementing storage protocols.
        existing_job: The active job dict from the database.
        file_id: Source file ID.
        registry: Domain registry for resolving domain objects.
        database_name: Active database name.

    Returns:
        Tuple of (job_id, domain_object_or_None, chunks_list).

    Raises:
        OperationError: If no chunks are found for the source (file may not be indexed).
    """
    job_id = existing_job["id"]
    detected_domain = existing_job.get("detected_domain")
    effective_domain_name = existing_job.get("forced_domain") or detected_domain
    domain = registry.get_domain(effective_domain_name) if effective_domain_name else None
    logger.info(
        "chunk_extraction_job_resumed",
        job_id=job_id,
        file_id=file_id,
        detected_domain=detected_domain,
        completed_chunks=existing_job.get("completed_chunks", 0),
        total_chunks=existing_job.get("total_chunks", 0),
    )

    # Clear stale error fields — match start_extraction's clear so a
    # resumed job doesn't show "extraction failed" while extraction is
    # actively running. Audit fix #H/core (resume keeps stale error).
    adapter.update_file(
        source_id=file_id,
        database_name=database_name,
        updates={
            "error_message": None,
            "error_stage": None,
        },
    )

    all_chunks = adapter.get_chunks_for_extraction(
        source_id=file_id,
        database_name=database_name,
    )
    if not all_chunks:
        error_msg = "No chunks found - file may not be indexed"
        logger.warning("import_analysis_no_chunks", file_id=file_id, error=error_msg)
        adapter.fail_extraction(file_id, error_msg)
        raise OperationError(error_msg, operation="import")

    return job_id, domain, all_chunks


def _create_fresh_extraction_job(
    *,
    adapter: SqliteAdapter,
    file_id: str,
    file_info: dict[str, Any],
    forced_domain: str | None,
    registry: Any,
    settings: Settings,
    analysis_depth: str,
    generate_embeddings: bool,
    task_id: str | None,
    domain_result: dict[str, Any] | None = None,
) -> tuple[str, Any, list[dict[str, Any]]]:
    """Detect domain, build extraction config, and create a new job.

    Runs the full domain-detection + template-formatting pipeline and
    persists the extraction config on the job so individual chunk
    handlers don't need to replicate it.

    Args:
        adapter: SqliteAdapter implementing storage protocols.
        file_id: Source file ID.
        file_info: File metadata dict.
        forced_domain: User-forced domain name, if any.
        registry: Domain registry for domain detection.
        settings: Application settings.
        analysis_depth: Extraction depth (quick/full).
        generate_embeddings: Whether to generate embeddings.
        task_id: Parent queue task ID.
        domain_result: Pre-computed ``detect_extraction_domain`` result from the
            confirmation gate (hoisted before the slot claim). When provided,
            detection is not re-run here; when ``None`` (standalone callers that
            don't pre-detect) detection happens inline as before.

    Returns:
        Tuple of (job_id, domain_object, chunks_list).

    Raises:
        OperationError: If no chunks are found for the source (file may not be indexed).
    """
    from chaoscypher_core.services.sources.engine.extraction.domains import (
        create_domain_sample_text,
    )
    from chaoscypher_core.services.sources.engine.extraction.orchestration import (
        detect_extraction_domain,
    )

    job_id = generate_id()
    logger.info(
        "chunk_extraction_job_creating",
        job_id=job_id,
        file_id=file_id,
        forced_domain=forced_domain,
    )

    all_chunks = adapter.get_chunks_for_extraction(
        source_id=file_id,
        database_name=settings.current_database,
    )
    if not all_chunks:
        error_msg = "No chunks found - file may not be indexed"
        logger.warning("import_analysis_no_chunks", file_id=file_id, error=error_msg)
        adapter.fail_extraction(file_id, error_msg)
        raise OperationError(error_msg, operation="import")

    # Detect domain — reuse the result computed by the confirmation gate
    # (hoisted before the slot claim) when present, else detect here. This
    # keeps the create path standalone for callers that don't pre-detect.
    if domain_result is None:
        sample_text = create_domain_sample_text(all_chunks, content_key="content")
        domain_result = detect_extraction_domain(
            registry=registry,
            forced_domain=forced_domain,
            sample_text=sample_text,
            filename=file_info.get("filename", ""),
            metadata=file_info.get("metadata", {}),
        )

    domain = domain_result["domain"]
    detected_domain = domain_result["detected_domain"]
    entity_guidance = domain_result["entity_guidance"]
    relationship_guidance = domain_result["relationship_guidance"]

    # Build extraction config from domain. Workstream 1 (2026-05-07):
    # the row is the source of truth for filtering_mode / extraction_depth /
    # content_filtering / enable_vision; the queue payload remains the
    # fallback when the row hasn't been persisted yet (legacy / test paths).
    source_row: dict[str, Any] | None = None
    try:
        source_row = adapter.get_source(file_id, settings.current_database)
    except (SQLAlchemyError, OperationalError) as exc:  # pragma: no cover — defensive
        logger.warning(
            "import_extraction_config_row_lookup_failed",
            source_id=file_id,
            error_type=type(exc).__name__,
        )
        source_row = None
    extraction_config = _build_extraction_config(
        domain=domain,
        entity_guidance=entity_guidance,
        relationship_guidance=relationship_guidance,
        settings=settings,
        file_info=file_info,
        source_row=source_row,
    )

    # Combine guidance for job-level storage
    combined_guidance = entity_guidance
    if relationship_guidance:
        combined_guidance = (
            f"{combined_guidance}\n\n{relationship_guidance}"
            if combined_guidance
            else relationship_guidance
        )

    _load_or_create_extraction_job(
        source_id=file_id,
        database_name=settings.current_database,
        adapter=adapter,
        job_id=job_id,
        extraction_depth=analysis_depth,
        generate_embeddings=generate_embeddings,
        parent_task_id=task_id,
        forced_domain=forced_domain,
        detected_domain=detected_domain,
        domain_guidance=combined_guidance,
        extraction_config=extraction_config,
    )

    logger.info(
        "chunk_extraction_job_created",
        job_id=job_id,
        file_id=file_id,
        detected_domain=detected_domain,
    )

    return job_id, domain, all_chunks


def _build_extraction_config(
    *,
    domain: Any,
    entity_guidance: str | None,
    relationship_guidance: str | None,
    settings: Settings,
    file_info: dict[str, Any],
    source_row: dict[str, Any] | None = None,
) -> str:
    """Build the JSON extraction config stored once per job.

    Collects domain templates, guidance, extraction limits, entity
    exclusions, and type constraints into a single JSON blob that
    each chunk handler reads instead of recomputing.

    Args:
        domain: Resolved domain object.
        entity_guidance: Entity-specific extraction guidance.
        relationship_guidance: Relationship-specific extraction guidance.
        settings: Application settings.
        file_info: File metadata dict (legacy fallback when ``source_row``
            is missing). Every user upload setting now lives on the
            source row; this dict is only used for
            backwards-compatible recovery.
        source_row: Authoritative source-row state. When present, drives
            ``filtering_mode``, ``content_filtering``, ``enable_vision``,
            and ``extraction_depth`` regardless of what the queue
            payload says.

    Returns:
        JSON string of the extraction configuration.
    """
    from chaoscypher_core.app_config.engine_factory import build_engine_settings
    from chaoscypher_core.services.sources.engine.extraction.orchestration import (
        format_extraction_templates,
    )

    # `settings` is the backend `app_config.Settings` (no `.extraction`
    # field). The snapshot below pulls retry-loop thresholds from
    # `EngineSettings.extraction`, so convert once here — same pattern
    # as `_extract_chunk_handler` and `_finalize_extraction_handler`.
    # Reading `settings.extraction.*` directly raises AttributeError on
    # the real backend Settings; the prior version only worked under
    # tests that monkeypatched `settings.extraction = MagicMock(...)`.
    engine_settings = build_engine_settings(settings)

    template_result = format_extraction_templates(
        domain,
        examples_enabled=engine_settings.llm.extraction_examples_enabled,
        examples_max_chars=engine_settings.llm.extraction_examples_max_chars,
    )

    domain_extraction_limits = domain.get_extraction_limits()
    domain_entity_exclusions = domain.get_entity_exclusions()

    domain_evidence_mode = (
        domain.get_evidence_validation_mode()
        if hasattr(domain, "get_evidence_validation_mode")
        else None
    )

    domain_strict_types = (
        domain.get_strict_entity_types() if hasattr(domain, "get_strict_entity_types") else False
    )
    valid_type_names: list[str] = []
    if domain_strict_types:
        domain_templates = domain.get_templates()
        valid_type_names = [
            t["name"] for t in domain_templates.get("node_templates", []) if t.get("name")
        ]

    domain_edge_type_constraints: dict[str, dict[str, list[str]]] = (
        domain.get_edge_type_constraints() if hasattr(domain, "get_edge_type_constraints") else {}
    )

    # Resolve the effective preset selector. Cascade (highest first):
    # source_row.filtering_mode > file_info.filtering_mode > domain jsonld
    # > engine default. Kept as its own variable and serialised under a
    # top-level ``filtering_mode`` key on the extraction_config JSON —
    # sibling to ``extraction_limits``, never inlined into it. The earlier
    # inlining was the source of ``domain_config_unknown_keys_dropped``
    # noise: ``resolve_filtering_config(domain_overrides=…)`` only accepts
    # ``FilteringConfig`` field names, and the preset selector isn't one.
    _filtering_mode_override: str | None = None
    if source_row is not None:
        _row_value = source_row.get("filtering_mode")
        if isinstance(_row_value, str) and _row_value:
            _filtering_mode_override = _row_value
    if _filtering_mode_override is None:
        _filtering_mode_override = file_info.get("filtering_mode")
    _domain_default_mode: str | None = (
        domain.get_filtering_mode() if hasattr(domain, "get_filtering_mode") else None
    )
    extraction_filtering_mode: str = (
        _filtering_mode_override
        or _domain_default_mode
        or engine_settings.extraction.extraction_filtering_mode
    )

    # Phase 4 (2026-05-08): thread per-source enable_direction_correction override
    # into extraction limits (which become domain_overrides for resolve_filtering_config).
    # Cascade: source_row > domain_extraction_limits (already present) > engine default.
    # When source_row has an explicit non-None value, write it into the limits dict so
    # it overrides any domain-level setting when the finalizer resolves FilteringConfig.
    if source_row is not None:
        _row_direction = source_row.get("enable_direction_correction")
        if isinstance(_row_direction, bool):
            if domain_extraction_limits is not None:
                domain_extraction_limits["enable_direction_correction"] = _row_direction
            else:
                domain_extraction_limits = {"enable_direction_correction": _row_direction}
    # When neither the row nor the domain dict specifies it, thread the engine-level
    # default so the finalizer always sees an explicit value (avoids relying on
    # FilteringConfig's own field default, which would ignore the settings.yaml knob).
    if (
        domain_extraction_limits is not None
        and "enable_direction_correction" not in domain_extraction_limits
    ):
        domain_extraction_limits["enable_direction_correction"] = (
            engine_settings.extraction.enable_direction_correction
        )
    elif domain_extraction_limits is None:
        domain_extraction_limits = {
            "enable_direction_correction": engine_settings.extraction.enable_direction_correction
        }

    # Phase 4 (2026-05-08): thread per-source protect_orphans override into extraction
    # limits (which become domain_overrides for resolve_filtering_config).
    # Cascade: source_row > domain_extraction_limits (already present) > engine default.
    if source_row is not None:
        _row_protect_orphans = source_row.get("protect_orphans")
        if isinstance(_row_protect_orphans, bool):
            if domain_extraction_limits is not None:
                domain_extraction_limits["protect_orphans"] = _row_protect_orphans
            else:
                domain_extraction_limits = {"protect_orphans": _row_protect_orphans}
    # When neither the row nor the domain dict specifies it, thread the engine-level
    # default so the finalizer always sees an explicit value.
    if domain_extraction_limits is not None and "protect_orphans" not in domain_extraction_limits:
        domain_extraction_limits["protect_orphans"] = engine_settings.extraction.protect_orphans
    elif domain_extraction_limits is None:
        domain_extraction_limits = {"protect_orphans": engine_settings.extraction.protect_orphans}

    # Phase 6 (2026-05-08): thread per-source max_entity_degree_override into
    # extraction limits so resolve_filtering_config picks it up as max_entity_degree.
    # Cascade: source_row.max_entity_degree_override (nullable int) > domain limit
    # already present > engine default (ExtractionSettings.max_entity_degree).
    # Only override when the row carries a positive integer; zero or negative would
    # turn off the cap unintentionally so treat those as "use domain/global default".
    if source_row is not None:
        _row_degree_override = source_row.get("max_entity_degree_override")
        if isinstance(_row_degree_override, int) and _row_degree_override > 0:
            if domain_extraction_limits is not None:
                domain_extraction_limits["max_entity_degree"] = _row_degree_override
            else:
                domain_extraction_limits = {"max_entity_degree": _row_degree_override}

    # Phase 5 (2026-05-18): a domain that declares ``strict_entity_types: true``
    # is asserting its node-template list is exhaustive. The same contract must
    # carry through to edge validation, otherwise ``Vienna -[interacts_with]->
    # Empress`` (Location source on a Character-only edge template) passes
    # silently on every non-strict preset. When the domain's extraction_limits
    # already pins ``strict_edge_type_constraints`` (e.g. a domain in transition)
    # the explicit value wins.
    if domain_extraction_limits is not None:
        if "strict_edge_type_constraints" not in domain_extraction_limits:
            domain_extraction_limits["strict_edge_type_constraints"] = domain_strict_types
    elif domain_strict_types:
        domain_extraction_limits = {"strict_edge_type_constraints": True}

    return json.dumps(
        {
            "node_templates_formatted": template_result["node_templates"],
            "edge_templates_formatted": template_result["edge_templates"],
            "entity_guidance": entity_guidance,
            "relationship_guidance": relationship_guidance,
            "entity_examples_formatted": template_result["entity_examples"],
            "relationship_examples_formatted": template_result["relationship_examples"],
            "extraction_limits": domain_extraction_limits,
            # Preset selector — sibling to extraction_limits, never inlined.
            "filtering_mode": extraction_filtering_mode,
            # Pydantic ExclusionRule round-trips through JSON as a dict
            # so the queue payload stays JSON-safe; the chunk-extraction
            # handler reconstitutes ExclusionRule objects on the consumer
            # side.
            "entity_exclusions": [r.model_dump() for r in (domain_entity_exclusions or [])],
            "strict_entity_types": domain_strict_types,
            "valid_entity_type_names": valid_type_names,
            "evidence_validation_mode": domain_evidence_mode,
            "edge_type_constraints": domain_edge_type_constraints,
            # Workstream 8 (2026-05-07) — snapshot the extraction-time
            # settings so a mid-job edit to ``settings.yaml`` does not
            # drift across in-flight chunks. The chunk handler reads
            # these via ``snapshot.get(...)`` with a fallback to live
            # settings; older snapshots (snapshot_version<2 or absent)
            # transparently fall through to settings.
            "extraction_temperature": engine_settings.llm.extraction_temperature,
            "extraction_max_tokens": engine_settings.llm.extraction_max_tokens,
            "extraction_examples_enabled": engine_settings.llm.extraction_examples_enabled,
            "extraction_examples_max_chars": engine_settings.llm.extraction_examples_max_chars,
            "loop_max_out_of_bounds": engine_settings.extraction.loop_max_out_of_bounds,
            "loop_max_source_type_repeat": engine_settings.extraction.loop_max_source_type_repeat,
            "loop_max_property_repeat": engine_settings.extraction.loop_max_property_repeat,
            "loop_invalid_relationship_rate_warmup": (
                engine_settings.extraction.loop_invalid_relationship_rate_warmup
            ),
            "loop_invalid_relationship_rate_threshold": (
                engine_settings.extraction.loop_invalid_relationship_rate_threshold
            ),
            "snapshot_version": 2,
        }
    )


def _apply_content_filtering(
    *,
    all_chunks: list[dict[str, Any]],
    domain: Any,
    file_info: dict[str, Any],
    file_id: str,
    source_row: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], Any]:
    """Apply domain-specific content exclusions to chunks.

    Resolves content matchers from the domain and strips/filters
    chunks accordingly. Returns the filtered chunk list and optional
    filter statistics.

    Args:
        all_chunks: List of chunk dicts.
        domain: Resolved domain object (may be None).
        file_info: File metadata dict (legacy fallback for the
            ``content_filtering`` toggle).
        file_id: Source file ID for logging.
        source_row: Authoritative source-row state (W1 2026-05-07).
            When present, drives the ``content_filtering`` decision.

    Returns:
        Tuple of (filtered_chunks, filter_stats_or_None).
    """
    from chaoscypher_core.services.sources.engine.extraction.orchestration import (
        filter_and_strip_chunks,
        resolve_content_exclusions,
    )

    if source_row is not None and "content_filtering" in source_row:
        content_filtering_enabled = bool(source_row.get("content_filtering"))
    else:
        content_filtering_enabled = file_info.get("content_filtering", True)
    filter_stats = None

    if content_filtering_enabled and domain:
        content_matchers = resolve_content_exclusions(domain)
        if content_matchers:
            all_chunks, filter_stats = filter_and_strip_chunks(all_chunks, content_matchers)
            logger.info(
                "content_exclusion_applied",
                source_id=file_id,
                total_chunks=filter_stats.total_chunks,
                excluded_chunks=filter_stats.excluded_chunks,
                categories=filter_stats.categories_matched,
                avg_stripped_ratio=filter_stats.avg_content_stripped_ratio,
            )

    return all_chunks, filter_stats


def _persist_filter_stats(
    adapter: Any,
    job_id: str,
    filter_stats: Any,
) -> None:
    """Persist filter stats on the job row.

    Idempotent — filtering is deterministic so re-runs on the resume
    path produce the same stats; overwriting with the same values is safe.
    Skips the write when stats are None or both fields are zero (no-op
    filtering produced no meaningful data).

    Args:
        adapter: Source repository adapter.
        job_id: Extraction job ID to update.
        filter_stats: FilterStats dataclass instance, or None.
    """
    if filter_stats and (
        filter_stats.excluded_chunks > 0 or filter_stats.avg_content_stripped_ratio > 0
    ):
        adapter.update_extraction_job(
            job_id,
            {
                "filtered_chunks": filter_stats.excluded_chunks,
                "filtered_content_ratio": filter_stats.avg_content_stripped_ratio,
            },
        )


class ImportOperationsService:
    """Service for queuing import operations.

    Handles importing files in various formats (CCX, documents, etc.)
    and orchestrates analysis, relationship extraction, and commit operations.
    All imports are queued and executed asynchronously.
    """

    def __init__(
        self,
        graph_repository: GraphRepository,
        config_manager: Any,
        source_manager: Any,
        trigger_service: Any,
        llm_service: Any,
        source_repository: Any,
        chunking_service: Any,
        indexing_service: Any,
        search_repository: Any = None,
        engine_settings: EngineSettings | None = None,
    ) -> None:
        """Initialize import operations service.

        This service is designed for WORKER CONTEXT ONLY. It requires chunking_service
        and indexing_service which are cached at worker startup for efficiency.

        For Cortex API layer (which only queues tasks), use queue_client directly
        instead of creating this service.

        Args:
            graph_repository: GraphRepository for graph operations
            config_manager: ConfigManager for settings
            source_manager: Source processing manager for file operations (SourceProcessingService)
            trigger_service: TriggerService for event handling
            llm_service: LLMService for AI operations
            source_repository: SqliteAdapter (implements SourceStorageProtocol) for orchestrator
            chunking_service: ChunkingService cached at worker level (REQUIRED)
            indexing_service: IndexingService cached at worker level (REQUIRED)
            search_repository: SearchRepository for fulltext + vector indexing (optional,
                created from settings if not provided)
            engine_settings: Cached EngineSettings from worker startup (optional,
                avoids per-request build_engine_settings calls)

        Raises:
            ValidationError: If chunking_service or indexing_service is None.

        """
        if chunking_service is None:
            raise ValidationError(
                "chunking_service is required - this service is for worker context only",
                field="chunking_service",
            )
        if indexing_service is None:
            raise ValidationError(
                "indexing_service is required - this service is for worker context only",
                field="indexing_service",
            )
        self.graph_repository = graph_repository
        self.config_manager = config_manager
        self.source_manager = source_manager
        self.trigger_service = trigger_service
        self.llm_service = llm_service
        self.source_repository = source_repository
        self.chunking_service = chunking_service
        self.indexing_service = indexing_service
        self.search_repository = search_repository
        self.engine_settings = engine_settings

        # Resumability: the three idempotent import handlers opt into
        # retry_on_crash=True so the queue reconciler requeues their
        # abandoned tasks instead of failing them with
        # error_type="worker_crashed". Non-idempotent operations keep
        # the default retry_on_crash=False.
        self.operation_handlers = {
            OP_IMPORT_CCX: self._import_ccx_handler,
            OP_IMPORT_COMMIT: HandlerSpec(
                handler=self._import_commit_handler,
                retry_on_crash=True,
            ),
            OP_IMPORT_ANALYSIS: HandlerSpec(
                handler=self._import_analysis_handler,
                retry_on_crash=True,
            ),
            OP_INDEX_DOCUMENT: HandlerSpec(
                handler=self._index_document_handler,
                retry_on_crash=True,
            ),
            "lexicon_import": self._lexicon_import_handler,
        }

        # LLM-queue handlers. Phase 5 Task C split ``embed_chunks`` out
        # of the indexing pipeline so the LLM-bound embedding stage runs
        # with the LLM queue's concurrency budget instead of gating the
        # ops queue. ``OP_EMBED_CHUNKS`` is idempotent (embedded_at is
        # the checkpoint) so retry_on_crash is safe.
        self.llm_operation_handlers = {
            OP_EMBED_CHUNKS: HandlerSpec(
                handler=self._embed_chunks_handler,
                retry_on_crash=True,
            ),
        }

        logger.info("import_operations_service_initialized")

    def register_handlers(self) -> None:
        """Register import operation handlers with queue.

        Registers the ops-queue handlers (CCX, commit, analysis,
        index-document, lexicon) on ``QUEUE_OPERATIONS`` and the
        LLM-queue handlers (``embed_chunks``) on ``QUEUE_LLM``.
        """
        # arg-type suppressed: register_handlers expects dict[str, HandlerSpec] but
        # mypy cannot prove the wrapped operation handlers (built via _wrap_handler /
        # HandlerSpec factories elsewhere) match the protocol; the actual values are
        # HandlerSpec instances constructed at module init.
        queue_client.register_handlers(QUEUE_OPERATIONS, self.operation_handlers)  # type: ignore[arg-type]
        queue_client.register_handlers(QUEUE_LLM, self.llm_operation_handlers)  # type: ignore[arg-type]

    def _resolve_engine_settings(self, settings: Settings) -> EngineSettings:
        """Return the EngineSettings view for engine-relevant reads.

        Reuses the worker-context ``engine_settings`` cached on the service
        when present (avoids a per-task ``build_engine_settings`` rebuild) and
        falls back to building from the app ``Settings`` at the boundary. Engine
        collaborators (search/commit) are typed against ``EngineSettings``; this
        keeps the backend ``Settings`` singleton out of the engine call paths.
        """
        if self.engine_settings is not None:
            return self.engine_settings
        from chaoscypher_core.app_config.engine_factory import build_engine_settings

        return build_engine_settings(settings)

    # ------------------------------------------------------------------
    # Queue methods -- delegate to shared queue_utils for single source of truth
    # ------------------------------------------------------------------
    async def queue_import_commit(
        self,
        file_id: str,
        commit_data: dict[str, Any],
        file_info: dict[str, Any],
        *,
        database_name: str,
        priority: int = 50,
        extra_metadata: dict[str, Any] | None = None,
    ) -> str:
        """Queue import commit operation (persists payload to DB first).

        ``commit_data`` is stashed on the source row
        via ``set_source_commit_payload`` before enqueue so the queue
        payload stays small. The adapter comes from
        ``self.source_repository``, which is wired through on the
        worker.

        Raises:
            OperationError: If ``source_repository`` is not configured.
        """
        if self.source_repository is None:
            msg = "ImportOperationsService.queue_import_commit requires source_repository"
            raise OperationError(msg, operation="import")
        return await _queue_import_commit(
            file_id,
            commit_data,
            file_info,
            self.source_repository,
            database_name=database_name,
            priority=priority,
            extra_metadata=extra_metadata,
        )

    async def queue_import_indexing(
        self,
        file_id: str,
        file_info: dict[str, Any],
        *,
        database_name: str,
        priority: int = 50,
        extra_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Queue document indexing (chunking + embeddings for RAG)."""
        return await _queue_import_indexing(
            file_id,
            file_info,
            database_name=database_name,
            priority=priority,
            extra_metadata=extra_metadata,
        )

    # ------------------------------------------------------------------
    # Handler dispatchers -- thin wrappers that delegate to sub-modules
    # ------------------------------------------------------------------
    async def _import_ccx_handler(
        self,
        data: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        """Execute CCX import operation.

        Args:
            data: Task data with file content and merge flag.
            metadata: Task metadata.
            task_id: Task ID for tracking.

        Returns:
            Result dictionary with import statistics and errors.

        """
        return await handle_import_ccx(
            data=data,
            graph_repository=self.graph_repository,
            source_repository=self.source_repository,
            engine_settings=self.engine_settings,
            metadata=metadata,
            task_id=task_id,
        )

    async def _import_commit_handler(
        self,
        data: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        """Execute import commit operation.

        Fast path: if the source is already ``commit_complete``, return
        a ``{"skipped": "already_committed"}`` result without touching
        the graph. This handles the "queue lost the ack but the DB row
        is fine" case — re-dispatched commit tasks that would otherwise
        double-write are now a no-op.

        Args:
            data: Task data with file ID, commit data, and file info.
            metadata: Task metadata.
            task_id: Task ID for tracking.

        Returns:
            Result dictionary with created entities, a skip marker, or
            an error.

        Raises:
            OperationError: If ``source_repository`` is not configured.
        """
        from chaoscypher_core.app_config import get_settings
        from chaoscypher_core.operations.pause_guard import check_paused

        file_id = data["file_id"]
        file_info_dict = data["file_info"]
        auto_enable = data.get("auto_enable", True)

        logger.info("import_commit_operation_processing", file_id=file_id, auto_enable=auto_enable)

        settings = get_settings()
        database_name = settings.current_database

        # Pause guard: runs before the idempotency fast path so a paused
        # source doesn't get spuriously marked already_committed on the
        # next retry.
        if self.source_repository is not None:
            pause_check = check_paused(
                source_id=file_id,
                database_name=database_name,
                adapter=self.source_repository,
            )
            if pause_check.paused:
                logger.info(
                    "handler_skipped_paused",
                    handler="_import_commit_handler",
                    source_id=file_id,
                    scope=pause_check.scope,
                    reason=pause_check.reason,
                )
                return {"skipped": "paused"}

        # Fast-path: already committed. Check BEFORE any heavy work.
        # Runs before the payload hydrate below because on the
        # already-committed path the commit handler has already cleared
        # the payload — reading it here would otherwise log a
        # false-positive "payload_not_found".
        #
        # F54: NEVER re-call ``complete_commit()`` here. ``complete_commit``
        # unconditionally overwrites the count columns AND
        # ``commit_completed_at`` with whatever it is given; if the
        # persisted counts are wrong (partial-commit-then-crash, future
        # bug, etc.) re-passing them locks the wrong values in. The fast
        # path must be a pure idempotent return.
        #
        # Self-heals a drifted status: if ``commit_complete=True`` but
        # ``status != COMMITTED`` (e.g. a re-extraction transition wrote
        # ``status=EXTRACTED`` back over an already-committed row), patch
        # only the ``status`` field via ``update_source`` so the row aligns
        # again. Counts and ``commit_completed_at`` are NOT touched —
        # whatever was persisted at the original commit stays authoritative.
        # Without status repair, the reconciler's ``extracted``-branch
        # classifier would keep re-dispatching commit until
        # ``recovery_attempts`` exhausts.
        if self.source_repository:
            existing = self.source_repository.get_source(file_id, database_name)
            if existing and existing.get("commit_complete"):
                prior_status = existing.get("status")
                persisted_nodes = existing.get("commit_nodes_created")
                persisted_edges = existing.get("commit_edges_created")
                persisted_templates = existing.get("commit_templates_created")

                # Always log persisted counts so ops can spot drift.
                logger.warning(
                    "commit_self_heal_already_complete",
                    file_id=file_id,
                    prior_status=prior_status,
                    commit_nodes_created=persisted_nodes,
                    commit_edges_created=persisted_edges,
                    commit_templates_created=persisted_templates,
                )

                # Drift detector: ``commit_complete=True`` with all counts
                # at zero/None is suspicious (legacy state from before
                # counts were tracked, or a partial-commit-then-flag-set
                # bug). We do NOT auto-fix the counts — the true graph
                # state is unknown without a recount, and silently
                # re-persisting whatever is in the row is exactly the
                # F54 bug. Surface for ops to investigate.
                if not persisted_nodes and not persisted_edges and not persisted_templates:
                    logger.warning(
                        "commit_self_heal_count_drift",
                        file_id=file_id,
                        prior_status=prior_status,
                        commit_nodes_created=persisted_nodes,
                        commit_edges_created=persisted_edges,
                        commit_templates_created=persisted_templates,
                    )

                # Status-only repair when status drifted away from
                # COMMITTED. Uses ``update_source`` to touch only the
                # ``status`` column, leaving counts and timestamps alone.
                if prior_status != SourceStatus.COMMITTED:
                    self.source_repository.update_source(
                        file_id,
                        {"status": SourceStatus.COMMITTED},
                    )
                    logger.warning(
                        "commit_status_self_healed",
                        file_id=file_id,
                        prior_status=prior_status,
                    )

                logger.info(
                    "commit_already_complete_fast_path",
                    file_id=file_id,
                    status=SourceStatus.COMMITTED,
                )
                return {
                    "skipped": "already_committed",
                    "file_id": file_id,
                    "status": SourceStatus.COMMITTED,
                }

        # Phase 5 Task D: rehydrate commit_data from the source row
        # rather than reading it from the queue payload. The
        # extraction finalizer (or the retry-commit path) wrote it via
        # set_source_commit_payload before the enqueue.
        if self.source_repository is None:
            msg = "Required source repository unavailable for commit"
            raise OperationError(msg, operation="import")
        commit_data = self.source_repository.get_source_commit_payload(file_id, database_name)
        if commit_data is None:
            logger.warning(
                "commit_payload_not_found",
                file_id=file_id,
                database_name=database_name,
            )
            return {
                "success": False,
                "skipped": True,
                "reason": "commit_payload_not_found",
                "file_id": file_id,
            }

        logger.info("import_commit_data_keys", keys=list(commit_data.keys()))
        logger.info(
            "import_commit_entities_count",
            entity_count=len(commit_data.get("entities", [])),
        )

        # Stage entry: zero recovery_attempts so accumulated false-positive
        # recoveries from extraction don't compound into commit and push the
        # counter toward the 10-attempt exhaustion cap on healthy sources.
        # Arrival at commit proves forward progress through extraction.
        try:
            self.source_repository.reset_source_recovery_attempts(
                source_id=file_id, database_name=database_name
            )
        except Exception as exc:
            logger.warning(
                "reset_recovery_attempts_failed",
                source_id=file_id,
                database_name=database_name,
                stage="commit",
                error_type=type(exc).__name__,
                error_message=str(exc),
            )

        # Source liveness heartbeat — see chaoscypher_core.services.sources.heartbeat.
        # Commit on a large extraction touches templates, nodes,
        # relationships, citations, and search indices; the 8-step
        # commit pipeline can run well over the reconciler interval.
        # The heartbeat keeps last_activity_at fresh so the reconciler
        # does not race a duplicate dispatch that would corrupt state.
        if self.source_repository is None:
            msg = "Required source repository unavailable for commit"
            raise OperationError(msg, operation="import")
        async with source_heartbeat(
            adapter=self.source_repository,
            source_id=file_id,
            database_name=database_name,
        ):
            return await self._run_commit(
                file_id=file_id,
                commit_data=commit_data,
                file_info_dict=file_info_dict,
                auto_enable=auto_enable,
                settings=settings,
            )

    async def _run_commit(
        self,
        *,
        file_id: str,
        commit_data: dict[str, Any],
        file_info_dict: dict[str, Any],
        auto_enable: bool,
        settings: Any,
    ) -> dict[str, Any]:
        """Inner commit body — wrapped by source_heartbeat in the public handler.

        Raises any exception from the commit pipeline. ``fail_commit`` is a
        best-effort side effect: if it itself fails, the failure is logged
        as ``fail_handler_raised`` and the ORIGINAL exception re-raises (the
        secondary appears on ``__context__``).
        """
        from chaoscypher_core.services.sources.engine.commit import (
            SourceCommitService,
        )

        try:
            # Get required repositories
            if not self.graph_repository or not self.source_repository:
                msg = "Required repositories unavailable"
                raise OperationError(msg, operation="import")

            # Use shared adapter (passed from worker context)
            adapter = self.source_repository

            # Build the EngineSettings view once at the operation boundary and
            # read every engine-relevant group (search/embedding) from it so the
            # engine collaborators never see the backend Settings singleton.
            engine_settings = self._resolve_engine_settings(settings)

            # Reuse search repository from worker context (fallback to new instance)
            if self.search_repository:
                search_repository = self.search_repository
            else:
                from chaoscypher_core.adapters.sqlite.repos import SearchRepository
                from chaoscypher_core.database.engine import get_engine

                search_repository = SearchRepository(
                    engine=get_engine(engine_settings.current_database),
                    vector_dim=engine_settings.search.vector_dimensions,
                    embedding_model=engine_settings.embedding.model,
                )

            # Create chaoscypher SourceCommitService using adapter
            commit_service = SourceCommitService(
                graph_repository=self.graph_repository,
                source_repository=adapter,
                sources_repository=adapter,
                indexing_repository=adapter,  # Implements storage protocol for indexing
                search_repository=search_repository,
                settings=engine_settings,
                reload_callback=None,  # No reload needed
            )

            # Payload-clear atomicity is owned by ``SourceCommitService``:
            # both ``_commit_impl`` and ``_commit_empty`` invoke
            # ``clear_source_commit_payload`` as the LAST write inside their
            # inner ``adapter.transaction()`` block, so the payload is
            # discarded in the same SQLite commit that flips status to
            # COMMITTED. A raised commit rolls back both, retaining the
            # payload for retry. Removing the outer transaction here is
            # the 2026-05-20 writer-lock-contention root fix: holding the
            # outer txn across the post-inner-txn LLM embedding await
            # (``commit_service._embed_created_templates``) was what kept
            # the SQLite writer lock pinned for the duration of the
            # Ollama HTTP call, starving sibling handlers' writes.
            result = await commit_service.commit(
                file_id, commit_data, file_info_dict, auto_enable=auto_enable
            )

            # Publish trigger events for created entities
            if self.trigger_service:
                for node_id in result.get("created_nodes", []):
                    self.trigger_service.publish_event_sync(
                        "node.create", {"entity_type": "node", "entity_id": node_id}
                    )
                for edge_id in result.get("created_edges", []):
                    self.trigger_service.publish_event_sync(
                        "edge.create", {"entity_type": "edge", "entity_id": edge_id}
                    )

            _commit_fname = file_info_dict.get("filename", "unknown")
            event_bus.emit(
                "task_completed",
                action=f"Committed to graph: {_commit_fname}",
                source="worker",
                details={
                    "source_id": file_id,
                    "filename": _commit_fname,
                    "nodes": len(result.get("created_nodes", [])),
                    "edges": len(result.get("created_edges", [])),
                },
                database_name=settings.current_database,
            )

            # Trigger a graph-snapshot refresh so the dashboard + export pipeline
            # see fresh per-template/source counts without the user having to
            # click "refresh".
            try:
                snapshot_task_id = await queue_client.enqueue_task(
                    queue=QUEUE_OPERATIONS,
                    operation=OP_BUILD_GRAPH_SNAPSHOT,
                    data={"database_name": settings.current_database},
                    priority=settings.priorities.background,
                    metadata={
                        "operation_type": OP_BUILD_GRAPH_SNAPSHOT,
                        "trigger": "post_commit",
                        "source_id": file_id,
                    },
                )
                logger.info(
                    "graph_snapshot_refresh_enqueued",
                    database_name=settings.current_database,
                    task_id=snapshot_task_id,
                    trigger="post_commit",
                )
            except Exception as refresh_exc:
                # Never let a snapshot-refresh failure undo the commit success.
                logger.warning(
                    "graph_snapshot_refresh_enqueue_failed",
                    database_name=settings.current_database,
                    error_type=type(refresh_exc).__name__,
                    error_message=str(refresh_exc),
                )

            return result
        except Exception as exc:
            logger.exception(
                "import_commit_operation_failed",
                file_id=file_id,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            if self.source_repository:
                try:
                    self.source_repository.fail_commit(file_id, str(exc))
                except Exception as fail_exc:
                    logger.warning(
                        "fail_handler_raised",
                        event_key="fail_handler_raised",
                        source_id=file_id,
                        original_exception_type=type(exc).__name__,
                        original_exception_message=str(exc),
                        fail_exception_type=type(fail_exc).__name__,
                        fail_exception_message=str(fail_exc),
                    )
            raise

    async def _import_analysis_handler(  # noqa: PLR0911 - one return per terminal import outcome
        self,
        data: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        """Execute import file analysis operation using distributed chunk extraction.

        Restart-safe: calling this handler multiple times for the same
        source reuses any existing non-terminal ChunkExtractionJob
        (instead of creating a duplicate), upserts ChunkExtractionTask
        rows for any groups that don't have one, and only enqueues
        tasks whose status is ``pending`` or ``failed``. A completed
        task is never re-enqueued.

        Workflow:
            1. Claim the extraction slot (gating — only one source at a time).
            2. Look for an active job for this source. If found, skip the
               detection+creation phase and restore domain context from
               the stored job. Otherwise run the full fresh path (domain
               detection, extraction_config build, create_extraction_job).
            3. Fetch chunks, apply content filtering, build groups, apply
               depth strategy — deterministic under the pinned domain.
            4. UPSERT task rows via ``_upsert_extraction_tasks``.
            5. List non-terminal tasks and enqueue only those.
            6. Touch ``last_activity_at`` so the source-reconciler can
               distinguish forward progress from a stall.

        Args:
            data: Task data with file ID and analysis configuration.
            metadata: Task metadata.
            task_id: Task ID for tracking.

        Returns:
            Result dictionary with job_id and chunks_queued count.

        Raises:
            ValidationError: If ``file_id`` is not a string.
        """
        from chaoscypher_core.app_config import get_settings
        from chaoscypher_core.operations.pause_guard import check_paused

        file_id = data.get("file_id")
        _file_info = data.get("file_info", {})  # Stored in job for finalize handler
        analysis_depth = data.get("analysis_depth", "full")
        generate_embeddings = data.get("generate_embeddings", True)
        logger.info(
            "import_analysis_operation_processing",
            file_id=file_id,
            analysis_depth=analysis_depth,
            mode="distributed_chunk_extraction",
        )

        settings = get_settings()
        # Build the EngineSettings view once for engine-relevant reads
        # (domain registry, chunking/analysis knobs). ``settings`` is retained
        # for app-only reads (queue priorities) and the boundary builders
        # downstream that take the backend Settings (``_create_fresh_extraction_job``,
        # ``trigger_next_waiting_extraction``).
        engine_settings = self._resolve_engine_settings(settings)
        # Use shared adapter (passed from worker context)
        adapter = self.source_repository

        # Pause guard: runs before try_claim_extraction so a paused
        # source never blocks the single-extraction slot.
        if isinstance(file_id, str) and adapter is not None:
            pause_check = check_paused(
                source_id=file_id,
                database_name=settings.current_database,
                adapter=adapter,
            )
            if pause_check.paused:
                logger.info(
                    "handler_skipped_paused",
                    handler="_import_analysis_handler",
                    source_id=file_id,
                    scope=pause_check.scope,
                    reason=pause_check.reason,
                )
                return {"skipped": "paused"}

        # Claim the extraction slot or enqueue as waiting
        if not isinstance(file_id, str):
            msg = "file_id must be a string"
            raise ValidationError(msg, field="file_id")

        # Early abort: reject extraction before spending any LLM tokens if the
        # source is already committed. Clean up any dangling pending task rows
        # so we do not keep DB state we will never use. Audit fix #H5.
        if adapter is not None:
            from chaoscypher_core.exceptions import InvalidStateError

            try:
                adapter.assert_extractable(file_id, settings.current_database)
            except InvalidStateError:
                adapter.delete_pending_chunk_tasks_for_source(
                    source_id=file_id,
                    database_name=settings.current_database,
                )
                logger.warning(
                    "extraction_aborted_source_already_committed",
                    source_id=file_id,
                    database_name=settings.current_database,
                )
                raise

        # --- Confirmation gate (hoisted BEFORE the slot claim) ---------------
        # Detection is a fast heuristic (no LLM); running it here lets the gate
        # decide park-vs-proceed before ``_try_claim_or_wait`` claims the single
        # global extraction slot. Parking after a claim would leak the slot
        # forever (F41 invariant below). gate_decision reads ONLY persisted
        # SourceRow state so recovery re-dispatch (_classify_indexed) and
        # trigger_next_waiting evaluate it identically.
        forced_domain = _file_info.get("forced_domain")
        domain_result: dict[str, Any] | None = None
        if adapter is not None:
            from chaoscypher_core.operations.importing.confirmation_gate import (
                gate_decision,
                park_for_confirmation,
                proposal_from_detection,
            )
            from chaoscypher_core.services.sources.engine.extraction.domains import (
                create_domain_sample_text,
                get_domain_registry,
            )
            from chaoscypher_core.services.sources.engine.extraction.orchestration import (
                detect_extraction_domain,
            )

            gate_source = adapter.get_source(file_id, settings.current_database)
            if gate_source is not None:
                # Row is the source of truth for the confirmed/forced domain.
                # On a recovery re-dispatch the rebuilt ``_file_info`` carries no
                # ``forced_domain`` (recovery._build_file_info_from_source omits
                # it), so without this fallback a CONFIRMED source would re-run
                # auto-detection and extract under a possibly-different domain,
                # defeating the confirmation guarantee in the crash window.
                # Mirrors how filtering_mode / extraction_depth are read from the
                # row rather than the queue payload.
                if not forced_domain:
                    forced_domain = gate_source.get("forced_domain")

                # Wizard §3.1 dedupe (2026-05-29): if the indexing handler
                # already wrote a detection_proposal (eager-detection step),
                # AND the gate will park, reuse it instead of re-running
                # detect_extraction_domain. This avoids a second heuristic run
                # and eliminates drift between the wizard's proposal and the
                # chip's proposal. For the proceed path, detection still runs
                # so domain_result is available to _create_fresh_extraction_job.
                _prewritten_proposal: dict[str, Any] | None = gate_source.get("detection_proposal")
                if _prewritten_proposal and gate_decision(gate_source) == "park":
                    park_for_confirmation(adapter, file_id, _prewritten_proposal)
                    logger.info(
                        "import_analysis_parked_for_confirmation",
                        file_id=file_id,
                        detected_domain=_prewritten_proposal.get("detected_domain"),
                        reused_eager_proposal=True,
                    )
                    return {
                        "status": "parked",
                        "file_id": file_id,
                        "detected_domain": _prewritten_proposal.get("detected_domain"),
                    }

                _gate_chunks = adapter.get_chunks_for_extraction(
                    source_id=file_id,
                    database_name=settings.current_database,
                )
                _sample = create_domain_sample_text(_gate_chunks, content_key="content")
                _registry = get_domain_registry(
                    engine_settings, database_name=settings.current_database
                )
                domain_result = detect_extraction_domain(
                    registry=_registry,
                    forced_domain=forced_domain,
                    sample_text=_sample,
                    filename=_file_info.get("filename", ""),
                    metadata=_file_info.get("metadata", {}),
                )

                if gate_decision(gate_source) == "park":
                    proposal = proposal_from_detection(domain_result)
                    park_for_confirmation(adapter, file_id, proposal)
                    logger.info(
                        "import_analysis_parked_for_confirmation",
                        file_id=file_id,
                        detected_domain=domain_result.get("detected_domain"),
                    )
                    return {
                        "status": "parked",
                        "file_id": file_id,
                        "detected_domain": domain_result.get("detected_domain"),
                    }

        # Slot-lifetime invariant: ``try_claim_extraction`` (inside
        # ``_try_claim_or_wait``) sets status=EXTRACTING. The claim must
        # be paired with either:
        #   - success-finalize (``finalize_extraction_handler`` →
        #     ``trigger_next_waiting_extraction``), or
        #   - failure-release (this handler's ``except`` block →
        #     ``fail_extraction`` + ``trigger_next_waiting_extraction``).
        # Never leak: if a code path claims the slot and neither
        # finalizes nor fails, the extraction queue stalls with no
        # recovery. Audit fix F41.
        waiting_result = _try_claim_or_wait(
            adapter=adapter,
            file_id=file_id,
            database_name=settings.current_database,
            analysis_depth=analysis_depth,
            generate_embeddings=generate_embeddings,
            file_info=_file_info,
        )
        if waiting_result is not None:
            return waiting_result

        try:
            # Clear any waiting flag (in case this was a waiting source that's now starting)
            adapter.clear_extraction_waiting(file_id)
            adapter.update_step_progress(file_id, 1, 3, "Preparing extraction")

            _start_fname = _file_info.get("filename", "unknown")
            event_bus.emit(
                "task_started",
                action=f"Extraction started: {_start_fname}",
                source="worker",
                details={"source_id": file_id, "filename": _start_fname},
                database_name=settings.current_database,
            )

            from chaoscypher_core.services.sources.engine.extraction.domains import (
                get_domain_registry,
            )
            from chaoscypher_core.services.sources.engine.extraction.orchestration import (
                apply_depth_strategy,
                build_extraction_groups,
            )

            # ``forced_domain`` is resolved once in the hoisted confirmation
            # gate above (before the slot claim); reuse it here.
            registry = get_domain_registry(engine_settings, database_name=settings.current_database)

            # Resolve or resume the extraction job and fetch chunks
            existing_job = adapter.get_active_extraction_job(
                source_id=file_id, database_name=settings.current_database
            )

            try:
                if existing_job is not None:
                    job_id, domain, all_chunks = _resume_extraction_job(
                        adapter=adapter,
                        existing_job=existing_job,
                        file_id=file_id,
                        registry=registry,
                        database_name=settings.current_database,
                    )
                else:
                    job_id, domain, all_chunks = _create_fresh_extraction_job(
                        adapter=adapter,
                        file_id=file_id,
                        file_info=_file_info,
                        forced_domain=forced_domain,
                        registry=registry,
                        settings=settings,
                        analysis_depth=analysis_depth,
                        generate_embeddings=generate_embeddings,
                        task_id=task_id,
                        domain_result=domain_result,
                    )
            except ValueError as ve:
                return {"status": "error", "error": str(ve), "file_id": file_id}

            # Apply content filtering, build groups, apply depth strategy.
            # Workstream 1 (2026-05-07): pass the source row so the toggle
            # comes from the persisted column rather than the queue payload.
            _filter_source_row: dict[str, Any] | None = None
            try:
                _filter_source_row = adapter.get_source(file_id, settings.current_database)
            except (SQLAlchemyError, OperationalError) as exc:  # pragma: no cover — defensive
                logger.warning(
                    "import_content_filter_row_lookup_failed",
                    source_id=file_id,
                    error_type=type(exc).__name__,
                )
                _filter_source_row = None
            all_chunks, filter_stats = _apply_content_filtering(
                all_chunks=all_chunks,
                domain=domain,
                file_info=_file_info,
                file_id=file_id,
                source_row=_filter_source_row,
            )

            # Record filtering stats on the job (both fresh and resume paths).
            # Filtering is deterministic so re-runs produce the same stats;
            # the write is idempotent — same values overwrite same values.
            _persist_filter_stats(adapter, job_id, filter_stats)

            # Surface user-regex timeout fallbacks as a quality signal.
            # Each hit means a user-supplied custom pattern returned its safe
            # default instead of the real answer — an adversarial or
            # accidentally-pathological regex silently bypassed the filter.
            if filter_stats is not None and filter_stats.regex_timeouts > 0:
                from chaoscypher_core.services.quality.counters import (
                    QualityCounter,
                    increment_quality_counter,
                )

                await increment_quality_counter(
                    adapter=adapter,
                    source_id=file_id,
                    database_name=settings.current_database,
                    counter=QualityCounter.USER_REGEX_TIMEOUT_HITS,
                    n=filter_stats.regex_timeouts,
                )

            total_chunks_before_depth = len(all_chunks)
            hierarchical_groups = build_extraction_groups(
                all_chunks,
                target_tokens=engine_settings.chunking.target_group_tokens,
                overlap=engine_settings.chunking.group_overlap,
            )

            if not hierarchical_groups:
                error_msg = "No extraction groups could be built from chunks"
                logger.warning(
                    "import_analysis_no_groups",
                    file_id=file_id,
                    error=error_msg,
                    chunks_available=total_chunks_before_depth,
                )
                adapter.fail_extraction_job(job_id, error_msg)
                adapter.fail_extraction(file_id, error_msg)
                return {"status": "error", "error": error_msg, "file_id": file_id}

            # Apply depth strategy (shared Core logic)
            total_groups = len(hierarchical_groups)
            hierarchical_groups = apply_depth_strategy(
                hierarchical_groups,
                analysis_depth,
                quick_sample_size=engine_settings.analysis.quick_sample_size,
            )

            # Cost / resource-exhaustion backstop: hard-fail before enqueuing
            # any OP_EXTRACT_CHUNK task when the full-mode fan-out exceeds the
            # per-source ceiling. Quick mode is already bounded by depth
            # sampling, so this only trips on a pathological full-mode source.
            # The raise is caught by this handler's ``except`` block, which
            # releases the extraction slot (fail_extraction +
            # trigger_next_waiting_extraction) and re-raises; the queue
            # classifies SourceFanoutLimitExceededError as permanent (no retry).
            enforce_source_fanout_ceiling(
                item_count=len(hierarchical_groups),
                max_items=engine_settings.chunking.max_groups_per_source,
                item_noun="chunk-groups",
                stage="extraction",
                setting_path="chunking.max_groups_per_source",
            )

            adapter.update_extraction_job_total(
                job_id=job_id,
                total_chunks=len(hierarchical_groups),
                database_name=settings.current_database,
            )

            logger.info(
                "chunk_extraction_groups_selected",
                job_id=job_id,
                file_id=file_id,
                total_groups=total_groups,
                selected_groups=len(hierarchical_groups),
                analysis_depth=analysis_depth,
                resumed=existing_job is not None,
            )

            # Upsert task rows + enqueue only non-terminal tasks
            chunks_enqueued = await self._enqueue_chunk_tasks(
                adapter=adapter,
                job_id=job_id,
                file_id=file_id,
                hierarchical_groups=hierarchical_groups,
                settings=settings,
            )

            # Checkpoint last_activity_at so the source-reconciler can
            # distinguish forward progress from a stall
            adapter.update_source_last_activity(
                source_id=file_id,
                database_name=settings.current_database,
                at_time=datetime.now(UTC),
            )

            logger.info(
                "import_analysis_chunks_queued",
                job_id=job_id,
                file_id=file_id,
                chunks_queued=chunks_enqueued,
                total_groups=len(hierarchical_groups),
                resumed=existing_job is not None,
            )

            return {
                "status": "queued",
                "file_id": file_id,
                "job_id": job_id,
                "chunks_queued": chunks_enqueued,
                "resumed": existing_job is not None,
            }

        except Exception as exc:
            logger.exception(
                "import_analysis_operation_failed",
                file_id=file_id,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )

            # Update import status to error
            try:
                adapter.fail_extraction(file_id, str(exc))
            except Exception as fail_exc:
                logger.warning(
                    "fail_handler_raised",
                    event_key="fail_handler_raised",
                    source_id=file_id,
                    original_exception_type=type(exc).__name__,
                    original_exception_message=str(exc),
                    fail_exception_type=type(fail_exc).__name__,
                    fail_exception_message=str(fail_exc),
                )
                raise

            # Slot-lifetime invariant: the slot we claimed via
            # try_claim_extraction is now released (status=ERROR), so we
            # must dispatch the next waiting source. Without this, one
            # ERROR row stalls the entire extraction queue with no
            # recovery path. Audit fix F41.
            #
            # Suppress dispatch failures so they cannot mask the original
            # handler exception — the queue-level error visibility on the
            # original exception matters more than the dispatch retry.
            # But log a breadcrumb so a regression in
            # ``trigger_next_waiting_extraction`` doesn't silently re-stall
            # the queue with no observability.
            try:
                await trigger_next_waiting_extraction(adapter, settings.current_database, settings)
            except Exception as dispatch_exc:
                logger.warning(
                    "trigger_next_waiting_extraction_failed",
                    source_id=file_id,
                    original_exception_type=type(exc).__name__,
                    original_exception_message=str(exc),
                    error_type=type(dispatch_exc).__name__,
                    error_message=str(dispatch_exc),
                )

            raise

        # No finally/disconnect - singleton adapter is long-lived

    async def _enqueue_chunk_tasks(
        self,
        adapter: SqliteAdapter,
        job_id: str,
        file_id: str,
        hierarchical_groups: list[dict[str, Any]],
        settings: Settings,
    ) -> int:
        """UPSERT chunk task rows and enqueue only non-terminal ones.

        Restart-safe: existing task rows (regardless of status) are
        left alone during the upsert, and only tasks whose status is
        ``pending`` or ``failed`` are dispatched to the LLM queue.
        A task that's already ``completed`` is never re-enqueued, so
        a handler re-dispatch can't duplicate work.

        Template data (guidance, examples) is stored once in the job's
        ``extraction_config`` column, not duplicated in each chunk's
        queue data. The chunk handler reads templates from the job
        record.

        Args:
            adapter: Storage adapter for database operations.
            job_id: Extraction job ID.
            file_id: Source file ID.
            hierarchical_groups: Groups to process.
            settings: Application settings.

        Returns:
            Number of chunk tasks actually dispatched to the queue on
            this call. Zero is a valid result — it means every group
            already has a terminal task row (commonly hit on the second
            attempt of a job whose tasks all completed before an
            unrelated crash).
        """
        # UPSERT task rows — skips indices that already have one, which
        # preserves work from any previous attempt on this same job.
        _upsert_extraction_tasks(
            job_id=job_id,
            groups=hierarchical_groups,
            database_name=settings.current_database,
            adapter=adapter,
        )

        adapter.update_step_progress(file_id, 2, 3, "Queuing chunks for analysis")

        # Mark job as running and set initial progress BEFORE queuing
        # chunks. This must happen first to avoid a race where the
        # worker picks up a chunk and updates progress before this code
        # sets the initial (0/N) state, causing the user to never see
        # "chunk 1/N". On resume this is a no-op write of the same
        # status, which is fine.
        adapter.start_extraction_job(job_id)
        total = len(hierarchical_groups)
        adapter.update_step_progress(file_id, 0, total, f"Analyzing chunk 1/{total}")

        # Pick up only tasks that still need work. Completed tasks are
        # deliberately excluded so a re-dispatch doesn't trample them.
        # 'queued' is included because a paused-skip leaves the row at
        # status='queued' with no live Valkey task — see the matching
        # comment in services/sources/recovery.py.
        non_terminal_tasks = adapter.list_extraction_tasks_by_status(
            job_id=job_id,
            statuses=["pending", "queued", "failed"],
            database_name=settings.current_database,
        )
        if not non_terminal_tasks:
            logger.info(
                "chunk_tasks_enqueue_noop_all_terminal",
                job_id=job_id,
                file_id=file_id,
                total_groups=len(hierarchical_groups),
            )
            return 0

        # Index groups by their chunk_index (their enumerate position
        # in build_extraction_groups output) so each non-terminal task
        # can be paired with its group to rebuild the queue payload.
        groups_by_index = dict(enumerate(hierarchical_groups))

        batch_specs: list[dict[str, Any]] = []
        enqueued_task_ids: list[str] = []
        for task in non_terminal_tasks:
            idx = task["chunk_index"]
            group = groups_by_index.get(idx)
            if group is None:
                # Defensive: task exists for an index that isn't in the
                # current groups. Can only happen if groups shrank
                # between runs (unexpected under deterministic
                # grouping). Skip rather than fail.
                logger.warning(
                    "chunk_task_index_out_of_range",
                    job_id=job_id,
                    chunk_index=idx,
                    total_groups=len(hierarchical_groups),
                )
                continue
            batch_specs.append(
                {
                    "operation": OP_EXTRACT_CHUNK,
                    "data": {
                        "chunk_task_id": task["id"],
                        "job_id": job_id,
                        "database_name": settings.current_database,
                        "chunk_index": idx,
                        "hierarchical_group_id": group.get("id"),
                        "small_chunk_ids": group.get("small_chunk_ids"),
                    },
                    "priority": settings.priorities.background,
                    "metadata": {
                        "job_id": job_id,
                        "chunk_task_id": task["id"],
                        "chunk_index": idx,
                        "operation_type": OP_EXTRACT_CHUNK,
                    },
                }
            )
            enqueued_task_ids.append(task["id"])

        if not batch_specs:
            return 0

        queue_task_ids = await queue_client.enqueue_tasks_batch(QUEUE_LLM, batch_specs)

        # Batch update: record the queue task id for every just-enqueued chunk task
        task_queue_pairs = list(zip(enqueued_task_ids, queue_task_ids, strict=True))
        adapter.mark_chunk_tasks_queued_batch(task_queue_pairs)

        return len(batch_specs)

    async def _index_document_handler(
        self,
        data: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        """Execute document indexing (chunking + persist; embedding is queued separately).

        The LLM-bound embedding stage runs on ``QUEUE_LLM`` via
        ``OP_EMBED_CHUNKS`` — this handler enqueues it after chunks are
        persisted. See ``indexing_handler.py`` for the split rationale.

        Args:
            data: Task data with file ID and file info.
            metadata: Task metadata.
            task_id: Queue dispatcher contract — ``_execute_handler`` always
                passes it. Must be accepted even though this handler does
                not currently forward it to ``handle_index_document``;
                dropping it makes every dispatch raise ``TypeError`` and
                stalls every import.

        Returns:
            Result dictionary with chunks_persisted, embed_task_id, and
            ``queued_for_embedding`` status.
        """
        return await handle_index_document(
            data=data,
            source_repository=self.source_repository,
            chunking_service=self.chunking_service,
            metadata=metadata,
            engine_settings=self.engine_settings,
        )

    async def _embed_chunks_handler(
        self,
        data: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        """Execute chunk embedding + indexing-stage finalization on QUEUE_LLM.

        Args:
            data: Task data with ``source_id`` and ``file_info``.
            metadata: Task metadata.
            task_id: Task ID for tracking cancellation.

        Returns:
            Result dictionary with chunks_count, embedding_model, and
            INDEXED status.
        """
        return await handle_embed_chunks(
            data=data,
            source_repository=self.source_repository,
            indexing_service=self.indexing_service,
            metadata=metadata,
            task_id=task_id,
        )

    async def _lexicon_import_handler(
        self,
        data: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        """Execute Lexicon package import operation.

        Args:
            data: Task data with owner_username, repo_name, version, database_name.
            metadata: Task metadata.
            task_id: Task ID for tracking.

        Returns:
            Result dictionary with import statistics and errors.

        """
        return await handle_lexicon_import(
            data=data,
            graph_repository=self.graph_repository,
            metadata=metadata,
            task_id=task_id,
        )
