# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Source processing Service.

Business logic for source processing operations.
"""

import asyncio
import hashlib
from pathlib import Path
from typing import Any

import structlog

from chaoscypher_core.exceptions import ExternalServiceError, NotFoundError, ValidationError
from chaoscypher_core.models import SourceStatus
from chaoscypher_core.utils.id import generate_id


logger = structlog.get_logger(__name__)


async def _hash_file_async(path: Path) -> str:
    """Hash a file's contents off the event loop.

    Args:
        path: Absolute path to the file to hash.

    Returns:
        Lowercase hex-encoded SHA-256 digest.

    """

    def _sync_hash() -> str:
        """Stream-hash the file in 1 MiB chunks and return the hex digest."""
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()

    return await asyncio.to_thread(_sync_hash)


async def _resolve_content_hash(
    *,
    file_content: bytes | None,
    staged_file_path: Path | None,
    content_hash: str | None,
) -> str | None:
    """Resolve the content hash, computing or verifying as needed.

    Three modes, one return:

    1. ``content_hash is None`` → compute and return it (bytes path:
       in-memory; staged path: thread-pool to avoid blocking the loop).
       Audit fix #H4: dedup never silently no-ops because the caller
       forgot to precompute.
    2. ``content_hash`` provided AND content available → recompute and
       compare. Mismatch raises ``ValidationError`` (F14 defense in
       depth) so a caller bug — typically reusing a hash from a
       different file — surfaces before we corrupt a SourceRow.
    3. ``content_hash`` provided but no content (caller already wrote
       and discarded the bytes elsewhere) → trust it; the caller is the
       only one with the content to verify against.

    Args:
        file_content: In-memory bytes, or None.
        staged_file_path: Pre-written temp file, or None.
        content_hash: Pre-computed SHA-256 hex digest, or None.

    Returns:
        Resolved hex digest. ``None`` only if both ``content_hash`` and
        all content sources were ``None`` — the caller validates this
        precondition before invocation.

    Raises:
        ValidationError: If the provided ``content_hash`` does not match
            the actual content (mode 2).
    """
    if content_hash is None:
        if file_content is not None:
            return hashlib.sha256(file_content).hexdigest()
        if staged_file_path is not None:
            return await _hash_file_async(Path(staged_file_path))
        return None

    actual_hash: str | None = None
    if file_content is not None:
        actual_hash = hashlib.sha256(file_content).hexdigest()
    elif staged_file_path is not None:
        actual_hash = await _hash_file_async(Path(staged_file_path))
    if actual_hash is not None and actual_hash != content_hash:
        msg = (
            "Provided content_hash does not match file content: "
            f"expected={content_hash[:16]}..., actual={actual_hash[:16]}..."
        )
        raise ValidationError(msg, field="content_hash")
    return content_hash


class SourceProcessingService:
    """Service for source processing operations.

    Example:
        >>> from chaoscypher_core.services.sources.api import get_source_processing_service
        >>> from chaoscypher_core.adapters.sqlite import get_db_session
        >>> from chaoscypher_core.settings import EngineSettings
        >>>
        >>> # Get service instance via factory
        >>> settings = EngineSettings()
        >>> with get_db_session("my_database") as session:
        ...     settings = get_settings()
        ...     service = get_source processing_service(session, settings)
        ...
        ...     # Upload a file for source processing
        ...     with open("document.pdf", "rb") as f:
        ...         response = await service.upload_file(
        ...             file_content=f.read(),
        ...             filename="document.pdf",
        ...             auto_analyze=True,
        ...             extraction_depth="full"
        ...         )
        ...     print(response.data["id"])
        ...     "if_abc123"
        ...
        ...     # Get file status
        ...     file_status = service.get_file("if_abc123")
        ...     print(file_status.data["status"])
        ...     "indexing"
        ...
        ...     # List all source processing files
        ...     files = service.list_files(status_filter="indexed", limit=10)
        ...     print(len(files.data))
        ...     3

    """

    def __init__(
        self,
        source_manager: Any,
        operations_manager: Any,
        config_manager: Any,
        validators: Any,
    ) -> None:
        """Initialize source processing service.

        Args:
            source_manager: Storage adapter implementing SourceStorageProtocol
            operations_manager: Operations manager for queueing
            config_manager: Config manager instance
            validators: SourceFileValidators instance

        """
        self.source_manager = source_manager
        self.operations_manager = operations_manager
        self.config_manager = config_manager
        self.validators = validators

    async def upload_file(
        self,
        file_content: bytes | None = None,
        filename: str = "",
        auto_analyze: bool = True,
        extraction_depth: str = "full",
        generate_embeddings: bool = True,
        enable_normalization: bool | None = None,
        forced_domain: str | None = None,
        origin_url: str | None = None,
        source_type_override: str | None = None,
        title_override: str | None = None,
        skip_duplicates: bool = False,
        enable_vision: bool | None = None,
        content_filtering: bool = True,
        auto_confirm: bool = False,
        filtering_mode: str | None = None,
        enable_direction_correction: bool | None = None,
        protect_orphans: bool | None = None,
        staged_file_path: Path | None = None,
        content_hash: str | None = None,
        file_size: int | None = None,
        # Phase 6 (2026-05-08): nullable per-source toggles
        enable_inverse_relationships: bool | None = None,
        max_entity_degree_override: int | None = None,
    ) -> dict[str, Any]:
        """Upload a file to the source processing staging area.

        Accepts either ``file_content`` (bytes) or ``staged_file_path`` (pre-written
        temp file). The staged path approach avoids loading large files into memory.

        Args:
            file_content: File content bytes (mutually exclusive with staged_file_path)
            filename: Original filename
            auto_analyze: Whether to automatically start analysis
            extraction_depth: Extraction depth mode (quick/full)
            generate_embeddings: Generate embeddings (always True)
            enable_normalization: Enable content normalization (encoding fixes,
                whitespace cleanup, OCR cleaning). ``None`` (default) defers to
                the file-type default; ``True`` / ``False`` is an explicit user
                override and is persisted on the source row (Workstream 1).
            forced_domain: Force a specific extraction domain (e.g., 'technical').
                If None, domain is auto-detected from content.
            origin_url: Original URL if the source was fetched from the web.
            source_type_override: Override the file-extension-derived source type
                (e.g., 'webpage' for URL imports).
            title_override: Override the default title (filename) with a custom title
                (e.g., page title for URL imports).
            skip_duplicates: If True, skip upload when identical content (by SHA-256
                hash) already exists in the database. Returns existing source with
                ``skipped_duplicate=True`` flag.
            enable_vision: Enable vision LLM processing for image-heavy documents.
                None=auto-detect, True=force, False=skip.
            content_filtering: If True (default), apply content exclusion filtering
                before entity extraction.
            auto_confirm: When True, bypass the domain-confirmation gate and
                proceed with the auto-detected domain (confirmation_required=False).
                When False (default) and no forced_domain is given, the source is
                parked at AWAITING_CONFIRMATION after indexing (Phase 4, 2026-05-28).
            filtering_mode: Filtering preset override (e.g., ``"strict"``, ``"balanced"``).
                None means use the domain default. Passed through to extraction options.
            enable_direction_correction: Phase 4 (2026-05-08). When not None, persists
                on the source row; None defers to cascade default.
            protect_orphans: Phase 4 (2026-05-08). When not None, persists on the source
                row; True keeps orphan entities, False drops them. None defers to cascade.
            enable_inverse_relationships: Phase 6 (2026-05-08). When not None, persists
                on the source row; None uses the global ExtractionSettings default.
            max_entity_degree_override: Phase 6 (2026-05-08). When a positive int,
                persists as the per-source degree cap; None uses domain/global default.
            staged_file_path: Pre-written temp file to move to staging (avoids memory).
            content_hash: Pre-computed SHA-256 hash. If omitted with staged_file_path,
                the hash is computed from disk in a worker thread.
            file_size: Pre-computed file size in bytes.

        Returns:
            Dictionary with file info

        """
        self.validators.require_source_processing_service()

        if file_content is None and staged_file_path is None:
            msg = "Provide either file_content (bytes) or staged_file_path (Path) — both were None"
            raise ValidationError(msg, field="file")

        # Resolve the content hash: compute when omitted, verify when
        # the caller provided one (F14 defense in depth — see helper for
        # the full mode table).
        content_hash = await _resolve_content_hash(
            file_content=file_content,
            staged_file_path=staged_file_path,
            content_hash=content_hash,
        )

        # Determine file size
        if file_size is None:
            if file_content is not None:
                file_size = len(file_content)
            elif staged_file_path is not None:
                # ASYNC240 suppressed: sync stat is acceptable for local SSD paths
                # (sub-ms). If staging moves to a network mount (NFS/SMB), wrap in
                # ``await asyncio.to_thread(Path(staged_file_path).stat)``.
                file_size = Path(staged_file_path).stat().st_size  # noqa: ASYNC240
            else:
                file_size = 0

        # Check for duplicate content.
        #
        # Opt-in dedup is best-effort under concurrent load — two simultaneous
        # uploads with the same hash and skip_duplicates=True can both pass the
        # find_by_content_hash check and create separate rows. The user can
        # delete the duplicate. We do not tighten this with a transaction lock
        # because (a) duplicates are intentional in the default path and (b)
        # the race window is small (one find + one insert). See
        # ``SourceRow.content_hash`` field comment for full rationale.
        if skip_duplicates and content_hash:
            settings = self.config_manager.get_settings()
            database_name = settings.current_database
            existing = self.source_manager.find_by_content_hash(database_name, content_hash)
            if existing:
                logger.info(
                    "duplicate_source_skipped",
                    filename=filename,
                    existing_id=existing["id"],
                    existing_status=existing.get("status"),
                    content_hash=content_hash,
                )
                response = dict(existing)
                response["skipped_duplicate"] = True
                response["existing_status"] = existing.get("status")
                return response

        # Check file size against the unified upload cap. Both file uploads
        # and URL fetches use settings.batching.max_upload_bytes so the same
        # content is accepted or rejected regardless of entry path. The
        # legacy ``source_processing.source_processing_max_file_size_gb``
        # field is deprecated (emits a warning at startup if explicitly set
        # in settings.yaml) and no longer consulted here — see F12 in
        # ``upload pipeline audit notes``.
        settings = self.config_manager.get_settings()
        max_size = settings.batching.max_upload_bytes

        if file_size > max_size:
            msg = f"File size exceeds maximum upload size of {max_size} bytes (max_upload_bytes)"
            raise ValidationError(msg)

        # Generate file ID
        file_id = generate_id()

        # Get database name and staging dir from settings
        database_name = settings.current_database
        staging_dir = str(settings.database_dir / "sources")

        # Resolve effective enable_vision before persistence: when the
        # caller passed None ("auto"), upload_source needs a concrete bool
        # because the column is non-nullable. The default-true policy
        # mirrors prior file_info behavior.
        effective_enable_vision = True if enable_vision is None else bool(enable_vision)

        # Resolve effective filtering_mode (column has a non-null default
        # of "balanced").
        effective_filtering_mode = filtering_mode if filtering_mode is not None else "balanced"

        # Domain-confirmation gate (2026-05-28): a source whose domain will be
        # auto-detected (no forced_domain) and which was not uploaded with the
        # bypass flag must be parked for human confirmation after indexing.
        # forced_domain set OR auto_confirm => never park.
        confirmation_required = (not auto_confirm) and (forced_domain is None)

        # Upload file via repository — Workstream 1 (2026-05-07): every
        # upload setting is persisted on the row so recovery / retry /
        # re-extract preserve user choice. The queue payload (file_info
        # below) shrinks to operational context only.
        source_file: dict[str, Any] = self.source_manager.upload_source(
            source_id=file_id,
            database_name=database_name,
            filename=filename,
            file_content=file_content,
            staging_dir=staging_dir,
            extraction_depth=extraction_depth,
            forced_domain=forced_domain,
            origin_url=origin_url,
            source_type_override=source_type_override,
            title_override=title_override,
            content_hash=content_hash,
            staged_file_path=staged_file_path,
            file_size=file_size,
            # Persist upload settings on the row (W1):
            auto_analyze=auto_analyze,
            enable_normalization=enable_normalization,
            enable_vision=effective_enable_vision,
            content_filtering=content_filtering,
            filtering_mode=effective_filtering_mode,
            # Phase 4 (2026-05-08): nullable booleans — None means "use
            # cascade default"; True/False is an explicit user override.
            enable_direction_correction=enable_direction_correction,
            protect_orphans=protect_orphans,
            # Phase 6 (2026-05-08): nullable per-source toggles
            enable_inverse_relationships=enable_inverse_relationships,
            max_entity_degree_override=max_entity_degree_override,
            # Domain-confirmation gate (2026-05-28)
            confirmation_required=confirmation_required,
        )

        # source_file is already a dict from storage adapter
        file_info = source_file

        # Queue payload. The source row is the single source of truth
        # for upload settings (Workstream 1, 2026-05-07). Every reader
        # — indexing handler, embedding handler, import service,
        # commit service — goes through ``adapter.get_source()`` to
        # pick up auto_analyze / extraction_depth / enable_normalization
        # / enable_vision / content_filtering / filtering_mode /
        # forced_domain. Mirroring those keys back into the queue
        # payload here is redundant and risks drift, so we only retain
        # ``generate_embeddings`` which is a runtime-only flag never
        # persisted on the row.
        file_info["generate_embeddings"] = generate_embeddings

        # ALWAYS queue indexing (chunking + embeddings for RAG) - this is FAST
        if self.operations_manager:
            try:
                result = await self.operations_manager.queue_import_indexing(
                    file_id=source_file["id"],
                    file_info=file_info,
                    database_name=settings.current_database,
                    priority=settings.priorities.background,
                )
                logger.info(
                    "indexing_queued",
                    file_id=source_file["id"],
                    task_id=result.get("task_id"),
                )
            except Exception as exc:
                logger.exception(
                    "indexing_queue_failed",
                    file_id=source_file["id"],
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                )
                self.source_manager.update_file(
                    source_file["id"],
                    database_name=database_name,
                    updates={
                        "status": SourceStatus.ERROR,
                        "error_message": f"Failed to queue indexing: {exc!s}",
                        "error_stage": "indexing",
                    },
                )
                raise ExternalServiceError(
                    service_name="Valkey",
                    reason="Failed to queue indexing task",
                ) from exc

        return source_file  # Already a dict from storage adapter

    def list_files(
        self, status_filter: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        """List import files."""
        self.validators.require_source_processing_service()

        # Get database name from settings
        settings = self.config_manager.get_settings()
        database_name = settings.current_database

        # Adapter returns summary dicts directly (Phase 3 dict-contract fix
        # for ``source_files.py`` — SQLModel entities no longer escape the
        # adapter boundary).
        summaries: list[dict[str, Any]] = self.source_manager.list_source_summaries(
            database_name=database_name, status=status_filter, limit=limit
        )
        return summaries

    def get_file(self, file_id: str) -> dict[str, Any]:
        """Get file details (excludes large entities/relationships arrays).

        Use paginated endpoints for entities and relationships:
        - GET /api/v1/source processing/files/{file_id}/entities
        - GET /api/v1/source processing/files/{file_id}/relationships
        """
        self.validators.require_source_processing_service()

        # Get database name from settings
        settings = self.config_manager.get_settings()
        database_name = settings.current_database

        # Adapter returns the detail dict directly (Phase 3 dict-contract fix).
        detail: dict[str, Any] | None = self.source_manager.get_source_detail(
            file_id, database_name
        )
        if not detail:
            msg = "ImportFile"
            raise NotFoundError(msg, file_id)
        return detail

    def get_stats(self) -> dict[str, Any]:
        """Get import stats."""
        self.validators.require_source_processing_service()

        settings = self.config_manager.get_settings()
        database_name = settings.current_database
        stats: dict[str, Any] = self.source_manager.get_stats(database_name)
        return stats
