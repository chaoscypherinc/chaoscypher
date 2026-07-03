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
    """Execute a CCX 3.0 import operation.

    Decodes the base64 package bytes and delegates to ``CcxImporter`` — the
    same upsert-by-IRI path the CLI uses for ``chaoscypher graph package
    load``. ``ccx-format`` validates the package fail-closed, so an invalid
    package raises (and the queue can decide retryability) rather than
    returning a partial result.

    Args:
        data: Task data with ``file_content`` (base64 package bytes) and
            ``merge`` flag.
        graph_repository: GraphRepository for graph operations.
        source_repository: Optional source repository (for ``sources.jsonl``
            payloads). May be ``None`` for template/knowledge-only packages.
        engine_settings: Accepted for call-site compatibility; ``CcxImporter``
            derives caps/validation from ``ccx-format`` and does not need it.
        metadata: Task metadata. ``database_name`` is read from here.
        task_id: Task ID for tracking.

    Returns:
        Result dictionary with import statistics, conformance classes, and
        any warnings.

    Raises:
        ValidationError: If ``file_content`` is missing from the task data.

    """
    from chaoscypher_core.services.package.importer import CcxImporter, ImportOptions

    encoded_content = data.get("file_content")
    merge = data.get("merge", False)
    database_name = (metadata or {}).get("database_name", "default")

    logger.info("import_ccx_operation_executing", merge=merge, database=database_name)

    if encoded_content is None:
        msg = "file_content is required"
        raise ValidationError(msg, field="file_content")

    try:
        file_content = base64.b64decode(encoded_content)
        importer = CcxImporter(
            graph_repository=graph_repository,
            sources_repository=source_repository,
        )
        # ``merge`` is retained on the API contract; CCX 3.0 import is
        # idempotent upsert-by-IRI, so re-import never duplicates regardless.
        options = ImportOptions(
            skip_existing_templates=merge,
            database_name=database_name,
        )
        stats = await importer.import_from_bytes(file_content, options=options)
        return {
            "success": not stats.errors,
            "nodes_imported": stats.nodes_imported,
            "edges_imported": stats.edges_imported,
            "templates_imported": stats.templates_imported,
            "templates_skipped": stats.templates_skipped,
            "sources_imported": stats.sources_imported,
            "chunks_imported": stats.chunks_imported,
            "conformance_classes": stats.conformance_classes,
            "checksum_verified": stats.checksum_verified,
            "errors": stats.errors,
            "warnings": stats.warnings,
            # Drives the post-import search-indexing enqueue in
            # ImportOperationsService._import_ccx_handler.
            "imported_source_ids": stats.imported_source_ids,
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
    from chaoscypher_core.services.package import CcxImporter, ImportOptions

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

        # 2. Import using CcxImporter (CCX 3.0 upsert-by-IRI).
        importer = CcxImporter(
            graph_repository=graph_repository,
            sources_repository=None,
            workflow_db=None,
        )

        import_options = ImportOptions(
            skip_existing_templates=False,
            import_templates=True,
            import_knowledge=True,
            import_workflows=True,
            import_sources=False,
            database_name=database_name,
        )

        stats = await importer.import_from_bytes(archive_data, import_options)

        logger.info(
            "lexicon_import_completed",
            package=package_key,
            nodes=stats.nodes_imported,
            edges=stats.edges_imported,
            templates=stats.templates_imported,
            templates_skipped=stats.templates_skipped,
            conformance_classes=stats.conformance_classes,
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
            "conformance_classes": stats.conformance_classes,
            "checksum_verified": stats.checksum_verified,
            "warnings": stats.warnings,
            "errors": stats.errors,
            # Knowledge-only import has no source — the worker enqueues
            # OP_INDEX_IMPORTED_NODES off this list to make the nodes searchable.
            "imported_node_ids": stats.imported_node_ids,
        }

    except Exception:
        logger.exception(
            "lexicon_import_failed",
            package=package_key,
        )
        raise
