# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""reStructuredText (.rst / .rest) loader.

RST is the canonical Python ecosystem documentation format and
routinely appears as ``README.rst`` or inside Sphinx source trees.
Before this loader existed, standalone ``.rst`` uploads failed with
"no loader available."

Implementation strategy: validate the document with docutils' ``null``
writer (silences output but flags fatal parse errors), then return the
**raw** RST source as content. RST markup is sparse enough that downstream
embedding / extraction works fine on the raw form, and that avoids the
docutils-rendered output's quirks (literal-block delimiters, footnote
mangling, etc.). When docutils chokes — common when files reference
Sphinx-only directives — we still return raw text and log a warning so
the upload doesn't fail.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog
from docutils.core import publish_string

from chaoscypher_core.plugins import PluginMetadata


if TYPE_CHECKING:
    from chaoscypher_core.settings import EngineSettings


logger = structlog.get_logger(__name__)


class RSTLoader:
    """Loader for reStructuredText files."""

    @property
    def supported_extensions(self) -> list[str]:
        """File extensions this loader supports."""
        return [".rst", ".rest", ".RST", ".REST"]

    @property
    def metadata(self) -> PluginMetadata:
        """Plugin metadata."""
        return PluginMetadata(
            plugin_id="rst",
            name="reStructuredText Loader",
            description="Loads .rst / .rest files.",
            version="1.0.0",
            author="ChaosCypher",
            category="loader",
            builtin=True,
            origin="builtin",
            tags=["document", "rst"],
        )

    def __init__(self, settings: EngineSettings | None = None) -> None:
        """Initialize RST loader.

        Args:
            settings: Settings instance (not currently used).
        """
        self.settings = settings

    def load_document(self, filepath: str) -> list[dict[str, Any]]:
        """Load an RST file.

        Args:
            filepath: Path to a .rst / .rest file.

        Returns:
            A single-item list with raw RST text content and metadata.
        """
        from chaoscypher_core.services.sources.loaders.base import check_loader_file_size
        from chaoscypher_core.utils.encoding import detect_encoding

        check_loader_file_size(filepath, self.settings)

        path = Path(filepath)
        encoding_used, raw, replacement_chars_count = detect_encoding(path)

        # Validate parseability via the null writer. ``report_level=5`` /
        # ``halt_level=5`` push docutils' tolerance to its maximum so
        # unknown Sphinx directives don't raise.
        parsed_ok = True
        try:
            publish_string(
                source=raw,
                writer="null",
                settings_overrides={
                    "report_level": 5,
                    "halt_level": 5,
                },
            )
        except Exception:
            parsed_ok = False
            logger.warning(
                "rst_render_failed_fallback_raw",
                filepath=str(path),
                exc_info=True,
            )

        logger.info(
            "rst_loaded",
            filepath=str(path),
            character_count=len(raw),
            encoding_used=encoding_used,
            parsed_ok=parsed_ok,
        )

        return [
            {
                "content": raw,
                "metadata": {
                    "source": str(path),
                    "extraction_method": "rst_parse" if parsed_ok else "rst_raw",
                    "encoding_used": encoding_used,
                    "replacement_chars_count": replacement_chars_count,
                    "content_type": "text/x-rst",
                },
            }
        ]

    def supports_ocr(self) -> bool:
        """RST files don't need OCR."""
        return False


__all__ = ["RSTLoader"]
