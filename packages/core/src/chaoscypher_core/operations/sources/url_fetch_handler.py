# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""URL fetch handler.

Background handler for ``OP_FETCH_URL`` (queue: ``operations``). The
``/sources/url`` Cortex route used to call the synchronous WebScraper
fetch inline, which kept the HTTP connection open for the full duration
of the remote fetch — slow servers stalled the worker. The route now
enqueues this handler and returns 202 immediately.

The handler fetches the URL, derives a safe filename from the page
title, and feeds the bytes through ``SourceProcessingService.upload_file``
so the result joins the standard upload pipeline (indexing → extraction
→ commit).

A placeholder ``SourceRow`` (status=PENDING, source_type='webpage') is
created *before* the fetch begins so the UI shows the import in flight
rather than showing nothing until the background job completes.  On any
pre-upload failure the placeholder is promoted to ERROR with
``error_stage='url_fetch'`` so the user can see what went wrong.

The handler drives the WebScraper with
the upload Content-Type allowlist + max_bytes pulled from settings, and
routes binary fetches (PDF, images) through a staged file with the
correct extension so the loader registry picks the right loader.
``ValidationError`` raised by the scraper (allowlist / size violation)
is converted to a row-level failure so the user sees a structured
message instead of a worker crash.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import re
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.app_config import get_settings
from chaoscypher_core.exceptions import ValidationError
from chaoscypher_core.utils.id import generate_id


if TYPE_CHECKING:
    from chaoscypher_core.services.sources import SourceProcessingService


logger = structlog.get_logger(__name__)

_MIN_CONTENT_BYTES = 50


# Map common Content-Type values to a sensible filesystem extension so
# the loader registry routes binary URL fetches to the right loader.
# Anything not in this map falls back to ``.bin`` (which the loader
# registry will reject — surfacing a clear "unsupported file type"
# error rather than silently mojibake-ing).
_CONTENT_TYPE_TO_EXTENSION: dict[str, str] = {
    "application/pdf": ".pdf",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/epub+zip": ".epub",
    "application/zip": ".zip",
    "application/x-zip-compressed": ".zip",
}


def _content_type_to_extension(content_type: str) -> str:
    """Return the filesystem suffix matching *content_type*.

    Unknown types fall through to ``.bin`` so the loader registry can
    surface a clear "unsupported file type" error instead of guessing.
    """
    return _CONTENT_TYPE_TO_EXTENSION.get(content_type.lower(), ".bin")


# Symmetric to ``_CONTENT_TYPE_TO_EXTENSION`` for textual responses.
# Without this, every textual fetch was staged as ``.md`` regardless of
# Content-Type — a URL serving JSON or CSV got the markdown loader and
# lost the W6/W7 format-aware loaders' line-by-line / dialect-sniffing
# behaviour. Unknown textual types fall back to ``.md`` because
# trafilatura emits markdown for HTML-ish content (the historical
# default).
_TEXTUAL_CONTENT_TYPE_TO_EXTENSION: dict[str, str] = {
    "text/html": ".md",  # trafilatura emits markdown
    "text/markdown": ".md",
    "text/plain": ".txt",
    "text/csv": ".csv",
    "text/xml": ".xml",
    "application/xml": ".xml",
    "application/json": ".json",
    "application/xhtml+xml": ".html",
}


def _textual_extension_for(content_type: str) -> str:
    """Return the filesystem suffix for a textual *content_type*.

    Unknown textual types fall through to ``.md`` (the historical
    default for trafilatura-extracted HTML).
    """
    return _TEXTUAL_CONTENT_TYPE_TO_EXTENSION.get(content_type.lower(), ".md")


def _write_tempfile(payload: bytes, suffix: str = ".md") -> str:
    """Write *payload* to a NamedTemporaryFile and return its path.

    Runs on the default executor via ``asyncio.to_thread`` so the blocking
    open + write does not stall the event loop for large payloads.
    """
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(payload)
        return tmp.name


def _safe_filename_from_title(title: str, fallback: str = "web_import") -> str:
    """Sanitize *title* into a filesystem-safe stem (≤ 100 chars)."""
    cleaned = re.sub(r"[^\w\s-]", "", (title or "").strip())
    return re.sub(r"\s+", " ", cleaned)[:100] or fallback


