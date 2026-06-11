# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""UploadService — owns sources-feature file-upload orchestration.

Extracted from ``sources/api.py``. Encapsulates:

- The process-wide upload concurrency semaphore (formerly a module-level
  mutable ``_upload_semaphore`` in ``api.py``).
- Content-type allowlist enforcement (formerly ``_validate_content_type``).
- Disk-headroom preflight (formerly ``_preflight_disk_for_upload``).
- Streaming an ``UploadFile`` to a temp file with SHA-256 hash + size cap
  (formerly ``_stream_upload_to_temp``).
- Single-file upload orchestration (formerly the body of the
  ``upload_file`` route handler).
- Batch orchestration (formerly the body of the ``upload_batch`` route
  handler).

Route handlers in ``api.py`` stay thin: parse form → delegate to this
service → shape response. Business logic lives here, testable without
the FastAPI stack.
"""

from __future__ import annotations

import asyncio
import hashlib
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.exceptions import ValidationError
from chaoscypher_core.services.events import event_bus
from chaoscypher_core.utils.disk import check_disk_space


if TYPE_CHECKING:
    from fastapi import UploadFile

    from chaoscypher_core.app_config import Settings
    from chaoscypher_core.services.sources import (
        SourceProcessingService as EngineSourceProcessingService,
    )


logger = structlog.get_logger(__name__)

__all__ = ["UploadService"]


class UploadService:
    """Owns the upload side of the sources feature.

    One instance per process (the factory caches it via FastAPI's DI).
    The semaphore is an instance attribute, so multiple UploadService
    instances do not share a global concurrency cap.
    """

    def __init__(
        self,
        settings: Settings,
        source_processing_service: EngineSourceProcessingService,
    ) -> None:
        """Initialize the service.

        Args:
            settings: Application settings (reads ``batching`` and ``data_dir``).
            source_processing_service: Core service that handles the
                "stage → enqueue processing" half of an upload.

        """
        self._settings = settings
        self._source_processing_service = source_processing_service
        self._semaphore = asyncio.Semaphore(settings.batching.upload_max_concurrent)

    def validate_content_type(self, file: UploadFile) -> None:
        """Reject uploads whose Content-Type isn't in the allowlist.

        Raises:
            ValidationError: When the uploaded file's content type is not
                allowed. The shared error handler maps ``ValidationError``
                to HTTP 400 (previous implementation raised 415 —
                acceptable behavior change per audit decision).

        """
        allowlist = self._settings.batching.upload_content_type_allowlist
        if "*" in allowlist:
            return
        ctype = (file.content_type or "").split(";", 1)[0].strip().lower()
        if ctype and ctype not in allowlist:
            msg = f"Content type '{ctype}' is not allowed"
            raise ValidationError(msg, field="content_type")

    def preflight_disk_for_upload(self, *, total_bytes: int | None = None) -> None:
        """Verify enough disk headroom exists to accept an upload safely.

        When ``total_bytes`` is omitted (single-file path), requires
        ``max_upload_bytes + upload_disk_headroom_bytes`` of free space on
        the data-dir filesystem — the worst-case for one upload.

        When ``total_bytes`` is provided (batch path, F9), requires
        ``total_bytes + upload_disk_headroom_bytes`` of free space so the
        batch as a whole cannot exceed disk: a 10x50 MB batch onto a
        100 MB-free disk now fails up front instead of writing N-1 files
        and dying on the last one.

        Args:
            total_bytes: Optional sum of file sizes for a batch upload.

        Raises:
            InsufficientStorageError: When free space is below the threshold
                (mapped to HTTP 507 by the shared error handler).

        """
        required = (
            total_bytes if total_bytes is not None else self._settings.batching.max_upload_bytes
        ) + self._settings.batching.upload_disk_headroom_bytes
        check_disk_space(Path(str(self._settings.data_dir)), min_bytes=required)

    async def stream_upload_to_temp(self, file: UploadFile) -> tuple[Path, str, int]:
        """Stream ``file`` to a temp file, returning (path, sha256, size).

        Enforces ``self._settings.batching.max_upload_bytes`` as an upper
        bound; overflow raises ``ValidationError`` (mapped to HTTP 400 by
        the shared handler).

        """
        chunk_size = self._settings.batching.upload_chunk_size
        max_bytes = self._settings.batching.max_upload_bytes
        hasher = hashlib.sha256()
        size = 0
        tmp_dir = self._settings.database_dir / "uploads"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        tmp = await asyncio.to_thread(
            tempfile.NamedTemporaryFile, delete=False, suffix=".upload", dir=tmp_dir
        )
        try:
            while chunk := await file.read(chunk_size):
                size += len(chunk)
                if size > max_bytes:
                    msg = f"Upload exceeds max_upload_bytes={max_bytes}"
                    raise ValidationError(msg, field="file")
                # arg-type suppressed: asyncio.to_thread infers tmp.write as () -> None
                # because tempfile.NamedTemporaryFile() returns IO[Any]; chunk is bytes
                # and the underlying file accepts bytes.
                await asyncio.to_thread(tmp.write, chunk)  # type: ignore[arg-type]
                hasher.update(chunk)
            await asyncio.to_thread(tmp.close)
            return Path(tmp.name), hasher.hexdigest(), size
        except Exception:
            await asyncio.to_thread(tmp.close)
            await asyncio.to_thread(Path(tmp.name).unlink, True)
            raise

    async def upload_single(
        self,
        *,
        file: UploadFile,
        safe_filename: str,
        extract_entities: bool,
        analysis_depth: str,
        enable_normalization: bool | None,
        forced_domain: str | None,
        skip_duplicates: bool,
        enable_vision: bool | None,
        content_filtering: bool,
        auto_confirm: bool = False,
        filtering_mode: str | None = None,
        enable_direction_correction: bool | None = None,
        protect_orphans: bool | None = None,
        enable_inverse_relationships: bool | None = None,
        max_entity_degree_override: int | None = None,
    ) -> dict[str, Any]:
        """Process a single upload: preflight → validate → stream → delegate → emit → cleanup.

        Used by both the ``upload_file`` route (single file) and
        ``upload_batch`` (fan-out via ``asyncio.gather``).

        The disk preflight runs here so that any caller of ``upload_single``
        (route handler today, future test harness / programmatic caller
        tomorrow) is guaranteed the disk-headroom invariant without having
        to remember to call ``preflight_disk_for_upload`` externally.
        ``upload_batch`` keeps its own upfront preflight as a fail-fast
        optimization before fan-out.

        Takes the semaphore around the streaming phase only — the
        downstream ``source_processing_service.upload_file`` call happens
        outside the semaphore (it enqueues, it doesn't block on I/O).

        """
        self.preflight_disk_for_upload()
        self.validate_content_type(file)
        async with self._semaphore:
            staged_path, content_hash, file_size = await self.stream_upload_to_temp(file)
        try:
            result = await self._source_processing_service.upload_file(
                filename=safe_filename,
                auto_analyze=extract_entities,
                extraction_depth=analysis_depth,
                generate_embeddings=True,  # Always enabled.
                enable_normalization=enable_normalization,
                forced_domain=forced_domain,
                skip_duplicates=skip_duplicates,
                enable_vision=enable_vision,
                content_filtering=content_filtering,
                auto_confirm=auto_confirm,
                filtering_mode=filtering_mode,
                enable_direction_correction=enable_direction_correction,
                protect_orphans=protect_orphans,
                enable_inverse_relationships=enable_inverse_relationships,
                max_entity_degree_override=max_entity_degree_override,
                staged_file_path=staged_path,
                content_hash=content_hash,
                file_size=file_size,
            )
            if not result.get("skipped_duplicate"):
                event_bus.emit(
                    "file_uploaded",
                    action=f"File uploaded: {safe_filename}",
                    source="user",
                    details={"source_id": result.get("id"), "filename": safe_filename},
                    database_name=self._settings.current_database,
                )
            return result
        finally:
            staged_path.unlink(missing_ok=True)

    async def upload_batch(
        self,
        *,
        files: list[UploadFile],
        sanitize_filename: Callable[[str | None], str],
        extract_entities: bool,
        analysis_depth: str,
        enable_normalization: bool | None,
        forced_domain: str | None,
        skip_duplicates: bool,
        enable_vision: bool | None = None,
        content_filtering: bool,
        auto_confirm: bool = False,
        filtering_mode: str | None = None,
        enable_direction_correction: bool | None = None,
        protect_orphans: bool | None = None,
        enable_inverse_relationships: bool | None = None,
        max_entity_degree_override: int | None = None,
    ) -> tuple[list[dict[str, Any]], list[tuple[str, str]]]:
        """Upload ``files`` concurrently; return (successes, errors).

        Errors is a list of ``(filename, message)`` tuples. Caller shapes
        the HTTP response.

        ``enable_normalization`` is tri-state: ``True`` / ``False`` are
        explicit user overrides; ``None`` is preserved on each source
        row so the indexing handler resolves it per-file from the
        filename at extraction time (W1, 2026-05-07). Resolving here
        was the legacy path and is intentionally removed so URL imports
        and CLI uploads share the same semantics as multipart uploads.

        Raises:
            ValidationError: If ``len(files) > max_upload_files``.

        """
        max_files = self._settings.batching.max_upload_files
        if len(files) > max_files:
            msg = f"Too many files: {len(files)} exceeds limit of {max_files}"
            raise ValidationError(msg, field="files")
        # Upfront content-type + preflight so a bad batch fails fast.
        for f in files:
            self.validate_content_type(f)
        # F9: preflight against the *batch total*, not the single-file cap.
        # Without this, a 10x50 MB batch onto a 100 MB-free disk would pass
        # the per-file preflight and write N-1 files before failing.
        # UploadFile.size is set by Starlette/FastAPI for multipart parts
        # (spooled or buffered); fall back to the per-file max as a worst
        # case when a client omits Content-Length and the size is unknown.
        max_per_file = self._settings.batching.max_upload_bytes

        def _file_bytes(uf: UploadFile) -> int:
            """Return the upload's declared size, falling back to the per-file cap."""
            sz = getattr(uf, "size", None)
            return int(sz) if isinstance(sz, int) else max_per_file

        total_bytes = sum(_file_bytes(f) for f in files)
        self.preflight_disk_for_upload(total_bytes=total_bytes)

        async def _one(upload_file: UploadFile) -> dict[str, Any]:
            """Run upload_single for one file with the shared batch parameters."""
            return await self.upload_single(
                file=upload_file,
                safe_filename=sanitize_filename(upload_file.filename),
                extract_entities=extract_entities,
                analysis_depth=analysis_depth,
                enable_normalization=enable_normalization,
                forced_domain=forced_domain,
                skip_duplicates=skip_duplicates,
                enable_vision=enable_vision,
                content_filtering=content_filtering,
                auto_confirm=auto_confirm,
                filtering_mode=filtering_mode,
                enable_direction_correction=enable_direction_correction,
                protect_orphans=protect_orphans,
                enable_inverse_relationships=enable_inverse_relationships,
                max_entity_degree_override=max_entity_degree_override,
            )

        results = await asyncio.gather(*[_one(f) for f in files], return_exceptions=True)
        successes: list[dict[str, Any]] = []
        errors: list[tuple[str, str]] = []
        for i, result in enumerate(results):
            if isinstance(result, BaseException):
                filename = files[i].filename or "unknown"
                logger.error(
                    "batch_upload_file_error",
                    filename=filename,
                    error=str(result),
                    exc_info=result,
                )
                # F2/F4/F10: surface a sanitized, capped exception message so
                # callers can distinguish e.g. ValidationError ("upload too big")
                # from InsufficientStorageError ("disk full") without leaking
                # full stack traces or sensitive paths.
                msg_attr = getattr(result, "message", None)
                msg = msg_attr if isinstance(msg_attr, str) and msg_attr else str(result)
                error_str = (
                    f"{type(result).__name__}: "
                    f"{msg[: self._settings.logs.error_message_preview_chars]}"
                )
                errors.append((filename, error_str))
            else:
                successes.append(result)
        return successes, errors
