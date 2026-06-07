# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""OP_VISION_PAGE module-level helpers.

Stateless helpers used by ``VisionOperationsService._handle_vision_page``:

- ``_render_image_bytes`` — read/render image bytes for a page row.
- ``_get_active_vision_model`` — resolve configured vision model name.
- ``_get_vision_max_output_tokens`` — resolve per-provider token cap.
- ``_enqueue_finalize`` — enqueue OP_VISION_FINALIZE when a job reaches
  its terminal transition.

The handler body itself lives in
``VisionOperationsService._handle_vision_page`` (vision_operations_service.py)
which satisfies the TaskHandler protocol (data, metadata=, task_id=).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog


if TYPE_CHECKING:
    from chaoscypher_core.settings import Settings


logger = structlog.get_logger(__name__)


def _get_active_vision_model(settings: Settings) -> str | None:
    """Resolve the configured vision model name for the active provider."""
    provider = settings.llm.chat_provider
    return getattr(settings.llm, f"{provider}_vision_model", None)


def _get_vision_max_output_tokens(settings: Settings) -> int | None:
    """Resolve the per-provider vision_max_output_tokens cap."""
    provider = settings.llm.chat_provider
    return getattr(settings.llm, f"{provider}_vision_max_output_tokens", None)


def _render_image_bytes(row: dict[str, Any], *, dpi: int) -> bytes:
    """Read image bytes for one row.

    For STANDALONE_IMAGE, reads the file directly (``dpi`` ignored — the
    source file is taken as-is).
    For PDF_PAGE, opens the source PDF and renders this one page via
    pypdfium2 at ``dpi`` resolution.

    Raises RuntimeError on render/IO failure (caller catches and marks
    the row FAILED with the error message).
    """
    from pathlib import Path

    from chaoscypher_core.vision.states import VisionPageKind

    image_path = Path(row["image_path"])

    if row["kind"] == VisionPageKind.STANDALONE_IMAGE.value:
        return image_path.read_bytes()

    if row["kind"] == VisionPageKind.PDF_PAGE.value:
        # Per-task render — opens the PDF, renders this one page, closes.
        # pypdfium2 lacks type stubs (no py.typed marker upstream).
        import io

        import pypdfium2 as pdfium  # type: ignore[import-untyped]

        pdf = pdfium.PdfDocument(str(image_path))
        try:
            page = pdf[row["page_number"] - 1]  # 1-indexed → 0-indexed
            try:
                bitmap = page.render(scale=dpi / 72)
                pil = bitmap.to_pil()
                buf = io.BytesIO()
                pil.save(buf, format="PNG")
                return buf.getvalue()
            finally:
                page.close()
        finally:
            pdf.close()

    msg = f"unknown VisionPageKind: {row['kind']!r}"
    # nosemgrep: cc-045-bare-stdlib-raise-in-core - programmer-error: invalid kind value caught at enqueue boundary
    raise RuntimeError(msg)


def _persist_page_image(
    image_bytes: bytes,
    *,
    data_dir: Any,
    database_name: str,
    source_id: str,
    page_number: int,
) -> None:
    """Write rendered PNG bytes to the canonical per-source location.

    Layout matches the one consumed by ``GET /sources/{id}/images`` and
    by both UI surfaces (``VisionPagesGrid`` and ``ChunkCitation``):

        ``{data_dir}/databases/<database>/images/<source_id>/page_{N}.png``

    Source of truth for the path is ``vision_images_dir`` in
    indexing_handler.py — cleanup paths (source-delete + indexing-failure)
    use the same helper, so writer and deleter cannot drift apart.

    Idempotent: a retry overwrites the existing file with fresh bytes.
    """
    from chaoscypher_core.operations.importing.indexing_handler import (
        vision_images_dir,
    )

    target_dir = vision_images_dir(data_dir, database_name, source_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / f"page_{page_number}.png").write_bytes(image_bytes)


async def _enqueue_finalize(*, source_id: str, job_id: str, database_name: str) -> None:
    """Enqueue OP_VISION_FINALIZE.

    Single-handler-per-transition guarantee is provided by the caller
    (only the terminal observer reaches here — the atomic counter check
    in ``increment_vision_job_completed_and_check`` ensures exactly one
    caller sees ``is_terminal=True``).
    """
    from chaoscypher_core.constants import OP_VISION_FINALIZE, QUEUE_OPERATIONS
    from chaoscypher_core.queue import queue_client

    await queue_client.enqueue_task(
        queue=QUEUE_OPERATIONS,
        operation=OP_VISION_FINALIZE,
        data={
            "source_id": source_id,
            "job_id": job_id,
            "database_name": database_name,
        },
        metadata={
            "source_id": source_id,
            "job_id": job_id,
            "operation_type": OP_VISION_FINALIZE,
        },
    )
    logger.info(
        "vision_finalize_enqueued",
        source_id=source_id,
        job_id=job_id,
    )
