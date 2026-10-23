# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""API endpoints for imported docs sites (spec 2026-10-22-site-import-design, decision 5).

Mounted at ``/sources/sites`` before the sources router, whose
``/{source_id}`` routes would otherwise take ``sites`` as a source id.

- ``POST /sources/sites/previews``: discover a site's pages, import nothing.
- ``POST /sources/sites``: create the site and enqueue ``import_site`` (202).
- ``GET /sources/sites``: the sites, paginated, each with its progress.
- ``GET /sources/sites/{site_id}``: one site with its progress.
- ``GET /sources/sites/{site_id}/pages``: the site's pages, as source summaries.
- ``POST /sources/sites/{site_id}/refreshes``: check the site for updates (202).
- ``DELETE /sources/sites/{site_id}``: delete the site (``?delete_pages=true``
  also deletes its pages through the source delete).

Resource nouns, not verbs (API_DESIGN.md): a preview and a refresh are
resources a POST creates.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from chaoscypher_core.app_config import Settings, get_settings
from chaoscypher_cortex.features.sources.api import get_source_service
from chaoscypher_cortex.features.sources.models import (
    PaginatedSitesResponse,
    PaginatedSourcesResponse,
    SiteImportRequest,
    SitePreviewRequest,
    SitePreviewResponse,
    SiteRefreshRequest,
    SiteResponse,
)
from chaoscypher_cortex.features.sources.service import SourceService
from chaoscypher_cortex.features.sources.sites_repository import SitesRepository
from chaoscypher_cortex.features.sources.sites_service import SitesService
from chaoscypher_cortex.shared.api.dependencies import PageParams
from chaoscypher_cortex.shared.api.responses import (
    AUTH_ERROR_RESPONSES,
    COMMON_ERROR_RESPONSES,
    CONFLICT_RESPONSE,
    NOT_FOUND_RESPONSE,
)
from chaoscypher_cortex.shared.auth.dependencies import CurrentUsername
from chaoscypher_cortex.shared.repositories.bundle import RepositoryBundle, get_repositories


router = APIRouter()


def get_sites_service(
    repos: Annotated[RepositoryBundle, Depends(get_repositories)],
    settings: Annotated[Settings, Depends(get_settings)],
    source_service: Annotated[SourceService, Depends(get_source_service)],
) -> SitesService:
    """Create the SitesService over the request's adapter and the existing source delete."""
    return SitesService(
        SitesRepository(repos.adapter, repos.database_name),
        settings=settings,
        delete_source=source_service.delete_source,
        list_sources=source_service.list_sources_enriched,
    )


SitesServiceDep = Annotated[SitesService, Depends(get_sites_service)]


@router.post(
    "/previews",
    response_model=SitePreviewResponse,
    status_code=status.HTTP_200_OK,
    responses={**COMMON_ERROR_RESPONSES, **AUTH_ERROR_RESPONSES},
)
async def create_site_preview(
    _: CurrentUsername,
    request: SitePreviewRequest,
    service: SitesServiceDep,
) -> SitePreviewResponse:
    """Discover the pages of the site at an address, without importing anything.

    The address passes the strict SSRF check first (400 when refused). The
    answer names the site, how its pages were found (``llms_txt``,
    ``sitemap``, ``crawl``, or ``none``), how many there are, the pages, and
    whether the count hit ``web.site_max_pages`` (``capped``).
    """
    return await service.preview(request.url)


@router.post(
    "",
    response_model=SiteResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={**COMMON_ERROR_RESPONSES, **AUTH_ERROR_RESPONSES, **CONFLICT_RESPONSE},
)
async def create_site(
    _: CurrentUsername,
    request: SiteImportRequest,
    service: SitesServiceDep,
    settings: Annotated[Settings, Depends(get_settings)],
) -> SiteResponse:
    """Import every page of a docs site (but the ``exclude``d ones) as its own source.

    Creates the site and enqueues ``import_site``; returns **202** with the
    site (status ``queued``). Poll ``GET /sources/sites/{id}`` for its
    progress. ``options`` are the single-URL import's upload options,
    applied to every page (no page waits for domain confirmation, and
    ``skip_duplicates`` does not apply to a site's pages).

    **Errors:** 400 for an address the SSRF check refuses or too many
    ``exclude`` entries; 409 when the site (the same address scope) is already
    imported, with that site in ``details.site`` (refresh it instead), or when
    the LLM provider has not been verified.
    """
    from chaoscypher_core.services.llm import require_extraction_ready

    await require_extraction_ready(settings)
    return await service.create(request)


