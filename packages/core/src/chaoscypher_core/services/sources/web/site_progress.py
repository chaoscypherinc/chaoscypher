# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""A site's progress, computed from its pages counted by status (site import design, decision 6).

Nothing about progress is stored on the site row: every reader (the API's site
routes, and the CLI and MCP surfaces that follow) folds the same page counts
(``SiteStorageProtocol.count_site_pages``) through :func:`progress_by_site`.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from chaoscypher_core.models import SourceStatus


if TYPE_CHECKING:
    from chaoscypher_core.ports.storage_sites import SitePageCount


class SiteProgress(BaseModel):
    """How far a site's pages have come.

    Every count but ``gone`` covers the pages the site still lists; a gone
    page (one a refresh no longer found) is counted only in ``gone``.

    Attributes:
        total: Pages the site lists (the pages of "43 of 71 pages").
        done: Finished pages: committed to the graph, or indexed when the
            page was imported without entity extraction.
        failed: Pages in ``error`` (a fetch that failed, or a later stage).
        awaiting_confirmation: Pages parked for the user to confirm the
            detected domain.
        in_progress: Every other listed page (fetching, indexing,
            extracting, committing).
        gone: Pages the site no longer lists (kept, never deleted).
        by_status: Listed pages per ``SourceStatus`` value.
    """

    total: int = Field(ge=0)
    done: int = Field(ge=0)
    failed: int = Field(ge=0)
    awaiting_confirmation: int = Field(ge=0)
    in_progress: int = Field(ge=0)
    gone: int = Field(ge=0)
    by_status: dict[str, int]

    @classmethod
    def empty(cls) -> SiteProgress:
        """The progress of a site with no pages yet."""
        return cls(
            total=0, done=0, failed=0, awaiting_confirmation=0, in_progress=0, gone=0, by_status={}
        )


def _is_done(status: str, *, auto_analyze: bool) -> bool:
    """Committed, or indexed when the page was imported without extraction."""
    if status == SourceStatus.COMMITTED:
        return True
    return status == SourceStatus.INDEXED and not auto_analyze


def progress_by_site(counts: Iterable[SitePageCount]) -> dict[str, SiteProgress]:
    """Fold page counts into one :class:`SiteProgress` per site that has pages.

    Args:
        counts: Page counts grouped by site, status, ``auto_analyze`` and
            gone mark (``count_site_pages``).

    Returns:
        ``site_id -> SiteProgress``; a site with no pages is absent (use
        :meth:`SiteProgress.empty`).
    """
    tallies: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    by_status: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for row in counts:
        tally = tallies[row["site_id"]]
        n = row["count"]
        if row["gone"]:
            tally["gone"] += n
            continue
        status = row["status"]
        by_status[row["site_id"]][status] += n
        tally["total"] += n
        if _is_done(status, auto_analyze=row["auto_analyze"]):
            tally["done"] += n
        elif status == SourceStatus.ERROR:
            tally["failed"] += n
        elif status == SourceStatus.AWAITING_CONFIRMATION:
            tally["awaiting_confirmation"] += n
        else:
            tally["in_progress"] += n
    return {
        site_id: SiteProgress(
            total=t["total"],
            done=t["done"],
            failed=t["failed"],
            awaiting_confirmation=t["awaiting_confirmation"],
            in_progress=t["in_progress"],
            gone=t["gone"],
            by_status=dict(by_status[site_id]),
        )
        for site_id, t in tallies.items()
    }


__all__ = ["SiteProgress", "progress_by_site"]
