# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""PDF Loader using pypdf.

Extracts text from PDF files using pypdf (BSD-3 licensed).
Output is plain text (no markdown structure preservation).

Implements BaseLoader protocol for auto-discovery by LoaderRegistry.

Hardening:
- Per-page try/except: a corrupt page no longer kills the whole document.
  Each failure is recorded in ``loader_pdf_pages_failed`` metadata which
  the indexing handler rolls up onto the source-row counter.
- Encrypted PDF detection: ``EncryptedPDFError`` is raised before page
  iteration so the API can surface a 422 with a clear "decrypt first" message.
- Image-only flag: when every page returned empty text, ``needs_vision=True``
  is set on the document metadata so the vision pipeline sees the signal.
- ``pdf_max_pages`` cap: operators can cap page count via settings to prevent
  a 10,000-page PDF from wedging a worker.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog


if TYPE_CHECKING:
    from chaoscypher_core.settings import EngineSettings


logger = structlog.get_logger(__name__)


class PdfLoader:
    """PDF loader using pypdf for text extraction.

    Uses pypdf.PdfReader for page-by-page text extraction.
    Output is plain text without structure preservation (no headers,
    tables, or formatting). Suitable for downstream LLM processing
    where raw text content is sufficient.

    Speed: Comparable to PyMuPDF (~0.1-0.2s/page)
    Output: Plain text
    License: BSD-3 (permissive)
    """

    @property
    def supported_extensions(self) -> list[str]:
        """File extensions this loader supports."""
        return [".pdf", ".PDF"]

    def __init__(self, settings: EngineSettings | None = None) -> None:
        """Initialize PDF loader.

        Args:
            settings: Engine settings. Used for ``loader.pdf_max_pages``.

        """
        self._settings = settings

    def load_document(self, filepath: str) -> list[dict[str, Any]]:  # noqa: C901, PLR0912, PLR0915 - per-page extraction loop dispatches across multiple PDF parsing failure modes (encrypted, OCR, image-only, etc.)
        """Load PDF with text extraction.

        Extracts text from each page and combines with page separators.

        Args:
            filepath: Path to PDF file.

        Returns:
            List of document chunks with content and metadata.

        Raises:
            EncryptedPDFError: When the PDF is password-protected.

        """
        try:
            from pypdf import PdfReader
        except ImportError:
            logger.exception(
                "pypdf not installed. Run: pip install pypdf\nFalling back to standard loaders."
            )
            raise

        from chaoscypher_core.services.sources.loaders.base import check_loader_file_size

        check_loader_file_size(filepath, self._settings)

        start_time = time.time()
        filepath_obj = Path(filepath)

        try:
            logger.info("pdf_loading_started", filepath=filepath, extraction_method="pypdf")

            reader = PdfReader(filepath)

            # Task 2 (Phase 5b): detect encrypted PDFs before iterating pages.
            # pypdf raises a generic exception on encrypted read; surface it
            # as a typed EncryptedPDFError so the API returns HTTP 422 with
            # an actionable "decrypt before uploading" message.
            #
            # 2026-05-09 hardening: ``is_encrypted=True`` is a false positive
            # on a large class of real-world PDFs — Adobe Acrobat-saved
            # documents, scanned OCR output, and many journal articles
            # declare encryption purely to advertise permission restrictions
            # ("don't print", "don't copy"), but the cryptographic envelope
            # is empty and ``decrypt("")`` succeeds. Refusing those files
            # blocks legitimate uploads.  We attempt empty-password
            # decryption first; only when that fails (returns 0 /
            # NOT_DECRYPTED, or raises) do we surface the typed error.
            if reader.is_encrypted:
                from pypdf import PasswordType

                try:
                    decrypt_result = reader.decrypt("")
                except Exception as exc:
                    logger.info(
                        "pdf_decrypt_empty_password_raised",
                        filepath=filepath,
                        error_type=type(exc).__name__,
                        error_message=str(exc),
                    )
                    decrypt_result = PasswordType.NOT_DECRYPTED
                if decrypt_result == PasswordType.NOT_DECRYPTED:
                    from chaoscypher_core.exceptions import EncryptedPDFError

                    raise EncryptedPDFError(filepath_obj.name)
                logger.info(
                    "pdf_decrypted_with_empty_password",
                    filepath=filepath,
                    decrypt_result=str(decrypt_result),
                )

            total_pages = len(reader.pages)

            # Task 4 (Phase 5b): cap page count via settings.
            loader_warnings: list[str] = []
            pdf_max_pages = (
                self._settings.loader.pdf_max_pages if self._settings is not None else None
            )
            pages_to_process = list(reader.pages)
            if pdf_max_pages is not None and total_pages > pdf_max_pages:
                pages_to_process = pages_to_process[:pdf_max_pages]
                warning = f"PDF truncated at {pdf_max_pages} pages (file has {total_pages} pages)"
                loader_warnings.append(warning)
                logger.info(
                    "pdf_pages_truncated",
                    filepath=filepath,
                    pdf_max_pages=pdf_max_pages,
                    total_pages=total_pages,
                )

            # Task 1 (Phase 5b): per-page try/except.
            # Each page extraction is wrapped independently so a corrupt page
            # does not discard the rest of the document.  Failures are tracked
            # in ``failed_pages`` and rolled up into loader_pdf_pages_failed.
            page_texts: list[str] = []
            failed_pages: list[tuple[int, str, str]] = []  # (index, exc_type, message)

            for page_index, page in enumerate(pages_to_process):
                try:
                    text = page.extract_text() or ""
                    page_texts.append(text)
                except Exception as exc:
                    failed_pages.append((page_index, type(exc).__name__, str(exc)))
                    logger.warning(
                        "pdf_page_extraction_failed",
                        filepath=filepath,
                        page_index=page_index,
                        error_type=type(exc).__name__,
                        error_message=str(exc),
                    )
                    # Append empty string so indices align with page_texts.
                    page_texts.append("")

            if failed_pages:
                summary_parts = [
                    f"page {idx} ({exc_type}: {msg})" for idx, exc_type, msg in failed_pages
                ]
                loader_warnings.append(
                    f"{len(failed_pages)} page(s) failed text extraction: "
                    + "; ".join(summary_parts)
                )

            extraction_time = time.time() - start_time

            # Combine all page texts with page separators
            full_text = "\n\n".join(page_texts)

            # Calculate average extraction speed (against pages actually attempted)
            attempted = len(pages_to_process)
            avg_speed = extraction_time / attempted if attempted > 0 else 0

            logger.info(
                "pdf_extraction_complete",
                character_count=len(full_text),
                page_count=total_pages,
                pages_attempted=attempted,
                pages_failed=len(failed_pages),
                extraction_time_seconds=round(extraction_time, 2),
                extraction_speed_per_page=round(avg_speed, 3),
            )

            logger.debug("extracted_content_preview", content_preview=full_text[:500])

            # Extract metadata from PDF if available
            pdf_metadata: dict[str, Any] = {}
            if reader.metadata:
                pdf_metadata = dict(reader.metadata) if hasattr(reader.metadata, "__iter__") else {}
            title = (
                pdf_metadata.get("title") or getattr(reader.metadata, "title", None)
                if reader.metadata
                else None
            )
            author = (
                pdf_metadata.get("author") or getattr(reader.metadata, "author", None)
                if reader.metadata
                else None
            )

            # Create document metadata
            metadata: dict[str, Any] = {
                "source": str(filepath_obj.absolute()),
                "filename": filepath_obj.name,
                "total_pages": total_pages,
                "total_characters": len(full_text),
                "extraction_method": "pypdf",
                "extraction_time_seconds": round(extraction_time, 3),
                "extraction_speed_per_page": round(avg_speed, 3),
                "format": "text",
                "structure_preserved": False,
                # Task 1: expose per-page failure count for indexing handler rollup
                "loader_pdf_pages_failed": len(failed_pages),
            }

            if title:
                metadata["title"] = title
            if author:
                metadata["author"] = author

            # Store per-page texts for vision merge (used by indexing handler)
            metadata["_page_texts"] = page_texts

            # Initial location_index covering the joined full_text. The
            # orchestrator REBUILDS this from _page_texts before chunking
            # in case vision_finalizer mutated _page_texts in the meantime
            # (appending visual-content descriptions). Emitting it here is
            # still useful for callers that don't run vision (CLI direct
            # path, tests). build_pdf_location_index is the single source
            # of truth for the math.
            from chaoscypher_core.utils.chunk import build_pdf_location_index

            metadata["location_index"] = build_pdf_location_index(page_texts)

            # Task 3 (Phase 5b): image-only PDF detection.
            # When every page returned empty text the document is likely
            # a scanned PDF — set needs_vision so the vision pipeline can
            # pick it up deliberately rather than missing it silently.
            non_empty_pages = sum(1 for t in page_texts if t.strip())
            if attempted > 0 and non_empty_pages == 0:
                metadata["needs_vision"] = True
                vision_warning = (
                    f"all {attempted} pages produced empty text; vision processing required"
                )
                loader_warnings.append(vision_warning)
                logger.info(
                    "pdf_image_only_detected",
                    filepath=filepath,
                    pages_attempted=attempted,
                )

            # Attach accumulated loader warnings (Tasks 1, 3, 4) to metadata
            # so the indexing handler can surface them via loader_warnings_count.
            if loader_warnings:
                metadata["loader_warnings"] = loader_warnings

            # Detect images per page using pypdfium2
            page_infos: list[dict[str, Any]] = []
            try:
                import pypdfium2 as pdfium  # type: ignore[import-untyped]

                pdf_doc = pdfium.PdfDocument(filepath)
                for page_idx in range(len(pdf_doc)):
                    pdf_page = pdf_doc[page_idx]
                    image_count = 0
                    for obj in pdf_page.get_objects():
                        if obj.type == pdfium.raw.FPDF_PAGEOBJ_IMAGE:
                            image_count += 1

                    page_infos.append(
                        {
                            "page_number": page_idx + 1,
                            "has_images": image_count > 0,
                            "image_count": image_count,
                        }
                    )
                pdf_doc.close()
            except ImportError:
                logger.warning("pypdfium2_not_installed", msg="Image detection unavailable")
            except Exception:
                logger.warning("image_detection_failed", filepath=filepath, exc_info=True)

            metadata["pages"] = page_infos
            image_page_count = sum(1 for p in page_infos if p.get("has_images"))
            metadata["image_page_count"] = image_page_count

            if image_page_count > 0:
                logger.info(
                    "pdf_images_detected",
                    image_page_count=image_page_count,
                    total_pages=total_pages,
                )

            return [{"content": full_text, "metadata": metadata}]

        except Exception as e:
            # Re-raise EncryptedPDFError without wrapping — it is already typed.
            from chaoscypher_core.exceptions import EncryptedPDFError

            if isinstance(e, EncryptedPDFError):
                raise
            logger.exception(
                "pdf_extraction_failed",
                filepath=filepath,
                error_type=type(e).__name__,
                error_message=str(e),
            )
            raise

    def supports_ocr(self) -> bool:
        """Check if this loader supports OCR.

        Returns:
            False - pypdf does not support OCR.

        """
        return False
