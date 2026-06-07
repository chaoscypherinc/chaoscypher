# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Generic Archive Handler (Fallback).

Processes mixed archives by routing each file to the appropriate loader
via LoaderRegistry. Used when no specific documentation format is detected.

Example:
    from chaoscypher_core.services.sources.loaders.archive.handlers import (
        GenericHandler,
    )

    handler = GenericHandler(settings)
    # Always returns a low specificity score (fallback).
    score = handler.can_handle(extracted_dir)
    documents = handler.process(extracted_dir, settings)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

import structlog

from chaoscypher_core.plugins.base import PluginMetadata


if TYPE_CHECKING:
    from pathlib import Path

    from chaoscypher_core.settings import EngineSettings


logger = structlog.get_logger(__name__)


class GenericHandler:
    """Fallback handler for mixed archives.

    Processes each file individually using the existing LoaderRegistry.
    Used when no specific documentation format (Sphinx, Markdown, OpenAPI)
    is detected.

    Features:
    - Routes each file to appropriate loader by extension
    - Skips unsupported file types with debug log
    - Handles mixed archives (PDFs + TXTs + random files)
    - Respects ignore patterns (node_modules, .git, etc.)
    """

    metadata: ClassVar[PluginMetadata] = PluginMetadata(
        name="generic",
        version="1.0.0",
        description="Generic fallback handler for mixed archives.",
        priority=1,
    )

    # Skip these directories
    SKIP_DIRS: ClassVar[set[str]] = {
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

    # Skip these file patterns
    SKIP_EXTENSIONS: ClassVar[set[str]] = {
        ".exe",
        ".dll",
        ".so",
        ".dylib",
        ".pyc",
        ".pyo",
        ".whl",
        ".egg",
        ".egg-info",
        ".class",
        ".jar",
        ".war",
        ".map",  # Source maps
        ".min.js",  # Minified JS
        ".min.css",  # Minified CSS
        ".lock",  # Lock files
    }

    SKIP_FILES: ClassVar[set[str]] = {
        ".DS_Store",
        "Thumbs.db",
        ".gitignore",
        ".gitattributes",
        ".npmrc",
        ".yarnrc",
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
    }

    @property
    def name(self) -> str:
        """Handler identifier."""
        return "generic"

    def __init__(self, settings: EngineSettings | None = None) -> None:
        """Initialize Generic handler.

        Args:
            settings: Engine settings for configuration.
        """
        self.settings = settings
        self._loader_registry = None  # Lazy initialization to avoid circular import
        # Phase 6 (2026-05-08): configurable skip lists. Instance-level sets
        # merge class-level defaults with operator overrides from LoaderSettings.
        if settings is not None:
            self._skip_dirs: set[str] = set(settings.loader.archive_skip_dirs)
            self._skip_extensions: set[str] = set(settings.loader.archive_skip_extensions)
            self._skip_files: set[str] = set(settings.loader.archive_skip_files)
        else:
            self._skip_dirs = set(self.SKIP_DIRS)
            self._skip_extensions = set(self.SKIP_EXTENSIONS)
            self._skip_files = set(self.SKIP_FILES)

    def can_handle(self, extracted_dir: Path) -> int:
        """Generic handler can always handle (it's the fallback).

        Args:
            extracted_dir: Path to extracted archive contents.

        Returns:
            Always ``10`` — lowest specificity score, acts as fallback for
            archives no specialised handler claimed.
        """
        return 10

    def find_root(self, extracted_dir: Path) -> Path:
        """Generic handler processes the whole tree — no narrowing.

        By design this handler iterates every file under ``extracted_dir``
        and routes each through :class:`LoaderRegistry`, so narrowing the
        root would shrink the set of files it sees. Returning the original
        directory keeps the fallback semantics intact.

        Args:
            extracted_dir: Path to extracted archive contents.

        Returns:
            ``extracted_dir`` unchanged.
        """
        return extracted_dir

    def process(
        self,
        extracted_dir: Path,
        settings: EngineSettings,
    ) -> list[dict[str, Any]]:
        """Process files using LoaderRegistry per-file dispatch.

        Args:
            extracted_dir: Path to extracted archive.
            settings: Engine settings.

        Returns:
            List of document chunks from all supported files.
        """
        logger.info("generic_processing_started", directory=str(extracted_dir))

        # Lazy import to avoid circular dependency
        from chaoscypher_core.services.sources.loaders.factory import (
            get_loader_registry,
        )

        registry = get_loader_registry(settings)
        supported_extensions = set(registry.list_supported_extensions())

        documents: list[dict[str, Any]] = []
        files_processed = 0
        files_skipped = 0

        # Find all files (not directories)
        all_files = [f for f in extracted_dir.rglob("*") if f.is_file()]

        logger.debug("generic_files_found", count=len(all_files))

        for file_path in all_files:
            # Check if file should be skipped
            if self._should_skip(file_path, extracted_dir):
                files_skipped += 1
                continue

            # Check if extension is supported
            file_ext = file_path.suffix.lower()

            if file_ext not in supported_extensions:
                logger.debug(
                    "generic_file_unsupported",
                    file=str(file_path),
                    extension=file_ext,
                )
                files_skipped += 1
                continue

            try:
                # Load document using registry
                file_docs = registry.load_document(str(file_path))

                if file_docs:
                    # Add archive-specific metadata
                    for doc in file_docs:
                        doc["metadata"]["archive_source"] = str(
                            file_path.relative_to(extracted_dir)
                        ).replace("\\", "/")
                        doc["metadata"]["doc_type"] = self.name

                    documents.extend(file_docs)
                    files_processed += 1

                    logger.debug(
                        "generic_file_processed",
                        file=str(file_path),
                        chunks=len(file_docs),
                    )

            except Exception as e:
                logger.warning(
                    "generic_file_processing_failed",
                    file=str(file_path),
                    error=str(e),
                    exc_info=True,
                )
                files_skipped += 1
                continue

        logger.info(
            "generic_processing_complete",
            documents_count=len(documents),
            files_processed=files_processed,
            files_skipped=files_skipped,
        )

        # Workstream 2 (2026-05-08): surface the per-file skip count to the
        # indexing handler via the first surviving document's metadata so
        # the source row's ``loader_files_skipped`` counter reflects it.
        if documents and files_skipped > 0:
            first_meta = documents[0].setdefault("metadata", {})
            if isinstance(first_meta, dict):
                first_meta["loader_files_skipped"] = (
                    int(first_meta.get("loader_files_skipped", 0) or 0) + files_skipped
                )

        # Phase 7 (2026-05-09 audit): match Markdown / Sphinx synthetic-doc pattern.
        # When every file in the archive is skipped (e.g. all .exe/.dll/.so), emit a
        # synthetic empty-content document so the indexing-handler rollup surfaces the
        # skip count instead of failing with a generic empty-content error.
        if not documents and files_skipped > 0:
            warning = f"All {files_skipped} files were skipped (extensions / paths in skip list)."
            documents.append(
                {
                    "content": "",
                    "metadata": {
                        "loader_files_skipped": files_skipped,
                        "loader_warnings": [warning],
                        "doc_type": self.name,
                    },
                }
            )

        return documents

    def _should_skip(self, file_path: Path, base_dir: Path) -> bool:
        """Check if file should be skipped.

        Args:
            file_path: Path to the file.
            base_dir: Base directory for relative path calculation.

        Returns:
            True if file should be skipped.
        """
        # Check filename (Phase 6: uses instance set from LoaderSettings)
        if file_path.name in self._skip_files:
            return True

        # Check extension (Phase 6: uses instance set from LoaderSettings)
        if file_path.suffix.lower() in self._skip_extensions:
            return True

        # Check if in skipped directory (Phase 6: uses instance set)
        try:
            relative_path = file_path.relative_to(base_dir)
            for part in relative_path.parts[:-1]:  # Exclude filename
                if part in self._skip_dirs:
                    return True
                if part.startswith("."):  # Skip hidden directories
                    return True
        except ValueError:
            pass

        return False


__all__ = ["GenericHandler"]
