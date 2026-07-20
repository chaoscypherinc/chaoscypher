# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""API endpoints for Sources feature — Core CRUD & Upload.

Source CRUD (list, get, create, update, delete), file upload (single, batch, URL),
tag management, and DI factories.

Sub-routers (registered in main.py):
- extraction_api: Extraction pipeline, commitment, processing control
- chunks_api: Chunks, citations, entities, relationships, templates, LLM metrics
- vision_pages_api: Vision-pipeline per-page retry endpoints
"""

import asyncio
import re
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

import structlog
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse

from chaoscypher_core.app_config import Settings, get_config_manager, get_settings
from chaoscypher_core.llm_queue import get_provider_factory
from chaoscypher_core.operations import queue_utils
from chaoscypher_core.operations.sources.processing import SourceFileValidators
from chaoscypher_core.services.sources import (
    SourceProcessingService as EngineSourceProcessingService,
)
from chaoscypher_core.utils.url_safety import validate_url_safety
from chaoscypher_cortex.features.sources.mappers import (
    add_duration_fields,
    build_domain_fingerprint_map,
    build_domain_icon_map,
    enrich_domain_changed,
    enrich_domain_icons,
)
from chaoscypher_cortex.features.sources.models import (
    BatchUploadError,
    BatchUploadResponse,
    DomainInfo,
    DomainListResponse,
    FilteringMode,
    OrphanTaskCleanupResponse,
    PaginatedSourcesResponse,
    ProcessingStatsResponse,
    RecoveryEventListResponse,
    RecoveryEventResponse,
    SourceImageInfo,
    SourceResponse,
    SourceSummaryResponse,
    SourceUpdate,
    TagResponse,
    UrlImportRequest,
    UrlImportResponse,
)
from chaoscypher_cortex.features.sources.service import SourceService
from chaoscypher_cortex.features.sources.tags_api import get_tag_service
from chaoscypher_cortex.features.sources.upload_service import UploadService
from chaoscypher_cortex.shared.api.dependencies import (
    PageParams,
)
from chaoscypher_cortex.shared.api.errors import (
    raise_if_not_found,
    resource_not_found_error,
    sanitize_filename,
)
from chaoscypher_cortex.shared.api.models import PaginationMetadata
from chaoscypher_cortex.shared.api.responses import (
    AUTH_ERROR_RESPONSES,
    COMMON_ERROR_RESPONSES,
    CONFLICT_RESPONSE,
    NOT_FOUND_RESPONSE,
    ErrorDetail,
)
from chaoscypher_cortex.shared.auth.dependencies import CurrentUsername
from chaoscypher_cortex.shared.repositories.bundle import (
    RepositoryBundle,
    get_repositories,
)


if TYPE_CHECKING:
    from chaoscypher_cortex.features.sources.tag_service import TagService

logger = structlog.get_logger(__name__)

router = APIRouter()


# ================================
# Dependency Injection Factories
# ================================


def get_source_service(
    repos: Annotated[RepositoryBundle, Depends(get_repositories)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> SourceService:
    """Create SourceService with RepositoryBundle."""
    from chaoscypher_core.app_config.engine_factory import (
        build_engine_settings,
    )
    from chaoscypher_core.services.graph.management import (
        SourceService as EngineSourceService,
    )

    engine_service = EngineSourceService(
        repository=repos.adapter,
        database_name=repos.database_name,
        settings=build_engine_settings(settings),
    )

    return SourceService(
        engine_service,
        database_name=repos.database_name,
        settings=settings,
        storage_adapter=repos.adapter,
        graph_repository=repos.graph,
        search_repository=repos.search,
    )


def get_source_processing_service(
    repos: Annotated[RepositoryBundle, Depends(get_repositories)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> EngineSourceProcessingService:
    """Get SourceProcessingService instance for upload/extraction/commit operations."""
    config_manager = get_config_manager()
    factory = get_provider_factory()
    llm_provider = factory.get_chat_provider()

    validators = SourceFileValidators(
        source_manager=repos.adapter,
        llm_provider=llm_provider,
        database_name=repos.database_name,
    )

    return EngineSourceProcessingService(
        source_manager=repos.adapter,
        operations_manager=queue_utils,
        config_manager=config_manager,
        validators=validators,
    )


def get_upload_service(
    settings: Annotated[Settings, Depends(get_settings)],
    source_processing_service: Annotated[
        EngineSourceProcessingService, Depends(get_source_processing_service)
    ],
) -> UploadService:
    """FastAPI-dependency factory for UploadService (CC001)."""
    return UploadService(
        settings=settings,
        source_processing_service=source_processing_service,
    )


# ================================
# Source List & Global Endpoints
# ================================


@router.get(
    "",
    response_model=PaginatedSourcesResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
    },
)
async def list_sources(
    _: CurrentUsername,
    service: Annotated[SourceService, Depends(get_source_service)],
    settings: Annotated[Settings, Depends(get_settings)],
    pagination: PageParams,
    source_type: str | None = Query(default=None),
    status: str | None = Query(default=None),
    enabled: str | None = Query(default=None),
    search: str | None = Query(default=None),
    tag_id: str | None = Query(default=None),
) -> PaginatedSourcesResponse:
    """List all sources with filtering and pagination.

    Filters:
    - source_type: Filter by source type (pdf, text, csv, etc.)
    - status: Filter by processing_status (ready, indexing, extracting,
      awaiting_confirmation, error)
    - enabled: Filter by enabled status ('enabled' or 'disabled')
    - search: Search in title and origin_url
    - tag_id: Filter by tag ID
    """
    page, page_size = pagination
    result = service.list_sources_enriched(
        page=page,
        page_size=page_size,
        source_type=source_type,
        status=status,
        enabled=enabled,
        search=search,
        tag_id=tag_id,
    )
    sources = result["sources"]
    total = result["total"]
    total_pages = (total + page_size - 1) // page_size if total > 0 else 1
    return PaginatedSourcesResponse(
        data=[SourceSummaryResponse(**s) for s in sources],
        pagination=PaginationMetadata(
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
            has_next=page < total_pages,
            has_prev=page > 1,
        ),
    )


@router.get(
    "/domains",
    response_model=DomainListResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
    },
)
async def list_domains(
    _: CurrentUsername,
    settings: Annotated[Settings, Depends(get_settings)],
) -> DomainListResponse:
    """List available extraction domains for the current database.

    Returns built-in domains and any per-database custom domains.
    Use this for UI dropdown when selecting extraction domain.

    **Returns:**
    - domains: List of available domains with name, description, builtin flag,
               extraction_density, and prompt_tokens for capacity calculation
    """
    from chaoscypher_core.app_config.engine_factory import build_engine_settings
    from chaoscypher_core.services.sources.engine.extraction.domains import (
        get_domain_registry,
    )

    registry = get_domain_registry(
        build_engine_settings(settings), database_name=settings.current_database
    )
    return DomainListResponse(
        domains=[DomainInfo(**d) for d in registry.list_domain_info()],
    )


@router.get(
    "/stats",
    response_model=ProcessingStatsResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
    },
)
async def get_processing_stats(
    _: CurrentUsername,
    source_processing_service: Annotated[
        EngineSourceProcessingService, Depends(get_source_processing_service)
    ],
) -> ProcessingStatsResponse:
    """Get processing statistics.

    **Returns:**
    - Total files uploaded
    - Files by status
    - Success/failure rates
    - Other metrics
    """
    return ProcessingStatsResponse(**source_processing_service.get_stats())


@router.post(
    "/url",
    response_model=UrlImportResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
    },
)
async def import_url(
    _: CurrentUsername,
    request: UrlImportRequest,
    settings: Annotated[Settings, Depends(get_settings)],
) -> UrlImportResponse:
    """Import a source from a URL.

    Validates the URL and enqueues an ``OP_FETCH_URL`` job on the
    operations queue. The worker fetches the page, extracts clean
    content as markdown, and feeds it through the standard upload
    pipeline (indexing → extraction → commit). The route returns
    immediately so a slow remote server cannot stall the connection.

    **Request Body:**
    - `url`: URL to import (required, must start with http:// or https://)
    - `extract_entities`: Extract entities and relationships (default: True)
    - `analysis_depth`: Extraction depth - quick or full (default: full)
    - `enable_normalization`: Enable content normalization (default: True)
    - `domain`: Force a specific extraction domain (optional)
    - `auto_confirm`: Bypass the domain-confirmation gate (default: False).

    **Returns 202 Accepted** with the queue task id. Poll the source
    list (or task status) to observe the fetch + indexing progress.

    **Errors:**
    - 400: Invalid URL (blocked scheme, loopback, or cloud metadata)
    - 409: LLM provider has not been verified (``LLM_NOT_VERIFIED``)
    """
    from chaoscypher_core.services.llm import require_extraction_ready

    await require_extraction_ready(settings)

    # Strict policy: block loopback/private/reserved in addition to metadata,
    # closing the DNS-rebinding window for user-submitted URLs.
    if not validate_url_safety(request.url, strict=True):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ErrorDetail(
                code="VALIDATION_FAILED",
                message="URL is not allowed (blocked scheme or cloud metadata endpoint)",
            ).model_dump(),
        )

    domain = request.domain if request.domain and request.domain != "__auto__" else None

    task_id = await queue_utils.queue_fetch_url(
        url=request.url,
        options={
            "auto_analyze": request.extract_entities,
            "extraction_depth": request.analysis_depth,
            "generate_embeddings": True,
            "enable_normalization": request.enable_normalization,
            "enable_vision": request.enable_vision,
            "forced_domain": domain,
            "skip_duplicates": request.skip_duplicates,
            "content_filtering": request.content_filtering,
            "filtering_mode": request.filtering_mode,
            "enable_direction_correction": request.enable_direction_correction,
            "protect_orphans": request.protect_orphans,
            "enable_inverse_relationships": request.enable_inverse_relationships,
            "max_entity_degree_override": request.max_entity_degree_override,
            "auto_confirm": request.auto_confirm,
        },
        database_name=settings.current_database,
        priority=settings.priorities.background,
    )
    return UrlImportResponse(task_id=task_id, url=request.url, status="queued")


# ============================================================================
# Image serving (MUST be before /{source_id} catch-all to avoid route conflict)
# ============================================================================


_SOURCE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_IMAGE_FILENAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}\.png$")


def _validate_source_id(source_id: str) -> None:
    """Reject source IDs that aren't safe URL/path tokens.

    Raises 404 (not 400) so we don't leak which IDs exist vs which
    are structurally invalid.
    """
    if not _SOURCE_ID_RE.match(source_id):
        raise resource_not_found_error("source", source_id)


def _validate_image_filename(filename: str) -> None:
    """Reject filenames that aren't simple <name>.png tokens."""
    if not _IMAGE_FILENAME_RE.match(filename):
        raise resource_not_found_error("image", filename)


@router.get(
    "/{source_id}/images",
    response_model=list[SourceImageInfo],
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
)
async def list_source_images(
    _: CurrentUsername,
    source_id: str,
    settings: Annotated[Settings, Depends(get_settings)],
) -> list[SourceImageInfo]:
    """List available rendered page images for a source document."""
    _validate_source_id(source_id)
    images_dir = (
        Path(str(settings.data_dir))
        / "databases"
        / settings.current_database
        / "images"
        / source_id
    )

    def _scan_images() -> list[SourceImageInfo]:
        """Glob the source's image directory and project to SourceImageInfo entries."""
        if not images_dir.exists():
            return []
        return [
            SourceImageInfo(
                filename=img_file.name,
                url=f"/sources/{source_id}/images/{img_file.name}",
            )
            for img_file in sorted(images_dir.glob("*.png"))
        ]

    return await asyncio.to_thread(_scan_images)


@router.get(
    "/{source_id}/images/{filename}",
    response_class=FileResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
)
async def get_source_image(
    _: CurrentUsername,
    source_id: str,
    filename: str,
    settings: Annotated[Settings, Depends(get_settings)],
) -> FileResponse:
    """Serve a rendered page image for a source document.

    Returns the raw PNG bytes via FastAPI's ``FileResponse`` — no Pydantic
    response model applies to binary file downloads.

    Defense-in-depth: although nginx ``auth_request`` gates this route
    in production, we also enforce ``CurrentUsername``, validate
    ``source_id``/``filename`` against strict regexes, and use
    ``Path.is_relative_to`` for the containment check.
    """
    _validate_source_id(source_id)
    _validate_image_filename(filename)

    base_dir = (
        Path(str(settings.data_dir))
        / "databases"
        / settings.current_database
        / "images"
        / source_id
    ).resolve()
    image_path = (base_dir / filename).resolve()

    if not image_path.is_relative_to(base_dir):
        raise resource_not_found_error("image", filename)

    raise_if_not_found(await asyncio.to_thread(image_path.is_file), "Image not found")
    return FileResponse(str(image_path), media_type="image/png")


# ================================
# Source Detail & CRUD Endpoints
# ================================


@router.get(
    "/{source_id}",
    response_model=SourceResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
)
async def get_source(
    _: CurrentUsername,
    source_id: str,
    service: Annotated[SourceService, Depends(get_source_service)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    """Get a source by ID."""
    source = raise_if_not_found(service.get_source(source_id), "Source not found")
    add_duration_fields(source)

    # Enrich with domain icon
    domain_icons = build_domain_icon_map(settings.current_database)
    enrich_domain_icons([source], domain_icons)

    enrich_domain_changed([source], build_domain_fingerprint_map(settings.current_database))

    return source


@router.post(
    "",
    response_model=SourceResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
    },
)
async def upload_file(
    _: CurrentUsername,
    upload_service: Annotated[UploadService, Depends(get_upload_service)],
    settings: Annotated[Settings, Depends(get_settings)],
    file: UploadFile = File(..., description="File to upload"),
    extract_entities: bool = Form(True, description="Extract entities and relationships"),
    analysis_depth: str = Form("full", description="Extraction depth (quick/full)"),
    enable_normalization: bool | None = Form(
        None,
        description="Enable content normalization (encoding fixes, whitespace cleanup, "
        "OCR cleaning). Defaults on for prose files (PDF, Markdown, audio); "
        "defaults off for structured formats (CSV, JSON, TSV, JSONL, NDJSON, XML). "
        "Explicitly set to override.",
    ),
    domain: str | None = Form(
        None,
        description="Force a specific extraction domain (e.g., 'technical', 'generic'). "
        "If not specified, domain is auto-detected from content.",
    ),
    skip_duplicates: bool = Form(
        False,
        description="Skip upload if identical content already exists (by SHA-256 hash).",
    ),
    enable_vision: bool | None = Form(
        None,
        description="Enable vision processing for images in PDFs and image files. "
        "None=auto (uses vision if model configured), True=force, False=skip.",
    ),
    content_filtering: bool = Form(
        True,
        description="Filter non-essential content from entity extraction. "
        "Filtered content remains searchable.",
    ),
    auto_confirm: bool = Form(
        False,
        description="Bypass the domain-confirmation gate and proceed with the "
        "auto-detected domain. When False (default) an auto-domain source is "
        "parked at 'awaiting_confirmation' after indexing for human review.",
    ),
    filtering_mode: FilteringMode | None = Form(
        None,
        description="Filtering preset override. None = domain default.",
    ),
    enable_direction_correction: bool | None = Form(
        None,
        description=(
            "Phase 4 (2026-05-08): when True, misdirected relationships are "
            "swapped to fix source/target order. When False, they are dropped. "
            "None = use domain config / global default (True)."
        ),
    ),
    protect_orphans: bool | None = Form(
        None,
        description=(
            "Phase 4 (2026-05-08): when True, orphan entities (no relationships) "
            "are kept. When False, they are dropped before commit. "
            "None = use domain config / global default (False)."
        ),
    ),
    enable_inverse_relationships: bool | None = Form(
        None,
        description=(
            "Phase 6 (2026-05-08): when False, inverse edges are NOT created during "
            "commit. When True (or None), domain-declared inverse pairs are added. "
            "None = use global default (True)."
        ),
    ),
    max_entity_degree_override: int | None = Form(
        None,
        description=(
            "Phase 6 (2026-05-08): hard cap on relationships per entity for this source. "
            "Overrides domain config and ExtractionSettings.max_entity_degree. "
            "None = use domain / global default."
        ),
    ),
) -> dict[str, Any]:
    """Upload a file to create a new source.

    **Returns 202 Accepted** immediately for non-blocking upload.

    **Returns 409** with one of:
    - ``LLM_NOT_VERIFIED`` — the selected provider hasn't been verified
      yet. Open Settings → LLM and click Test.
    - ``EXTRACTION_MODEL_MISSING`` — the configured Ollama chat /
      extraction / vision model isn't pulled on any reachable instance.
      ``details.missing_models`` lists the offenders.

    ``enable_normalization`` is tri-state at the API boundary:
    ``True`` / ``False`` are explicit user overrides; ``None`` means
    "use the per-file-type default" and is preserved on the source row
    so the indexing handler resolves it at extraction time. Resolving
    at the route was the legacy path; the row-canonical model (W1,
    2026-05-07) makes the handler the single resolution site so URL
    imports and CLI uploads share the same semantics as multipart
    uploads.
    """
    from chaoscypher_core.services.llm import require_extraction_ready

    await require_extraction_ready(settings)
    # Normalize the auto sentinel exactly like the URL-import path
    # (api.py:~338): ``__auto__`` (and empty) means "no forced domain" so the
    # confirmation gate engages instead of forcing a bogus domain + bypass.
    domain = domain if domain and domain != "__auto__" else None
    return await upload_service.upload_single(
        file=file,
        safe_filename=sanitize_filename(file.filename),
        extract_entities=extract_entities,
        analysis_depth=analysis_depth,
        enable_normalization=enable_normalization,
        forced_domain=domain,
        skip_duplicates=skip_duplicates,
        enable_vision=enable_vision,
        content_filtering=content_filtering,
        auto_confirm=auto_confirm,
        filtering_mode=filtering_mode,
        enable_direction_correction=enable_direction_correction,
        protect_orphans=protect_orphans,
        enable_inverse_relationships=enable_inverse_relationships,
        max_entity_degree_override=max_entity_degree_override,
    )


@router.post(
    "/batch",
    response_model=BatchUploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
    },
)
async def upload_batch(
    _: CurrentUsername,
    upload_service: Annotated[UploadService, Depends(get_upload_service)],
    settings: Annotated[Settings, Depends(get_settings)],
    files: list[UploadFile] = File(..., description="Files to upload"),
    extract_entities: bool = Form(True, description="Extract entities and relationships"),
    analysis_depth: str = Form("full", description="Extraction depth (quick/full)"),
    enable_normalization: bool | None = Form(
        None,
        description="Enable content normalization (encoding fixes, whitespace cleanup, "
        "OCR cleaning). Defaults on for prose files; defaults off for structured formats "
        "(CSV, JSON, TSV, JSONL, NDJSON, XML). Explicitly set to override per-batch.",
    ),
    domain: str | None = Form(
        None,
        description="Force a specific extraction domain. If not specified, auto-detected.",
    ),
    skip_duplicates: bool = Form(
        False,
        description="Skip files whose content already exists (by SHA-256 hash).",
    ),
    enable_vision: bool | None = Form(
        None,
        description="Enable vision processing for images in PDFs and image files. "
        "None=auto (uses vision if model configured), True=force, False=skip.",
    ),
    content_filtering: bool = Form(
        True,
        description="Filter non-essential content from entity extraction. "
        "Filtered content remains searchable.",
    ),
    auto_confirm: bool = Form(
        False,
        description="Bypass the domain-confirmation gate and proceed with the "
        "auto-detected domain. When False (default) an auto-domain source is "
        "parked at 'awaiting_confirmation' after indexing for human review.",
    ),
    filtering_mode: FilteringMode | None = Form(
        None,
        description="Filtering preset override. None = domain default.",
    ),
    enable_direction_correction: bool | None = Form(
        None,
        description=(
            "Phase 4 (2026-05-08): when True, misdirected relationships are "
            "swapped to fix source/target order. When False, they are dropped. "
            "None = use domain config / global default (True)."
        ),
    ),
    protect_orphans: bool | None = Form(
        None,
        description=(
            "Phase 4 (2026-05-08): when True, orphan entities (no relationships) "
            "are kept. When False, they are dropped before commit. "
            "None = use domain config / global default (False)."
        ),
    ),
    enable_inverse_relationships: bool | None = Form(
        None,
        description=(
            "Phase 6 (2026-05-08): when False, inverse edges are NOT created during "
            "commit. When True (or None), domain-declared inverse pairs are added. "
            "None = use global default (True)."
        ),
    ),
    max_entity_degree_override: int | None = Form(
        None,
        description=(
            "Phase 6 (2026-05-08): hard cap on relationships per entity for this source. "
            "Overrides domain config and ExtractionSettings.max_entity_degree. "
            "None = use domain / global default."
        ),
    ),
) -> BatchUploadResponse:
    """Upload multiple files to create new sources.

    **Returns 202 Accepted** immediately for non-blocking batch upload.

    Returns 409 ``LLM_NOT_VERIFIED`` if the LLM provider has not been
    verified — the whole batch is refused; the frontend prompts the
    operator to configure their LLM and retry. Returns 409
    ``EXTRACTION_MODEL_MISSING`` if a configured Ollama model is not
    pulled on any reachable instance.
    """
    from chaoscypher_core.services.llm import require_extraction_ready

    await require_extraction_ready(settings)
    # Normalize the auto sentinel exactly like the URL-import path
    # (api.py:~338): ``__auto__`` (and empty) means "no forced domain" so the
    # confirmation gate engages instead of forcing a bogus domain + bypass.
    domain = domain if domain and domain != "__auto__" else None
    successes, errors = await upload_service.upload_batch(
        files=files,
        sanitize_filename=sanitize_filename,
        extract_entities=extract_entities,
        analysis_depth=analysis_depth,
        enable_normalization=enable_normalization,
        forced_domain=domain,
        skip_duplicates=skip_duplicates,
        enable_vision=enable_vision,
        content_filtering=content_filtering,
        auto_confirm=auto_confirm,
        filtering_mode=filtering_mode,
        enable_direction_correction=enable_direction_correction,
        protect_orphans=protect_orphans,
        enable_inverse_relationships=enable_inverse_relationships,
        max_entity_degree_override=max_entity_degree_override,
    )
    return BatchUploadResponse(
        uploaded=len(successes),
        failed=len(errors),
        files=[SourceResponse(**s) for s in successes],
        errors=[BatchUploadError(filename=fn, error=msg) for fn, msg in errors],
    )


@router.patch(
    "/{source_id}",
    response_model=SourceResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
)
async def update_source(
    _: CurrentUsername,
    source_id: str,
    source_data: SourceUpdate,
    service: Annotated[SourceService, Depends(get_source_service)],
) -> dict[str, Any]:
    """Update a source."""
    source = service.update_source(
        source_id=source_id,
        title=source_data.title,
        processing_status=source_data.processing_status,
        enabled=source_data.enabled,
        user_metadata=source_data.user_metadata,
    )
    return raise_if_not_found(source, "Source not found")


@router.delete(
    "/{source_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
)
async def delete_source(
    _: CurrentUsername,
    source_id: str,
    service: Annotated[SourceService, Depends(get_source_service)],
) -> None:
    """Delete a source (hard delete).

    WARNING: This will cascade delete all chunks and citations!

    The service call is offloaded to a thread to avoid blocking the event
    loop during ``shutil.rmtree`` on the source image directory.
    """
    success = await asyncio.to_thread(service.delete_source, source_id)
    raise_if_not_found(success, "Source not found")


@router.post(
    "/{source_id}/retry",
    response_model=SourceResponse,
    status_code=status.HTTP_200_OK,
    responses={
        **COMMON_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
        **CONFLICT_RESPONSE,
    },
)
async def retry_source(
    _: CurrentUsername,
    source_id: str,
    service: Annotated[SourceService, Depends(get_source_service)],
) -> SourceResponse:
    """Manually retry an errored source.

    Resets the source to the appropriate pre-failure state (based on
    ``error_stage``) and dispatches the next queue task so processing
    resumes immediately. Clears ``error_message``, ``error_stage``, and
    ``recovery_attempts``.

    **Routing by error_stage:**
    - ``commit`` → reset to ``extracted`` (retry commit only)
    - ``extraction`` → reset to ``indexed`` (retry extraction)
    - ``indexing`` / ``recovery_exhausted`` / other → reset to ``pending``

    **Returns 409 Conflict** if the source is not in the ``error`` state.
    """
    return await service.retry_source(source_id)


@router.post(
    "/{source_id}/re_extract",
    response_model=SourceResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        **COMMON_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
        **CONFLICT_RESPONSE,
    },
)
async def reextract_source(
    _: CurrentUsername,
    source_id: str,
    service: Annotated[SourceService, Depends(get_source_service)],
) -> SourceResponse:
    """Manually re-run entity extraction on a source (audit fix #F49).

    Distinct from ``/retry``:

    - **Retry** preserves the cached ``commit_payload`` and re-runs only
      the failed stage (cheap — no LLM tokens spent on commit-only
      retries).
    - **Re-extract** discards the cached payload and any extraction
      results, resets the source to ``indexed``, and re-runs the LLM
      extraction (expensive — costs LLM tokens).

    Allowed transitions:

    - ``committed`` → atomic delete-graph-artifacts + reset to
      ``indexed`` + re-extract.
    - ``error`` (after any post-INDEXING stage) → reset to ``indexed``,
      clear payload, re-extract.
    - ``indexed`` / ``extracted`` / ``extracting`` / ``mcp_extracting`` /
      ``committing`` → forcibly reset to ``indexed``, clear payload,
      re-extract.

    Rejected:

    - ``pending`` / ``indexing`` → 400; the source has not produced any
      extraction artifact yet, so a re-extract is not meaningful — wait
      for indexing to complete (or use the regular extraction trigger).
    """
    return await service.reextract_source(source_id)