@router.get(
    "",
    response_model=PaginatedSitesResponse,
    responses={**COMMON_ERROR_RESPONSES, **AUTH_ERROR_RESPONSES},
)
async def list_sites(
    _: CurrentUsername,
    service: SitesServiceDep,
    pagination: PageParams,
) -> PaginatedSitesResponse:
    """List the imported sites, newest first, each with its progress (``?page=&page_size=``)."""
    page, page_size = pagination
    return service.list_sites(page=page, page_size=page_size)


@router.get(
    "/{site_id}",
    response_model=SiteResponse,
    responses={**COMMON_ERROR_RESPONSES, **AUTH_ERROR_RESPONSES, **NOT_FOUND_RESPONSE},
)
async def get_site(
    _: CurrentUsername,
    site_id: str,
    service: SitesServiceDep,
) -> SiteResponse:
    """One site with its progress, computed from its pages counted by status."""
    return service.get(site_id)


@router.get(
    "/{site_id}/pages",
    response_model=PaginatedSourcesResponse,
    responses={**COMMON_ERROR_RESPONSES, **AUTH_ERROR_RESPONSES, **NOT_FOUND_RESPONSE},
)
async def list_site_pages(
    _: CurrentUsername,
    site_id: str,
    service: SitesServiceDep,
    pagination: PageParams,
) -> PaginatedSourcesResponse:
    """The site's pages, newest first, as the sources list shows them (``?page=&page_size=``).

    Each is a source summary with its ``origin_url``; ``site_gone_at`` is set on a
    page a refresh no longer found on the site (kept until the user removes it).
    """
    page, page_size = pagination
    return service.list_pages(site_id, page=page, page_size=page_size)


@router.post(
    "/{site_id}/refreshes",
    response_model=SiteResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
        **CONFLICT_RESPONSE,
    },
)
async def create_site_refresh(
    _: CurrentUsername,
    site_id: str,
    service: SitesServiceDep,
    settings: Annotated[Settings, Depends(get_settings)],
    request: SiteRefreshRequest | None = None,
) -> SiteResponse:
    """Check a site for updates ("Check for updates").

    Enqueues a refresh and returns **202** with the site (status ``queued``):
    discovery runs again and every page is fetched; an unchanged page is left
    alone, a changed one is re-imported in place, a new one is added, and one
    the site no longer lists is marked gone (``progress.gone``), never deleted.
    The pages the site leaves out (``excluded_pages``) stay out; a body with
    ``exclude`` replaces them from now on (``[]`` brings every page back).

    **Errors:** 400 for more ``exclude`` entries than ``web.site_max_pages``;
    404 for an unknown site; 409 while a live run of it is queued
    or under way (a run past the operations timeout is stale and does not
    count; the refresh resumes it), or when the LLM provider has not been
    verified.
    """
    from chaoscypher_core.services.llm import require_extraction_ready

    await require_extraction_ready(settings)
    return await service.refresh(site_id, exclude=request.exclude if request else None)


@router.delete(
    "/{site_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
        **CONFLICT_RESPONSE,
    },
)
async def delete_site(
    _: CurrentUsername,
    site_id: str,
    service: SitesServiceDep,
    delete_pages: Annotated[
        bool,
        Query(description="Also delete the site's pages (through the source delete)."),
    ] = False,
) -> None:
    """Delete a site. Its pages stay as ordinary sources unless ``delete_pages`` is true.

    **Errors:** 404 for an unknown site; 409 while a live run of it is queued
    or under way (wait for it, or for it to go stale).
    """
    await service.delete(site_id, delete_pages=delete_pages)


__all__ = ["get_sites_service", "router"]
