# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""A site's progress is computed from its pages counted by status (design decision 6)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from chaoscypher_core.services.sources.web import SiteProgress, progress_by_site


if TYPE_CHECKING:
    from chaoscypher_core.ports.storage_sites import SitePageCount


def _count(
    status: str, count: int, *, site: str = "s1", auto_analyze: bool = True, gone: bool = False
) -> SitePageCount:
    return {
        "site_id": site,
        "status": status,
        "auto_analyze": auto_analyze,
        "gone": gone,
        "count": count,
    }


def test_pages_fold_into_done_failed_waiting_and_in_progress() -> None:
    progress = progress_by_site(
        [
            _count("committed", 3),
            _count("indexed", 2, auto_analyze=False),  # imported without extraction: finished
            _count("indexed", 1),  # extraction still to come
            _count("extracting", 1),
            _count("error", 2),
            _count("awaiting_confirmation", 1),
            _count("committed", 4, gone=True),
            _count("pending", 7, site="s2"),
        ]
    )
    assert progress["s1"] == SiteProgress(
        total=10,
        done=5,
        failed=2,
        awaiting_confirmation=1,
        in_progress=2,
        gone=4,
        by_status={
            "committed": 3,
            "indexed": 3,
            "extracting": 1,
            "error": 2,
            "awaiting_confirmation": 1,
        },
    )
    assert progress["s2"].total == 7 and progress["s2"].in_progress == 7


def test_a_site_with_no_pages_has_empty_progress() -> None:
    assert progress_by_site([]) == {}
    assert SiteProgress.empty() == SiteProgress(
        total=0, done=0, failed=0, awaiting_confirmation=0, in_progress=0, gone=0, by_status={}
    )
