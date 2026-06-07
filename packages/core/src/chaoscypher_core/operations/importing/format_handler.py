# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Format-specific import handlers for CCX and Lexicon packages.

Provides standalone async handler functions for importing data from specific
file formats (CCX bundles) and external package registries (Lexicon).
These are called by ``ImportOperationsService`` and share its dependency
context via explicit parameters.
"""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.exceptions import ValidationError


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite.repos import GraphRepository
    from chaoscypher_core.app_config import EngineSettings

logger = structlog.get_logger(__name__)


async def handle_import_ccx(
    data: dict[str, Any],
    graph_repository: GraphRepository,
    source_repository: Any | None = None,
    engine_settings: EngineSettings | None = None,
    metadata: dict[str, Any] | None = None,
    task_id: str | None = None,
) -> dict[str, Any]:
    """Execute CCX import operation.

    Delegates to ``ImportService`` — the same path the CLI uses for
    ``chaoscypher graph package load``. Before this fix the handler
    called a nonexistent ``graph_repository.import_graph_from_ccx``
    method (broken since the ``.cxl → .ccx`` rename in 2db50eade);
    every API-driven import failed with AttributeError.

    Args:
        data: Task data with ``file_content`` (base64 zip bytes) and
            ``merge`` flag.
        graph_repository: GraphRepository for graph operations.
        source_repository: Optional source repository (for sources.jsonl
            payloads). May be ``None`` for template/knowledge-only packages.
        engine_settings: Optional engine settings (drives archive size /
            file-count caps in ``ImportService``).
        metadata: Task metadata. ``database_name`` is read from here.
        task_id: Task ID for tracking.

    Returns:
        Result dictionary with import statistics and errors.

    Raises:
        ValidationError: If ``file_content`` is missing from the task data.

    """
    from chaoscypher_core.services.package.importer.models import ImportOptions
    from chaoscypher_core.services.package.importer.service import ImportService

    encoded_content = data.get("file_content")
    merge = data.get("merge", False)
    database_name = (metadata or {}).get("database_name", "default")

    logger.info("import_ccx_operation_executing", merge=merge, database=database_name)

    if encoded_content is None:
        msg = "file_content is required"
        raise ValidationError(msg, field="file_content")

    try:
        file_content = base64.b64decode(encoded_content)
        service = ImportService(
            graph_repository=graph_repository,
            sources_repository=source_repository,
            engine_settings=engine_settings,
        )
        # ``merge=True`` from the API maps to "reuse existing templates by
        # name rather than minting fresh ones" — the closest semantic
        # match in ``ImportOptions``.
        options = ImportOptions(
            skip_existing_templates=merge,
            database_name=database_name,
        )
        stats = await service.import_from_bytes(file_content, options=options)
        return {
            "success": not stats.errors,
            "nodes_imported": stats.nodes_imported,
            "edges_imported": stats.edges_imported,
            "templates_imported": stats.templates_imported,
            "templates_skipped": stats.templates_skipped,
            "checksum_verified": stats.checksum_verified,
            "errors": stats.errors,
            "warnings": stats.warnings,
        }
    except Exception:
        logger.exception("import_ccx_operation_failed")
        raise


async def handle_lexicon_import(
    data: dict[str, Any],
    graph_repository: GraphRepository,
    metadata: dict[str, Any] | None = None,
    task_id: str | None = None,
) -> dict[str, Any]:
    """Execute Lexicon package import operation.

    Downloads a package from Lexicon and imports it to the database.

    Args:
        data: Task data with owner_username, repo_name, version, database_name.
        graph_repository: GraphRepository for graph operations.
        metadata: Task metadata.
        task_id: Task ID for tracking.

    Returns:
        Result dictionary with import statistics and errors.

    """
    from chaoscypher_core.app_config import get_settings
    from chaoscypher_core.services.lexicon import (
        DictLexiconStorage,
        LexiconDownloadRequest,
        LexiconService,
    )
    from chaoscypher_core.services.package import ImportOptions, ImportService

    owner_username = data["owner_username"]
    repo_name = data["repo_name"]
    version = data.get("version", "latest")
    database_name = data["database_name"]

    settings = get_settings()
    package_key = f"{owner_username}/{repo_name}"

    logger.info(
        "lexicon_import_starting",
        package=package_key,
        version=version,
        database=database_name,
        task_id=task_id,
    )

    try:
        # 1. Download from Lexicon
        storage_data = {
            "url": settings.lexicon.api_url,
            "token": settings.lexicon.token,
            "refresh_token": settings.lexicon.refresh_token,
            "username": settings.lexicon.username,
        }
        storage = DictLexiconStorage(storage_data)
        lexicon_service = LexiconService(storage)

        download_request = LexiconDownloadRequest(
            owner_username=owner_username,
            repo_name=repo_name,
            version=version,
        )
        archive_data = await lexicon_service.download(download_request)

        logger.info(
            "lexicon_package_downloaded",
            package=package_key,
            size=len(archive_data),
        )

        # 2. Import using ImportService
        import_service = ImportService(
            graph_repository=graph_repository,
            sources_repository=None,
            workflow_db=None,
        )

        import_options = ImportOptions(
            verify_checksums=True,
            skip_existing_templates=False,
            import_templates=True,
            import_knowledge=True,
            import_workflows=True,
            import_sources=False,
            database_name=database_name,
        )

        stats = await import_service.import_from_bytes(archive_data, import_options)

        logger.info(
            "lexicon_import_completed",
            package=package_key,
            nodes=stats.nodes_imported,
            edges=stats.edges_imported,
            templates=stats.templates_imported,
            templates_skipped=stats.templates_skipped,
            workflows=stats.workflows_imported,
            checksum_verified=stats.checksum_verified,
            errors=len(stats.errors),
            warnings=len(stats.warnings),
        )

        return {
            "success": len(stats.errors) == 0,
            "package": package_key,
            "version": version,
            "nodes_imported": stats.nodes_imported,
            "edges_imported": stats.edges_imported,
            "templates_imported": stats.templates_imported,
            "templates_skipped": stats.templates_skipped,
            "workflows_imported": stats.workflows_imported,
            "workflow_edges_imported": stats.workflow_edges_imported,
            "triggers_imported": stats.triggers_imported,
            "checksum_verified": stats.checksum_verified,
            "warnings": stats.warnings,
            "errors": stats.errors,
        }

    except Exception:
        logger.exception(
            "lexicon_import_failed",
            package=package_key,
        )
        raise
