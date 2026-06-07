# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Import Service - Main orchestrator for CCX package imports.

Provides the ImportService class which handles extracting and importing
CCX packages into the ChaosCypher knowledge graph.

Example:
    from chaoscypher_core.services.package.importer import ImportService, ImportOptions

    service = ImportService(graph_repository, sources_repository)
    stats = await service.import_from_bytes(archive_data)
    print(f"Imported {stats.total_items} items")
"""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.services.export.utils import FileIntegrityChecker
from chaoscypher_core.services.package.importer.loaders import (
    KnowledgeLoader,
    SourceLoader,
    TemplateLoader,
    WorkflowLoader,
)
from chaoscypher_core.services.package.importer.models import (
    IdMapper,
    ImportOptions,
    ImportStats,
)
from chaoscypher_core.services.sources.loaders.archive import (
    ArchiveExtractionError,
    ArchiveExtractor,
    ArchiveSecurityError,
)
from chaoscypher_core.settings import EngineSettings


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite.repos import GraphRepository


logger = structlog.get_logger(__name__)


def _list_directory(path: Path) -> list[Path]:
    """List directory contents, returning empty list if directory doesn't exist."""
    return list(path.iterdir()) if path.exists() else []


class ImportService:
    """Orchestrates CCX package imports into ChaosCypher.

    Handles extracting ZIP archives, parsing content files, verifying
    checksums, and importing data in the correct dependency order.

    Attributes:
        graph_repository: Graph repository for node/edge/template operations.
        sources_repository: Optional repository for sources/chunks/citations.
        workflow_db: Optional workflow database for triggers.

    Example:
        >>> service = ImportService(graph_repo, sources_repo)
        >>> stats = await service.import_from_bytes(archive_bytes)
        >>> if stats.is_success:
        ...     print(f"Imported {stats.total_items} items")
    """

    def __init__(
        self,
        graph_repository: GraphRepository,
        sources_repository: Any | None = None,
        workflow_db: Any | None = None,
        engine_settings: EngineSettings | None = None,
    ) -> None:
        """Initialize import service.

        Args:
            graph_repository: Graph repository for graph operations.
            sources_repository: Optional repository for sources import.
            workflow_db: Optional workflow database for triggers.
            engine_settings: Engine settings for archive caps (max size, max
                files). Defaults to EngineSettings() which applies the
                configured archive limits (500 MB / 10 000 files).
        """
        self.graph_repository = graph_repository
        self.sources_repository = sources_repository
        self.workflow_db = workflow_db
        self._engine_settings = engine_settings or EngineSettings()

    async def import_from_bytes(
        self,
        archive_data: bytes,
        options: ImportOptions | None = None,
    ) -> ImportStats:
        """Import a CCX package from bytes.

        Extracts the archive to a temporary directory, parses content,
        and imports data in dependency order.

        Args:
            archive_data: ZIP archive bytes.
            options: Import options (defaults if not provided).

        Returns:
            ImportStats with counts and any errors/warnings.

        Example:
            >>> stats = await service.import_from_bytes(archive_bytes)
        """
        options = options or ImportOptions()
        stats = ImportStats()

        # Create temp directory for extraction
        temp_dir = Path(tempfile.mkdtemp(prefix="ccx_import_"))

        try:
            logger.info(
                "import_started",
                archive_size=len(archive_data),
                database=options.database_name,
            )

            # Extract archive — offload to thread; _extract_zip does blocking
            # disk I/O (write_bytes + streaming extract via ArchiveExtractor).
            await asyncio.to_thread(self._extract_zip, archive_data, temp_dir, stats)

            if stats.errors:
                return stats

            # Import from extracted directory
            await self._import_from_directory(temp_dir, options, stats)

        except Exception as e:
            logger.exception("import_failed", error=str(e))
            stats.errors.append(f"Import failed: {e}")
        finally:
            # Cleanup temp directory
            shutil.rmtree(temp_dir, ignore_errors=True)

        logger.info(
            "import_completed",
            total_items=stats.total_items,
            errors=len(stats.errors),
            warnings=len(stats.warnings),
        )

        return stats

    async def import_from_path(
        self,
        archive_path: Path,
        options: ImportOptions | None = None,
    ) -> ImportStats:
        """Import a CCX package from a file path.

        Args:
            archive_path: Path to the .ccx archive file.
            options: Import options.

        Returns:
            ImportStats with counts and any errors/warnings.
        """
        if not await asyncio.to_thread(archive_path.exists):
            stats = ImportStats()
            stats.errors.append(f"Archive not found: {archive_path}")
            return stats

        archive_data = await asyncio.to_thread(archive_path.read_bytes)
        return await self.import_from_bytes(archive_data, options)

    def _extract_zip(
        self,
        archive_data: bytes,
        dest_dir: Path,
        stats: ImportStats,
    ) -> None:
        """Extract ZIP archive to destination directory with zip-bomb caps.

        Delegates to ArchiveExtractor so the same per-file size cap,
        total-decompressed-size cap, member-count cap, and symlink/device
        rejection that protect source-loader archives also protect .ccx
        package imports. Caps come from the EngineSettings.archive field
        (defaults: 500 MB total, 10 000 files).

        Args:
            archive_data: ZIP archive bytes.
            dest_dir: Destination directory for extraction.
            stats: ImportStats to record any errors.
        """
        # ArchiveExtractor.extract() requires a Path; materialise the bytes
        # as a named temp file inside dest_dir so no extra cleanup is needed.
        archive_path = dest_dir / "_ccx_upload.zip"
        try:
            archive_path.write_bytes(archive_data)
            extractor = ArchiveExtractor(settings=self._engine_settings)
            extractor.extract(archive_path, dest_dir)
            logger.debug("archive_extracted", dest=str(dest_dir))
        except (ArchiveSecurityError, ArchiveExtractionError) as e:
            stats.errors.append(f"Security: archive rejected: {e}")
        except Exception as e:
            stats.errors.append(f"Failed to extract archive: {e}")
        finally:
            archive_path.unlink(missing_ok=True)

    async def _import_from_directory(
        self,
        extracted_dir: Path,
        options: ImportOptions,
        stats: ImportStats,
    ) -> None:
        """Import content from extracted directory.

        Args:
            extracted_dir: Directory containing extracted package files.
            options: Import options.
            stats: ImportStats for recording results.
        """
        mapper = IdMapper()

        # Log what files exist in the extracted directory
        extracted_files = await asyncio.to_thread(_list_directory, extracted_dir)
        logger.info(
            "extracted_directory_contents",
            path=str(extracted_dir),
            files=[f.name for f in extracted_files],
        )

        # Parse manifest
        manifest = self._parse_manifest(extracted_dir, stats)
        if manifest and options.verify_checksums:
            self._verify_checksums(extracted_dir, manifest, stats)
            stats.checksum_verified = len(stats.errors) == 0

        # Import in dependency order:
        # 1. Templates (nodes reference templates)
        if options.import_templates:
            await self._import_templates(extracted_dir, mapper, stats, options)

        # 2. Knowledge (nodes + edges)
        if options.import_knowledge:
            await self._import_knowledge(extracted_dir, mapper, stats, options)

        # 3. Workflows
        if options.import_workflows:
            await self._import_workflows(extracted_dir, mapper, stats, options)

        # 4. Sources (chunks, citations, tags)
        if options.import_sources and self.sources_repository:
            await self._import_sources(extracted_dir, mapper, stats, options)

    def _parse_manifest(
        self,
        extracted_dir: Path,
        stats: ImportStats,
    ) -> dict[str, Any] | None:
        """Parse manifest.json from extracted directory.

        Args:
            extracted_dir: Directory containing manifest.json.
            stats: ImportStats for recording errors.

        Returns:
            Manifest dictionary or None if not found/invalid.
        """
        manifest_path = extracted_dir / "manifest.json"
        if not manifest_path.exists():
            stats.warnings.append("No manifest.json found")
            return None

        try:
            manifest_data: dict[str, Any] = json.loads(manifest_path.read_text(encoding="utf-8"))
            return manifest_data
        except Exception as e:
            stats.warnings.append(f"Failed to parse manifest.json: {e}")
            return None

    def _verify_checksums(
        self,
        extracted_dir: Path,
        manifest: dict[str, Any],
        stats: ImportStats,
    ) -> None:
        """Verify file checksums from manifest.

        Args:
            extracted_dir: Directory containing files.
            manifest: Parsed manifest with checksum info.
            stats: ImportStats for recording errors.
        """
        content_files = manifest.get("content_files", [])
        dest_resolved = extracted_dir.resolve()

        for file_info in content_files:
            filename = file_info.get("filename")
            expected_sha512 = file_info.get("sha512")
            expected_sha256 = file_info.get("sha256")

            if not filename or not expected_sha512:
                continue

            # Validate path from untrusted manifest to prevent traversal
            member_path = Path(filename)
            if member_path.is_absolute() or ".." in member_path.parts:
                stats.errors.append(f"Security: unsafe path in manifest: {filename}")
                continue
            file_path = (extracted_dir / member_path).resolve()
            if not file_path.is_relative_to(dest_resolved):
                stats.errors.append(f"Security: path escapes destination: {filename}")
                continue

            if not file_path.exists():
                stats.warnings.append(f"Missing file: {filename}")
                continue

            is_valid = FileIntegrityChecker.verify_file_checksum(
                str(file_path),
                expected_sha512,
                expected_sha256,
            )

            if not is_valid:
                stats.errors.append(f"Checksum mismatch: {filename}")

    async def _import_templates(
        self,
        extracted_dir: Path,
        mapper: IdMapper,
        stats: ImportStats,
        options: ImportOptions,
    ) -> None:
        """Import templates from templates.jsonld."""
        templates_path = extracted_dir / "templates.jsonld"
        if not templates_path.exists():
            logger.info("no_templates_file", path=str(templates_path))
            return

        try:
            data = json.loads(templates_path.read_text(encoding="utf-8"))
            logger.info(
                "templates_file_loaded",
                path=str(templates_path),
                has_templates_key="templates" in data,
                template_count=len(data.get("templates", [])) if isinstance(data, dict) else 0,
            )
            loader = TemplateLoader(
                self.graph_repository,
                skip_existing=options.skip_existing_templates,
            )
            loader.load(data, mapper, stats, options.database_name)
        except Exception as e:
            stats.errors.append(f"Failed to import templates: {e}")

    async def _import_knowledge(
        self,
        extracted_dir: Path,
        mapper: IdMapper,
        stats: ImportStats,
        options: ImportOptions,
    ) -> None:
        """Import knowledge nodes and edges from knowledge.jsonld."""
        knowledge_path = extracted_dir / "knowledge.jsonld"
        if not knowledge_path.exists():
            logger.info("no_knowledge_file", path=str(knowledge_path))
            return

        try:
            data = json.loads(knowledge_path.read_text(encoding="utf-8"))
            logger.info(
                "knowledge_file_loaded",
                path=str(knowledge_path),
                node_count=len(data.get("nodes", [])) if isinstance(data, dict) else 0,
                edge_count=len(data.get("edges", [])) if isinstance(data, dict) else 0,
            )
            loader = KnowledgeLoader(self.graph_repository)
            loader.load(data, mapper, stats, options.database_name)
        except Exception as e:
            stats.errors.append(f"Failed to import knowledge: {e}")

    async def _import_workflows(
        self,
        extracted_dir: Path,
        mapper: IdMapper,
        stats: ImportStats,
        options: ImportOptions,
    ) -> None:
        """Import workflows from workflows.jsonld."""
        workflows_path = extracted_dir / "workflows.jsonld"
        if not workflows_path.exists():
            logger.debug("no_workflows_file")
            return

        try:
            data = json.loads(workflows_path.read_text(encoding="utf-8"))
            loader = WorkflowLoader(self.graph_repository, self.workflow_db)
            loader.load(data, mapper, stats, options.database_name)
        except Exception as e:
            stats.errors.append(f"Failed to import workflows: {e}")

    async def _import_sources(
        self,
        extracted_dir: Path,
        mapper: IdMapper,
        stats: ImportStats,
        options: ImportOptions,
    ) -> None:
        """Import sources from sources.jsonl."""
        sources_path = extracted_dir / "sources.jsonl"
        if not sources_path.exists():
            logger.debug("no_sources_file")
            return

        try:
            # Parse JSONL (one JSON object per line)
            sources_data = []
            with sources_path.open(encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        sources_data.append(json.loads(line))

            loader = SourceLoader(self.sources_repository)
            loader.load(sources_data, mapper, stats, options.database_name)
        except Exception as e:
            stats.errors.append(f"Failed to import sources: {e}")


__all__ = ["ImportService"]
