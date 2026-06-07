# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Counts API Endpoints.

GET /api/v1/counts - Get resource counts.
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlmodel import Session

from chaoscypher_core.app_config import Settings, get_settings
from chaoscypher_core.constants import SYSTEM_TEMPLATE_IDS
from chaoscypher_core.database import get_sqlite_adapter
from chaoscypher_core.repo_factories import get_graph_repository
from chaoscypher_core.services.graph.engine.stats import CountsService
from chaoscypher_cortex.features.counts.models import CountsResponse
from chaoscypher_cortex.shared.api.responses import COMMON_ERROR_RESPONSES
from chaoscypher_cortex.shared.auth.dependencies import CurrentUsername
from chaoscypher_cortex.shared.database import get_current_session


# Create router
router = APIRouter()


# Dependency to get counts service
def get_counts_service(
    session: Annotated[Session, Depends(get_current_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> CountsService:
    """Get CountsService instance (uses engine service directly)."""
    adapter = get_sqlite_adapter(database_name=settings.current_database)
    graph_repository = get_graph_repository(session, settings.current_database)

    # SqliteAdapter implements SourcesProtocol protocol via SourcesMixin
    return CountsService(
        graph_repository=graph_repository,
        sources_repository=adapter,
        database_name=settings.current_database,
    )


# ============================================================================
# Counts Operations
# ============================================================================


@router.get(
    "",
    response_model=CountsResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
    },
)
async def get_counts(
    _: CurrentUsername,
    counts_service: Annotated[CountsService, Depends(get_counts_service)],
) -> CountsResponse:
    """Get counts of all resources efficiently.

    This endpoint is optimized for the header navigation display.

    **Returns:**
    - `knowledge_nodes`: Non-system nodes (excludes workflows, lenses)
    - `links`: Total edge count
    - `templates`: User-created templates (excludes system templates)
    - `workflows`: Workflow count (nodes with template_id='system_workflow')
    - `lenses`: Lens count (nodes with template_id='system_lens')
    - `sources`: Document sources (PDFs, text, CSV, etc.)

    **Performance:**
    - Uses efficient counting methods where possible
    - Filters system templates and nodes
    """
    counts_dict = counts_service.get_counts(system_template_ids=SYSTEM_TEMPLATE_IDS)
    return CountsResponse(**counts_dict)
