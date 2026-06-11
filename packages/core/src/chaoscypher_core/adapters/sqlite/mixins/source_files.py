# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Source File CRUD Mixin for SqliteAdapter.

Handles source file upload, retrieval, listing, and updates.
Part of the unified SourceStorageProtocol implementation.

Deletion is handled by SourcesMixin.delete_source() which provides
full cascade deletion for both SourceStorageProtocol and SourcesProtocol.

Related mixins (split for maintainability):
- source_files_indexing.py: Status lifecycle, embeddings, extraction gating
- source_files_extraction_jobs.py: Extraction job CRUD and status
- source_files_chunk_tasks.py: Chunk task CRUD, analytics, recovery
"""

from datetime import UTC, datetime
from datetime import datetime as _dt
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import Boolean, DateTime, Integer, text, update
from sqlalchemy.orm import load_only
from sqlmodel import select

from chaoscypher_core.adapters.sqlite.mixin_base import SqliteMixinBase
from chaoscypher_core.adapters.sqlite.models import SourceRow
from chaoscypher_core.models import SourceErrorStage, SourceStatus
from chaoscypher_core.services.quality.counters import QualityCounter


logger = structlog.get_logger(__name__)


def _validate_field_type(model_cls: type, field: str, value: Any) -> None:
    """Reject obvious type mismatches before SQLAlchemy auto-coerces or explodes.

    Only catches the common drift: string-where-datetime-expected,
    string-where-int-expected, etc. Not exhaustive — SQLAlchemy will still
    flush-fail on truly invalid values. Goal is to raise a clear
    application-level error instead of letting the session enter
    PendingRollbackError. Audit fix #M7.
    """
    if value is None:
        return
    column = model_cls.__table__.columns.get(field)
    if column is None:
        return  # not a real column (relationship, hybrid, etc.)
    sa_type = column.type
    if isinstance(sa_type, DateTime) and not isinstance(value, _dt):
        msg = f"update_file: field {field!r} expects datetime, got {type(value).__name__}"
        raise ValueError(msg)  # noqa: TRY004 — application contract, not programmer error
    if isinstance(sa_type, Integer) and not isinstance(value, (int, bool)):
        msg = f"update_file: field {field!r} expects int, got {type(value).__name__}"
        raise ValueError(msg)  # noqa: TRY004
    if isinstance(sa_type, Boolean) and not isinstance(value, bool):
        msg = f"update_file: field {field!r} expects bool, got {type(value).__name__}"
        raise ValueError(msg)  # noqa: TRY004


def _source_to_summary_dict(source: SourceRow) -> dict[str, Any]:
    """Project a ``SourceRow`` into the list-view summary dict callers consume.

    Matches the column set loaded by the list query below; do not read any
    attribute that isn't in that ``load_only`` projection or SQLAlchemy will
    lazy-load it and blow the performance budget (see CLAUDE.md "SQLAlchemy
    Query Performance" rules).
    """
    return {
        "id": source.id,
        "database_name": source.database_name,
        "filename": source.filename,
        "file_type": source.file_type,
        "file_size": source.file_size,
        "status": source.status,
        "created_at": source.created_at.isoformat() if source.created_at else None,
        "indexing_complete": source.indexing_complete,
        "extraction_complete": source.extraction_complete,
        "commit_complete": source.commit_complete,
        "chunk_count": source.chunk_count or 0,
        "embedding_model": source.embedding_model,
        "embedding_dimensions": source.embedding_dimensions,
        "entities_count": source.extraction_entities_count or 0,
        "relationships_count": source.extraction_relationships_count or 0,
        "commit_nodes_created": source.commit_nodes_created or 0,
        "commit_edges_created": source.commit_edges_created or 0,
        "commit_templates_created": source.commit_templates_created or 0,
        "error_message": source.error_message,
        "error_stage": source.error_stage,
        "extraction_depth": source.extraction_depth,
        "indexing_duration_seconds": source.get_indexing_duration_seconds(),
        "extraction_duration_seconds": source.get_extraction_duration_seconds(),
        "current_step": source.current_step,
        "total_steps": source.total_steps,
        "step_description": source.step_description,
    }


def _source_to_detail_dict(source: SourceRow) -> dict[str, Any]:
    """Project a ``SourceRow`` (fully loaded) into the detail-view dict.

    Per-source entity / relationship rows live in dedicated tables
    (migration 0042); detail callers fetch them through the paginated
    endpoints. Counts are surfaced from the row's
    ``extraction_entities_count`` / ``extraction_relationships_count``
    counters.
    """
    result: dict[str, Any] = source.model_dump(mode="json")

    result["indexing_duration_seconds"] = source.get_indexing_duration_seconds()
    result["extraction_duration_seconds"] = source.get_extraction_duration_seconds()
    result["commit_duration_seconds"] = source.get_commit_duration_seconds()

    result["entities_count"] = source.extraction_entities_count or 0
    result["relationships_count"] = source.extraction_relationships_count or 0
    result["suggested_templates_count"] = len(result.get("suggested_templates", []))
    result["suggested_edge_templates_count"] = len(result.get("suggested_edge_templates", []))

    result["embedding_summary"] = {
        "document_chunks": {
            "count": source.chunk_count or 0,
            "embeddings": source.chunk_count or 0,
            "model": source.embedding_model,
            "dimensions": source.embedding_dimensions,
        },
        "entities": {
            "count": source.extraction_entities_count or 0,
            "embeddings": source.embeddings_count or 0,
            "embeddings_generated": source.embeddings_generated,
            "model": source.embeddings_model,
            "generated_at": result.get("embeddings_generated_at"),
        },
    }

    return result


class SourceLifecycleMixin(SqliteMixinBase):
    """Mixin providing source file CRUD operations for SQLite storage.

    Implements operations for:
    - Source uploads and file handling
    - Source retrieval (single and list)
    - Source field updates

    Note: Source deletion is handled by SourcesMixin.delete_source().
    This mixin contributes to the unified SourceStorageProtocol.
    See related mixins for lifecycle status, extraction jobs, and chunk tasks.
    """

    def find_by_content_hash(self, database_name: str, content_hash: str) -> dict[str, Any] | None:
        """Find a source by content hash within a database (any status).

        Loads ALL columns (no load_only projection) because the result flows
        through the duplicate-skip API response which needs the full
        SourceResponse shape including created_at/updated_at. The duplicate-
        skip path is exceptional, so the perf cost of loading every column
        is negligible.

        Includes errored sources so re-uploads with skip_duplicates=True
        return the errored sibling instead of creating a fresh orphan
        row + file pair. Callers decide what to do with an errored hit
        (typically: surface to the user with a 'retry' affordance).

        Args:
            database_name: The database to search in.
            content_hash: SHA-256 hex digest of the file content.

        Returns:
            Source dict if found, None otherwise.

        """
        self._ensure_connected()
        # ORDER BY created_at ASC ensures the OLDEST row wins when duplicates
        # exist. Matters when a URL fetch placeholder cleanup fails: the
        # canonical (older) row stays as the duplicate-detection target
        # rather than the orphan placeholder. Without an explicit ORDER BY,
        # SQLite's iteration order is implementation-defined.
        statement = (
            select(SourceRow)
            .where(
                SourceRow.database_name == database_name,
                SourceRow.content_hash == content_hash,
            )
            .order_by(SourceRow.created_at.asc())
        )
        result = self.session.exec(statement)
        source = result.first()
        return self._entity_to_dict(source) if source else None

    @staticmethod
    def _write_to_staging(
        filepath: Path,
        file_content: bytes | None,
        staged_file_path: Path | None,
    ) -> None:
        """Write content to *filepath*, which must already have its parent created.

        Moves *staged_file_path* if provided, otherwise writes *file_content*.
        Raises ``ValueError`` if neither is supplied.

        File-write-before-DB-commit ordering
        ------------------------------------
        ``upload_source`` writes the bytes to disk *before* inserting the
        ``SourceRow``. This is intentional — the row's ``filepath`` column must
        reference a real path so any later resumability check can locate the
        bytes. Writing first also lets us detect ENOSPC / permission errors
        before the DB sees a doomed row.

        The trade-off: if the process crashes after this function returns but
        before ``_maybe_commit()`` succeeds, the staged file is left orphaned
        on disk with no corresponding row. The in-function ``except``-branch in
        ``upload_source`` calls ``_cleanup_staged_file`` for the in-process
        failure case, but a hard kill (SIGKILL, OOM, container crash) can still
        leave a residue.

        Hard-crash residue is cleaned by the periodic
        ``_orphan_files_cleanup_loop`` in the neuron worker, which sweeps
        ``staging_dir/<source_id>/`` directories with no matching
        ``SourceRow.id`` whose mtime is older than
        ``SourceRecoverySettings.orphan_files_retention_days`` (default
        1 day). See ``chaoscypher_core.services.sources.orphan_files``.
        The existing ``OP_CLEANUP_ORPHANS`` operation handles **graph
        orphans only** (edges, nodes, templates with dangling references)
        — separate concern.
        """
        if staged_file_path and staged_file_path.exists():
            import shutil

            shutil.move(str(staged_file_path), str(filepath))
        elif file_content is not None:
            filepath.write_bytes(file_content)
        else:
            msg = "Either file_content or staged_file_path must be provided"
            raise ValueError(msg)

    @staticmethod
    def _cleanup_staged_file(filepath: Path) -> None:
        """Best-effort removal of a staged file and its now-empty parent dir."""
        try:
            filepath.unlink(missing_ok=True)
            if filepath.parent.exists() and not list(filepath.parent.iterdir()):
                filepath.parent.rmdir()
        except OSError as exc:
            logger.warning(
                "source_upload_cleanup_failed",
                filepath=str(filepath),
                error_type=type(exc).__name__,
                error_message=str(exc),
            )

    def upload_source(
        self,
        source_id: str,
        database_name: str,
        filename: str,
        file_content: bytes | None = None,
        staging_dir: str = "",
        extraction_depth: str = "full",
        forced_domain: str | None = None,
        origin_url: str | None = None,
        source_type_override: str | None = None,
        title_override: str | None = None,
        content_hash: str | None = None,
        staged_file_path: Path | None = None,
        file_size: int | None = None,
        # --- Upload-settings persistence (Workstream 1, 2026-05-07) ---
        auto_analyze: bool = True,
        enable_normalization: bool | None = None,
        enable_vision: bool = True,
        content_filtering: bool = True,
        filtering_mode: str = "balanced",
        # Phase 4 (2026-05-08): nullable per-source toggles
        enable_direction_correction: bool | None = None,
        protect_orphans: bool | None = None,
        # Phase 6 (2026-05-08): nullable per-source toggles
        enable_inverse_relationships: bool | None = None,
        max_entity_degree_override: int | None = None,
        # Domain-confirmation gate (2026-05-28)
        confirmation_required: bool = False,
    ) -> dict[str, Any]:
        """Upload file and create source record atomically.

        Single-transaction contract:
        1. Validate the staging path against traversal.
        2. Stage bytes/file at the final filepath.
        3. Insert row with the final filepath.
        4. Commit once.
        On any failure, unlink the staged file (no row exists to roll back).

        Crash window: bytes are durable on disk before the row commits. If the
        process is hard-killed between step 2 and step 4 the file is orphaned —
        see ``_write_to_staging``'s docstring for the rationale and the
        ``OP_CLEANUP_ORPHAN_FILES`` follow-up in `the public issue tracker`.
        ``OP_CLEANUP_ORPHANS`` does not cover this case (it only cleans graph
        items).
        """
        self._ensure_connected()

        if file_size is None:
            if staged_file_path and staged_file_path.exists():
                file_size = staged_file_path.stat().st_size
            elif file_content is not None:
                file_size = len(file_content)
            else:
                file_size = 0

        file_ext = Path(filename).suffix.lstrip(".").lower() if "." in filename else None
        source_type = source_type_override or (file_ext or "file")

        safe_filename = Path(filename).name
        if not safe_filename or safe_filename.startswith("."):
            safe_filename = f"upload_{source_id}"
        filepath = Path(staging_dir) / source_id / safe_filename
        if not filepath.resolve().is_relative_to(Path(staging_dir).resolve()):
            msg = "Path traversal detected in filename or source_id"
            raise ValueError(msg)

        filepath.parent.mkdir(parents=True, exist_ok=True)
        wrote_file = False
        committed = False
        source: SourceRow | None = None
        try:
            self._write_to_staging(filepath, file_content, staged_file_path)
            wrote_file = True

            source = SourceRow(
                id=source_id,
                database_name=database_name,
                filename=filename,
                filepath=str(filepath),
                file_type=file_ext,
                file_size=file_size,
                content_hash=content_hash,
                title=title_override or filename,
                source_type=source_type,
                origin_url=origin_url,
                status=SourceStatus.PENDING,
                extraction_depth=extraction_depth,
                forced_domain=forced_domain,
                # Upload-settings persistence (W1, 2026-05-07): single
                # source of truth lives on the row, so recovery / retry /
                # re-extract preserve user choice.
                auto_analyze=auto_analyze,
                enable_normalization=enable_normalization,
                enable_vision=enable_vision,
                content_filtering=content_filtering,
                filtering_mode=filtering_mode,
                # Phase 4 (2026-05-08): NULL = fall back to cascade default
                enable_direction_correction=enable_direction_correction,
                protect_orphans=protect_orphans,
                # Phase 6 (2026-05-08): NULL = use global default
                enable_inverse_relationships=enable_inverse_relationships,
                max_entity_degree_override=max_entity_degree_override,
                # Domain-confirmation gate (2026-05-28)
                confirmation_required=confirmation_required,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            self.session.add(source)
            self._maybe_commit()
            committed = True
            self.session.refresh(source)

            result = self._entity_to_dict(source)
            assert result is not None
            return result

        except Exception:
            # If we never got past _maybe_commit, the row never reached durable
            # storage and we must clean up the staged file. Once committed=True,
            # the row exists; do not unlink.
            if wrote_file and not committed:
                self._cleanup_staged_file(filepath)
            # Detach any pending/persistent SourceRow from the session so a
            # subsequent query doesn't see a phantom via the identity map.
            # expunge is a no-op for detached objects, so the broader gate is safe.
            if source is not None:
                from sqlalchemy import inspect as sa_inspect

                state = sa_inspect(source)
                if state.persistent or state.pending:
                    self.session.expunge(source)
            raise

    def get_file(self, source_id: str, database_name: str) -> dict[str, Any] | None:
        """Get source processing file by ID and database.

        Uses a narrow ``load_only`` projection so the dict only carries
        lifecycle / metric columns (not per-source entity rows — those
        live in dedicated tables since migration 0042).
        """
        self._ensure_connected()
        entity = self._load_source_entity(source_id, database_name, exclude_large_columns=True)
        return self._entity_to_dict(entity) if entity else None

    def list_source_ids(self, database_name: str) -> set[str]:
        """Return all SourceRow.id values for the given database.

        Used by the orphan-file cleanup loop to diff against staging_dir
        contents. Read-only, single column projection — does not pull any
        of the heavy JSON columns.
        """
        self._ensure_connected()
        statement = select(SourceRow.id).where(SourceRow.database_name == database_name)
        return set(self.session.exec(statement).all())

    def get_source_detail(self, source_id: str, database_name: str) -> dict[str, Any] | None:
        """Return a fully-loaded detail dict for the given source, or ``None``.

        Loads every column on the source row (per-source entity /
        relationship rows live in dedicated tables since migration 0042
        and are paginated separately) and runs the detail projection
        inline; callers receive a ready-to-serialise dict instead of a
        SQLModel entity.
        """
        entity = self._load_source_entity(source_id, database_name, exclude_large_columns=False)
        return _source_to_detail_dict(entity) if entity else None

    def _load_source_entity(
        self, source_id: str, database_name: str, exclude_large_columns: bool = True
    ) -> SourceRow | None:
        """Internal loader: returns the raw SourceRow entity.

        Kept private because the rest of the world should consume the
        dict-shaped public methods (``get_source``, ``get_source_detail``).
        SQLModel entities escape only inside this adapter module.

        Args:
            source_id: The file ID to retrieve
            database_name: The database name
            exclude_large_columns: If True, uses a narrow ``load_only``
                projection skipping JSON columns (``user_metadata``,
                ``cross_chunk_filtering_log``, …). Set to False when the
                caller needs the full row (e.g. detail page).
        """
        self._ensure_connected()
        statement = select(SourceRow).where(
            SourceRow.id == source_id, SourceRow.database_name == database_name
        )

        if exclude_large_columns:
            # Narrow projection: skip JSON columns (user_metadata,
            # cross_chunk_filtering_log) for cheap row reads. Per-source
            # entity / relationship rows live in dedicated tables.
            statement = statement.options(
                load_only(
                    SourceRow.id,
                    SourceRow.database_name,
                    SourceRow.filename,
                    SourceRow.filepath,
                    SourceRow.file_type,
                    SourceRow.file_size,
                    SourceRow.title,
                    SourceRow.status,
                    SourceRow.created_at,
                    SourceRow.indexing_complete,
                    SourceRow.extraction_complete,
                    SourceRow.commit_complete,
                    SourceRow.indexing_started_at,
                    SourceRow.indexing_completed_at,
                    SourceRow.chunk_count,
                    SourceRow.embedding_model,
                    SourceRow.embedding_dimensions,
                    SourceRow.extraction_started_at,
                    SourceRow.extraction_completed_at,
                    SourceRow.extraction_depth,
                    SourceRow.extraction_entities_count,
                    SourceRow.extraction_relationships_count,
                    SourceRow.extraction_domain,
                    SourceRow.extraction_domain_auto,
                    SourceRow.forced_domain,
                    SourceRow.commit_started_at,
                    SourceRow.commit_completed_at,
                    SourceRow.commit_nodes_created,
                    SourceRow.commit_edges_created,
                    SourceRow.commit_templates_created,
                    SourceRow.error_message,
                    SourceRow.error_stage,
                    SourceRow.recovery_attempts,
                    SourceRow.source_document_node_id,
                    SourceRow.analysis_id,
                    SourceRow.current_extraction_job_id,
                    SourceRow.current_step,
                    SourceRow.total_steps,
                    SourceRow.step_description,
                    SourceRow.embeddings_generated,
                    SourceRow.embeddings_count,
                    SourceRow.embeddings_model,
                    SourceRow.embeddings_generated_at,
                    # Cached quality scores (so quality scoring can read them
                    # without triggering a separate load or hitting the
                    # "no extraction data" fallback).
                    SourceRow.cached_quality_grade,
                    SourceRow.cached_quality_label,
                    SourceRow.cached_richness_score,
                    SourceRow.cached_avg_entity_quality,
                    SourceRow.cached_avg_relationship_quality,
                    SourceRow.cached_connectivity_ratio,
                    SourceRow.cached_topology_score,
                    SourceRow.cached_density_ratio,
                    SourceRow.cached_density_score,
                    SourceRow.cached_pollution_penalty,
                    SourceRow.cached_structural_penalty,
                    SourceRow.cached_hub_skew,
                    SourceRow.cached_reciprocal_rate,
                    SourceRow.cached_coverage_score,
                    SourceRow.cached_low_quality_entity_count,
                    SourceRow.cached_low_quality_relationship_count,
                    SourceRow.cached_scores_at,
                    SourceRow.cached_scores_version,
                )
            )

        result = self.session.exec(statement)
        return result.first()

    def list_files(
        self, database_name: str, status: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        """List source processing files for database with optional status filter.

        Narrow ``load_only`` projection skips JSON columns for
        performance. Includes cached quality score fields for efficient
        quality queries.
        """
        self._ensure_connected()
        # Use load_only to exclude large JSON columns but include cached scores
        statement = (
            select(SourceRow)
            .options(
                load_only(
                    # Core identification
                    SourceRow.id,
                    SourceRow.database_name,
                    SourceRow.filename,
                    SourceRow.filepath,
                    SourceRow.file_type,
                    SourceRow.file_size,
                    SourceRow.title,
                    SourceRow.source_type,
                    SourceRow.origin_url,
                    SourceRow.version,
                    SourceRow.parent_id,
                    # Status tracking
                    SourceRow.status,
                    SourceRow.indexing_complete,
                    SourceRow.extraction_complete,
                    SourceRow.commit_complete,
                    SourceRow.enabled,
                    SourceRow.error_message,
                    SourceRow.error_stage,
                    # Indexing stats
                    SourceRow.indexing_started_at,
                    SourceRow.indexing_completed_at,
                    SourceRow.chunk_count,
                    SourceRow.total_content_length,
                    SourceRow.embedding_model,
                    SourceRow.embedding_dimensions,
                    # Extraction stats
                    SourceRow.extraction_started_at,
                    SourceRow.extraction_completed_at,
                    SourceRow.extraction_depth,
                    SourceRow.extraction_entities_count,
                    SourceRow.extraction_relationships_count,
                    SourceRow.extraction_domain,
                    SourceRow.extraction_domain_auto,
                    SourceRow.forced_domain,
                    SourceRow.current_extraction_job_id,
                    SourceRow.extraction_queued_at,
                    # Domain confirmation gate (migration 0049)
                    SourceRow.confirmation_required,
                    SourceRow.extraction_confirmed_at,
                    SourceRow.detection_proposal,
                    # Embeddings
                    SourceRow.embeddings_generated,
                    SourceRow.embeddings_count,
                    SourceRow.embeddings_model,
                    SourceRow.embeddings_generated_at,
                    # Commit stats
                    SourceRow.commit_started_at,
                    SourceRow.commit_completed_at,
                    SourceRow.commit_nodes_created,
                    SourceRow.commit_edges_created,
                    SourceRow.commit_templates_created,
                    SourceRow.source_document_node_id,
                    # Progress tracking
                    SourceRow.current_step,
                    SourceRow.total_steps,
                    SourceRow.step_description,
                    # LLM metrics
                    SourceRow.llm_total_calls,
                    SourceRow.llm_successful_calls,
                    SourceRow.llm_failed_calls,
                    SourceRow.llm_total_input_tokens,
                    SourceRow.llm_total_output_tokens,
                    SourceRow.llm_estimated_cost_usd,
                    SourceRow.llm_model,
                    # Timestamps
                    SourceRow.created_at,
                    SourceRow.updated_at,
                    SourceRow.analysis_id,
                    # Cached quality scores (for efficient quality queries)
                    SourceRow.cached_quality_grade,
                    SourceRow.cached_quality_label,
                    SourceRow.cached_richness_score,
                    SourceRow.cached_avg_entity_quality,
                    SourceRow.cached_avg_relationship_quality,
                    SourceRow.cached_connectivity_ratio,
                    SourceRow.cached_topology_score,
                    SourceRow.cached_density_ratio,
                    SourceRow.cached_density_score,
                    SourceRow.cached_pollution_penalty,
                    SourceRow.cached_low_quality_entity_count,
                    SourceRow.cached_low_quality_relationship_count,
                    SourceRow.cached_scores_at,
                    SourceRow.cached_scores_version,
                )
            )
            .where(SourceRow.database_name == database_name)
        )

        if status:
            statement = statement.where(SourceRow.status == status)

        statement = statement.order_by(SourceRow.created_at.desc()).limit(limit)  # type: ignore[attr-defined]

        results = self.session.exec(statement)
        return self._entities_to_dicts(results.all())

    def list_source_summaries(
        self, database_name: str, status: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Return summary dicts for the source list view.

        Runs the minimal-column projection loader and applies the summary
        projection inline so callers never see SQLModel entities.
        """
        entities = self._list_source_entities(
            database_name=database_name, status=status, limit=limit
        )
        return [_source_to_summary_dict(entity) for entity in entities]

    def _list_source_entities(
        self, database_name: str, status: str | None = None, limit: int = 100
    ) -> list[SourceRow]:
        """Internal loader: returns SourceRow entities with the list-view column projection.

        Only loads columns needed for list view display (~26ms vs 1.1s with
        all cols). Kept private; public callers consume ``list_source_summaries``.
        """
        self._ensure_connected()
        # Load ONLY essential columns for list view - SQLAlchemy ORM overhead
        # scales dramatically with column count (40 cols = 1.1s, 13 cols = 26ms)
        statement = (
            select(SourceRow)
            .options(
                load_only(
                    # Core identification
                    SourceRow.id,
                    SourceRow.database_name,
                    SourceRow.filename,
                    SourceRow.file_type,
                    SourceRow.file_size,
                    # Status tracking
                    SourceRow.status,
                    SourceRow.created_at,
                    SourceRow.indexing_complete,
                    SourceRow.extraction_complete,
                    SourceRow.commit_complete,
                    # Indexing stats for tooltip
                    SourceRow.chunk_count,
                    SourceRow.embedding_model,
                    SourceRow.embedding_dimensions,
                    # Counts for display
                    SourceRow.extraction_entities_count,
                    SourceRow.extraction_relationships_count,
                    # Commit stats for tooltip
                    SourceRow.commit_nodes_created,
                    SourceRow.commit_edges_created,
                    SourceRow.commit_templates_created,
                    # Error info
                    SourceRow.error_message,
                    SourceRow.error_stage,
                    # Analysis depth for display
                    SourceRow.extraction_depth,
                    # Timestamps for duration calculation
                    SourceRow.indexing_started_at,
                    SourceRow.indexing_completed_at,
                    SourceRow.extraction_started_at,
                    SourceRow.extraction_completed_at,
                    # Progress tracking for UI
                    SourceRow.current_step,
                    SourceRow.total_steps,
                    SourceRow.step_description,
                )
            )
            .where(SourceRow.database_name == database_name)
        )

        if status:
            statement = statement.where(SourceRow.status == status)

        statement = statement.order_by(SourceRow.created_at.desc()).limit(limit)  # type: ignore[attr-defined]

        results = self.session.exec(statement)
        return list(results.all())

    def update_file(
        self,
        source_id: str,
        database_name: str,
        updates: dict[str, Any],
    ) -> None:
        """Update source processing file fields, scoped by database.

        Strict contract:
        - Lookup uses (source_id, database_name) — cross-database collisions
          are impossible. Audit fix H1.
        - Missing source raises NotFoundError. Callers can no longer
          silently no-op when the row was deleted out from under them.
          Audit fix H2.
        - Immutable fields (id, database_name, created_at, updated_at) are
          silently skipped — these are managed by the model lifecycle.
        - Unknown fields raise ValueError.
        - Type validation enforced via _validate_field_type. Audit fix M7.

        Raises:
            NotFoundError: When (source_id, database_name) does not exist.
            ValueError: When ``updates`` contains an unknown field name or
                a value whose type is incompatible with the column.
        """
        from chaoscypher_core.exceptions import NotFoundError

        self._ensure_connected()
        statement = select(SourceRow).where(
            SourceRow.id == source_id,
            SourceRow.database_name == database_name,
        )
        source = self.session.exec(statement).first()

        if not source:
            raise NotFoundError("source", source_id)

        immutable_fields = {"id", "database_name", "created_at", "updated_at"}
        for field, value in updates.items():
            if field in immutable_fields:
                continue
            if not hasattr(source, field):
                msg = f"update_file: unknown field {field!r} for source {source_id}"
                raise ValueError(msg)
            _validate_field_type(SourceRow, field, value)
            setattr(source, field, value)

        source.updated_at = datetime.now(UTC)
        self.session.add(source)
        self._maybe_commit()

    # ========================================================================
    # Quality counters (Workstream 2, 2026-05-07)
    # ========================================================================

    # Counter columns that aren't integer counters and therefore can't go
    # through ``increment_source_counter``. They live in ``QualityCounter``
    # for parity with the rest of the observability surface but are written
    # via ``update_source_columns`` — JSON columns set as a whole value
    # rather than atomically incremented.
    _NON_INTEGER_QUALITY_COUNTERS: frozenset[str] = frozenset(
        {
            QualityCounter.LOADER_HTML_DROPPED_TAGS.value,
            QualityCounter.LOADER_PPTX_SHAPES_SKIPPED.value,
        }
    )

    # Allowlist of integer counter columns the atomic increment helper is
    # permitted to touch. Derived from the ``QualityCounter`` enum minus the
    # JSON-shaped exclusions so the enum stays the single source of truth —
    # adding a new counter to the enum is sufficient; no separate allowlist
    # edit required. The allowlist still acts as defense-in-depth at the
    # adapter boundary: ``column not in allowlist`` rejects arbitrary
    # strings before the f-string interpolation in the UPDATE statement.
    # The drift test in
    # ``tests/unit/adapters/sqlite/test_counter_allowlist_matches_enum.py``
    # asserts the relationship and fails CI if a JSON exclusion is added
    # without updating ``_NON_INTEGER_QUALITY_COUNTERS``.
    _COUNTER_COLUMN_ALLOWLIST: frozenset[str] = (
        frozenset(c.value for c in QualityCounter) - _NON_INTEGER_QUALITY_COUNTERS
    )

    def increment_source_counter(
        self,
        *,
        source_id: str,
        database_name: str,
        column: str,
        n: int = 1,
    ) -> None:
        """Atomically add ``n`` to a single counter column.

        Uses a server-side ``COALESCE(col, 0) + :n`` UPDATE so the
        increment is correct even when the column starts NULL and even
        when multiple workers race on the same row. ``column`` is
        validated against ``_COUNTER_COLUMN_ALLOWLIST`` before being
        interpolated into the statement; never accept arbitrary input.

        Counters are monotonic-increase observability — there is no
        legitimate decrement.  Reject ``n < 1`` so a buggy caller cannot
        corrupt the running total.  ``increment_quality_counter`` already
        swallows exceptions and surfaces them as
        ``quality_counter_increment_failed`` log lines, so this fail-loud
        guard is observable without breaking the pipeline.

        Raises:
            ValueError: When ``column`` is not in the allowlist or when
                ``n`` is less than 1.
        """
        if n < 1:
            msg = f"Counter increment must be positive; got n={n}"
            raise ValueError(msg)
        if column not in self._COUNTER_COLUMN_ALLOWLIST:
            msg = f"Counter column not allowlisted: {column}"
            raise ValueError(msg)
        self._ensure_connected()
        # ``column`` is allowlist-checked above, so the f-string
        # interpolation is safe; the user-supplied values bind through
        # the named parameters.
        sql = (
            f"UPDATE sources SET {column} = COALESCE({column}, 0) + :n, "
            "updated_at = :now WHERE id = :sid AND database_name = :db"
        )
        self.session.execute(
            text(sql),
            {
                "n": n,
                "now": datetime.now(UTC),
                "sid": source_id,
                "db": database_name,
            },
        )
        self._maybe_commit()

    def update_source_columns(
        self,
        *,
        source_id: str,
        database_name: str,
        updates: dict[str, Any],
    ) -> None:
        """Bulk-set arbitrary columns on a source row in one statement.

        Used by ``reset_quality_counters`` and ``set_loader_encoding`` to
        write multiple columns atomically. Unlike ``update_file`` this
        helper does NOT enforce the immutable-field guard or the
        type-validation pass - callers are trusted to pass values that
        match the column types. Reserved for the quality-counter
        helpers; broader updates should still go through ``update_file``.

        Unknown column names raise ``ValueError`` (mirroring
        ``update_file``'s ``hasattr`` guard) so a typo'd reset column —
        e.g. ``cleaner_chars_remoevd`` — fails loudly instead of writing
        a non-persisted attribute and silently skipping the reset.

        Raises:
            NotFoundError: When (source_id, database_name) does not
                exist.
            ValueError: When ``updates`` contains a column name that is
                not a real ``SourceRow`` field.
        """
        from chaoscypher_core.exceptions import NotFoundError

        self._ensure_connected()
        statement = select(SourceRow).where(
            SourceRow.id == source_id,
            SourceRow.database_name == database_name,
        )
        source = self.session.exec(statement).one_or_none()
        if source is None:
            raise NotFoundError("source", source_id)
        for col, value in updates.items():
            if not hasattr(source, col):
                msg = f"update_source_columns: unknown field {col!r} for source {source_id}"
                raise ValueError(msg)
            setattr(source, col, value)
        source.updated_at = datetime.now(UTC)
        self.session.add(source)
        self._maybe_commit()

    # ========================================================================
    # User-Controlled Pause / Resume
    # ========================================================================

    def set_source_paused(
        self,
        *,
        source_id: str,
        database_name: str,
        is_paused: bool,
        reason: str | None = None,
    ) -> None:
        """Set or clear the pause flag on a single source.

        When `is_paused` is True, records `paused_at` (now, UTC) and
        `paused_reason`. When False, clears both. Used by the pause
        feature and by service-layer bulk operations.
        """
        self._ensure_connected()

        values: dict[str, Any] = {"is_paused": is_paused}
        if is_paused:
            values["paused_at"] = datetime.now(UTC)
            values["paused_reason"] = reason
        else:
            values["paused_at"] = None
            values["paused_reason"] = None

        stmt = (
            update(SourceRow)
            .where(
                SourceRow.id == source_id,
                SourceRow.database_name == database_name,
            )
            .values(**values)
        )
        self.session.execute(stmt)
        self._maybe_commit()

    def create_url_placeholder(
        self,
        *,
        source_id: str,
        database_name: str,
        url: str,
    ) -> None:
        """Create a placeholder row for a URL import that is in flight.

        Visible in the UI immediately so users see the fetch is pending.
        The real row (with actual metadata) replaces this placeholder once
        the fetch succeeds; if the fetch fails, ``fail_url_fetch`` promotes
        the row to ERROR with ``error_stage='url_fetch'``.

        Args:
            source_id: Pre-generated ID for the placeholder row.
            database_name: Target database.
            url: The URL being fetched (used as filename and title).

        """
        self._ensure_connected()
        row = SourceRow(
            id=source_id,
            database_name=database_name,
            filename=url,
            filepath="",
            file_type=None,
            file_size=0,
            title=url,
            source_type="webpage",
            origin_url=url,
            status=SourceStatus.PENDING,
            step_description="Fetching URL",
            current_step=1,
            total_steps=2,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        self.session.add(row)
        self._maybe_commit()

    def fail_url_fetch(self, source_id: str, error: str, database_name: str) -> None:
        """Mark a URL placeholder row as failed at fetch stage.

        Delegates to SourceIndexingMixin._apply_failure (mixin sibling on
        the same SqliteAdapter) so the failure-write is centralized.

        Scopes the lookup by ``database_name`` so the wrong tenant's row
        cannot be touched even if a stale ``source_id`` collides — a
        no-op + WARNING log makes the mismatch visible in ops dashboards
        rather than silently corrupting another tenant.

        Args:
            source_id: ID of the placeholder row to mark as failed.
            error: Human-readable error message.
            database_name: Database the placeholder belongs to. The row
                must match BOTH ``id`` and ``database_name``; mismatches
                are logged at WARNING and no-op.

        """
        self._ensure_connected()
        statement = select(SourceRow).where(
            SourceRow.id == source_id,
            SourceRow.database_name == database_name,
        )
        source = self.session.exec(statement).first()
        if not source:
            logger.warning(
                "fail_url_fetch_row_not_found",
                source_id=source_id,
                database_name=database_name,
            )
            return
        self._apply_failure(source, stage=SourceErrorStage.URL_FETCH, error=error)  # type: ignore[attr-defined]  # resolved at runtime via SqliteAdapter mixin composition

    def bulk_set_sources_paused(
        self,
        *,
        source_ids: list[str],
        database_name: str,
        is_paused: bool,
        reason: str | None = None,
    ) -> int:
        """Apply pause/resume to multiple sources in a single statement.

        Returns the number of rows affected. An empty source_ids list
        short-circuits to 0 without touching the database.
        """
        if not source_ids:
            return 0

        self._ensure_connected()

        values: dict[str, Any] = {"is_paused": is_paused}
        if is_paused:
            values["paused_at"] = datetime.now(UTC)
            values["paused_reason"] = reason
        else:
            values["paused_at"] = None
            values["paused_reason"] = None

        stmt = (
            update(SourceRow)
            .where(
                SourceRow.id.in_(source_ids),
                SourceRow.database_name == database_name,
            )
            .values(**values)
        )
        result = self.session.execute(stmt)
        self._maybe_commit()
        return int(result.rowcount or 0)
