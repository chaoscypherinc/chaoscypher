# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Web content cleaner using trafilatura.

Specialized cleaner for web-scraped HTML content that extracts main content
and removes boilerplate (navigation, ads, footers, etc.).

Uses trafilatura for high-quality web content extraction, with fallback
to basic BeautifulSoup extraction if trafilatura is unavailable.

Example:
    from chaoscypher_core.services.sources.normalizer.cleaners import WebCleaner
    from chaoscypher_core.services.sources.normalizer.models import NormalizerSettings

    settings = NormalizerSettings()
    cleaner = WebCleaner(settings)

    html_content = '''
    <html>
    <head><title>Article</title></head>
    <body>
        <nav>Menu items</nav>
        <article>
            <h1>Main Article</h1>
            <p>Important content here.</p>
        </article>
        <footer>Copyright 2024</footer>
    </body>
    </html>
    '''

    result = cleaner.clean(html_content, {"content_type": "html"})
    # result.content contains just the article content
    # result.chars_removed reports how much boilerplate was stripped

"""

import re
from typing import ClassVar

import structlog

from chaoscypher_core.app_config import get_settings
from chaoscypher_core.plugins.base import PluginMetadata
from chaoscypher_core.services.sources.normalizer.cleaners.base import CleanerResult
from chaoscypher_core.services.sources.normalizer.models import (
    ContentType,
    NormalizerSettings,
)


logger = structlog.get_logger(__name__)


class WebCleaner:
    """Cleaner for web/HTML content using trafilatura.

    Extracts main content from HTML documents, removing boilerplate elements
    like navigation, sidebars, footers, and advertisements. Outputs clean
    text or markdown depending on settings.

    Attributes:
        settings: Configuration controlling extraction behavior.

    Example:
        cleaner = WebCleaner(NormalizerSettings())

        html = "<html><body><article><p>Content</p></article></body></html>"
        result = cleaner.clean(html, {"content_type": "html"})
        assert "trafilatura_extraction" in result.ops or "basic_html_extraction" in result.ops

    """

    metadata: ClassVar[PluginMetadata] = PluginMetadata(
        name="web_cleaner",
        version="1.0.0",
        description="HTML extraction using trafilatura.",
        priority=30,
    )

    def __init__(self, settings: NormalizerSettings) -> None:
        """Initialize the web cleaner.

        Args:
            settings: Normalizer settings controlling cleaner behavior.

        """
        self.settings = settings
        self._trafilatura_available: bool | None = None

    @property
    def name(self) -> str:
        """Return the cleaner name."""
        return "web_cleaner"

    def clean(self, content: str, metadata: dict | None = None) -> CleanerResult:
        """Clean web/HTML content by extracting main text.

        Only processes content if metadata indicates HTML or web content type.
        For non-HTML content, returns the input unchanged.

        Args:
            content: The HTML content to extract from.
            metadata: Must contain 'content_type' as 'html' or 'web' to trigger
                extraction. Otherwise content is returned unchanged.

        Returns:
            :class:`CleanerResult` with the extracted content and ops list.
            ``chars_removed`` reports the net before/after length delta (the
            stripped boilerplate); ``lines_removed`` and
            ``paragraphs_deduplicated`` stay 0 — the web cleaner extracts
            main content rather than removing per-line / per-paragraph
            artifacts.

        """
        if not content:
            return CleanerResult(content=content)

        # Only process HTML/web content
        metadata = metadata or {}
        content_type = metadata.get("content_type", "")

        # Check if this is HTML content
        is_html = content_type in (
            ContentType.HTML,
            ContentType.WEB,
            "html",
            "web",
        ) or self._looks_like_html(content)

        if not is_html:
            return CleanerResult(content=content)

        operations: list[str] = []

        # Try trafilatura first (best quality)
        if self._is_trafilatura_available():
            extracted = self._extract_with_trafilatura(content)
            if extracted:
                operations.append("trafilatura_extraction")
                logger.debug(
                    "web_extraction_complete",
                    method="trafilatura",
                    original_length=len(content),
                    extracted_length=len(extracted),
                )
                return CleanerResult(
                    content=extracted,
                    ops=operations,
                    chars_removed=max(0, len(content) - len(extracted)),
                )

        # Fallback to basic extraction
        extracted = self._extract_basic(content)
        if extracted and extracted != content:
            operations.append("basic_html_extraction")
            logger.debug(
                "web_extraction_complete",
                method="basic",
                original_length=len(content),
                extracted_length=len(extracted),
            )
            return CleanerResult(
                content=extracted,
                ops=operations,
                chars_removed=max(0, len(content) - len(extracted)),
            )

        return CleanerResult(content=content, ops=operations)

    def _looks_like_html(self, content: str) -> bool:
        """Check if content appears to be HTML.

        Args:
            content: Content to check.

        Returns:
            True if content appears to be HTML.

        """
        # Check for common HTML indicators
        html_patterns = [
            r"<!DOCTYPE\s+html",
            r"<html[\s>]",
            r"<head[\s>]",
            r"<body[\s>]",
            r"<div[\s>]",
            r"<p[\s>]",
            r"<article[\s>]",
        ]

        # Use half the content-type detection window (preserves original
        # 1000 vs 2000 ratio against normalizer/service.py).
        content_start = content[
            : get_settings().web.content_type_detection_window_bytes // 2
        ].lower()
        return any(re.search(pattern, content_start, re.IGNORECASE) for pattern in html_patterns)

    def _is_trafilatura_available(self) -> bool:
        """Check if trafilatura is available.

        Returns:
            True if trafilatura can be imported.

        """
        if self._trafilatura_available is None:
            try:
                import trafilatura  # noqa: F401

                self._trafilatura_available = True
            except ImportError:
                logger.warning(
                    "trafilatura_not_installed",
                    message="Install trafilatura for better web extraction",
                )
                self._trafilatura_available = False

        return self._trafilatura_available

    def _extract_with_trafilatura(self, html: str) -> str | None:
        """Extract main content using trafilatura.

        Args:
            html: HTML content to extract from.

        Returns:
            Extracted text content, or None if extraction failed.

        """
        try:
            import trafilatura

            # Determine output format based on settings
            output_format = "markdown" if self.settings.target_format == "markdown" else "txt"

            # Extract with trafilatura — use operator-configured settings
            # (Phase 6, 2026-05-08) so precision/recall and content inclusions
            # are tunable without code changes.
            return trafilatura.extract(
                html,
                output_format=output_format,
                include_comments=self.settings.web_trafilatura_include_comments,
                include_tables=True,
                include_images=self.settings.web_trafilatura_include_images,
                include_links=True,
                deduplicate=True,
                favor_precision=self.settings.web_trafilatura_favor_precision,
            )

        except Exception:
            logger.exception("trafilatura_extraction_failed")
            return None

    def _extract_basic(self, html: str) -> str:
        """Basic HTML extraction fallback using regex.

        Simple extraction that removes HTML tags and normalizes whitespace.
        Used when trafilatura is not available.

        Args:
            html: HTML content to extract from.

        Returns:
            Extracted text content.

        """
        # Remove script and style elements completely
        text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)

        # Remove chrome elements — configurable via web_basic_strip_tags
        # (Phase 6, 2026-05-08). Previous hardcoded list: nav, footer, header, aside.
        strip_tags = (
            list(self.settings.web_basic_strip_tags)
            if self.settings
            else ["nav", "footer", "header", "aside"]
        )
        for tag in strip_tags:
            text = re.sub(rf"<{tag}[^>]*>.*?</{tag}>", "", text, flags=re.DOTALL | re.IGNORECASE)

        # Convert common block elements to newlines
        text = re.sub(r"<(p|div|br|h[1-6]|li|tr)[^>]*>", "\n", text, flags=re.IGNORECASE)

        # Remove all remaining HTML tags
        text = re.sub(r"<[^>]+>", "", text)

        # Decode common HTML entities
        html_entities = {
            "&nbsp;": " ",
            "&amp;": "&",
            "&lt;": "<",
            "&gt;": ">",
            "&quot;": '"',
            "&#39;": "'",
            "&apos;": "'",
        }
        for entity, char in html_entities.items():
            text = text.replace(entity, char)

        # Normalize whitespace
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n\s*\n", "\n\n", text)
        return text.strip()


__all__ = ["WebCleaner"]
