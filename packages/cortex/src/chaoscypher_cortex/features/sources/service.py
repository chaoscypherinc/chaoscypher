# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Backend wrapper for engine SourceService (VSA compliance).

Delegates all source/citation management to chaoscypher service.
Tag operations are handled by TagService (tag_service.py).
"""

from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core import policy
from chaoscypher_core.constants import (
    OP_INDEX_DOCUMENT,
    OP_VISION_FINALIZE,
    OP_VISION_PAGE,
)
from chaoscypher_core.models import SourceErrorStage, SourceStatus
from chaoscypher_core.operations import queue_utils
from chaoscypher_core.operations.importing.confirmation_gate import (
    confirm_extraction as confirm_extraction_gate,
)
from chaoscypher_core.operations.importing.confirmation_gate import (
    gate_decision,
    park_for_confirmation,
)
from chaoscypher_core.services.events import event_bus
from chaoscypher_core.services.sources.management.re_extraction import force_re_extract
from chaoscypher_cortex.features.sources.mappers import (
    attach_quality_scores,
)


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite import SqliteAdapter
    from chaoscypher_core.adapters.sqlite.repos import GraphRepository, SearchRepository
    from chaoscypher_core.app_config import Settings
    from chaoscypher_core.services.graph.management.source import (
        SourceService as EngineSourceService,
    )
    from chaoscypher_cortex.features.sources.extraction_api import (
        BulkConfirmExtractionResponse,
    )
    from chaoscypher_cortex.features.sources.models import SourceResponse


logger = structlog.get_logger(__name__)

# Module-level template name cache (persists across per-request SourceService instances)
# Bounded to prevent unbounded growth (cleared when exceeding max size)
_template_name_cache: dict[str, str] = {}

# System template ID → friendly name mapping.
# Dynamically derived from default_templates.py so new templates are picked up automatically.
from chaoscypher_core.templates.default_templates import get_all_default_templates


_SYSTEM_TEMPLATE_NAMES: dict[str, str] = {
    t["id"]: t["name"] for t in get_all_default_templates() if t["id"].startswith("system_")
}

# Maps an in-flight SourceStatus to the human-readable error_message
# that abort_processing persists. The target status is always
# SourceStatus.ERROR (handled inside abort_processing's adapter call)
# so it is no longer carried here. None = no message override.
_ABORT_TRANSITIONS: dict[str, str | None] = {
    SourceStatus.PENDING: "Processing aborted by user",
    SourceStatus.INDEXING: "Indexing aborted by user",
    SourceStatus.VISION_PENDING: "Vision processing aborted by user",
    SourceStatus.EXTRACTING: "Extraction aborted by user",
    SourceStatus.COMMITTING: "Commit aborted by user",
}

# Translate the in-flight lifecycle status (gerund) to the persisted
# error stage (noun) so the retry endpoint can decode where to resume.
# Audit fix #C2 — without this, abort writes "extracting" but retry
# only knows "extraction" and falls through to PENDING (full restart).
# VISION_PENDING maps to INDEXING because vision sits inside the indexing
# pipeline (PENDING → INDEXING → VISION_PENDING → INDEXING resume → INDEXED),
# so retry resumes the whole indexing stage.
_ABORT_STAGE_MAP: dict[str, SourceErrorStage] = {
    SourceStatus.PENDING: SourceErrorStage.INDEXING,
    SourceStatus.INDEXING: SourceErrorStage.INDEXING,
    SourceStatus.VISION_PENDING: SourceErrorStage.INDEXING,
    SourceStatus.EXTRACTING: SourceErrorStage.EXTRACTION,
    SourceStatus.COMMITTING: SourceErrorStage.COMMIT,
}


class SourceService:
    """Backend wrapper for engine SourceService.

    Provides VSA-compliant service layer that:
    - Delegates all business logic to engine SourceService
    - Maintains API compatibility with existing endpoints

    The chaoscypher service handles all business logic:
    - Source CRUD (create, read, update, delete, archive)
    - Chunk operations (get, list by source)
    - Citation tracking (by entity, by source)
    - Statistics calculation

    Tag operations are handled by TagService (tag_service.py).
    """

    def __init__(
        self,
        engine_service: EngineSourceService,
        database_name: str,
        settings: Settings,
        storage_adapter: SqliteAdapter,
        graph_repository: GraphRepository | None = None,
        search_repository: SearchRepository | None = None,
    ):
        """Initialize source service.

        Args:
            engine_service: Engine SourceService instance
            database_name: Database name for source operations
            settings: Application settings
            storage_adapter: SqliteAdapter for extraction/processing queries
            graph_repository: Optional graph repository for template name resolution
            search_repository: Optional search repository for cascade delete cleanup

        """
        self.engine_service = engine_service
        self.database_name = database_name
        self.settings = settings
        self.graph_repository = graph_repository
        self.search_repository = search_repository
        self.storage_adapter = storage_adapter

    # ================================
    # Source Operations
    # ================================

    def get_source(self, source_id: str) -> dict[str, Any] | None:
        """Get a source by ID."""
        return self.engine_service.get_source(source_id)

    def list_sources(
        self,
        page: int = 1,
        page_size: int | None = None,
        source_type: str | None = None,
        status: str | None = None,
        enabled: str | None = None,
        search: str | None = None,
        tag_id: str | None = None,
    ) -> dict[str, Any]:
        """List sources with filtering and pagination."""
        if page_size is None:
            page_size = self.settings.pagination.default_page_size
        return self.engine_service.list_sources(
            page=page,
            page_size=page_size,
            source_type=source_type,
            status=status,
            enabled=enabled,
            search=search,
            tag_id=tag_id,
        )

    async def trigger_extraction(
        self,
        source_id: str,
        analysis_depth: str = "full",
        domain: str | None = None,
        force: bool = False,
        filtering_mode: str | None = None,
        content_filtering: bool = True,
        enable_direction_correction: bool | None = None,
        protect_orphans: bool | None = None,
        enable_inverse_relationships: bool | None = None,
        max_entity_degree_override: int | None = None,
    ) -> dict[str, Any]:
        """Queue manual entity extraction for an indexed (or committed+force) source.

        Validates source existence + status + LLM provider availability,
        builds the queue ``file_info`` payload, and enqueues the analysis
        task. Returns the queued status envelope. Raises typed domain
        exceptions that the HTTP handler maps to status codes.

        Args:
            source_id: Source file ID.
            analysis_depth: ``quick`` or ``full`` (default).
            domain: Force a specific extraction domain; ``None`` or
                ``__auto__`` auto-detects.
            force: When True, allow re-extraction on an already-committed source.
            filtering_mode: Optional filtering-mode override.
            content_filtering: Enable chunk content-filtering.
            enable_direction_correction: When not None, persists the value on
                the source row so the import worker reads it. None leaves the
                current row value untouched.
            protect_orphans: When not None, persists the value on the source
                row. True keeps orphan entities; False drops them. None leaves
                the current row value untouched.
            enable_inverse_relationships: When not None, persists the value on
                the source row so the commit worker reads it. None leaves the
                current row value untouched.
            max_entity_degree_override: When a positive int, persists the
                per-source degree cap on the source row so the extraction
                worker applies it. None leaves the current row value untouched.

        Returns:
            ``{"source_id": source_id, "status": SourceStatus.EXTRACTING}``

        Raises:
            NotFoundError: Source does not exist.
            ValidationError: Source status is not eligible for extraction.
            ExternalServiceError: No LLM provider configured.
            OperationError: Queue enqueue failed.
        """
        from chaoscypher_core.exceptions import (
            ExternalServiceError,
            NotFoundError,
            OperationError,
            ValidationError,
        )

        source = self.get_source(source_id)
        if source is None:
            raise NotFoundError("Source", source_id)

        source_status = source.get("status", "")
        allowed_statuses = {SourceStatus.INDEXED}
        if force:
            allowed_statuses.add(SourceStatus.COMMITTED)

        if source_status not in allowed_statuses:
            detail = (
                f"Source status is '{source_status}', extraction requires 'indexed'"
                if not force
                else (
                    f"Source status is '{source_status}', extraction requires "
                    "'indexed' or 'committed' (with force=True)"
                )
            )
            raise ValidationError(detail)

        # Re-extraction on a COMMITTED source: reset commit state + graph artifacts
        # before dispatching, so the new extraction can claim the extraction slot
        # and the prior graph nodes/edges do not pollute the new commit.
        # Both writes run inside adapter.transaction() via force_re_extract so the
        # adapter-side reset rolls back on any exception.
        if force and source_status == SourceStatus.COMMITTED:
            if self.graph_repository is None:
                raise OperationError(
                    "graph_repository required for force-re-extract on committed source",
                    operation="re_extract",
                )
            force_re_extract(
                source_id=source_id,
                database_name=self.database_name,
                storage_adapter=self.storage_adapter,
                graph_repository=self.graph_repository,
            )

        try:
            from chaoscypher_core.llm_queue import get_provider_factory

            factory = get_provider_factory()
            factory.get_chat_provider()
        except Exception as exc:
            raise ExternalServiceError(
                "llm",
                reason="No LLM provider configured. Use an MCP client to extract.",
            ) from exc

        resolved_domain = domain if domain and domain != "__auto__" else None

        # Phase 4 (2026-05-08): persist enable_direction_correction and
        # protect_orphans on the source row when explicitly provided so the
        # import worker reads them from the authoritative row rather than the
        # queue payload.
        # Phase 6 (2026-05-08): extended with enable_inverse_relationships and
        # max_entity_degree_override (both nullable per-source overrides).
        _toggle_updates: dict[str, bool | int] = {}
        if enable_direction_correction is not None:
            _toggle_updates["enable_direction_correction"] = enable_direction_correction
        if protect_orphans is not None:
            _toggle_updates["protect_orphans"] = protect_orphans
        if enable_inverse_relationships is not None:
            _toggle_updates["enable_inverse_relationships"] = enable_inverse_relationships
        if max_entity_degree_override is not None:
            _toggle_updates["max_entity_degree_override"] = max_entity_degree_override
        if _toggle_updates:
            self.storage_adapter.update_source_columns(
                source_id=source_id,
                database_name=self.database_name,
                updates=_toggle_updates,
            )

        # Unified confirmation gate (design §3.2 / §6.2): evaluate gate_decision
        # against an effective source view that already reflects the resolved
        # forced_domain, so a caller that supplies an explicit domain is treated
        # as a "forced" trigger and proceeds unconditionally, while an unforced
        # manual trigger on a gate-eligible source (confirmation_required=True,
        # not yet confirmed, not already past INDEXED) parks identically to the
        # upload path. The effective_source merges resolved_domain in-memory so
        # gate_decision sees the correct state without a round-trip to storage.
        effective_source: dict[str, Any] = {
            **source,
            "forced_domain": resolved_domain
            if resolved_domain is not None
            else source.get("forced_domain"),
        }
        if gate_decision(effective_source) == "park":
            from chaoscypher_core.app_config.engine_factory import (
                build_engine_settings,
            )
            from chaoscypher_core.services.sources.engine.extraction.domains import (
                create_domain_sample_text,
                get_domain_registry,
            )
            from chaoscypher_core.services.sources.engine.extraction.orchestration import (
                detect_extraction_domain,
            )

            _chunks = self.storage_adapter.get_chunks_for_extraction(
                source_id=source_id,
                database_name=self.database_name,
            )
            _sample = create_domain_sample_text(_chunks, content_key="content")
            # Pass the full engine settings so user-installed custom domain plugins
            # are discovered via _get_user_plugins_path — matching the worker path
            # in import_service.py which calls get_domain_registry(settings, ...).
            # Passing None previously skipped the user-plugin path entirely.
            _registry = get_domain_registry(
                build_engine_settings(self.settings),
                database_name=self.database_name,
            )
            domain_result = detect_extraction_domain(
                registry=_registry,
                forced_domain=None,
                sample_text=_sample,
                filename=source.get("filename", ""),
                metadata={},
            )
            proposal = {
                "ranking": domain_result.get("ranking", []),
                "confidence": domain_result.get("confidence"),
                "detected_domain": domain_result.get("detected_domain"),
                "low_confidence": domain_result.get("low_confidence", False),
            }
            park_for_confirmation(self.storage_adapter, source_id, proposal)
            logger.info("manual_extraction_parked", source_id=source_id)
            return {"source_id": source_id, "status": SourceStatus.AWAITING_CONFIRMATION}

        file_info: dict[str, Any] = {
            "id": source_id,
            "filepath": source.get("filepath"),
            "file_type": source.get("file_type"),
            "filename": source.get("filename"),
            "auto_analyze": True,
            "analysis_depth": analysis_depth,
            "generate_embeddings": True,
            "forced_domain": resolved_domain,
            "filtering_mode": filtering_mode,
            "content_filtering": content_filtering,
        }

        try:
            await queue_utils.queue_import_analysis(
                file_id=source_id,
                file_info=file_info,
                analysis_depth=analysis_depth,
                database_name=self.database_name,
                generate_embeddings=True,
                priority=self.settings.priorities.background,
                extra_metadata={"triggered_by": "manual_api"},
            )
        except Exception as exc:
            logger.exception(
                "manual_extraction_queue_failed",
                source_id=source_id,
                error_type=type(exc).__name__,
            )
            raise OperationError(
                "Failed to queue extraction task",
                operation="queue_extraction",
            ) from exc

        logger.info(
            "manual_extraction_triggered",
            source_id=source_id,
            analysis_depth=analysis_depth,
            domain=resolved_domain,
            force=force,
        )

        return {"source_id": source_id, "status": SourceStatus.EXTRACTING}

    async def confirm_extraction(
        self,
        source_id: str,
        analysis_depth: str = "full",
        domain: str | None = None,
        filtering_mode: str | None = None,
        content_filtering: bool | None = None,
        enable_direction_correction: bool | None = None,
        protect_orphans: bool | None = None,
        enable_inverse_relationships: bool | None = None,
        max_entity_degree_override: int | None = None,
    ) -> dict[str, Any]:
        """Record a confirm decision regardless of where the source is — thin pass-through.

        Validates the source exists, then delegates to the **state-aware** core
        ``confirm_extraction`` gate primitive, which branches on the persisted
        status (wizard §3.2 "confirm-vs-gate race"):

        - **``awaiting_confirmation``** (gate parked it first): CAS-flips
          ``awaiting_confirmation → indexed``, persists the chosen domain +
          overrides, sets ``extraction_confirmed_at`` write-once, re-queues.
        - **Pre-gate** (``pending`` / ``indexing`` / ``vision_pending`` /
          ``indexed``, in the wizard the user confirms while embedding still
          runs): records ``forced_domain`` + ``extraction_confirmed_at`` +
          overrides WITHOUT changing status or re-queueing; the analysis stage
          then proceeds on its own (no park).
        - **Past the gate** (``extracting`` and beyond) / already confirmed /
          errored: ``ConflictError`` (HTTP 409) — too late to change the domain.

        A lost CAS on the parked branch (a concurrent confirm already flipped
        the row) is a benign no-op: the source is already heading to extraction,
        so we return the same indexed envelope without raising.

        The all-Core branching keeps this service a thin pass-through (CC031: no
        ``HTTPException``; the 409 originates as a Core ``ConflictError``).

        Raises:
            NotFoundError: Source does not exist.
            ConflictError: Source is past the gate / already confirmed / errored.
        """
        from chaoscypher_core.exceptions import NotFoundError

        source = self.get_source(source_id)
        if source is None:
            raise NotFoundError("Source", source_id)

        # State classification lives in the Core primitive (one "brain"): it
        # reads the persisted status and raises ConflictError for the past-gate /
        # already-confirmed / errored cases. This service no longer pre-gates on
        # awaiting_confirmation so the wizard's confirm-before-park case works.
        pre_confirm_status = source.get("status")

        resolved_domain = domain if domain and domain != "__auto__" else None
        # Build overrides with ONLY the keys actually supplied (drop None) so an
        # absent option leaves the persisted upload-time column as-is rather
        # than clobbering it. Writing the all-keys dict would NULL the NOT NULL
        # filtering_mode column (IntegrityError) and silently overwrite an
        # upload-time content_filtering=False with the True default. Mirrors the
        # MCP handler's present-keys-only pattern (mcp/server.py:_CONFIRM_*).
        # analysis_depth always flows through (the gate reads it for the queue
        # depth) and is non-optional at the API boundary.
        overrides: dict[str, Any] = {"analysis_depth": analysis_depth}
        _optional_overrides: dict[str, Any] = {
            "filtering_mode": filtering_mode,
            "content_filtering": content_filtering,
            "enable_direction_correction": enable_direction_correction,
            "protect_orphans": protect_orphans,
            "enable_inverse_relationships": enable_inverse_relationships,
            "max_entity_degree_override": max_entity_degree_override,
        }
        overrides.update({k: v for k, v in _optional_overrides.items() if v is not None})

        recorded = await confirm_extraction_gate(
            adapter=self.storage_adapter,
            file_id=source_id,
            chosen_domain=resolved_domain,
            overrides=overrides,
        )
        # Parked branch (or its benign CAS-loss) lands at INDEXED; the pre-gate
        # branch records the decision WITHOUT changing status, so report the
        # source's actual status (still indexing/indexed/vision_pending).
        result_status = (
            SourceStatus.INDEXED
            if pre_confirm_status == SourceStatus.AWAITING_CONFIRMATION
            else (pre_confirm_status or SourceStatus.INDEXED)
        )
        logger.info(
            "source_extraction_confirmed",
            source_id=source_id,
            domain=resolved_domain,
            recorded=recorded,
            status=result_status,
        )
        return {"source_id": source_id, "status": result_status}

    async def confirm_extraction_bulk(self, source_ids: list[str]) -> BulkConfirmExtractionResponse:
        """Confirm each source independently; collect per-item outcomes.

        One source failing does not abort the rest. Each confirm uses the
        source's detected domain (domain=None -> core reads forced/proposal).
        """
        from chaoscypher_cortex.features.sources.extraction_api import (
            BulkConfirmExtractionResponse,
            BulkConfirmItem,
        )

        results: list[BulkConfirmItem] = []
        for sid in source_ids:
            try:
                await self.confirm_extraction(source_id=sid)
                results.append(BulkConfirmItem(source_id=sid, ok=True))
            except Exception as exc:
                msg_attr = getattr(exc, "message", None)
                msg = msg_attr if isinstance(msg_attr, str) and msg_attr else str(exc)
                results.append(
                    BulkConfirmItem(
                        source_id=sid,
                        ok=False,
                        error=msg[: self.settings.logs.error_message_preview_chars],
                    )
                )
        confirmed = sum(1 for r in results if r.ok)
        return BulkConfirmExtractionResponse(
            confirmed=confirmed,
            failed=len(results) - confirmed,
            results=results,
        )

    async def reclassify_source(
        self,
        source_id: str,
        domain: str,
        analysis_depth: str = "full",
        content_filtering: bool = True,
    ) -> dict[str, Any]:
        """Reclassify a source under a different domain and queue re-extraction.

        Reclassification is a convenience wrapper around ``trigger_extraction``
        with ``force=True``. It accepts any source in ``indexed`` or ``committed``
        status:

        - ``indexed``: queues extraction immediately (no reset needed).
        - ``committed``: atomically resets graph artifacts + source state via
          ``force_re_extract`` then queues extraction.

        The ``domain`` argument is always forwarded as ``forced_domain`` so
        auto-detection is bypassed.

        Args:
            source_id: Source file ID.
            domain: Domain name to use (e.g. ``"medical"``, ``"legal"``).
            analysis_depth: ``quick`` or ``full`` (default).
            content_filtering: Enable chunk content-filtering.

        Returns:
            ``{"source_id": source_id, "status": SourceStatus.EXTRACTING}``

        Raises:
            NotFoundError: Source does not exist.
            ValidationError: Source is not in a reclassifiable state.
            ExternalServiceError: No LLM provider configured.
            OperationError: Queue enqueue failed.
        """
        from chaoscypher_core.exceptions import NotFoundError

        source = self.get_source(source_id)
        if source is None:
            raise NotFoundError("Source", source_id)

        return await self.trigger_extraction(
            source_id=source_id,
            analysis_depth=analysis_depth,
            domain=domain,
            force=True,
            content_filtering=content_filtering,
        )

    def list_sources_enriched(
        self,
        page: int = 1,
        page_size: int | None = None,
        source_type: str | None = None,
        status: str | None = None,
        enabled: str | None = None,
        search: str | None = None,
        tag_id: str | None = None,
    ) -> dict[str, Any]:
        """List sources with duration fields, tags (batched), and domain icons enriched.

        Returns the same shape as ``list_sources`` but with each source dict
        already carrying ``indexing_duration_seconds`` / ``extraction_duration_seconds``
        / ``extraction_domain_icon`` and a ``tags`` list. The API handler
        assembles the pagination envelope on top of this.

        N+1 avoidance: tags are fetched for every source in a single batch
        query rather than per-row.
        """
        from chaoscypher_cortex.features.sources.mappers import (
            add_duration_fields,
            build_domain_fingerprint_map,
            build_domain_icon_map,
            enrich_domain_changed,
            enrich_domain_icons,
        )

        result = self.list_sources(
            page=page,
            page_size=page_size,
            source_type=source_type,
            status=status,
            enabled=enabled,
            search=search,
            tag_id=tag_id,
        )

        sources = [add_duration_fields(s) for s in result.get("sources", [])]

        source_ids = [s["id"] for s in sources]
        all_tags = self.engine_service.get_source_tags_batch(source_ids)
        for source in sources:
            source_tags = all_tags.get(source["id"], [])
            source["tags"] = [
                {"id": t["id"], "name": t["name"], "color": t.get("color")} for t in source_tags
            ]

        domain_icons = build_domain_icon_map(self.database_name)
        enrich_domain_icons(sources, domain_icons)

        fingerprint_map = build_domain_fingerprint_map(self.database_name)
        enrich_domain_changed(sources, fingerprint_map)

        return {"sources": sources, "total": result.get("total", 0)}

    def update_source(
        self,
        source_id: str,
        title: str | None = None,
        processing_status: str | None = None,
        enabled: bool | None = None,
        user_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Update a source."""
        return self.engine_service.update_source(
            source_id=source_id,
            title=title,
            processing_status=processing_status,
            enabled=enabled,
            user_metadata=user_metadata,
        )

    def delete_source(self, source_id: str) -> bool:
        """Delete a source (hard delete) with cascade cleanup."""
        result = self.engine_service.delete_source(
            source_id,
            graph_repo=self.graph_repository,
            search_repo=self.search_repository,
        )

        # Clean up rendered vision images
        if result:
            import shutil
            from pathlib import Path

            images_dir = (
                Path(str(self.settings.data_dir))
                / "databases"
                / self.database_name
                / "images"
                / source_id
            )
            if images_dir.exists():
                shutil.rmtree(images_dir)

        return result

    # ================================
    # Chunk Operations
    # ================================

    def get_chunks(
        self,
        source_id: str,
        page: int = 1,
        page_size: int | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        """Get all chunks for a source."""
        if page_size is None:
            page_size = self.settings.pagination.default_page_size
        return self.engine_service.get_chunks_by_source(
            source_id=source_id, page=page, page_size=page_size, status=status
        )

    def get_chunk(self, chunk_id: str) -> dict[str, Any] | None:
        """Get a single chunk by ID."""
        return self.engine_service.get_chunk(chunk_id)

    def get_chunks_by_ids(self, chunk_ids: list[str]) -> list[dict[str, Any]]:
        """Fetch multiple small chunks by ID.

        Used by ChunkSourceDataPanel (per-chunk rerun feature) to display
        the raw chunk text for an extraction task's grouped
        ``small_chunk_ids`` alongside the cleaned LLM input.
        """
        if not chunk_ids:
            return []
        chunks: list[dict[str, Any]] = self.storage_adapter.get_chunks_by_ids(
            chunk_ids=chunk_ids,
            database_name=self.storage_adapter.database_name,
        )
        return chunks

    # ================================
    # Citation Operations
    # ================================

    def _resolve_template_name(self, template_id: str | None) -> str | None:
        """Resolve template ID to template name.

        Uses module-level caching to avoid repeated lookups across requests.

        Args:
            template_id: Template ID to resolve (e.g., "template_abc123" or "system_template_item")

        Returns:
            Template name if found, original template_id if not resolvable

        """
        if not template_id:
            return template_id

        # Check module-level cache first (with size bound)
        if template_id in _template_name_cache:
            return _template_name_cache[template_id]

        if len(_template_name_cache) >= self.settings.batching.template_name_cache_size:
            _template_name_cache.clear()

        # Handle system templates with friendly names
        if template_id in _SYSTEM_TEMPLATE_NAMES:
            name = _SYSTEM_TEMPLATE_NAMES[template_id]
            _template_name_cache[template_id] = name
            return name

        # Try to resolve from graph repository
        if self.graph_repository:
            template = self.graph_repository.get_template(template_id)
            if template:
                _template_name_cache[template_id] = template.name
                return template.name

        # Cache the original value to avoid repeated lookups
        _template_name_cache[template_id] = template_id
        return template_id

    def get_citations(
        self,
        source_id: str,
        page: int = 1,
        page_size: int | None = None,
    ) -> dict[str, Any]:
        """Get all citations for a source."""
        if page_size is None:
            page_size = self.settings.pagination.default_page_size
        result = self.engine_service.get_citations_by_source(
            source_id=source_id, page=page, page_size=page_size
        )
        citations = result["citations"]
        total = result["total"]

        # Resolve template IDs to template names
        citation_dicts = []
        for citation in citations:
            # Storage protocol always returns dicts — use dict access directly
            if citation.get("entity_type"):
                citation["entity_type"] = self._resolve_template_name(citation["entity_type"])
            citation_dicts.append(citation)

        return {
            "citations": citation_dicts,
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    def get_citations_by_entity(self, entity_uri: str) -> list[dict[str, Any]]:
        """Get all citations for an entity."""
        result = self.engine_service.get_citations_by_entity(
            entity_uri=entity_uri,
            page=1,
            page_size=self.settings.pagination.max_page_size,
        )
        # Extract just the citation dicts from the results
        return [r["citation"] for r in result["citations"]]

    # ================================
    # Statistics
    # ================================

    def get_source_stats(self, source_id: str) -> dict[str, Any]:
        """Get statistics for a source."""
        return self.engine_service.get_source_stats(source_id)

    # ================================
    # Extraction Status & Control
    # ================================

    def get_extraction_status(self, source_id: str) -> dict[str, Any]:
        """Get extraction status and progress for a source.

        Args:
            source_id: Source file ID.

        Returns:
            Extraction progress dict with job status, chunk counts, and timing.

        Raises:
            ValueError: If source not found.

        """
        adapter = self.storage_adapter

        file_info = adapter.get_file(source_id, self.database_name)
        if not file_info:
            msg = f"Source {source_id} not found"
            raise ValueError(msg)

        job_id = file_info.get("current_extraction_job_id")
        if not job_id:
            return {
                "source_id": source_id,
                "status": file_info.get("status"),
                "has_extraction_job": False,
                "message": "No active extraction job for this source",
            }

        job = adapter.get_extraction_job(job_id)
        if not job:
            return {
                "source_id": source_id,
                "status": file_info.get("status"),
                "has_extraction_job": False,
                "message": "Extraction job not found",
            }

        summary = adapter.get_chunk_tasks_summary(job_id)
        current_chunk = adapter.get_running_chunk_task(job_id)

        total = job.get("total_chunks", 0)
        completed = job.get("completed_chunks", 0)
        failed = job.get("failed_chunks", 0)
        progress_percent = ((completed + failed) / total * 100) if total > 0 else 0

        return {
            "source_id": source_id,
            "job_id": job_id,
            "status": job.get("status"),
            "has_extraction_job": True,
            "total_chunks": total,
            "completed_chunks": completed,
            "failed_chunks": failed,
            "progress_percent": round(progress_percent, 1),
            "chunks_by_status": summary.get("by_status", {}),
            "total_entities": summary.get("total_entities", 0),
            "total_relationships": summary.get("total_relationships", 0),
            "extraction_depth": job.get("extraction_depth"),
            "started_at": job.get("started_at"),
            "completed_at": job.get("completed_at"),
            "current_chunk": current_chunk,
        }

    async def cancel_extraction(self, source_id: str) -> None:
        """Cancel extraction for a source.

        Cancels pending/queued extraction chunks. Running/completed chunks unaffected.
        Reverts source status to 'indexed'.

        Args:
            source_id: Source file ID.

        Raises:
            ValueError: If source not found or no active extraction job.

        """
        from chaoscypher_core.constants import QUEUE_LLM
        from chaoscypher_core.queue import queue_client

        adapter = self.storage_adapter

        file_info = adapter.get_file(source_id, self.database_name)
        if not file_info:
            msg = f"Source {source_id} not found"
            raise ValueError(msg)

        job_id = file_info.get("current_extraction_job_id")
        if not job_id:
            msg = "No active extraction job for this source"
            raise ValueError(msg)

        await queue_client.cancel_by_metadata(metadata={"job_id": job_id}, queue=QUEUE_LLM)
        adapter.update_extraction_job(job_id, {"status": "cancelled"})
        adapter.cancel_extraction(source_id)

    async def abort_processing(self, source_id: str) -> None:
        """Abort all in-progress processing for a source.

        Cancels queued/running tasks (indexing or extraction) and resets status.

        Args:
            source_id: Source file ID.

        Raises:
            ValueError: If source not found.
            RuntimeError: If source is not in a processing state.

        """
        from chaoscypher_core.constants import OP_IMPORT_COMMIT, QUEUE_LLM, QUEUE_OPERATIONS
        from chaoscypher_core.queue import queue_client

        adapter = self.storage_adapter

        file_info = adapter.get_file(source_id, self.database_name)
        if not file_info:
            msg = f"Source {source_id} not found"
            raise ValueError(msg)

        current_status = file_info.get("status")
        processing_statuses = {
            SourceStatus.PENDING,
            SourceStatus.INDEXING,
            SourceStatus.VISION_PENDING,
            SourceStatus.EXTRACTING,
            SourceStatus.COMMITTING,
        }

        if current_status not in processing_statuses:
            # Edge case: source is COMMITTED but a per-page vision retry is still
            # in flight (user clicked "Retry N failed" on the vision panel then
            # immediately clicked Stop). The source status never left COMMITTED
            # because vision retries don't re-enter VISION_PENDING, yet the
            # per-page OP_VISION_PAGE tasks are actively running on QUEUE_LLM and
            # would run to completion despite the explicit cancel.
            #
            # Before surfacing the "not currently processing" 400, check whether
            # there are PENDING vision_page_descriptions rows for this source.
            # If any exist, cancel the vision tasks and mark the rows as failed
            # so the recovery reconciler does not re-enqueue them.  source.status
            # intentionally stays COMMITTED — only the in-flight retry is aborted.
            if current_status == SourceStatus.COMMITTED:
                from chaoscypher_core.vision.states import VisionPageStatus

                pending_pages = adapter.list_vision_page_descriptions(
                    source_id, statuses=[VisionPageStatus.PENDING]
                )
                if pending_pages:
                    await queue_client.cancel_by_metadata(
                        metadata={"source_id": source_id, "operation_type": OP_VISION_PAGE},
                        queue=QUEUE_LLM,
                    )
                    await queue_client.cancel_by_metadata(
                        metadata={"source_id": source_id, "operation_type": OP_VISION_FINALIZE},
                        queue=QUEUE_OPERATIONS,
                    )
                    for page in pending_pages:
                        adapter.update_vision_page_description(
                            page_id=page["id"],
                            new_status=VisionPageStatus.FAILED,
                            description=None,
                            finish_reason=None,
                            error_message="aborted by user",
                            expected_current_status=VisionPageStatus.PENDING,
                        )
                    logger.info(
                        "abort_committed_vision_retry",
                        source_id=source_id,
                        pages_aborted=len(pending_pages),
                    )
                    return

            msg = f"Source is not currently processing (status: {current_status})"
            raise RuntimeError(msg)

        # Cancel queued tasks depending on the processing stage
        if current_status in {SourceStatus.PENDING, SourceStatus.INDEXING}:
            await queue_client.cancel_by_metadata(
                metadata={"file_id": source_id, "operation_type": OP_INDEX_DOCUMENT},
                queue=QUEUE_OPERATIONS,
            )
        elif current_status == SourceStatus.VISION_PENDING:
            # Vision pages run on QUEUE_LLM (per-page LLM calls); the
            # finalizer that re-enters the indexing handler runs on
            # QUEUE_OPERATIONS. Cancel both so neither completes after
            # the user aborts.
            await queue_client.cancel_by_metadata(
                metadata={"source_id": source_id, "operation_type": OP_VISION_PAGE},
                queue=QUEUE_LLM,
            )
            await queue_client.cancel_by_metadata(
                metadata={"source_id": source_id, "operation_type": OP_VISION_FINALIZE},
                queue=QUEUE_OPERATIONS,
            )
        elif current_status == SourceStatus.EXTRACTING:
            job_id = file_info.get("current_extraction_job_id")
            if job_id:
                await queue_client.cancel_by_metadata(metadata={"job_id": job_id}, queue=QUEUE_LLM)
                adapter.update_extraction_job(job_id, {"status": "cancelled"})
        elif current_status == SourceStatus.COMMITTING:
            await queue_client.cancel_by_metadata(
                metadata={"file_id": source_id, "operation_type": OP_IMPORT_COMMIT},
                queue=QUEUE_OPERATIONS,
            )

        # Transition to the appropriate status via the adapter
        # state-machine. ``abort_processing`` clears the step-progress
        # fields so the UI stops showing the last in-flight label
        # (e.g. "Analyzing chunk 1/5") once the job has been stopped.
        error_message = _ABORT_TRANSITIONS.get(current_status)
        adapter.abort_processing(
            source_id,
            error_stage=_ABORT_STAGE_MAP[current_status].value,
            error_message=error_message,
        )

    # ================================
    # Data Access (entities, relationships, llm-metrics)
    # ================================

    def get_entities(
        self,
        source_id: str,
        page: int = 1,
        per_page: int | None = None,
        sort_by: str = "default",
        sort_order: str = "desc",
    ) -> dict[str, Any]:
        """Get paginated entities for a source with quality scores.

        Reads from the per-source ``source_entities`` table (migration
        0042) instead of slicing an in-memory JSON blob — sort modes
        backed by an index (default / confidence / name / type) run as
        indexed SQL. ``quality`` sort requires the full set because the
        score is computed from multiple entity fields plus source-level
        context.

        Args:
            source_id: Source file ID.
            page: Page number (1-indexed).
            per_page: Items per page.
            sort_by: Sort field (default, quality, confidence, name, type).
            sort_order: Sort direction (asc, desc).

        Returns:
            Dict with entities list and pagination info.

        Raises:
            ValueError: If source not found.

        """
        effective_per_page = (
            per_page if per_page is not None else self.settings.pagination.default_page_size
        )
        effective_per_page = max(effective_per_page, 1)
        page = max(page, 1)
        adapter = self.storage_adapter

        source_metadata = adapter.get_source_extraction_metadata(source_id, self.database_name)
        if source_metadata is None:
            msg = f"Source {source_id} not found"
            raise ValueError(msg)

        if sort_by == "quality":
            # Quality scoring needs the full entity set up-front so the
            # in-Python sort can rank by ``quality_score`` before
            # paginating; keep the legacy semantics intact.
            all_entities = adapter.list_source_entities(source_id, self.database_name)
            total = len(all_entities)
            attach_quality_scores(all_entities, source_metadata, self.database_name)
            reverse = sort_order != "asc"
            all_entities.sort(key=lambda e: e.get("quality_score", 0), reverse=reverse)
            start_idx = (page - 1) * effective_per_page
            end_idx = start_idx + effective_per_page
            page_entities = all_entities[start_idx:end_idx]
        else:
            slice_result = adapter.get_source_entities_page(
                source_id,
                self.database_name,
                page=page,
                per_page=effective_per_page,
                sort_by=sort_by,
                sort_order=sort_order,
            )
            page_entities = slice_result["entities"]
            total = slice_result["total"]
            attach_quality_scores(page_entities, source_metadata, self.database_name)

        total_pages = (total + effective_per_page - 1) // effective_per_page if total > 0 else 1

        return {
            "entities": page_entities,
            "pagination": {
                "page": page,
                "page_size": effective_per_page,
                "total": total,
                "total_pages": total_pages,
                "has_next": page < total_pages,
                "has_prev": page > 1,
            },
        }

    def get_relationships(
        self,
        source_id: str,
        page: int = 1,
        per_page: int | None = None,
    ) -> dict[str, Any]:
        """Get paginated relationships for a source.

        Reads from the per-source ``source_relationships`` table (migration
        0042); the from/to entity names come from the adapter's JOIN
        against ``source_entities``.

        Args:
            source_id: Source file ID.
            page: Page number (1-indexed).
            per_page: Items per page (default from settings).

        Returns:
            Dict with relationships list and pagination info.

        Raises:
            ValueError: If source not found.

        """
        effective_per_page = (
            per_page if per_page is not None else self.settings.pagination.default_page_size
        )
        effective_per_page = max(effective_per_page, 1)
        page = max(page, 1)
        adapter = self.storage_adapter

        source_metadata = adapter.get_source_extraction_metadata(source_id, self.database_name)
        if source_metadata is None:
            msg = f"Source {source_id} not found"
            raise ValueError(msg)

        slice_result = adapter.get_source_relationships_page(
            source_id,
            self.database_name,
            page=page,
            per_page=effective_per_page,
        )
        page_relationships = slice_result["relationships"]
        total = slice_result["total"]
        total_pages = (total + effective_per_page - 1) // effective_per_page if total > 0 else 1

        return {
            "relationships": page_relationships,
            "pagination": {
                "page": page,
                "page_size": effective_per_page,
                "total": total,
                "total_pages": total_pages,
                "has_next": page < total_pages,
                "has_prev": page > 1,
            },
        }

    def get_llm_metrics(self, source_id: str) -> dict[str, Any]:
        """Get LLM metrics summary for a source.

        Args:
            source_id: Source file ID.

        Returns:
            Dict with summary metrics and has_metrics flag.

        Raises:
            ValueError: If source not found.

        """
        adapter = self.storage_adapter

        file_info = adapter.get_file(source_id, self.database_name)
        if not file_info:
            msg = f"Source {source_id} not found"
            raise ValueError(msg)

        summary = {
            "total_calls": file_info.get("llm_total_calls", 0),
            "successful_calls": file_info.get("llm_successful_calls", 0),
            "failed_calls": file_info.get("llm_failed_calls", 0),
            "retry_calls": file_info.get("llm_retry_calls", 0),
            "first_try_successes": file_info.get("llm_first_try_successes", 0),
            "retry_successes": file_info.get("llm_retry_successes", 0),
            "permanent_failures": file_info.get("llm_permanent_failures", 0),
            "total_input_tokens": file_info.get("llm_total_input_tokens", 0),
            "total_output_tokens": file_info.get("llm_total_output_tokens", 0),
            "wasted_tokens": file_info.get("llm_wasted_tokens", 0),
            "avg_call_duration_ms": file_info.get("llm_avg_call_duration_ms"),
            "total_duration_ms": file_info.get("llm_total_duration_ms", 0),
            "estimated_cost_usd": file_info.get("llm_estimated_cost_usd"),
            "error_counts": file_info.get("llm_error_counts") or {},
            "model": file_info.get("llm_model", ""),
        }

        total_calls = summary["total_calls"]
        total_tokens = summary["total_input_tokens"] + summary["total_output_tokens"]
        wasted_tokens = summary["wasted_tokens"]

        summary["success_rate"] = (
            summary["successful_calls"] / total_calls if total_calls > 0 else 0.0
        )
        summary["retry_rate"] = summary["retry_calls"] / total_calls if total_calls > 0 else 0.0
        summary["waste_percentage"] = wasted_tokens / total_tokens if total_tokens > 0 else 0.0

        return {
            "source_id": source_id,
            "summary": summary,
            "has_metrics": total_calls > 0,
        }

    def list_llm_calls(
        self,
        source_id: str,
        page: int = 1,
        per_page: int | None = None,
        success: bool | None = None,
    ) -> dict[str, Any]:
        """List individual LLM calls for a source with pagination.

        Args:
            source_id: Source file ID.
            page: Page number (1-indexed).
            per_page: Items per page (default from settings).
            success: Filter by success status.

        Returns:
            Dict with calls list and pagination info.

        Raises:
            ValueError: If source not found.

        """
        effective_per_page = (
            per_page if per_page is not None else self.settings.pagination.default_page_size
        )
        adapter = self.storage_adapter

        file_info = adapter.get_file(source_id, self.database_name)
        if not file_info:
            msg = f"Source {source_id} not found"
            raise ValueError(msg)

        total = adapter.count_llm_call_metrics(
            database_name=self.database_name,
            source_id=source_id,
            success=success,
        )

        total_pages = (total + effective_per_page - 1) // effective_per_page if total > 0 else 1
        offset = (page - 1) * effective_per_page

        calls = adapter.list_llm_call_metrics(
            database_name=self.database_name,
            source_id=source_id,
            success=success,
            limit=effective_per_page,
            offset=offset,
        )

        return {
            "calls": calls,
            "pagination": {
                "page": page,
                "page_size": effective_per_page,
                "total": total,
                "total_pages": total_pages,
                "has_next": page < total_pages,
                "has_prev": page > 1,
            },
        }

    # ================================
    # Extraction Task Operations
    # ================================

    def get_extraction_tasks(
        self,
        source_id: str,
        page: int = 1,
        page_size: int | None = None,
        include_content: bool = False,
    ) -> dict[str, Any]:
        """Get extraction tasks (LLM processing groups) for a source.

        Args:
            source_id: Source file ID.
            page: Page number (1-indexed).
            page_size: Items per page (default from settings).
            include_content: Include full input_text and llm_response_json.

        Returns:
            Dict with tasks list and pagination info.

        """
        effective_page_size = (
            page_size
            if page_size is not None
            else self.settings.pagination.extraction_tasks_page_size
        )
        tasks, total = self.storage_adapter.get_extraction_tasks_for_source(
            source_id=source_id,
            database_name=self.database_name,
            page=page,
            per_page=effective_page_size,
            include_text_content=include_content,
        )
        return {
            "tasks": tasks,
            "total": total,
            "page": page,
            "page_size": effective_page_size,
        }

    def get_extraction_task(self, task_id: str) -> dict[str, Any] | None:
        """Get a single extraction task with full details.

        Args:
            task_id: Extraction task ID.

        Returns:
            Task dict with full details, or None if not found.

        """
        return self.storage_adapter.get_extraction_task_detail(task_id)

    def get_extraction_task_stats(self, source_id: str) -> dict[str, Any] | None:
        """Get aggregate statistics for extraction tasks.

        Args:
            source_id: Source file ID.

        Returns:
            Stats dict with min/avg/max metrics, or None if no statistics available.

        """
        stats = self.storage_adapter.get_extraction_task_stats(
            source_id=source_id,
            database_name=self.database_name,
        )
        if stats is None:
            return None

        # Compute aggregate filtering stats from per-chunk filtering logs
        filtering_agg = self._aggregate_filtering_stats(source_id)
        if filtering_agg:
            stats.update(filtering_agg)
        return stats

    def _aggregate_filtering_stats(self, source_id: str) -> dict[str, Any]:
        """Compute aggregate filtering statistics across all chunk tasks.

        Args:
            source_id: Source file ID.

        Returns:
            Dict with filtering aggregate fields.

        """
        tasks = self.storage_adapter.get_extraction_tasks_filtering_logs(
            source_id=source_id,
            database_name=self.database_name,
        )
        if not tasks:
            return {}

        total_entity_filtered = 0
        total_rel_filtered = 0
        stage_counts: dict[str, dict[str, int]] = {}

        for task in tasks:
            log = task.get("filtering_log")
            if not log or not isinstance(log, dict):
                continue
            for stage in log.get("stages", []):
                stage_name = stage.get("stage", "unknown")
                removed = stage.get("removed_count", 0)
                if stage_name not in stage_counts:
                    stage_counts[stage_name] = {"removed": 0, "chunk_count": 0}
                stage_counts[stage_name]["removed"] += removed
                stage_counts[stage_name]["chunk_count"] += 1

                # Classify by stage name prefix
                if (
                    "entity" in stage_name
                    or "type_rescue" in stage_name
                    or "implausible" in stage_name
                ):
                    total_entity_filtered += removed
                else:
                    total_rel_filtered += removed

        summary = [
            {"stage": name, "total_removed": data["removed"], "chunk_count": data["chunk_count"]}
            for name, data in sorted(stage_counts.items())
        ]

        return {
            "total_entities_filtered": total_entity_filtered,
            "total_relationships_filtered": total_rel_filtered,
            "filtering_stage_summary": summary if summary else None,
        }

    def get_cross_chunk_filtering_log(self, source_id: str) -> dict[str, Any] | None:
        """Get the cross-chunk deduplication filtering log for a source.

        Returns the persisted ``sources.cross_chunk_filtering_log`` JSON
        column (migration 0042) — previously lived inside
        ``extraction_results.metadata.filtering_log``.

        Args:
            source_id: Source file ID.

        Returns:
            Filtering log dict, or None when not set.

        """
        metadata = self.storage_adapter.get_source_extraction_metadata(
            source_id, self.database_name
        )
        if not metadata:
            return None
        filtering_log = metadata.get("cross_chunk_filtering_log")
        if not isinstance(filtering_log, dict):
            return None
        return filtering_log

    def list_recovery_events(
        self,
        source_id: str,
        *,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Return the recovery audit trail for a source.

        Surfaces in the source detail page's recovery panel so operators
        can diagnose the "auto-recovered N times" warning without
        grepping logs. Newest event first.

        Args:
            source_id: Source file ID.
            limit: Maximum events to return. Default 50 covers the
                10-attempt exhaustion cap with margin.

        Returns:
            List of event dicts ordered ``attempt_at`` desc.
        """
        return self.storage_adapter.list_recovery_events(
            source_id=source_id,
            database_name=self.database_name,
            limit=limit,
        )

    def get_extraction_tasks_for_charts(self, source_id: str) -> list[dict[str, Any]]:
        """Get all extraction tasks with minimal fields for chart rendering.

        Args:
            source_id: Source file ID.

        Returns:
            List of task dicts with chart-relevant fields only.

        """
        return self.storage_adapter.get_extraction_tasks_for_charts(
            source_id=source_id,
            database_name=self.database_name,
        )

    # ================================
    # Manual Retry
    # ================================

    async def retry_source(self, source_id: str) -> SourceResponse:
        """Implement POST /sources/{id}/retry.

        Resets an errored source to the appropriate pre-failure state
        (derived from error_stage) and dispatches the next queue task so
        the source resumes processing immediately.

        Args:
            source_id: Source file ID to retry.

        Returns:
            SourceResponse for the updated source.

        Raises:
            NotFoundError: If the source does not exist (-> HTTP 404).
            ConflictError: If the source is not currently in the ``error``
                state (-> HTTP 409).

        """
        from chaoscypher_core.exceptions import ConflictError, NotFoundError
        from chaoscypher_cortex.features.sources.models import SourceResponse

        source = self.engine_service.get_source(source_id)
        if source is None:
            raise NotFoundError("Source", source_id)

        if source.get("status") != SourceStatus.ERROR:
            raise ConflictError(
                "Retry is only supported for sources in the error state.",
                details={"code": "SOURCE_NOT_IN_ERROR_STATE"},
            )

        if source.get("is_paused"):
            raise ConflictError(
                "Source is paused; un-pause before retrying.",
                details={"code": "SOURCE_PAUSED"},
            )
        state = self.storage_adapter.get_system_state()
        if state and state.get("processing_paused"):
            raise ConflictError(
                "System processing is paused; un-pause before retrying.",
                details={"code": "SYSTEM_PAUSED"},
            )

        # Determine resume-from status based on error_stage.
        # Decode via SourceErrorStage for type-safety — guards against future
        # enum/string drift between the abort write-site and this read-site.
        # Audit fix #C2 (combined with _ABORT_STAGE_MAP translation in abort_processing).
        # Audit decision #M: when exhausted, consult last_failed_stage so we
        # resume at the correct pre-exhaustion stage rather than PENDING.
        raw_stage = source.get("error_stage") or ""
        if raw_stage == SourceErrorStage.RECOVERY_EXHAUSTED.value:
            raw_stage = source.get("last_failed_stage") or ""

        try:
            error_stage = SourceErrorStage(raw_stage)
        except ValueError:
            error_stage = None  # unknown / legacy value → fall through to PENDING

        if error_stage is SourceErrorStage.COMMIT:
            new_status = SourceStatus.EXTRACTED
        elif error_stage is SourceErrorStage.EXTRACTION:
            new_status = SourceStatus.INDEXED
        else:
            # indexing, url_fetch, unknown, or no prior stage → full restart
            new_status = SourceStatus.PENDING

        # Pre-fetch the commit payload we'll need for dispatch BEFORE we
        # reset the row. If the fetch fails (DB lock, JSON deserialize,
        # missing row) the source stays in ERROR — operator can retry.
        # Audit fix #H8. The commit payload is the canonical store for
        # commit input since extraction finalizer's ``_queue_commit_phase``
        # writes it via ``set_source_commit_payload`` — per-source entity
        # / relationship tables back-stop the rebuild path used by the
        # recovery service.
        prefetched: dict[str, Any] = {}
        if new_status == SourceStatus.EXTRACTED:
            payload = self.storage_adapter.get_source_commit_payload(source_id, self.database_name)
            prefetched["commit_payload"] = payload or {}

        # Reset source state in storage.
        # Audit fix #F44: when the retry transitions back to a pre-commit
        # stage (PENDING or INDEXED), clear the stale ``commit_payload`` so
        # the next commit can't pick up the previous extraction's data
        # ahead of the freshly-extracted payload. The commit-only retry
        # (target = EXTRACTED) preserves the payload — that *is* the data
        # we want to retry.
        clear_payload = new_status in (
            SourceStatus.PENDING,
            SourceStatus.INDEXED,
        )
        self.storage_adapter.reset_for_retry(
            source_id=source_id,
            database_name=self.database_name,
            new_status=new_status,
            clear_commit_payload=clear_payload,
        )

        # Dispatch appropriate queue task (with prefetched data when applicable)
        await self._dispatch_retry_task(
            source_id=source_id,
            source=source,
            new_status=new_status,
            prefetched=prefetched,
        )

        # Emit event for observability
        event_bus.emit(
            "source_retry_requested",
            action=f"Manual retry: {source_id} → {new_status}",
            source="user",
            details={
                "source_id": source_id,
                "prior_error_stage": raw_stage,
                "new_status": new_status,
            },
            database_name=self.database_name,
        )

        logger.info(
            "source_retry_requested",
            source_id=source_id,
            database_name=self.database_name,
            prior_error_stage=raw_stage,
            new_status=new_status,
        )

        # Return updated source
        updated = self.engine_service.get_source(source_id)
        return SourceResponse(**updated) if updated else SourceResponse(**source)

    async def reextract_source(self, source_id: str) -> SourceResponse:
        """Implement POST /sources/{id}/re-extract.

        Distinct from ``retry_source``: Re-extract throws away any cached
        extraction work (``commit_payload``, ``extraction_results``) and
        re-runs the LLM extraction from indexed chunks. This is the
        "expensive" action — it spends LLM tokens.

        Allowed transitions:
          - COMMITTED → run ``force_re_extract`` (deletes the source's
            graph artifacts atomically with the row reset, then dispatches
            the analysis task).
          - ERROR (after any post-INDEXING stage) → reset row to INDEXED,
            clear commit_payload, dispatch.
          - INDEXED, EXTRACTED, EXTRACTING, MCP_EXTRACTING, COMMITTING →
            forcibly reset to INDEXED, clear commit_payload, clear active
            job pointer, dispatch. (Cancellation of an actively-running
            extraction job row is F53 — for now we only clear the pointer;
            the handler will discover its slot has been reassigned and
            no-op out.)
          - PENDING / INDEXING → reject; the source has no extraction
            artifacts to re-run yet.

        Args:
            source_id: Source file ID to re-extract.

        Returns:
            SourceResponse for the updated source.

        Raises:
            NotFoundError: Source does not exist.
            ValidationError: Source status is not eligible for re-extract.
            ConflictError: Source is paused or system processing is paused.
            OperationError: graph_repository missing for a COMMITTED source,
                or queue dispatch failed.
            ExternalServiceError: No LLM provider configured.
        """
        from chaoscypher_core.exceptions import (
            ConflictError,
            ExternalServiceError,
            NotFoundError,
            OperationError,
            ValidationError,
        )
        from chaoscypher_cortex.features.sources.models import SourceResponse

        source = self.engine_service.get_source(source_id)
        if source is None:
            raise NotFoundError("Source", source_id)

        source_status = source.get("status", "")
        # Re-extract is only meaningful once indexing has produced chunks.
        # Reject anything before INDEXED — there is no extraction artifact
        # to discard yet, and a normal triggerExtraction is the right path.
        ineligible_statuses = {SourceStatus.PENDING, SourceStatus.INDEXING}
        if source_status in ineligible_statuses:
            msg = (
                "Cannot re-extract a source that hasn't been indexed yet "
                f"(status={source_status!r})."
            )
            raise ValidationError(
                msg,
                details={"code": "SOURCE_NOT_INDEXED"},
            )

        if source.get("is_paused"):
            raise ConflictError(
                "Source is paused; un-pause before re-extracting.",
                details={"code": "SOURCE_PAUSED"},
            )
        state = self.storage_adapter.get_system_state()
        if state and state.get("processing_paused"):
            raise ConflictError(
                "System processing is paused; un-pause before re-extracting.",
                details={"code": "SYSTEM_PAUSED"},
            )

        # Verify an LLM provider is configured before we tear down the
        # current state — saves the user from a re-extract that resets
        # everything and then fails on enqueue.
        try:
            from chaoscypher_core.llm_queue import get_provider_factory

            factory = get_provider_factory()
            factory.get_chat_provider()
        except Exception as exc:
            raise ExternalServiceError(
                "llm",
                reason="No LLM provider configured. Use an MCP client to extract.",
            ) from exc

        # Branch on current status to choose the right reset primitive.
        if source_status == SourceStatus.COMMITTED:
            if self.graph_repository is None:
                raise OperationError(
                    "graph_repository required for re-extract on a committed source",
                    operation="re_extract",
                )
            # force_re_extract is the atomic graph-delete + row-reset helper.
            # It now clears commit_payload too (audit fix #F44), so the new
            # extraction starts from a fully clean slate.
            force_re_extract(
                source_id=source_id,
                database_name=self.database_name,
                storage_adapter=self.storage_adapter,
                graph_repository=self.graph_repository,
            )
        elif source_status == SourceStatus.ERROR:
            # Errored source: use reset_for_retry which clears the error
            # bookkeeping and (with clear_commit_payload=True) the stale
            # payload in one shot. Forces target = INDEXED regardless of
            # which stage failed.
            self.storage_adapter.reset_for_retry(
                source_id=source_id,
                database_name=self.database_name,
                new_status=SourceStatus.INDEXED,
                clear_commit_payload=True,
            )
        else:
            # INDEXED, EXTRACTED, EXTRACTING, MCP_EXTRACTING, COMMITTING:
            # reset_for_retry's WHERE status='error' guard rejects these,
            # so we route through the dedicated state-machine method
            # introduced in Phase 5 Task E (see
            # ``SourceIndexingMixin.reset_to_indexed_for_re_extract``).
            # F53 will add proper ChunkExtractionJob row cancellation here;
            # for now the method only NULLs current_extraction_job_id so
            # any running handler discovers its slot has been reassigned
            # and exits on its next checkpoint. The
            # ``clear_source_commit_payload`` call nulls the heavy column
            # without rewriting the row twice.
            self.storage_adapter.reset_to_indexed_for_re_extract(source_id)
            self.storage_adapter.clear_source_commit_payload(source_id, self.database_name)
            # Phase 1 (2026-05-08): the COMMITTED-state path runs through
            # reset_for_re_extraction, which already calls reset_quality_counters.
            # The INDEXED / EXTRACTED / EXTRACTING / MCP_EXTRACTING / COMMITTING
            # cases skipped the helper, so prior-run quality counters survived a
            # re-extract. Bringing this branch to parity.
            #
            # Out of scope for this fix: reset_for_retry (ERROR path) does not call
            # reset_quality_counters either; that's a separate audit gap tracked in
            # the Phase 1 design doc.
            from chaoscypher_core.services.quality.counters import (
                reset_quality_counters,
            )

            reset_quality_counters(self.storage_adapter, source_id, self.database_name)

        # Build the file_info for OP_IMPORT_ANALYSIS — same shape as the
        # commit-only retry path so the worker sees a consistent payload.
        forced_domain_value = source.get("forced_domain")
        if not forced_domain_value and not source.get("extraction_domain_auto", True):
            forced_domain_value = source.get("extraction_domain")

        analysis_depth = source.get("extraction_depth") or "full"
        file_info: dict[str, Any] = {
            "id": source_id,
            "filepath": source.get("filepath"),
            "file_type": source.get("file_type"),
            "filename": source.get("filename"),
            "auto_analyze": True,
            "analysis_depth": analysis_depth,
            "generate_embeddings": True,
            "forced_domain": forced_domain_value,
            "content_filtering": True,
        }

        # Non-atomic by design: state teardown above commits before this
        # dispatch. If queueing fails the source is left in INDEXED with
        # commit_payload=None and no in-flight analysis task. The user
        # can re-trigger via Re-extract; the adapter operations are
        # idempotent. We accept this rather than a 2-phase commit because
        # the queue layer is not transactional with the DB.
        try:
            await queue_utils.queue_import_analysis(
                file_id=source_id,
                file_info=file_info,
                analysis_depth=analysis_depth,
                database_name=self.database_name,
                generate_embeddings=True,
                priority=self.settings.priorities.background,
                extra_metadata={"triggered_by": "manual_reextract"},
            )
        except Exception as exc:
            logger.exception(
                "manual_reextract_queue_failed",
                source_id=source_id,
                error_type=type(exc).__name__,
            )
            raise OperationError(
                "Failed to queue re-extraction task",
                operation="queue_reextract",
            ) from exc

        event_bus.emit(
            "source_reextract_requested",
            action=f"Manual re-extract: {source_id} (was {source_status})",
            source="user",
            details={
                "source_id": source_id,
                "prior_status": source_status,
            },
            database_name=self.database_name,
        )

        logger.info(
            "source_reextract_requested",
            source_id=source_id,
            database_name=self.database_name,
            prior_status=source_status,
        )

        # Return updated source
        updated = self.engine_service.get_source(source_id)
        return SourceResponse(**updated) if updated else SourceResponse(**source)

    async def _dispatch_retry_task(
        self,
        *,
        source_id: str,
        source: dict[str, Any],
        new_status: str,
        prefetched: dict[str, Any] | None = None,
    ) -> None:
        """Dispatch the appropriate queue task for a manual retry.

        Routing table:
        - new_status=pending  → INDEX_DOCUMENT (re-index from scratch)
        - new_status=indexed  → IMPORT_ANALYSIS (re-extract entities)
        - new_status=extracted → IMPORT_COMMIT (re-commit stored results)

        Args:
            source_id: Source file ID.
            source: Source dict from the engine service (pre-reset snapshot).
            new_status: Target status after reset.
            prefetched: Heavy-column data fetched upstream in ``retry_source``
                before the row was reset. Avoids a second DB round-trip and
                ensures the fetch failure leaves the row in ERROR (audit fix #H8).

        """
        # Prefer forced_domain (set on the source row at upload time per fix #1).
        # Fall back to extraction_domain when extraction has run and recorded a
        # non-auto choice, preserving compat with rows persisted before the
        # upload-time persistence fix.
        forced_domain_value = source.get("forced_domain")
        if not forced_domain_value and not source.get("extraction_domain_auto", True):
            forced_domain_value = source.get("extraction_domain")

        file_info: dict[str, Any] = {
            "id": source_id,
            "filepath": source.get("filepath"),
            "file_type": source.get("file_type"),
            "filename": source.get("filename"),
            "auto_analyze": True,
            "analysis_depth": source.get("extraction_depth") or "full",
            "generate_embeddings": True,
            "forced_domain": forced_domain_value,
            "content_filtering": True,
        }

        extra_metadata: dict[str, Any] = {"triggered_by": "manual_retry"}

        if new_status == SourceStatus.PENDING:
            await queue_utils.queue_import_indexing(
                file_id=source_id,
                file_info=file_info,
                database_name=self.database_name,
                priority=self.settings.priorities.background,
                extra_metadata=extra_metadata,
            )
        elif new_status == SourceStatus.INDEXED:
            await queue_utils.queue_import_analysis(
                file_id=source_id,
                file_info=file_info,
                analysis_depth=file_info["analysis_depth"],
                database_name=self.database_name,
                generate_embeddings=True,
                priority=self.settings.priorities.background,
                extra_metadata=extra_metadata,
            )
        else:
            # new_status == extracted → re-commit the stored payload.
            # The commit payload was pre-fetched upstream in retry_source
            # before the row was reset (audit fix #H8). Using the
            # prefetched value avoids a second DB round-trip and,
            # critically, ensures that any fetch failure leaves the
            # source in ERROR rather than in a reset-but-unqueued limbo
            # state. Per-source entity / relationship rows back-stop
            # the rebuild path used by the recovery service when the
            # commit payload is missing.
            payload: dict[str, Any] = (prefetched or {}).get("commit_payload") or {}

            commit_data: dict[str, Any] = {
                "entities": payload.get("entities", []),
                "relationships": payload.get("relationships", []),
                "suggested_templates": payload.get("suggested_templates", []),
                "suggested_edge_templates": payload.get("suggested_edge_templates", []),
                "inverse_relationships": payload.get("inverse_relationships", {}),
                "create_templates": True,
                "auto_enable": True,
            }
            await queue_utils.queue_import_commit(
                file_id=source_id,
                commit_data=commit_data,
                file_info=file_info,
                adapter=self.storage_adapter,
                database_name=self.database_name,
                priority=self.settings.priorities.background,
                extra_metadata=extra_metadata,
            )

    def get_source_templates(
        self,
        source_id: str,
        template_type: str | None = None,
        page: int = 1,
        per_page: int = 50,
    ) -> dict[str, Any]:
        """Get paginated templates for a source.

        Args:
            source_id: Source file ID.
            template_type: Filter by type (node/edge).
            page: Page number (1-indexed).
            per_page: Items per page.

        Returns:
            Dict with templates list and pagination info.

        Raises:
            ValueError: If source not found.

        """
        # Verify file exists
        file_info = self.storage_adapter.get_file(source_id, self.database_name)
        if not file_info:
            msg = f"Source {source_id} not found"
            raise ValueError(msg)

        if not self.graph_repository:
            msg = "graph_repository is required for template retrieval"
            raise RuntimeError(msg)

        from chaoscypher_core.services.graph.management.template import TemplateService

        template_service = TemplateService(graph_repository=self.graph_repository)
        result = template_service.list_templates(
            template_type=template_type,
            page=page,
            page_size=per_page,
            source_id=source_id,
        )

        return {
            "templates": result["data"],
            "pagination": {
                "page": result["pagination"]["page"],
                "page_size": result["pagination"]["page_size"],
                "total": result["pagination"]["total"],
                "total_pages": result["pagination"]["total_pages"],
                "has_next": result["pagination"]["has_next"],
                "has_prev": result["pagination"]["has_prev"],
            },
        }

    # ================================
    # Admin / Maintenance Operations
    # ================================

    def cleanup_orphan_tasks(self) -> dict[str, int]:
        """Trigger an immediate orphan chunk task cleanup pass.

        Reads retention_days from SourceRecoverySettings, converts to
        seconds, and calls adapter.cleanup_orphaned_chunk_tasks. Returns
        deleted count and retention days for the API response.

        Returns:
            Dict with deleted_count and retention_days keys.

        """
        retention_days = self.settings.source_recovery.orphan_task_retention_days
        older_than_seconds = retention_days * policy.SECONDS_PER_DAY

        deleted_count = self.storage_adapter.cleanup_orphaned_chunk_tasks(
            older_than_seconds=older_than_seconds,
        )

        logger.info(
            "admin_orphan_task_cleanup_triggered",
            deleted_count=deleted_count,
            retention_days=retention_days,
        )

        return {
            "deleted_count": deleted_count,
            "retention_days": retention_days,
        }