async def handle_fetch_url(  # noqa: PLR0911, PLR0915
    data: dict[str, Any],
    source_processing_service: SourceProcessingService,
    metadata: dict[str, Any] | None = None,
    task_id: str | None = None,
) -> dict[str, Any]:
    """Fetch a URL, stream content to disk, feed it through the upload pipeline.

    Workflow:
    1. Create a placeholder SourceRow with status=PENDING and
       source_type='webpage' so the UI shows the fetch is in flight.
    2. Fetch the URL with the same byte cap as file uploads, validating
       the response Content-Type against the upload allowlist.
    3. For textual responses, encode the extracted markdown and check
       it fits within the cap.
    4. For binary responses (PDF, images), stage the bytes verbatim
       with the right extension so the loader registry picks the
       correct loader.
    5. Hand off to SourceProcessingService.upload_file with
       ``staged_file_path`` (avoids loading content into memory twice).
    6. On any pre-upload failure, mark the placeholder ERROR with
       ``error_stage='url_fetch'`` so the user sees what happened.

    Args:
        data: ``{"url": str, "options": {...upload_file kwargs...}}``.
        source_processing_service: Worker-wired source processing service.
        metadata: Queue task metadata; must contain ``database_name`` (required).
        task_id: Queue task id, for tracing.

    Returns:
        On success the ``upload_file`` result dict (id, filename, status,
        ...). On a fetch failure ``{"status": "error", "url", "error",
        "source_id"}`` — the queue records this as a normal task result
        rather than a worker crash, so retry-on-crash does not trigger.
    """
    from chaoscypher_core.adapters.web.search import WebScraper

    url = data["url"]
    options: dict[str, Any] = data.get("options") or {}
    # ``max_upload_bytes`` + ``upload_content_type_allowlist`` live on the
    # unified ``BatchingSettings`` group, which is the SAME class on the app
    # ``Settings`` singleton and on ``EngineSettings`` post-union, so the value
    # read here is identical to the engine view. This handler receives no
    # ``EngineSettings`` object (its ``source_processing_service`` carries only
    # managers and its caller lives in the worker package), so the engine
    # batching values are read at this boundary; ``settings.web`` (also a
    # unified class) is threaded into the WebScraper below for the same reason.
    settings = get_settings()
    max_bytes = settings.batching.max_upload_bytes
    allowlist = list(settings.batching.upload_content_type_allowlist)

    placeholder_id = generate_id()
    storage = source_processing_service.source_manager
    if metadata is None or "database_name" not in metadata:
        msg = "URL fetch task metadata missing required 'database_name' key"
        # nosemgrep: cc-045-bare-stdlib-raise-in-core - programmer error: metadata contract violation; queue consumer must populate database_name before dispatch
        raise ValueError(msg)
    database_name = metadata["database_name"]

    storage.create_url_placeholder(
        source_id=placeholder_id,
        database_name=database_name,
        url=url,
    )

    logger.info("fetch_url_starting", url=url, source_id=placeholder_id, task_id=task_id)

    try:
        scraper = WebScraper(allowlist=allowlist, max_bytes=max_bytes, web_settings=settings.web)
        try:
            result = await scraper.extract_full_content(url, max_bytes=max_bytes)
        except ValidationError as exc:
            # Allowlist / size violations: turn into a row-level error.
            error_msg = f"Failed to fetch URL: {exc.message}"
            logger.warning(
                "fetch_url_validation_error",
                url=url,
                error=exc.message,
                task_id=task_id,
            )
            storage.fail_url_fetch(placeholder_id, error_msg, database_name)
            return {
                "status": "error",
                "url": url,
                "error": error_msg,
                "source_id": placeholder_id,
            }

        if result.error:
            error_msg = f"Failed to fetch URL: {result.error}"
            logger.warning("fetch_url_failed", url=url, error=result.error, task_id=task_id)
            storage.fail_url_fetch(placeholder_id, error_msg, database_name)
            return {
                "status": "error",
                "url": url,
                "error": error_msg,
                "source_id": placeholder_id,
            }

        # Branch on text vs binary. Both paths funnel into the standard
        # upload pipeline via ``staged_file_path``; only the staged
        # contents and filename differ.
        page_title = (result.title or "Untitled Page").strip()
        safe_title = _safe_filename_from_title(page_title)

        if result.is_binary:
            payload = result.bytes or b""
            if len(payload) < _MIN_CONTENT_BYTES:
                error_msg = (
                    "Fetched binary content is too short or empty. "
                    "Page may require JavaScript or be inaccessible."
                )
                logger.warning(
                    "fetch_url_empty_content",
                    url=url,
                    length=len(payload),
                    is_binary=True,
                )
                storage.fail_url_fetch(placeholder_id, error_msg, database_name)
                return {
                    "status": "error",
                    "url": url,
                    "error": error_msg,
                    "source_id": placeholder_id,
                }
            if len(payload) > max_bytes:
                error_msg = f"Fetched content exceeds max_upload_bytes={max_bytes}"
                logger.warning("fetch_url_content_too_large", url=url, size=len(payload))
                storage.fail_url_fetch(placeholder_id, error_msg, database_name)
                return {
                    "status": "error",
                    "url": url,
                    "error": error_msg,
                    "source_id": placeholder_id,
                }

            suffix = _content_type_to_extension(result.content_type)
            filename = f"{safe_title}{suffix}"
            content_hash = hashlib.sha256(payload).hexdigest()
            staged_path = await asyncio.to_thread(_write_tempfile, payload, suffix)

            logger.info(
                "fetch_url_succeeded",
                url=url,
                title=page_title,
                content_bytes=len(payload),
                content_type=result.content_type,
                is_binary=True,
                task_id=task_id,
            )
        else:
            content = result.content or ""
            if len(content) < _MIN_CONTENT_BYTES:
                error_msg = "Extracted content is too short or empty. Page may require JavaScript."
                logger.warning("fetch_url_empty_content", url=url, length=len(content))
                storage.fail_url_fetch(placeholder_id, error_msg, database_name)
                return {
                    "status": "error",
                    "url": url,
                    "error": error_msg,
                    "source_id": placeholder_id,
                }

            encoded = content.encode("utf-8")
            if len(encoded) > max_bytes:
                error_msg = f"Encoded content exceeds max_upload_bytes={max_bytes}"
                logger.warning("fetch_url_content_too_large", url=url, size=len(encoded))
                storage.fail_url_fetch(placeholder_id, error_msg, database_name)
                return {
                    "status": "error",
                    "url": url,
                    "error": error_msg,
                    "source_id": placeholder_id,
                }

            ext = _textual_extension_for(result.content_type)
            filename = f"{safe_title}{ext}"
            content_hash = hashlib.sha256(encoded).hexdigest()
            payload = encoded
            staged_path = await asyncio.to_thread(_write_tempfile, encoded, ext)

            logger.info(
                "fetch_url_succeeded",
                url=url,
                title=page_title,
                content_bytes=len(encoded),
                content_type=result.content_type,
                is_binary=False,
                encoding_used=result.encoding_used,
                task_id=task_id,
            )

        # The placeholder stays alive across upload_file. On success we
        # delete it (the new SourceRow inside upload_file becomes the
        # canonical row). On failure the outer except promotes it to
        # ERROR via fail_url_fetch so the user sees what went wrong.
        try:
            result_dict = await source_processing_service.upload_file(
                staged_file_path=Path(staged_path),
                file_size=len(payload),
                content_hash=content_hash,
                filename=filename,
                auto_analyze=options.get("auto_analyze", True),
                extraction_depth=options.get("extraction_depth", "full"),
                generate_embeddings=options.get("generate_embeddings", True),
                enable_normalization=options.get("enable_normalization"),
                # W1 (2026-05-07): URL imports now thread enable_vision so
                # the source row records the user's vision choice.
                enable_vision=options.get("enable_vision", True),
                forced_domain=options.get("forced_domain"),
                origin_url=url,
                source_type_override="webpage",
                title_override=page_title,
                skip_duplicates=options.get("skip_duplicates", False),
                content_filtering=options.get("content_filtering", True),
                filtering_mode=options.get("filtering_mode"),
                # Phase 4 (2026-05-08): per-source toggle columns.
                enable_direction_correction=options.get("enable_direction_correction"),
                protect_orphans=options.get("protect_orphans"),
                # Phase 6 (2026-05-08): per-source toggle columns.
                enable_inverse_relationships=options.get("enable_inverse_relationships"),
                max_entity_degree_override=options.get("max_entity_degree_override"),
                # Domain-confirmation gate: forwarded from the URL import request.
                auto_confirm=options.get("auto_confirm", False),
            )
        finally:
            with contextlib.suppress(OSError):
                Path(staged_path).unlink(missing_ok=True)  # noqa: ASYNC240

        # Upload succeeded — placeholder served its purpose, delete it.
        # Suppress + log: if the placeholder delete fails (transient DB
        # error), the canonical row already exists. Re-raising would
        # surface as a task failure and trigger a retry, producing a
        # duplicate URL fetch. The orphan placeholder is cleaner than
        # double work.
        #
        # find_by_content_hash is ORDER BY created_at ASC so the canonical
        # (older) row wins on subsequent uploads — the orphan does not
        # block dedup. The orphan will eventually be cleaned by the
        # OP_CLEANUP_ORPHANS / graph cleanup maintenance task; we record
        # database_name + SQL error code here for ops triage.
        try:
            storage.delete_source(placeholder_id, database_name)
        except Exception as delete_exc:
            # SQLAlchemy errors expose the driver's SQLSTATE / error code on
            # `.orig` (DBAPIError); plain exceptions may carry a `.code`.
            sql_code = getattr(getattr(delete_exc, "orig", None), "sqlstate", None)
            if sql_code is None:
                sql_code = getattr(delete_exc, "code", None)
            logger.warning(
                "url_fetch_placeholder_delete_failed",
                source_id=placeholder_id,
                canonical_source_id=result_dict.get("id"),
                database_name=database_name,
                sql_error_code=sql_code,
                error_type=type(delete_exc).__name__,
                error_message=str(delete_exc),
            )
        return result_dict

    except Exception as exc:
        with contextlib.suppress(Exception):
            storage.fail_url_fetch(placeholder_id, f"Unexpected error: {exc}", database_name)
        raise


__all__ = ["handle_fetch_url"]
