# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Sphinx HTML Documentation Handler.

Processes Sphinx-generated HTML documentation, extracting main content
from HTML files while preserving document hierarchy.

Uses BeautifulSoup for precise HTML parsing (NOT trafilatura, which
strips technical content like parameter tables and code blocks).

Example:
    from chaoscypher_core.services.sources.loaders.archive.handlers import (
        SphinxHTMLHandler,
    )

    handler = SphinxHTMLHandler(settings)
    score = handler.can_handle(extracted_dir)
    if score > 0:
        documents = handler.process(extracted_dir, settings)
"""

from __future__ import annotations

import fnmatch
from typing import TYPE_CHECKING, Any, ClassVar

import structlog

from chaoscypher_core.plugins.base import PluginMetadata


if TYPE_CHECKING:
    from pathlib import Path

    from chaoscypher_core.settings import EngineSettings


logger = structlog.get_logger(__name__)


class SphinxHTMLHandler:
    """Handler for Sphinx HTML documentation.

    Features:
    - Extracts main content using BeautifulSoup with precise selectors
    - Preserves document hierarchy from file paths
    - Skips non-content pages (genindex, search, etc.)
    - Handles cross-references and internal links

    Detection Indicators:
    - _static/ directory
    - genindex.html
    - searchindex.js
    - .doctrees/ directory (if present)
    - sphinx_rtd_theme CSS files
    """

    metadata: ClassVar[PluginMetadata] = PluginMetadata(
        name="sphinx_html",
        version="1.0.0",
        description="Sphinx/ReadTheDocs HTML documentation.",
        priority=10,
    )

    # CSS selectors for content extraction (in priority order)
    CONTENT_SELECTORS: ClassVar[list[str]] = [
        "div[itemprop='articleBody']",
        "div.body",
        "div.document",
        "article",
        "main",
    ]

    # Selectors to strip from content
    STRIP_SELECTORS: ClassVar[list[str]] = [
        "a.headerlink",  # ¶ symbols in Python docs
        "script",
        "style",
    ]

    @property
    def name(self) -> str:
        """Handler identifier."""
        return "sphinx_html"

    def __init__(self, settings: EngineSettings | None = None) -> None:
        """Initialize Sphinx handler.

        Args:
            settings: Engine settings for configuration.
        """
        self.settings = settings
        # Cap how deep we walk when looking for a nested Sphinx build. Real
        # source archives park their generated HTML at depths like
        # ``docs/_build/html/`` (depth 3); the default of 5 leaves slack
        # without rglob-ing through gigantic archives. Configurable via
        # ``settings.archive.max_walk_depth``.
        self._max_walk_depth: int = settings.archive.max_walk_depth if settings is not None else 5
        # Phase 7 audit-remediation (2026-05-09): lifted from the
        # SKIP_PATTERNS ClassVar so operators can override via
        # LoaderSettings.sphinx_skip_patterns.
        self._skip_patterns: list[str] = (
            list(settings.loader.sphinx_skip_patterns)
            if settings is not None
            else [
                "genindex.html",
                "search.html",
                "searchindex.js",
                "objects.inv",
                "_static/*",
                "_sources/*",
                "_modules/*",
                "_images/*",
                ".buildinfo",
            ]
        )
        # Memoize the best candidate per extracted_dir so can_handle() and
        # find_root() share one subtree scan. Keyed by resolved absolute
        # path (same directory may be handed in with different casings).
        self._scan_cache: dict[Path, tuple[int, Path]] = {}

    def can_handle(self, extracted_dir: Path) -> int:
        """Check for Sphinx HTML indicators, including nested docs roots.

        Walks up to ``settings.archive.max_walk_depth`` levels (default 5)
        to find the subdirectory with the strongest Sphinx signal (e.g.
        ``docs/_build/html/`` inside a source archive). Without this walk
        a Sphinx build nested under an outer directory would score ``0`` at
        the top level and fall through to :class:`GenericHandler`.

        Args:
            extracted_dir: Path to extracted archive contents.

        Returns:
            Specificity score (0 if below threshold, 50-100 on match). Score
            reflects the accumulated indicator weight from the best
            candidate directory.
        """
        score, _ = self._best_candidate(extracted_dir)
        return score

    def find_root(self, extracted_dir: Path) -> Path:
        """Return the subdirectory that actually holds the Sphinx build.

        Returns ``extracted_dir`` unchanged when no nested Sphinx root was
        found (e.g. the archive is already rooted at the docs or no
        handler indicators exist — the caller will just pass the original
        directory through to :meth:`process`).

        Args:
            extracted_dir: Path to extracted archive contents.

        Returns:
            The directory whose immediate children include the strongest
            Sphinx markers, or ``extracted_dir`` when nothing scored above
            the detection threshold.
        """
        score, root = self._best_candidate(extracted_dir)
        if score <= 0:
            return extracted_dir
        return root

    def _best_candidate(self, extracted_dir: Path) -> tuple[int, Path]:
        """Walk the subtree and return ``(score, root)`` for the strongest hit.

        Replicates the nested-root discovery the retired
        ``DocumentationDetector._detect_sphinx`` performed: every
        ``index.html`` under ``extracted_dir`` (down to
        ``settings.archive.max_walk_depth`` levels) is inspected for Sphinx
        indicators, and the directory with the highest accumulated confidence
        wins. Results are memoized so ``can_handle`` and ``find_root`` share
        one scan per archive.
        """
        cache_key = extracted_dir.resolve() if extracted_dir.exists() else extracted_dir
        cached = self._scan_cache.get(cache_key)
        if cached is not None:
            return cached

        best_score = 0
        best_root = extracted_dir
        best_indicators: list[str] = []

        # Enumerate every potential root: the top-level directory plus any
        # subdirectory containing an ``index.html`` within the depth cap.
        for candidate in self._candidate_roots(extracted_dir):
            confidence, indicators = self._score_candidate(candidate)
            if confidence < 0.5:
                continue
            score = int(min(confidence, 1.0) * 100)
            if score > best_score:
                best_score = score
                best_root = candidate
                best_indicators = indicators

        if best_indicators:
            logger.debug(
                "sphinx_detection_result",
                score=best_score,
                indicators=best_indicators,
                root=str(best_root),
            )

        result = (best_score, best_root)
        self._scan_cache[cache_key] = result
        return result

    # Filenames whose presence in a subdirectory marks it as a potential
    # Sphinx build root. ``index.html`` covers every normal Sphinx build;
    # ``searchindex.js`` / ``genindex.html`` / ``objects.inv`` catch
    # partial builds or fixtures that lack ``index.html``.
    _ROOT_MARKER_FILES: ClassVar[tuple[str, ...]] = (
        "index.html",
        "searchindex.js",
        "genindex.html",
        "objects.inv",
    )

    def _candidate_roots(self, extracted_dir: Path) -> list[Path]:
        """Yield directories to check for Sphinx indicators.

        The archive root is always considered. Additionally any directory
        within ``settings.archive.max_walk_depth`` levels that contains a
        Sphinx marker file (``index.html``, ``searchindex.js``,
        ``genindex.html``, or ``objects.inv``) becomes a candidate. This
        mirrors the retired ``DocumentationDetector``'s nested-root walk
        without scanning every HTML file in a large archive.
        """
        candidates: list[Path] = [extracted_dir]
        if not extracted_dir.is_dir():
            return candidates

        seen: set[Path] = {extracted_dir}
        for marker in self._ROOT_MARKER_FILES:
            # rglob is unbounded; enforce _max_walk_depth ourselves so large
            # archives don't trigger pathological scans.
            for match in extracted_dir.rglob(marker):
                parent = match.parent
                try:
                    depth = len(parent.relative_to(extracted_dir).parts)
                except ValueError:
                    continue
                if depth > self._max_walk_depth:
                    continue
                if parent in seen:
                    continue
                seen.add(parent)
                candidates.append(parent)

        return candidates

    def _score_candidate(self, candidate: Path) -> tuple[float, list[str]]:
        """Compute Sphinx confidence + indicator list for a single directory.

        Mirrors the indicator weights the retired
        :class:`DocumentationDetector` used for ``_detect_sphinx`` so
        detection thresholds stay compatible across the refactor.
        """
        indicators_found: list[str] = []
        confidence = 0.0

        if (candidate / "_static").is_dir():
            indicators_found.append("_static/ directory")
            confidence += 0.3

        if (candidate / "genindex.html").exists():
            indicators_found.append("genindex.html")
            confidence += 0.25

        if (candidate / "searchindex.js").exists():
            indicators_found.append("searchindex.js")
            confidence += 0.25

        if (candidate / ".doctrees").is_dir():
            indicators_found.append(".doctrees/ directory")
            confidence += 0.1

        static_dir = candidate / "_static"
        if static_dir.is_dir():
            for css_file in static_dir.glob("*.css"):
                if "sphinx" in css_file.name.lower() or "rtd" in css_file.name.lower():
                    indicators_found.append(f"Sphinx theme CSS: {css_file.name}")
                    confidence += 0.1
                    break

        return confidence, indicators_found

    def process(
        self,
        extracted_dir: Path,
        settings: EngineSettings,
    ) -> list[dict[str, Any]]:
        """Process Sphinx HTML documentation.

        Args:
            extracted_dir: Path to extracted archive.
            settings: Engine settings.

        Returns:
            List of document chunks, one per HTML file.
        """
        logger.info("sphinx_processing_started", directory=str(extracted_dir))

        documents: list[dict[str, Any]] = []
        files_skipped = 0

        # Find all HTML files
        html_files = list(extracted_dir.rglob("*.html"))
        html_files.extend(extracted_dir.rglob("*.htm"))

        logger.debug("sphinx_html_files_found", count=len(html_files))

        for html_path in html_files:
            # Check if file should be skipped
            if self._should_skip(html_path, extracted_dir):
                logger.debug("sphinx_file_skipped", file=str(html_path))
                files_skipped += 1
                continue

            try:
                content, metadata = self._extract_html_content(html_path, extracted_dir)

                if content and content.strip():
                    documents.append(
                        {
                            "content": content,
                            "metadata": metadata,
                        }
                    )
                    logger.debug(
                        "sphinx_file_processed",
                        file=str(html_path),
                        content_length=len(content),
                    )
                else:
                    logger.debug("sphinx_file_empty", file=str(html_path))
                    files_skipped += 1

            except Exception as e:
                logger.warning(
                    "sphinx_file_processing_failed",
                    file=str(html_path),
                    error=str(e),
                    exc_info=True,
                )
                files_skipped += 1
                continue

        logger.info(
            "sphinx_processing_complete",
            documents_count=len(documents),
            files_processed=len(html_files),
            files_skipped=files_skipped,
        )

        # Surface per-file skip count via the first surviving document's
        # metadata so the indexing handler can roll it up onto the source row.
        if documents and files_skipped > 0:
            first_meta = documents[0].setdefault("metadata", {})
            if isinstance(first_meta, dict):
                first_meta["loader_files_skipped"] = (
                    int(first_meta.get("loader_files_skipped", 0) or 0) + files_skipped
                )

        # When every file was skipped (no surviving documents), emit a
        # synthetic empty-content doc with a loader_warnings entry so the
        # user sees a meaningful failure rather than a generic empty-content
        # error.
        if not documents and files_skipped > 0:
            documents.append(
                {
                    "content": "",
                    "metadata": {
                        "loader_files_skipped": files_skipped,
                        "loader_warnings": [
                            f"All {files_skipped} files were skipped during processing."
                        ],
                        "doc_type": self.name,
                    },
                }
            )

        return documents

    def _should_skip(self, file_path: Path, base_dir: Path) -> bool:
        """Check if file should be skipped (non-content page).

        Args:
            file_path: Path to the file.
            base_dir: Base directory for relative path calculation.

        Returns:
            True if file should be skipped.
        """
        relative_path = file_path.relative_to(base_dir)
        relative_str = str(relative_path).replace("\\", "/")

        for pattern in self._skip_patterns:
            if fnmatch.fnmatch(relative_str, pattern):
                return True
            if fnmatch.fnmatch(file_path.name, pattern):
                return True

        return False

    def _extract_html_content(
        self,
        html_path: Path,
        base_dir: Path,
    ) -> tuple[str, dict[str, Any]]:
        """Extract main content from HTML file using BeautifulSoup.

        Args:
            html_path: Path to HTML file.
            base_dir: Base directory for hierarchy calculation.

        Returns:
            Tuple of (content_text, metadata_dict).
        """
        from chaoscypher_core.utils.encoding import detect_encoding

        try:
            from bs4 import BeautifulSoup
        except ImportError:
            logger.warning("beautifulsoup4_not_installed", fallback="raw_text")
            # Fallback: return raw text without HTML parsing
            encoding_used, content, replacement_chars_count = detect_encoding(html_path)
            metadata = self._build_metadata(html_path, base_dir, None)
            metadata["encoding_used"] = encoding_used
            metadata["replacement_chars_count"] = replacement_chars_count
            return content, metadata

        encoding_used, html_content, replacement_chars_count = detect_encoding(html_path)
        soup = BeautifulSoup(html_content, "html.parser")

        # Extract title
        title = self._extract_title(soup)

        # Find main content using selectors (priority order)
        main_content = None
        for selector in self.CONTENT_SELECTORS:
            main_content = soup.select_one(selector)
            if main_content:
                break

        if not main_content:
            # Fallback to body
            main_content = soup.body

        if not main_content:
            empty_metadata = self._build_metadata(html_path, base_dir, title)
            empty_metadata["encoding_used"] = encoding_used
            empty_metadata["replacement_chars_count"] = replacement_chars_count
            return "", empty_metadata

        # Strip unwanted elements
        for strip_selector in self.STRIP_SELECTORS:
            for element in main_content.select(strip_selector):
                element.decompose()

        # Extract text content
        text = main_content.get_text(separator="\n", strip=True)

        # Clean up multiple newlines
        import re

        text = re.sub(r"\n{3,}", "\n\n", text)

        metadata = self._build_metadata(html_path, base_dir, title)
        metadata["encoding_used"] = encoding_used
        metadata["replacement_chars_count"] = replacement_chars_count

        return text, metadata

    def _extract_title(self, soup: Any) -> str | None:
        """Extract document title from HTML.

        Args:
            soup: BeautifulSoup object.

        Returns:
            Title string or None.
        """
        # Try <title> tag
        if soup.title:
            from typing import cast

            title = soup.title.get_text(strip=True)
            # Clean up Sphinx title format (e.g., "Page Title — Project Name")
            if " — " in title:
                title = title.split(" — ")[0].strip()
            if " - " in title:
                title = title.split(" - ")[0].strip()
            return cast("str", title)

        # Try first h1
        h1 = soup.find("h1")
        if h1:
            return cast("str", h1.get_text(strip=True))

        return None

    def _build_metadata(
        self,
        file_path: Path,
        base_dir: Path,
        title: str | None,
    ) -> dict[str, Any]:
        """Build metadata dictionary for document.

        Args:
            file_path: Path to the source file.
            base_dir: Base directory for hierarchy calculation.
            title: Extracted document title.

        Returns:
            Metadata dictionary.
        """
        relative_path = file_path.relative_to(base_dir)
        hierarchy = str(relative_path.parent).replace("\\", "/")

        if hierarchy == ".":
            hierarchy = ""

        return {
            "source": str(relative_path).replace("\\", "/"),
            "filename": file_path.name,
            "hierarchy": hierarchy,
            "doc_type": self.name,
            "title": title,
        }


__all__ = ["SphinxHTMLHandler"]
