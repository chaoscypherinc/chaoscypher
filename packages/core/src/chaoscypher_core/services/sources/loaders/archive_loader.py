# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Documentation Archive Loader.

Loads documentation archives (ZIP/TAR.GZ) containing:
- Sphinx HTML documentation
- Markdown documentation (MkDocs, Docusaurus, etc.)
- OpenAPI specifications
- Mixed files (fallback to per-file processing)

Auto-discovered by LoaderRegistry. Extraction happens here; format-specific
handler selection is delegated to :class:`ArchiveHandlerRegistry`, which
walks every registered handler (built-ins + user plugins under
``{data_dir}/plugins/archive_handlers/``) and picks the one whose
``can_handle()`` reports the highest specificity score.

Example:
    from chaoscypher_core.services.sources.loaders import LoaderRegistry

    registry = LoaderRegistry(settings)
    documents = registry.load_document("docs.zip")
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog


if TYPE_CHECKING:
    from chaoscypher_core.settings import EngineSettings

from chaoscypher_core.exceptions import NotFoundError, OperationError
from chaoscypher_core.services.sources.loaders.archive import ArchiveExtractor
from chaoscypher_core.services.sources.loaders.archive.handlers.registry import (
    ArchiveHandlerRegistry,
)


logger = structlog.get_logger(__name__)


class ArchiveLoader:
    """Documentation archive loader for ZIP and TAR.GZ files.

    Auto-discovered by LoaderRegistry via *_loader.py naming convention.

    Workflow:
    1. Extract archive to temp directory
    2. Find the most specific handler via :class:`ArchiveHandlerRegistry`
    3. Process with that handler
    4. Collect and return document chunks
    5. Cleanup temp directory

    Supported Formats:
    - Sphinx HTML: Static HTML documentation with _static/, genindex.html
    - Markdown: Directories with multiple .md/.mdx files
    - OpenAPI: Swagger/OpenAPI JSON or YAML specifications
    - Generic: Mixed files processed individually via LoaderRegistry

    Supported Extensions:
    - .zip, .ZIP
    - .tar.gz, .tgz, .TGZ
    """

    @property
    def supported_extensions(self) -> list[str]:
        """File extensions this loader supports."""
        return [".zip", ".ZIP", ".tar.gz", ".tgz", ".TGZ"]

    def __init__(self, settings: EngineSettings) -> None:
        """Initialize archive loader.

        Args:
            settings: Engine settings for configuration. Forwarded to the
                extractor and to :class:`ArchiveHandlerRegistry` so user
                plugins under ``{settings.paths.data_dir}/plugins/archive_handlers/``
                participate in handler selection.
        """
        self.settings = settings
        self._extractor = ArchiveExtractor(settings=settings)
        self._handler_registry = ArchiveHandlerRegistry(settings=settings)

    def load_document(
        self,
        filepath: str,
    ) -> list[dict[str, Any]]:
        """Load documentation archive.

        Args:
            filepath: Path to archive file.

        Returns:
            List of document chunks with content and metadata.

        Raises:
            NotFoundError: If archive doesn't exist.
            OperationError: If no handler claims the extracted directory.
            ArchiveExtractionError: If extraction fails.
            ArchiveSecurityError: If archive contains unsafe paths.
        """
        archive_path = Path(filepath)

        if not archive_path.exists():
            raise NotFoundError("Archive", filepath)

        logger.info(
            "archive_loading_started",
            filepath=filepath,
            archive_size=archive_path.stat().st_size,
        )

        # Create temporary directory for extraction
        temp_dir = None
        try:
            temp_dir = Path(tempfile.mkdtemp(prefix="chaoscypher_archive_"))

            # Step 1: Extract archive
            logger.debug("archive_extracting", temp_dir=str(temp_dir))
            extracted_dir = self._extractor.extract(archive_path, temp_dir)

            # Step 2: Select handler via registry (built-ins + user plugins)
            logger.debug("archive_selecting_handler")
            handler = self._handler_registry.find_handler(extracted_dir)

            if handler is None:
                # GenericHandler always returns 10, so this should not happen
                # unless the registry was deliberately emptied. Fail loudly
                # rather than silently returning nothing.
                msg = f"No archive handler claimed {extracted_dir}"
                raise OperationError(msg, operation="archive_load")

            # Recompute score for metadata (find_handler already called
            # can_handle but doesn't expose the score).
            try:
                handler_score = handler.can_handle(extracted_dir)
            except Exception:
                logger.warning(
                    "archive_handler_score_recompute_failed",
                    handler=handler.metadata.name,
                    exc_info=True,
                )
                handler_score = 0

            # Step 3: Let the handler narrow the root before processing.
            # Sphinx/MkDocs archives often bury their docs under nested
            # directories (``docs/_build/html/``, ``docs/``), and handlers
            # need to see that narrower root so relative paths, asset
            # resolution, and file walks all key off the real docs root.
            try:
                processing_dir = handler.find_root(extracted_dir)
            except Exception:
                logger.warning(
                    "archive_handler_find_root_failed",
                    handler=handler.metadata.name,
                    exc_info=True,
                )
                processing_dir = extracted_dir

            # Step 4: Process with handler using the narrowed root.
            logger.debug(
                "archive_processing",
                handler=handler.name,
                processing_dir=str(processing_dir),
                archive_root=str(extracted_dir),
                score=handler_score,
            )
            documents = handler.process(processing_dir, self.settings)

            # Add archive source to all documents. detection_format uses
            # handler.name (same string the loader has always surfaced via
            # the old DocFormat enum values) and detection_confidence is
            # derived from the specificity score on the 0.0-1.0 scale the
            # old DetectionResult used.
            detection_format = handler.name
            detection_confidence = max(0.0, min(handler_score / 100.0, 1.0))
            for doc in documents:
                doc["metadata"]["archive_file"] = archive_path.name
                doc["metadata"]["detection_format"] = detection_format
                doc["metadata"]["detection_confidence"] = detection_confidence

            logger.info(
                "archive_loading_complete",
                filepath=filepath,
                format=detection_format,
                confidence=detection_confidence,
                documents_count=len(documents),
            )

            return documents

        except Exception as e:
            logger.error(
                "archive_loading_failed",
                filepath=filepath,
                error=str(e),
                exc_info=True,
            )
            raise

        finally:
            # Step 4: Cleanup temp directory
            if temp_dir and temp_dir.exists():
                try:
                    shutil.rmtree(temp_dir)
                    logger.debug("archive_temp_cleanup", temp_dir=str(temp_dir))
                except Exception as cleanup_error:
                    logger.warning(
                        "archive_temp_cleanup_failed",
                        temp_dir=str(temp_dir),
                        error=str(cleanup_error),
                    )

    def supports_ocr(self) -> bool:
        """Archive loader doesn't support OCR directly.

        Individual file loaders may support OCR.
        """
        return False


__all__ = ["ArchiveLoader"]
