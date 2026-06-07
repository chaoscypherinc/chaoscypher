# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Markdown Documentation Handler.

Processes directories of Markdown files, stripping frontmatter and
normalizing content while preserving document structure.

Example:
    from chaoscypher_core.services.sources.loaders.archive.handlers import (
        MarkdownHandler,
    )

    handler = MarkdownHandler(settings)
    score = handler.can_handle(extracted_dir)
    if score > 0:
        documents = handler.process(extracted_dir, settings)
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, ClassVar

import structlog

from chaoscypher_core.plugins.base import PluginMetadata


if TYPE_CHECKING:
    from pathlib import Path

    from chaoscypher_core.settings import EngineSettings


logger = structlog.get_logger(__name__)


class MarkdownHandler:
    """Handler for Markdown documentation (MkDocs, Docusaurus, etc.).

    Features:
    - Processes .md and .mdx files
    - Strips YAML/TOML frontmatter
    - Preserves heading structure
    - Handles docs/ subdirectory convention

    Detection Indicators:
    - 10+ .md/.mdx files
    - docs/ directory with markdown
    - mkdocs.yml or docusaurus.config.js
    - README.md as entry point
    """

    metadata: ClassVar[PluginMetadata] = PluginMetadata(
        name="markdown_docs",
        version="1.0.0",
        description="Markdown documentation archives (MkDocs, Docusaurus, etc.).",
        priority=5,
    )

    # File patterns to process
    PATTERNS: ClassVar[list[str]] = [".md", ".mdx"]

    # Skip these files
    SKIP_FILES: ClassVar[set[str]] = {
        ".DS_Store",
        "Thumbs.db",
        "CHANGELOG.md",
        "CONTRIBUTING.md",
        "CODE_OF_CONDUCT.md",
    }

    @property
    def name(self) -> str:
        """Handler identifier."""
        return "markdown"

    def __init__(self, settings: EngineSettings | None = None) -> None:
        """Initialize Markdown handler.

        Args:
            settings: Engine settings for configuration.
        """
        self.settings = settings
        # Depth cap for the nested-config walk. ``mkdocs.yml`` /
        # ``docusaurus.config.js`` live at the site root; the default of 5
        # levels lets us look through an outer wrapper dir or two without
        # thrashing on deep source trees. Configurable via
        # ``settings.archive.max_walk_depth``.
        self._max_walk_depth: int = settings.archive.max_walk_depth if settings is not None else 5
        # Minimum file count and confidence for a candidate to be accepted.
        # Configurable via ``settings.archive.markdown_min_files`` /
        # ``settings.archive.markdown_min_confidence``.
        self._markdown_min_files: int = (
            settings.archive.markdown_min_files if settings is not None else 5
        )
        self._markdown_min_confidence: float = (
            settings.archive.markdown_min_confidence if settings is not None else 0.5
        )
        # Phase 6 (2026-05-08): configurable skip lists. The instance-level
        # sets are populated from LoaderSettings so operators can override
        # via settings.yaml or env vars.
        # Phase 7 audit-remediation (2026-05-09): SKIP_DIRS was also lifted
        # from a ClassVar to LoaderSettings.markdown_skip_dirs.
        self._skip_files: set[str] = (
            set(settings.loader.markdown_skip_files) | {".DS_Store", "Thumbs.db"}
            if settings is not None
            else set(self.SKIP_FILES)
        )
        self._skip_dirs: set[str] = (
            set(settings.loader.markdown_skip_dirs)
            if settings is not None
            else {
                "node_modules",
                ".git",
                "__pycache__",
                "venv",
                ".venv",
                "dist",
                "build",
                ".next",
                ".nuxt",
                "coverage",
                ".cache",
                ".idea",
                ".vscode",
            }
        )
        # Memoize the resolved root so can_handle() and find_root() share
        # the same subtree analysis. Keyed by resolved absolute path.
        self._scan_cache: dict[Path, tuple[int, Path]] = {}

    def can_handle(self, extracted_dir: Path) -> int:
        """Check for Markdown documentation indicators, including nested roots.

        Walks for MkDocs/Docusaurus config files within
        ``settings.archive.max_walk_depth`` levels (default 5) so a site
        bundled inside a wrapper directory still scores. Without this, an
        archive whose only Markdown layout lives in a ``site/`` subfolder
        would fall through to :class:`GenericHandler` and lose the
        Markdown-specific parsing.

        Args:
            extracted_dir: Path to extracted archive contents.

        Returns:
            Specificity score (0 if below threshold, 50-95 on match).
        """
        score, _ = self._best_candidate(extracted_dir)
        return score

    def find_root(self, extracted_dir: Path) -> Path:
        """Return the directory that actually holds the Markdown docs site.

        Picks the ``docs/`` subfolder when the majority of Markdown lives
        there (the MkDocs / Docusaurus convention) and falls back to the
        directory containing ``mkdocs.yml`` or ``docusaurus.config.js``.
        Returns ``extracted_dir`` unchanged when no narrower root is
        found.

        Args:
            extracted_dir: Path to extracted archive contents.

        Returns:
            Narrower docs root, or ``extracted_dir`` if nothing qualifies.
        """
        score, root = self._best_candidate(extracted_dir)
        if score <= 0:
            return extracted_dir
        return root

    def _best_candidate(self, extracted_dir: Path) -> tuple[int, Path]:
        """Walk the subtree for the strongest Markdown candidate.

        Replicates ``DocumentationDetector._detect_markdown`` — files are
        counted, and a ``docs/`` subfolder is preferred when it holds the
        bulk of the Markdown. Nested config files (``mkdocs.yml``,
        ``docusaurus.config.js``) let us recognise sites whose root lives
        under a wrapper directory. Results are memoized.
        """
        cache_key = extracted_dir.resolve() if extracted_dir.exists() else extracted_dir
        cached = self._scan_cache.get(cache_key)
        if cached is not None:
            return cached

        best_score = 0
        best_root = extracted_dir
        best_indicators: list[str] = []

        for candidate in self._candidate_roots(extracted_dir):
            confidence, indicators, md_count = self._score_candidate(candidate)
            if md_count < self._markdown_min_files or confidence < self._markdown_min_confidence:
                continue
            score = int(min(confidence, 0.95) * 100)
            if score > best_score:
                best_score = score
                best_root = self._select_narrower_root(candidate, md_count)
                best_indicators = indicators

        if best_indicators:
            logger.debug(
                "markdown_detection_result",
                score=best_score,
                indicators=best_indicators,
                root=str(best_root),
            )

        result = (best_score, best_root)
        self._scan_cache[cache_key] = result
        return result

    def _candidate_roots(self, extracted_dir: Path) -> list[Path]:
        """Return directories worth scoring as potential Markdown site roots.

        Starts from the archive root, then adds the parent of any
        ``mkdocs.yml`` / ``docusaurus.config.js`` that sits within the
        depth cap — these two files are the strongest nested-root signal.
        """
        candidates: list[Path] = [extracted_dir]
        if not extracted_dir.is_dir():
            return candidates

        seen: set[Path] = {extracted_dir}
        for marker in ("mkdocs.yml", "docusaurus.config.js"):
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

    def _select_narrower_root(self, candidate: Path, md_count: int) -> Path:
        """Prefer ``candidate/docs`` when it contains the bulk of the Markdown.

        Matches the retired ``DocumentationDetector``'s rule: if ``docs/``
        holds more than half of the Markdown files we counted, treat it as
        the docs root so :meth:`process` doesn't waste time on unrelated
        repo files. Uses :meth:`_find_markdown_files` on both sides so the
        comparison is consistent across case-insensitive filesystems
        (Windows' ``rglob("*.md")`` and ``rglob("*.MD")`` otherwise return
        duplicates and skew the ratio).
        """
        docs_dir = candidate / "docs"
        if not docs_dir.is_dir():
            return candidate
        docs_md_count = len(self._find_markdown_files(docs_dir))
        if docs_md_count * 2 > md_count:
            return docs_dir
        return candidate

    def _score_candidate(self, candidate: Path) -> tuple[float, list[str], int]:
        """Compute Markdown confidence, indicators, and md file count.

        Ported from the retired detector so detection thresholds match
        the pre-refactor behaviour.
        """
        indicators_found: list[str] = []
        confidence = 0.0

        md_files = self._find_markdown_files(candidate)
        md_count = len(md_files)

        if md_count >= 10:
            indicators_found.append(f"{md_count} markdown files found")
            confidence += 0.5 + min((md_count - 10) * 0.02, 0.3)

        if (candidate / "docs").is_dir():
            docs_md_count = len(list((candidate / "docs").rglob("*.md")))
            if docs_md_count > 0:
                indicators_found.append(f"docs/ directory with {docs_md_count} MD files")
                confidence += 0.15

        if (candidate / "mkdocs.yml").exists():
            indicators_found.append("mkdocs.yml")
            confidence += 0.2

        if (candidate / "docusaurus.config.js").exists():
            indicators_found.append("docusaurus.config.js")
            confidence += 0.2

        if (candidate / "README.md").exists():
            indicators_found.append("README.md")
            confidence += 0.05

        return confidence, indicators_found, md_count

    def process(
        self,
        extracted_dir: Path,
        settings: EngineSettings,
    ) -> list[dict[str, Any]]:
        """Process Markdown documentation.

        Args:
            extracted_dir: Path to extracted archive.
            settings: Engine settings.

        Returns:
            List of document chunks, one per markdown file.
        """
        logger.info("markdown_processing_started", directory=str(extracted_dir))

        documents: list[dict[str, Any]] = []
        files_skipped = 0
        md_files = self._find_markdown_files(extracted_dir)

        logger.debug("markdown_files_found", count=len(md_files))

        for md_path in md_files:
            try:
                content, metadata = self._process_markdown_file(md_path, extracted_dir)

                if content and content.strip():
                    documents.append(
                        {
                            "content": content,
                            "metadata": metadata,
                        }
                    )
                    logger.debug(
                        "markdown_file_processed",
                        file=str(md_path),
                        content_length=len(content),
                    )
                else:
                    logger.debug("markdown_file_empty", file=str(md_path))
                    files_skipped += 1

            except Exception as e:
                logger.warning(
                    "markdown_file_processing_failed",
                    file=str(md_path),
                    error=str(e),
                    exc_info=True,
                )
                files_skipped += 1
                continue

        logger.info(
            "markdown_processing_complete",
            documents_count=len(documents),
            files_processed=len(md_files),
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

    def _find_markdown_files(self, base_dir: Path) -> list[Path]:
        """Find all markdown files, excluding skipped directories.

        Args:
            base_dir: Base directory to search.

        Returns:
            List of markdown file paths.
        """
        md_files: list[Path] = []

        for pattern in ["*.md", "*.mdx", "*.MD", "*.MDX"]:
            for file_path in base_dir.rglob(pattern):
                # Check if file is in a skipped directory
                if self._should_skip_path(file_path, base_dir):
                    continue

                # Check if file should be skipped (Phase 6: uses instance set)
                if file_path.name in self._skip_files:
                    continue

                md_files.append(file_path)

        return sorted(md_files)

    def _should_skip_path(self, file_path: Path, base_dir: Path) -> bool:
        """Check if path contains a skipped directory.

        Args:
            file_path: Path to check.
            base_dir: Base directory for relative path calculation.

        Returns:
            True if path should be skipped.
        """
        relative_path = file_path.relative_to(base_dir)

        for part in relative_path.parts[:-1]:  # Exclude filename
            if part in self._skip_dirs:
                return True
            if part.startswith("."):  # Skip hidden directories
                return True

        return False

    def _process_markdown_file(
        self,
        md_path: Path,
        base_dir: Path,
    ) -> tuple[str, dict[str, Any]]:
        """Process a single markdown file.

        Args:
            md_path: Path to markdown file.
            base_dir: Base directory for hierarchy calculation.

        Returns:
            Tuple of (content, metadata).
        """
        from chaoscypher_core.utils.encoding import detect_encoding

        encoding_used, raw_content, replacement_chars_count = detect_encoding(md_path)

        # Strip frontmatter
        content, frontmatter = self._strip_frontmatter(raw_content)

        # Extract title
        title = self._extract_title(content, frontmatter)

        # Build metadata
        metadata = self._build_metadata(md_path, base_dir, title, frontmatter)
        metadata["encoding_used"] = encoding_used
        metadata["replacement_chars_count"] = replacement_chars_count

        return content, metadata

    def _strip_frontmatter(self, content: str) -> tuple[str, dict[str, Any]]:
        """Strip YAML or TOML frontmatter from markdown content.

        Args:
            content: Raw markdown content.

        Returns:
            Tuple of (content_without_frontmatter, frontmatter_dict).
        """
        frontmatter: dict[str, Any] = {}

        # YAML frontmatter: ---\n...\n---
        yaml_pattern = r"^---\s*\n(.*?)\n---\s*\n"
        yaml_match = re.match(yaml_pattern, content, re.DOTALL)

        if yaml_match:
            frontmatter_text = yaml_match.group(1)
            try:
                import yaml

                frontmatter = yaml.safe_load(frontmatter_text) or {}
            except Exception:
                logger.debug("yaml_frontmatter_parse_failed")
            content = content[yaml_match.end() :]
            return content, frontmatter

        # TOML frontmatter: +++\n...\n+++
        toml_pattern = r"^\+\+\+\s*\n(.*?)\n\+\+\+\s*\n"
        toml_match = re.match(toml_pattern, content, re.DOTALL)

        if toml_match:
            # TOML parsing would require tomli, just extract as metadata
            frontmatter = {"_raw_toml": toml_match.group(1)}
            content = content[toml_match.end() :]
            return content, frontmatter

        return content, frontmatter

    def _extract_title(
        self,
        content: str,
        frontmatter: dict[str, Any],
    ) -> str | None:
        """Extract document title from frontmatter or first heading.

        Args:
            content: Markdown content (without frontmatter).
            frontmatter: Parsed frontmatter dictionary.

        Returns:
            Title string or None.
        """
        # Try frontmatter title
        if "title" in frontmatter:
            return str(frontmatter["title"])

        # Try first H1 heading
        h1_match = re.match(r"^#\s+(.+?)(?:\n|$)", content)
        if h1_match:
            return h1_match.group(1).strip()

        return None

    def _build_metadata(
        self,
        file_path: Path,
        base_dir: Path,
        title: str | None,
        frontmatter: dict[str, Any],
    ) -> dict[str, Any]:
        """Build metadata dictionary for document.

        Args:
            file_path: Path to the source file.
            base_dir: Base directory for hierarchy calculation.
            title: Extracted document title.
            frontmatter: Parsed frontmatter dictionary.

        Returns:
            Metadata dictionary.
        """
        relative_path = file_path.relative_to(base_dir)
        hierarchy = str(relative_path.parent).replace("\\", "/")

        if hierarchy == ".":
            hierarchy = ""

        metadata: dict[str, Any] = {
            "source": str(relative_path).replace("\\", "/"),
            "filename": file_path.name,
            "hierarchy": hierarchy,
            "doc_type": self.name,
            "title": title,
        }

        # Include useful frontmatter fields
        for key in ["description", "tags", "category", "author", "date"]:
            if key in frontmatter:
                metadata[key] = frontmatter[key]

        return metadata


__all__ = ["MarkdownHandler"]
