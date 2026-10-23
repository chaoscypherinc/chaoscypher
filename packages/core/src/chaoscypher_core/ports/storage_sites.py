# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Site storage port: imported docs sites and their pages (migration 0021).

A site is a ``source_sites`` row; each of its pages is a source whose
``site_id`` names it. Sync ``def`` methods returning TypedDicts (``data["key"]``
access, CC002), like the rest of the storage-port surface. Satisfied by
``SourceSitesMixin`` on the SQLite adapter.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, Protocol, TypedDict


if TYPE_CHECKING:
    from datetime import datetime


class SourceSiteRecord(TypedDict):
    """One imported site. ``status`` is a ``SiteStatus`` value.

    ``run_started_at``: when the current run was queued or started (written
    with every in-progress status). ``run_cursor``: how many of the discovered
    pages the open pass has handled, ``None`` when no pass is open.
    ``excluded_pages``: the page identities (``page_identity``) the user left
    out of the site, sorted; a refresh keeps them out (empty: none).
    """

    id: str
    database_name: str
    url: str
    title: str
    discovery_method: str | None
    status: str
    page_count: int
    options: dict[str, Any] | None
    error_message: str | None
    created_at: datetime
    last_checked_at: datetime | None
    run_started_at: datetime | None
    run_cursor: int | None
    excluded_pages: list[str]


class SitePageRecord(TypedDict):
    """The columns of one page (a source row) a site run reads."""

    id: str
    origin_url: str | None
    title: str | None
    status: str
    error_stage: str | None
    auto_analyze: bool
    content_hash: str | None
    filepath: str
    site_gone_at: datetime | None


class SitePageCount(TypedDict):
    """How many of a site's pages share a status, an ``auto_analyze`` choice and a gone mark."""

    site_id: str
    status: str
    auto_analyze: bool
    gone: bool
    count: int


class SiteStorageProtocol(Protocol):
    """Sites and the pages that name them."""

    def create_site(
        self,
        *,
        site_id: str,
        database_name: str,
        url: str,
        title: str,
        status: str,
        options: Mapping[str, Any] | None,
        excluded_pages: Sequence[str] = (),
    ) -> SourceSiteRecord:
        """Insert a site row and return it (``excluded_pages``: identities left out)."""
        ...

    def get_site(self, site_id: str, database_name: str) -> SourceSiteRecord | None:
        """Return the site, or ``None`` when it is not in ``database_name``."""
        ...

    def list_sites(
        self, database_name: str, *, page: int, page_size: int
    ) -> tuple[list[SourceSiteRecord], int]:
        """Return one page of sites, newest first, and the total count."""
        ...

    def update_site(self, site_id: str, database_name: str, updates: Mapping[str, Any]) -> None:
        """Write the given columns of a site row (a missing row is a no-op)."""
        ...

    def list_running_sites(self, database_name: str) -> list[SourceSiteRecord]:
        """Every site whose run is queued or under way (by its status)."""
        ...

    def delete_site(self, site_id: str, database_name: str) -> bool:
        """Delete the site row (its pages' ``site_id`` is set to NULL); False when absent."""
        ...

    def list_site_pages(self, site_id: str, database_name: str) -> list[SitePageRecord]:
        """Return every page of the site, gone ones included."""
        ...

    def count_site_pages(self, site_ids: Sequence[str], database_name: str) -> list[SitePageCount]:
        """Count the sites' pages grouped by site, status, ``auto_analyze`` and gone mark."""
        ...

    def set_site_pages_gone(
        self, source_ids: Sequence[str], database_name: str, gone_at: datetime | None
    ) -> int:
        """Mark pages gone at ``gone_at`` (``None`` clears the mark); the rows changed."""
        ...

    def restage_site_page(
        self,
        source_id: str,
        database_name: str,
        *,
        payload: bytes,
        content_hash: str,
        title: str,
    ) -> None:
        """Write a page's new content over its staged file and send the row back to PENDING."""
        ...


__all__ = [
    "SitePageCount",
    "SitePageRecord",
    "SiteStorageProtocol",
    "SourceSiteRecord",
]
