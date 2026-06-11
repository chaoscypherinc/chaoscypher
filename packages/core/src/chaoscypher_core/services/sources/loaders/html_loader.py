# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Standalone HTML file loader.

Strips chrome elements (``script``, ``style``, ``nav``, ``aside``,
``footer``, ``header``, ``noscript``) to extract clean prose. This is
more aggressive than the archive Sphinx handler (which only strips
``script`` / ``style`` plus ``.headerlink`` anchors) because standalone
HTML uploads are typically blog posts or article pages where the
user's intent is to extract the main content. If you need to preserve
sidebars or footers, use the archive workflow instead.

Captures ``<title>`` in metadata and decomposes it from the soup so
the title text doesn't appear twice in extracted content. Routes
through the shared ``detect_encoding`` helper so legacy cp1252 /
Latin-1 HTML (Windows IE-saved pages, older corporate sites) keeps
its characters intact.

Added because a single ``.html`` file uploaded standalone previously
had no loader and the upload failed with "no loader available."
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog
from bs4 import BeautifulSoup

from chaoscypher_core.plugins import PluginMetadata


if TYPE_CHECKING:
    from chaoscypher_core.settings import EngineSettings


logger = structlog.get_logger(__name__)

# Tags whose content is never user-visible body text and would only add
# noise to extraction (script/style are pure code; the rest are
# layout/navigation chrome).
_DROP_TAGS = ("script", "style", "nav", "aside", "footer", "header", "noscript")


class HTMLLoader:
    """Loader for standalone ``.html`` / ``.htm`` / ``.xhtml`` files."""

    @property
    def supported_extensions(self) -> list[str]:
        """File extensions this loader supports."""
        return [".html", ".htm", ".xhtml", ".HTML", ".HTM", ".XHTML"]

    @property
    def metadata(self) -> PluginMetadata:
        """Plugin metadata."""
        return PluginMetadata(
            plugin_id="html",
            name="HTML Loader",
            description="Loads .html / .htm / .xhtml files.",
            version="1.0.0",
            author="ChaosCypher",
            category="loader",
            builtin=True,
            origin="builtin",
            tags=["document", "html"],
        )

    def __init__(self, settings: EngineSettings | None = None) -> None:
        """Initialize HTML loader.

        Args:
            settings: Settings instance (not currently used).
        """
        self.settings = settings

    def load_document(self, filepath: str) -> list[dict[str, Any]]:
        """Load an HTML file and return its visible text plus title metadata.

        Args:
            filepath: Path to an .html / .htm / .xhtml file.

        Returns:
            A single-item list with keys ``content`` (extracted text) and
            ``metadata`` (filepath, extraction_method, encoding_used,
            title, content_type).
        """
        from chaoscypher_core.utils.encoding import detect_encoding

        path = Path(filepath)
        encoding_used, raw, replacement_chars_count = detect_encoding(path)

        soup = BeautifulSoup(raw, "html.parser")
        dropped_per_tag: dict[str, int] = {}
        for tag_name in _DROP_TAGS:
            for tag in soup.find_all(tag_name):
                tag.decompose()
                dropped_per_tag[tag_name] = dropped_per_tag.get(tag_name, 0) + 1

        title = ""
        if soup.title and soup.title.string:
            title = soup.title.string.strip()
            # Remove from body to avoid the title text appearing twice
            # in extracted content (once in metadata, once in body).
            soup.title.decompose()

        # Convert <br> to newlines so paragraph breaks survive in text.
        for br in soup.find_all("br"):
            br.replace_with("\n")
        text = soup.get_text(separator="\n", strip=True)

        logger.info(
            "html_loaded",
            filepath=str(path),
            character_count=len(text),
            encoding_used=encoding_used,
            title=title,
            dropped_per_tag=dropped_per_tag,
        )

        return [
            {
                "content": text,
                "metadata": {
                    "source": str(path),
                    "extraction_method": "html_parse",
                    "encoding_used": encoding_used,
                    "replacement_chars_count": replacement_chars_count,
                    "title": title,
                    "content_type": "text/html",
                    "loader_html_dropped_tags": dropped_per_tag,
                },
            }
        ]

    def supports_ocr(self) -> bool:
        """HTML files don't need OCR."""
        return False


__all__ = ["HTMLLoader"]
