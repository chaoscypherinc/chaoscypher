# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Extraction pipeline endpoints for Sources feature.

Handles extraction lifecycle, task monitoring, and processing control:
- Extraction: POST/GET/DELETE /sources/{id}/extraction (trigger, progress, cancellation)
- Extraction Tasks: GET /sources/{id}/extraction/tasks, /stats, /charts
- Processing: DELETE /sources/{id}/processing (abort)
"""

from typing import TYPE_CHECKING, Annotated, Any

import structlog
from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import BaseModel, field_validator

from chaoscypher_cortex.features.sources.api import get_source_service
from chaoscypher_cortex.features.sources.models import (
    ExtractionStatusResponse,
    ExtractionTaskChartPoint,
    ExtractionTaskListResponse,
    ExtractionTaskResponse,
    ExtractionTaskStatsResponse,
    FilteringLogResponse,
    FilteringMode,
)
from chaoscypher_cortex.shared.api.dependencies import (
    PageParams,  # noqa: TC001 - FastAPI runtime dep
)
from chaoscypher_cortex.shared.api.errors import (
    raise_if_not_found,
    resource_not_found_error,
    validation_error,
)
from chaoscypher_cortex.shared.api.responses import (
    AUTH_ERROR_RESPONSES,
    COMMON_ERROR_RESPONSES,
    CONFLICT_RESPONSE,
    NOT_FOUND_RESPONSE,
    SERVICE_UNAVAILABLE_RESPONSE,
)
from chaoscypher_cortex.shared.auth.dependencies import (
    CurrentUsername,  # noqa: TC001 - FastAPI runtime dep
)


if TYPE_CHECKING:
    from chaoscypher_cortex.features.sources.service import SourceService

logger = structlog.get_logger(__name__)

router = APIRouter()


# ================================
# Request Models
# ================================


class TriggerExtractionRequest(BaseModel):
    """Request model for triggering manual extraction on a source."""

    analysis_depth: str = "full"
    domain: str | None = None
    force: bool = False
    filtering_mode: FilteringMode | None = None
    content_filtering: bool = True
    enable_direction_correction: bool | None = None
    protect_orphans: bool | None = None
    # Phase 6 (2026-05-08): per-source inverse-relationships toggle and degree cap.
    enable_inverse_relationships: bool | None = None
    max_entity_degree_override: int | None = None


class ConfirmExtractionRequest(BaseModel):
    """Confirm a parked source's domain + extraction options before extraction.

    Mirrors TriggerExtractionRequest (the editable extraction options) minus
    ``force`` — confirm always re-resolves config via the create path.
    """

    analysis_depth: str = "full"
    domain: str | None = None
    filtering_mode: FilteringMode | None = None
    # Tri-state: None (default) = leave the persisted upload-time value as-is;
    # True/False = explicit override. A non-optional default would silently
    # clobber an upload-time content_filtering=False on every default confirm.
    content_filtering: bool | None = None
    enable_direction_correction: bool | None = None
    protect_orphans: bool | None = None
    # Phase 6 (2026-05-08): per-source inverse-relationships toggle and degree cap.
    enable_inverse_relationships: bool | None = None
    max_entity_degree_override: int | None = None

    @field_validator("max_entity_degree_override")
    @classmethod
    def _reject_nonpositive_degree(cls, v: int | None) -> int | None:
        """A degree cap must be a positive int (matches TriggerExtractionRequest)."""
        if v is not None and v <= 0:
            msg = "max_entity_degree_override must be a positive integer"
            raise ValueError(msg)
        return v


class ReclassifyRequest(BaseModel):
    """Request model for reclassifying a source under a different domain.

    Reclassification resets any prior extraction state (for committed sources)
    and queues a new extraction pass with the specified domain. Use this
    instead of passing ``force_domain`` at upload time — decoupling domain
    selection from the upload flow lets you correct the domain after the
    source has been indexed and inspected.
    """

    domain: str


class BulkConfirmExtractionRequest(BaseModel):
    """Confirm many parked sources in one call.

    Each source is confirmed independently with its detected domain + the
    proposal's options (no per-item overrides — use the single endpoint to
    override). Per-item failures do not abort the batch.
    """

    source_ids: list[str]


class BulkConfirmItem(BaseModel):
    """Per-source outcome of a bulk confirm (BatchUploadResponse-shaped)."""

    source_id: str
    ok: bool
    error: str | None = None


class BulkConfirmExtractionResponse(BaseModel):
    """Aggregate result for ``POST /sources/confirmation``."""

    confirmed: int
    failed: int
    results: list[BulkConfirmItem]


# ================================
# Extraction Sub-resource
# ================================


@router.post(
    "/confirmation",
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
    },
)
async def confirm_extraction_bulk(
    _: CurrentUsername,
    service: Annotated[SourceService, Depends(get_source_service)],
    request: BulkConfirmExtractionRequest,
) -> BulkConfirmExtractionResponse:
    """Confirm a list of parked sources, each with its detected domain.

    Per-item, independent: one source failing (e.g. wrong status) does not
    abort the others. Already-confirmed or past-gate sources surface as
    ``ok=False`` items (the Core gate raises ``ConflictError``, which the
    bulk loop catches per-item).

    **Returns 202 Accepted** with a per-item ``{source_id, ok, error}`` envelope.
    """
    return await service.confirm_extraction_bulk(source_ids=request.source_ids)


@router.post(
    "/{source_id}/extraction",
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
        **CONFLICT_RESPONSE,
        **SERVICE_UNAVAILABLE_RESPONSE,
    },
)
async def trigger_extraction(
    _: CurrentUsername,
    source_id: str,
    service: Annotated[SourceService, Depends(get_source_service)],
    request: TriggerExtractionRequest | None = None,
) -> dict[str, Any]:
    """Trigger entity extraction on an indexed source.

    Validates the source exists and is in a valid state for extraction,
    checks that an LLM provider is configured, and queues the extraction job.

    **Request Body (optional):**
    - ``analysis_depth``: Extraction depth - quick or full (default: full)
    - ``domain``: Force a specific extraction domain (optional, auto-detected if omitted)
    - ``force``: If True, allow re-extraction on already-committed sources (default: False)

    **Returns 202 Accepted** with source_id and status.

    **Errors:**
    - 404: Source not found
    - 400: Source is not in a valid state for extraction
    - 503: No LLM provider configured
    """
    body = request or TriggerExtractionRequest()
    return await service.trigger_extraction(
        source_id=source_id,
        analysis_depth=body.analysis_depth,
        domain=body.domain,
        force=body.force,
        filtering_mode=body.filtering_mode,
        content_filtering=body.content_filtering,
        enable_direction_correction=body.enable_direction_correction,
        protect_orphans=body.protect_orphans,
        enable_inverse_relationships=body.enable_inverse_relationships,
        max_entity_degree_override=body.max_entity_degree_override,
    )


@router.post(
    "/{source_id}/confirmation",
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
        **CONFLICT_RESPONSE,
        **SERVICE_UNAVAILABLE_RESPONSE,
    },
)
async def confirm_extraction(
    _: CurrentUsername,
    source_id: str,
    service: Annotated[SourceService, Depends(get_source_service)],
    request: ConfirmExtractionRequest | None = None,
) -> dict[str, Any]:
    """Confirm a source's detected domain + options and release it for extraction.

    State-aware (handles the wizard's confirm-vs-gate race). Persists the chosen
    domain and any option overrides, then branches on the source's status:

    - **Parked** (``awaiting_confirmation``): CAS-flips to ``indexed`` and
      re-queues the normal extraction path.
    - **Pre-gate** (``pending`` / ``indexing`` / ``vision_pending`` / ``indexed``,
      not yet confirmed): records the decision without changing status or
      re-queueing; the analysis stage then proceeds on its own.

    **Returns 202 Accepted** with ``{source_id, status}``.

    **Errors:**
    - 404: Source not found.
    - 409: Source is past the extraction gate, already confirmed, or errored.
    """
    body = request or ConfirmExtractionRequest()
    return await service.confirm_extraction(
        source_id=source_id,
        analysis_depth=body.analysis_depth,
        domain=body.domain,
        filtering_mode=body.filtering_mode,
        content_filtering=body.content_filtering,
        enable_direction_correction=body.enable_direction_correction,
        protect_orphans=body.protect_orphans,
        enable_inverse_relationships=body.enable_inverse_relationships,
        max_entity_degree_override=body.max_entity_degree_override,
    )


@router.get(
    "/{source_id}/extraction",
    response_model=ExtractionStatusResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
)
async def get_extraction_status(
    _: CurrentUsername,
    source_id: str,
    service: Annotated[SourceService, Depends(get_source_service)],
) -> ExtractionStatusResponse:
    """Get extraction status and progress for a source.

    Returns detailed extraction progress information including:
    - Current extraction job status
    - Chunk-level progress with ETA
    - Entity and relationship counts

    **Errors:**
    - 404: Source not found
    """
    try:
        return ExtractionStatusResponse(**service.get_extraction_status(source_id))
    except ValueError as e:
        raise resource_not_found_error("source", source_id) from e


@router.delete(
    "/{source_id}/extraction",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
)
async def cancel_extraction(
    _: CurrentUsername,
    source_id: str,
    service: Annotated[SourceService, Depends(get_source_service)],
) -> Response:
    """Cancel extraction for a source.

    Cancels all pending and queued extraction chunks for the current job.
    Already running or completed chunks are not affected.
    Source status reverts to 'indexed' (RAG still usable).

    **Errors:**
    - 404: Source not found or no active extraction job
    """
    try:
        await service.cancel_extraction(source_id)
    except ValueError as e:
        raise resource_not_found_error("source", source_id) from e
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{source_id}/reclassify",
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
        **CONFLICT_RESPONSE,
        **SERVICE_UNAVAILABLE_RESPONSE,
    },
)
async def reclassify_source(
    _: CurrentUsername,
    source_id: str,
    request: ReclassifyRequest,
    service: Annotated[SourceService, Depends(get_source_service)],
) -> dict[str, Any]:
    """Reclassify a source under a different extraction domain.

    Changes the domain used for entity extraction and queues a new
    extraction pass. For sources that have already been committed, this
    atomically resets graph artifacts (nodes, edges, templates) before
    dispatching so the new extraction starts clean.

    **When to use:** When auto-detection picked the wrong domain, or when
    you want to re-run extraction with a different domain template after
    inspecting the initial results. Prefer this over setting ``domain``
    at upload time — reclassify decouples domain selection from the upload
    flow.

    **Eligible statuses:** ``indexed`` (extraction never run) or ``committed``
    (re-extraction with full reset).

    **Request body:**
    - ``domain``: Domain name to use (e.g. ``"medical"``, ``"legal"``).
      Use ``GET /api/v1/sources/domains`` to list available domains.

    **Returns 202 Accepted** with source_id and status.

    **Errors:**
    - 404: Source not found
    - 400: Source is not in a valid state for reclassification
    - 503: No LLM provider configured
    """
    return await service.reclassify_source(
        source_id=source_id,
        domain=request.domain,
    )


# ================================
# Extraction Task Endpoints
# ================================


@router.get(
    "/{source_id}/extraction/tasks",
    response_model=ExtractionTaskListResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
    },
)
async def get_extraction_tasks(
    _: CurrentUsername,
    source_id: str,
    service: Annotated[SourceService, Depends(get_source_service)],
    pagination: PageParams,
    include_content: bool = Query(False, description="Include input_text and llm_response_json"),
) -> dict[str, Any]:
    """Get extraction tasks (LLM processing groups) for a source.

    Returns detailed information about how chunks were grouped and processed
    by the LLM during entity extraction. Useful for debugging and analytics.

    Args:
        source_id: The source file ID
        service: Source service (injected)
        pagination: Validated (page, page_size) tuple (injected)
        include_content: If True, include full input_text and llm_response_json.
            If False (default), only include lengths for performance.
    """
    page, page_size = pagination
    return service.get_extraction_tasks(
        source_id=source_id,
        page=page,
        page_size=page_size,
        include_content=include_content,
    )


@router.get(
    "/{source_id}/extraction/tasks/{task_id}",
    response_model=ExtractionTaskResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
)
async def get_extraction_task(
    _: CurrentUsername,
    task_id: str,
    service: Annotated[SourceService, Depends(get_source_service)],
) -> dict[str, Any]:
    """Get a single extraction task with full details including content."""
    task = service.get_extraction_task(task_id)
    return raise_if_not_found(task, "Extraction task not found")


@router.get(
    "/{source_id}/extraction/stats",
    response_model=ExtractionTaskStatsResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
)
async def get_extraction_task_stats(
    _: CurrentUsername,
    source_id: str,
    service: Annotated[SourceService, Depends(get_source_service)],
) -> dict[str, Any]:
    """Get aggregate statistics for extraction tasks.

    Returns min/avg/max statistics for tokens, duration, and other metrics
    computed via SQL aggregates. This provides accurate data for ALL tasks
    without loading every row, enabling efficient analytics charts.
    """
    stats = service.get_extraction_task_stats(source_id)
    return raise_if_not_found(stats, "No extraction statistics available for this source")


@router.get(
    "/{source_id}/extraction/charts",
    response_model=list[ExtractionTaskChartPoint],
    responses={
        **COMMON_ERROR_RESPONSES,
    },
)
async def get_extraction_tasks_for_charts(
    _: CurrentUsername,
    source_id: str,
    service: Annotated[SourceService, Depends(get_source_service)],
) -> list[ExtractionTaskChartPoint]:
    """Get all extraction tasks with minimal fields for chart rendering.

    Returns only the fields needed for charts (no pagination, no content):
    - chunk_index, status, retry_count, entity_count, relationship_count,
      input_text_length, llm_duration_ms

    This enables charts to display data for ALL tasks efficiently.
    """
    return [
        ExtractionTaskChartPoint(**row)
        for row in service.get_extraction_tasks_for_charts(source_id)
    ]


@router.get(
    "/{source_id}/extraction/filteringlog",
    response_model=FilteringLogResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
)
async def get_extraction_filtering_log(
    _: CurrentUsername,
    source_id: str,
    service: Annotated[SourceService, Depends(get_source_service)],
) -> FilteringLogResponse:
    """Get cross-chunk deduplication filtering log.

    Returns the filtering log from the cross-chunk deduplication stage,
    stored in extraction_results.metadata.filtering_log. This shows
    entities and relationships removed during structural filtering,
    exact/semantic dedup, and relationship dedup.

    **Response shape note:** The filtering log is pipeline-internal and
    evolves as new filtering stages are added, so the response is exposed
    as a free-form ``dict[str, Any]`` (aliased as ``FilteringLogResponse``
    for OpenAPI naming); see the DTO docstring for rationale.

    **Errors:**
    - 404: Source not found or no filtering log available
    """
    log = service.get_cross_chunk_filtering_log(source_id)
    return raise_if_not_found(log, "No cross-chunk filtering log available for this source")


# ================================
# Processing Control
# ================================


@router.delete(
    "/{source_id}/processing",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
)
async def abort_processing(
    _: CurrentUsername,
    source_id: str,
    service: Annotated[SourceService, Depends(get_source_service)],
) -> Response:
    """Abort all in-progress processing for a source.

    Cancels all queued/running tasks (indexing or extraction) and resets status.

    **Errors:**
    - 404: Source not found
    - 400: Source is not in a processing state
    """
    try:
        await service.abort_processing(source_id)
    except ValueError as e:
        raise resource_not_found_error("source", source_id) from e
    except RuntimeError as e:
        # Surface the server-constructed reason (e.g. "Source is not currently
        # processing (status: committed)") instead of the generic
        # "Invalid data provided for abort_processing" envelope. The
        # RuntimeError messages here are constructed from internal state,
        # never user input, so they are safe to display.
        raise validation_error("abort_processing", internal_error=e, user_message=str(e)) from e
    return Response(status_code=status.HTTP_204_NO_CONTENT)
