# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Export API Endpoints.

POST /api/v1/exports - Queue graph export
POST /api/v1/exports/by_sources - Queue source-filtered export
POST /api/v1/exports/import - Queue CCX import.
"""

import asyncio
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Body, Depends, File, Query, UploadFile, status
from sqlmodel import Session

from chaoscypher_core.app_config import Settings, get_settings
from chaoscypher_core.operations.export_operations_service import (
    ExportOperationsService,
)
from chaoscypher_core.repo_factories import (
    get_graph_repository,
)
from chaoscypher_core.utils.disk import check_disk_space
from chaoscypher_cortex.features.export.models import ExportResponse, ImportResponse
from chaoscypher_cortex.features.export.service import ExportService
from chaoscypher_cortex.shared.api.responses import (
    AUTH_ERROR_RESPONSES,
    COMMON_ERROR_RESPONSES,
    SERVICE_UNAVAILABLE_RESPONSE,
)
from chaoscypher_cortex.shared.auth.dependencies import CurrentUsername
from chaoscypher_cortex.shared.database import get_current_session


router = APIRouter()


def get_export_service(
    session: Annotated[Session, Depends(get_current_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ExportService:
    """Get ExportService instance (VSA pattern).

    Creates service with operations manager from shared infrastructure.
    """
    graph_repo = get_graph_repository(session, settings.current_database)

    export_service = ExportOperationsService(
        graph_repository=graph_repo,
    )

    return ExportService(export_service, settings)


@router.post(
    "",
    response_model=ExportResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **SERVICE_UNAVAILABLE_RESPONSE,
    },
)
async def create_export(
    _: CurrentUsername,
    export_service: Annotated[ExportService, Depends(get_export_service)],
    include_templates: bool = Query(True, description="Include user templates in export"),
    include_knowledge: bool = Query(True, description="Include knowledge nodes and edges"),
    include_workflows: bool = Query(
        True, description="Include workflow nodes, steps, and triggers"
    ),
    include_sources: bool = Query(True, description="Include document sources and metadata"),
    include_embeddings: bool = Query(
        False, description="Include embedding vectors (for same-model migration)"
    ),
) -> ExportResponse:
    """Queue a knowledge graph export as a .ccx file.

    **Creates a compressed package (CCX v2.0 format)** containing selected graph components.

    **Query Parameters:**
    - `include_templates`: Include user-created templates
    - `include_knowledge`: Include knowledge graph nodes and edges
    - `include_workflows`: Include workflows and triggers
    - `include_sources`: Include document sources and metadata
    - `include_embeddings`: Include embedding vectors (for same-model migration)

    **Returns:**
    - 202 Accepted: Task queued with task_id for status tracking

    **How to Download:**
    1. Note the returned `task_id`
    2. Poll `GET /api/v1/queue/tasks/{task_id}` for status
    3. When status is "complete", download via `GET /api/v1/queue/tasks/{task_id}/result`

    **Export Contents:**
    - Templates: User-created node/edge templates
    - Knowledge: All knowledge graph nodes and edges
    - Workflows: Workflow definitions, steps, and triggers
    - Sources: Document sources with chunks and metadata
    """
    check_disk_space(Path(str(get_settings().data_dir)))
    return await export_service.queue_export(
        include_templates=include_templates,
        include_knowledge=include_knowledge,
        include_workflows=include_workflows,
        include_sources=include_sources,
        include_embeddings=include_embeddings,
    )


@router.post(
    "/import",
    response_model=ImportResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **SERVICE_UNAVAILABLE_RESPONSE,
    },
)
async def create_import(
    _: CurrentUsername,
    export_service: Annotated[ExportService, Depends(get_export_service)],
    file: UploadFile = File(
        ..., description="CCX package file"
    ),  # FastAPI File() is safe as default
    merge: bool = Query(False, description="Merge with existing data (True) or replace (False)"),
) -> ImportResponse:
    """Queue a knowledge graph package import.

    **Uploads and processes a .ccx file (CCX v2.0 format)** containing graph data.

    **Request:**
    - `file`: .ccx package file to import
    - `merge`: Whether to merge (True) or replace (False) existing data

    **Merge vs Replace:**
    - **Merge (True)**: Adds imported data to existing graph
    - **Replace (False)**: Clears existing data before importing

    **Returns:**
    - 202 Accepted: Task queued with task_id for status tracking

    **How to Track:**
    1. Note the returned `task_id`
    2. Poll `GET /api/v1/queue/tasks/{task_id}` for status
    3. When complete, get results via `GET /api/v1/queue/tasks/{task_id}/result`

    **Import Results Include:**
    - Number of templates imported
    - Number of nodes imported
    - Number of edges imported
    - Number of workflows imported
    - Any errors or warnings

    **Errors:**
    - 400: Invalid CCX file format
    - 503: Operations service unavailable
    """
    # Stream to temp file to avoid holding full upload in ASGI memory during transfer.
    # CCX files are small ZIP exports (typically <10MB), so reading back into bytes
    # for queue serialization is acceptable — unlike source uploads which can be 10GB.
    import tempfile
    from pathlib import Path

    from chaoscypher_core.app_config import get_settings
    from chaoscypher_core.exceptions import ValidationError
    from chaoscypher_core.utils.disk import check_disk_space

    settings = get_settings()
    chunk_size = settings.batching.upload_chunk_size
    max_bytes = settings.batching.max_upload_bytes

    # Preflight free space and cap the streamed size so an oversized upload can
    # neither fill the data volume (temp write) nor spike RAM (read_bytes).
    check_disk_space(
        Path(str(settings.data_dir)),
        min_bytes=max_bytes + settings.batching.upload_disk_headroom_bytes,
    )

    size = 0
    with tempfile.NamedTemporaryFile(delete=False, suffix=".import") as tmp:
        tmp_path = Path(tmp.name)
        while chunk := await file.read(chunk_size):
            size += len(chunk)
            if size > max_bytes:
                break  # stop reading; the temp file is unlinked in finally below
            tmp.write(chunk)
    try:
        if size > max_bytes:
            msg = f"Import exceeds max_upload_bytes={max_bytes}"
            raise ValidationError(msg, field="file")
        content = await asyncio.to_thread(tmp_path.read_bytes)
    finally:
        tmp_path.unlink(missing_ok=True)  # noqa: ASYNC240

    return await export_service.queue_import(
        file_content=content, filename=file.filename or "unknown.ccx", merge=merge
    )


@router.post(
    "/by_sources",
    response_model=ExportResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **SERVICE_UNAVAILABLE_RESPONSE,
    },
)
async def create_export_by_sources(
    _: CurrentUsername,
    export_service: Annotated[ExportService, Depends(get_export_service)],
    source_ids: list[str] = Body(..., description="List of source UUIDs to include in export"),
    include_templates: bool = Query(True, description="Include templates linked to these sources"),
    include_embeddings: bool = Query(
        False, description="Include embedding vectors (for same-model migration)"
    ),
) -> ExportResponse:
    """Queue a source-filtered graph export as a .ccx file.

    **Creates an export containing only data related to specified sources.**

    This is useful when you want to export just the knowledge graph data that
    originated from specific documents/sources, along with their related entities,
    edges, and templates.

    **Request Body:**
    - `source_ids`: List of source UUIDs to include (required)

    **Query Parameters:**
    - `include_templates`: Include templates linked to or used by these sources
    - `include_embeddings`: Include embedding data in exported chunks

    **Export Includes:**
    - Entities that have citations from the specified sources
    - Edges where both endpoints are in the entity set (referential integrity)
    - Templates linked to the sources (via TemplateSourceAssignment)
    - Templates used by the exported entities
    - Source metadata, chunks, citations, and tags

    **Returns:**
    - 202 Accepted: Task queued with task_id for status tracking

    **How to Download:**
    1. Note the returned `task_id`
    2. Poll `GET /api/v1/queue/tasks/{task_id}` for status
    3. When status is "complete", download via `GET /api/v1/queue/tasks/{task_id}/result`
    """
    check_disk_space(Path(str(get_settings().data_dir)))
    return await export_service.queue_export_by_sources(
        source_ids=source_ids,
        include_templates=include_templates,
        include_embeddings=include_embeddings,
    )
