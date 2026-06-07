# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Centralized API v1 Router Registration.

Collects all feature routers and mounts them under /api/v1/.
"""

from importlib.metadata import entry_points

import structlog
from fastapi import APIRouter

# features.local_auth mounts its own router directly in main.py at
# /api/v1/auth (nginx hits /api/v1/auth/verify for auth_request).
from chaoscypher_cortex.features.admin_plugins.api import router as admin_plugins_router
from chaoscypher_cortex.features.backup.api import router as backup_router
from chaoscypher_cortex.features.chats.api import router as conversations_router
from chaoscypher_cortex.features.counts.api import router as counts_router
from chaoscypher_cortex.features.dashboard import router as dashboard_router
from chaoscypher_cortex.features.databases.api import router as databases_router
from chaoscypher_cortex.features.diagnostics.api import router as diagnostics_router
from chaoscypher_cortex.features.edges.api import router as edges_router
from chaoscypher_cortex.features.edition.api import router as edition_router
from chaoscypher_cortex.features.export.api import router as export_router
from chaoscypher_cortex.features.graph import grounding_router
from chaoscypher_cortex.features.graph.api import router as graph_router
from chaoscypher_cortex.features.graph_snapshot.api import router as graph_snapshot_router
from chaoscypher_cortex.features.health.api import router as health_router
from chaoscypher_cortex.features.lexicon.api import router as lexicon_router
from chaoscypher_cortex.features.llm.api import router as llm_router
from chaoscypher_cortex.features.logs.api import router as logs_router
from chaoscypher_cortex.features.nodes.api import router as nodes_router
from chaoscypher_cortex.features.pause.api import (
    sources_router as pause_sources_router,
)
from chaoscypher_cortex.features.pause.api import (
    system_router as pause_system_router,
)
from chaoscypher_cortex.features.quality.api import router as quality_router
from chaoscypher_cortex.features.queue.api import router as queue_router
from chaoscypher_cortex.features.search.api import router as search_router
from chaoscypher_cortex.features.settings import ollama_models_router
from chaoscypher_cortex.features.settings.api import router as settings_router
from chaoscypher_cortex.features.sources.api import router as sources_router
from chaoscypher_cortex.features.sources.chunk_attempts_api import (
    router as sources_chunk_attempts_router,
)
from chaoscypher_cortex.features.sources.chunk_rerun_api import (
    router as sources_chunk_rerun_router,
)
from chaoscypher_cortex.features.sources.chunks_api import router as sources_chunks_router
from chaoscypher_cortex.features.sources.extraction_api import (
    router as sources_extraction_router,
)
from chaoscypher_cortex.features.sources.tags_api import router as source_tags_router
from chaoscypher_cortex.features.sources.vision_pages_api import (
    router as sources_vision_pages_router,
)
from chaoscypher_cortex.features.templates.api import router as templates_router
from chaoscypher_cortex.features.tools.api import router as tools_router
from chaoscypher_cortex.features.triggers.api import router as triggers_router
from chaoscypher_cortex.features.upgrade.api import router as upgrade_router
from chaoscypher_cortex.features.workflows.api import router as workflows_router
from chaoscypher_cortex.features.workflows.execution_api import (
    router as workflows_execution_router,
)


logger = structlog.get_logger(__name__)


def discover_extensions(api: APIRouter) -> None:
    """Discover and mount routers from installed extension packages.

    Scans the ``chaoscypher.extensions`` entry-point group for installed
    packages that provide additional API routers (e.g., enterprise features).
    Each entry point must expose a callable that accepts an ``APIRouter``.

    Args:
        api: The API router to mount extension routers onto.
    """
    eps = entry_points(group="chaoscypher.extensions")
    for ep in eps:
        try:
            register_fn = ep.load()
            register_fn(api)
            logger.info("extension_registered", name=ep.name)
        except Exception:
            logger.warning(
                "extension_registration_failed",
                name=ep.name,
                exc_info=True,
            )


def create_api_router() -> APIRouter:
    """Create and return the top-level API v1 router with all feature sub-routers.

    Returns:
        APIRouter with all feature routers mounted.

    """
    api = APIRouter(prefix="/api/v1")

    api.include_router(health_router, prefix="", tags=["health"])
    api.include_router(edition_router, prefix="/edition", tags=["edition"])
    # features.local_auth router is mounted directly in main.py (it carries
    # its own /api/v1/auth prefix).
    api.include_router(lexicon_router, prefix="/lexicon", tags=["lexicon"])
    api.include_router(workflows_router, prefix="/workflows", tags=["workflows"])
    api.include_router(workflows_execution_router, prefix="/workflows", tags=["workflows"])
    api.include_router(tools_router, prefix="/tools", tags=["tools"])
    api.include_router(triggers_router, prefix="/triggers", tags=["triggers"])
    api.include_router(conversations_router, prefix="/chats", tags=["chats"])
    api.include_router(graph_router, prefix="/graph", tags=["graph"])
    api.include_router(graph_snapshot_router, prefix="/graph/snapshot", tags=["graph"])
    api.include_router(counts_router, prefix="/counts", tags=["counts"])
    api.include_router(edges_router, prefix="/edges", tags=["edges"])
    api.include_router(templates_router, prefix="/templates", tags=["templates"])
    api.include_router(nodes_router, prefix="/nodes", tags=["nodes"])
    api.include_router(search_router, prefix="/search", tags=["search"])
    api.include_router(settings_router, prefix="/settings", tags=["settings"])
    api.include_router(ollama_models_router, prefix="/settings/ollama", tags=["settings"])
    api.include_router(databases_router, prefix="/databases", tags=["databases"])
    api.include_router(queue_router, prefix="/queue", tags=["queue"])
    api.include_router(export_router, prefix="/exports", tags=["export"])
    api.include_router(llm_router, prefix="/llm", tags=["llm"])
    # NOTE: source_tags_router MUST come before sources_router to avoid path conflict
    # (sources_router has /{source_id} which would otherwise match "/tags" as source_id)
    api.include_router(source_tags_router, prefix="/sources/tags", tags=["sources"])
    # Pause/resume endpoints. Mounted BEFORE sources_router so the
    # literal /sources/pause and /sources/resume bulk endpoints match
    # before sources_router's /{source_id} patterns.
    api.include_router(pause_sources_router, prefix="/sources", tags=["pause"])
    api.include_router(pause_system_router, prefix="/system/processing", tags=["pause"])
    api.include_router(sources_router, prefix="/sources", tags=["sources"])
    api.include_router(sources_extraction_router, prefix="/sources", tags=["sources"])
    api.include_router(sources_chunks_router, prefix="/sources", tags=["sources"])
    api.include_router(sources_chunk_rerun_router, prefix="/sources", tags=["sources"])
    api.include_router(sources_chunk_attempts_router, prefix="/sources", tags=["sources"])
    api.include_router(sources_vision_pages_router, prefix="/sources", tags=["sources"])
    api.include_router(grounding_router, prefix="/graph/grounding", tags=["grounding"])
    api.include_router(quality_router, prefix="/quality", tags=["quality"])
    api.include_router(backup_router, prefix="/backup", tags=["Backup"])
    api.include_router(logs_router, prefix="/logs", tags=["logs"])
    api.include_router(diagnostics_router, prefix="/diagnostics", tags=["diagnostics"])
    # Upgrade/maintenance-mode endpoints. Whitelisted by the upgrade-gate
    # middleware so the UI can talk to this namespace even when the DB is
    # blocked on a tier-2 migration.
    api.include_router(upgrade_router, tags=["upgrade"])
    # Admin plugin reload (carries its own /admin/plugins prefix on the router).
    api.include_router(admin_plugins_router)
    # Aggregated live-status endpoint for the UI polling loop. Mounted
    # at /system/dashboard via the router's own path prefix.
    api.include_router(dashboard_router, prefix="/system", tags=["dashboard"])

    # Discover and mount extension routers (e.g., enterprise features)
    discover_extensions(api)

    return api


__all__ = ["create_api_router", "discover_extensions"]
