# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Repository for imported docs sites — VSA wrapper around SiteStorageProtocol.

The Cortex service layer never reaches into Core's storage adapter directly.
This thin wrapper exposes the site rows and their pages' counts and returns
the storage TypedDicts unchanged; the service maps them to Pydantic
responses. The list reads project their columns (``load_only``) in the
adapter's ``SourceSitesMixin``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from chaoscypher_core.ports.storage_sites import (
        SitePageCount,
        SitePageRecord,
        SiteStorageProtocol,
        SourceSiteRecord,
    )


class SitesRepository:
    """VSA repository over SiteStorageProtocol, scoped to one database."""

    def __init__(self, storage: SiteStorageProtocol, database_name: str) -> None:
        """Initialize the repository.

        Args:
            storage: The Core site storage adapter (port).
            database_name: Active database name; every read and write is
                scoped to it.
        """
        self._storage = storage
        self._database_name = database_name

    @property
    def database_name(self) -> str:
        """The database every call is scoped to."""
        return self._database_name

    def create(
        self, *, url: str, options: Mapping[str, Any], exclude: Sequence[str] | None
    ) -> SourceSiteRecord:
        """Insert a queued site row (Core's one rule: ``site_runs.create_site_row``)."""
        from chaoscypher_core.operations.sources.site_runs import create_site_row

        return create_site_row(
            self._storage,
            url=url,
            database_name=self._database_name,
            options=options,
            exclude=exclude,
        )

    def begin_refresh(self, site_id: str, *, exclude: Sequence[str] | None) -> None:
        """Queue the site for a check (Core's one rule: ``site_runs.begin_site_refresh``)."""
        from chaoscypher_core.operations.sources.site_runs import begin_site_refresh

        begin_site_refresh(self._storage, site_id, self._database_name, exclude=exclude)

    def get(self, site_id: str) -> SourceSiteRecord | None:
        """Return the site, or ``None``."""
        return self._storage.get_site(site_id, self._database_name)

    def list_sites(self, *, page: int, page_size: int) -> tuple[list[SourceSiteRecord], int]:
        """Return one page of sites, newest first, and the total count."""
        return self._storage.list_sites(self._database_name, page=page, page_size=page_size)

    def update(self, site_id: str, updates: Mapping[str, Any]) -> None:
        """Write columns of a site row."""
        self._storage.update_site(site_id, self._database_name, updates)

    def delete(self, site_id: str) -> bool:
        """Delete the site row (its pages stay, unlinked)."""
        return self._storage.delete_site(site_id, self._database_name)

    def list_pages(self, site_id: str) -> list[SitePageRecord]:
        """Every page of the site."""
        return self._storage.list_site_pages(site_id, self._database_name)

    def find_by_scope(self, url: str, *, page_size: int) -> SourceSiteRecord | None:
        """The site already imported for *url* (Core's one rule: the same scope)."""
        from chaoscypher_core.operations.sources.site_runs import find_site_by_scope

        return find_site_by_scope(self._storage, self._database_name, url, page_size=page_size)

    def count_pages(self, site_ids: Sequence[str]) -> list[SitePageCount]:
        """The sites' pages counted by status, ``auto_analyze`` and gone mark."""
        return self._storage.count_site_pages(site_ids, self._database_name)


__all__ = ["SitesRepository"]
