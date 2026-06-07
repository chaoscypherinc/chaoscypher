# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Text Document Loader.

Loads plain text files (.txt, .md) using simple file I/O.
Implements BaseLoader protocol for auto-discovery.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog


if TYPE_CHECKING:
    from chaoscypher_core.settings import EngineSettings


logger = structlog.get_logger(__name__)


class TextLoader:
    """Plain text file loader.

    Uses simple file I/O for UTF-8 text files.
    Supports .txt, .md, and other plain text formats.
    """

    @property
    def supported_extensions(self) -> list[str]:
        """File extensions this loader supports."""
        return [".txt", ".TXT", ".md", ".MD", ".log", ".LOG"]

    def __init__(self, settings: EngineSettings | None = None) -> None:
        """Initialize text loader.

        Args:
            settings: Settings instance (not currently used)

        """
        self.settings = settings

    def load_document(self, filepath: str) -> list[dict[str, Any]]:
        """Load text file.

        Uses :func:`chaoscypher_core.utils.encoding.detect_encoding` so that
        cp1252 / Latin-1 files keep their characters intact instead of
        having every special character replaced with ``U+FFFD``. The
        chosen encoding is recorded in the document metadata under
        ``encoding_used`` so the source's data-quality counter can
        surface it.

        Args:
            filepath: Path to text file

        Returns:
            List with single document containing full text content

        """
        from chaoscypher_core.services.sources.loaders.base import check_loader_file_size
        from chaoscypher_core.utils.encoding import detect_encoding

        check_loader_file_size(filepath, self.settings)

        logger.info("text_loading_started", filepath=filepath)

        try:
            encoding_used, text, replacement_chars_count = detect_encoding(Path(filepath))

            logger.info(
                "text_loaded",
                character_count=len(text),
                filepath=filepath,
                encoding_used=encoding_used,
            )

            return [
                {
                    "content": text,
                    "metadata": {
                        "source": filepath,
                        "extraction_method": "read_text",
                        "encoding_used": encoding_used,
                        "replacement_chars_count": replacement_chars_count,
                    },
                }
            ]

        except Exception as e:
            logger.exception(
                "text_loader_failed",
                filepath=filepath,
                error_type=type(e).__name__,
                error_message=str(e),
            )
            raise

    def supports_ocr(self) -> bool:
        """Text files don't need OCR."""
        return False


__all__ = ["TextLoader"]