@router.get(
    "/{source_id}/recovery_events",
    response_model=RecoveryEventListResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
)
async def list_source_recovery_events(
    _: CurrentUsername,
    source_id: str,
    service: Annotated[SourceService, Depends(get_source_service)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> RecoveryEventListResponse:
    """Return the recovery audit trail for a source.

    Backs the source detail page's recovery panel so operators can
    see which recoveries fired, when, why, and what was dispatched —
    instead of grepping container logs.

    **Returns:** events newest first (max ``limit``).
    """
    if service.get_source(source_id) is None:
        raise resource_not_found_error("source", source_id)

    rows = service.list_recovery_events(source_id, limit=limit)
    return RecoveryEventListResponse(
        events=[RecoveryEventResponse(**r) for r in rows],
    )


# ================================
# Admin / Maintenance Endpoints
# ================================


@router.post(
    "/cleanup/orphan_tasks",
    response_model=OrphanTaskCleanupResponse,
    status_code=status.HTTP_200_OK,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
    },
)
async def cleanup_orphan_tasks(
    _: CurrentUsername,
    service: Annotated[SourceService, Depends(get_source_service)],
) -> OrphanTaskCleanupResponse:
    """Trigger immediate cleanup of orphaned chunk tasks.

    Deletes chunk_extraction_tasks rows with status='orphaned' and
    created_at older than the configured retention period (default 7 days,
    via SourceRecoverySettings.orphan_task_retention_days).

    Also runs periodically as a worker background loop. Use this
    endpoint for ad-hoc cleanup after bulk operations or during
    debugging.
    """
    result = service.cleanup_orphan_tasks()
    return OrphanTaskCleanupResponse(**result)


# ================================
# Tag Endpoints
# ================================


@router.get(
    "/{source_id}/tags",
    response_model=list[TagResponse],
    responses={
        **COMMON_ERROR_RESPONSES,
    },
)
async def get_source_tags(
    _: CurrentUsername,
    source_id: str,
    service: Annotated[TagService, Depends(get_tag_service)],
) -> list[dict[str, Any]]:
    """Get all tags assigned to a source."""
    return service.get_source_tags(source_id)


@router.post(
    "/{source_id}/tags/{tag_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
)
async def assign_tag_to_source(
    _: CurrentUsername,
    source_id: str,
    tag_id: str,
    service: Annotated[TagService, Depends(get_tag_service)],
) -> None:
    """Assign a tag to a source."""
    try:
        service.assign_tag(source_id, tag_id)
    except ValueError as e:
        raise resource_not_found_error("source_or_tag", source_id) from e


@router.delete(
    "/{source_id}/tags/{tag_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
)
async def unassign_tag_from_source(
    _: CurrentUsername,
    source_id: str,
    tag_id: str,
    service: Annotated[TagService, Depends(get_tag_service)],
) -> None:
    """Unassign a tag from a source."""
    success = service.unassign_tag(source_id, tag_id)
    raise_if_not_found(success, "Tag assignment not found")


# (Image serving routes are above /{source_id} to avoid route conflicts)
