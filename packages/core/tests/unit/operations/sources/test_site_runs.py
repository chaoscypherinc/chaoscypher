# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The site rules every surface shares (``operations.sources.site_runs``).

A run is live only while its status says so and it started within the
operations worker's timeout (a killed worker leaves the status behind); a
site left running with no live task is failed at worker startup; one site
scope is imported once per database; a site's pages never wait for domain
confirmation and are never skipped as duplicates. Real migrated database.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import evict_engine
from chaoscypher_core.database.migrations.runner import upgrade_to_head
from chaoscypher_core.exceptions import ConflictError
from chaoscypher_core.models import SiteStatus
from chaoscypher_core.operations.sources.site_runs import (
    SITE_RUN_INTERRUPTED_MESSAGE,
    fail_interrupted_runs,
    find_site_by_scope,
    refuse_while_running,
    run_stale_after_seconds,
    site_page_options,
    site_run_live,
)


if TYPE_CHECKING:
    from chaoscypher_core.ports.storage_sites import SourceSiteRecord


DB = "default"


@pytest.fixture
def adapter(tmp_path: Path) -> Iterator[SqliteAdapter]:
    db_path = tmp_path / "app.db"
    upgrade_to_head(db_path)
    built = SqliteAdapter(str(db_path), database_name=DB)
    built.connect()
    yield built
    built.disconnect()
    evict_engine(db_path)


def _site(
    adapter: SqliteAdapter, site_id: str, status: str, url: str = "https://d.example.com"
) -> SourceSiteRecord:
    return adapter.create_site(
        site_id=site_id, database_name=DB, url=url, title=site_id, status=status, options={}
    )


def test_every_in_progress_write_stamps_the_run_start(adapter: SqliteAdapter) -> None:
    created = _site(adapter, "s1", SiteStatus.QUEUED)
    assert created["run_started_at"] is not None
    idle = _site(adapter, "s2", SiteStatus.IMPORTED)
    assert idle["run_started_at"] is None

    adapter.update_site("s2", DB, {"status": SiteStatus.REFRESHING})

    stored = adapter.get_site("s2", DB)
    assert stored is not None and stored["run_started_at"] is not None


def test_a_run_older_than_the_operations_timeout_is_stale(adapter: SqliteAdapter) -> None:
    site = _site(adapter, "s1", SiteStatus.IMPORTING)
    now = datetime.now(UTC)
    limit = run_stale_after_seconds()

    assert site_run_live(site, now=now)
    assert not site_run_live(site, now=now + timedelta(seconds=limit + 1))
    adapter.update_site("s1", DB, {"status": SiteStatus.IMPORTED})
    finished = adapter.get_site("s1", DB)
    assert finished is not None and not site_run_live(finished, now=now)


def test_the_stale_limit_is_the_operations_timeout_plus_margin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from chaoscypher_core.app_config import get_settings
    from chaoscypher_core.queue.worker_timeouts import RECONCILER_SAFETY_MARGIN_SECONDS

    monkeypatch.setattr(
        "chaoscypher_core.queue.worker_timeouts.read_workers_yaml_timeout", lambda _t: None
    )
    expected = get_settings().timeouts.operations_worker_default + RECONCILER_SAFETY_MARGIN_SECONDS
    assert run_stale_after_seconds() == expected


def test_refuse_while_running_ignores_a_stale_run(adapter: SqliteAdapter) -> None:
    live = _site(adapter, "live", SiteStatus.REFRESHING)
    with pytest.raises(ConflictError):
        refuse_while_running(live, "a refresh")

    old = datetime.now(UTC) - timedelta(seconds=run_stale_after_seconds() + 60)
    adapter.update_site("live", DB, {"status": SiteStatus.REFRESHING, "run_started_at": old})
    stale = adapter.get_site("live", DB)
    assert stale is not None
    refuse_while_running(stale, "a refresh")  # no error: the run is stale


def test_fail_interrupted_runs_spares_a_run_with_a_live_task(adapter: SqliteAdapter) -> None:
    _site(adapter, "queued-live", SiteStatus.QUEUED)
    _site(adapter, "queued-dead", SiteStatus.QUEUED)
    _site(adapter, "importing", SiteStatus.IMPORTING)
    _site(adapter, "refreshing", SiteStatus.REFRESHING)
    _site(adapter, "done", SiteStatus.IMPORTED)
    adapter.update_site("importing", DB, {"run_cursor": 3})

    failed = fail_interrupted_runs(
        adapter, DB, has_live_task=lambda site: site["id"] == "queued-live"
    )

    assert sorted(failed) == ["importing", "queued-dead", "refreshing"]
    status = {s["id"]: s for s in adapter.list_sites(DB, page=1, page_size=10)[0]}
    assert status["queued-live"]["status"] == SiteStatus.QUEUED
    assert status["done"]["status"] == SiteStatus.IMPORTED
    for site_id in failed:
        assert status[site_id]["status"] == SiteStatus.FAILED
        assert status[site_id]["error_message"] == SITE_RUN_INTERRUPTED_MESSAGE
    # The cursor stays, so the next check resumes where the run stopped.
    assert status["importing"]["run_cursor"] == 3


@pytest.mark.parametrize(
    "spelling",
    [
        "https://d.example.com",
        "https://d.example.com/",
        "https://D.example.com/index.html",
        "https://d.example.com:443/#top",
    ],
)
def test_find_site_by_scope_matches_every_spelling_of_one_site(
    adapter: SqliteAdapter, spelling: str
) -> None:
    _site(adapter, "root", SiteStatus.IMPORTED, url="https://d.example.com/")
    _site(adapter, "docs", SiteStatus.IMPORTED, url="https://d.example.com/docs")

    found = find_site_by_scope(adapter, DB, spelling, page_size=1)

    assert found is not None and found["id"] == "root"
    docs = find_site_by_scope(adapter, DB, "https://d.example.com/docs/", page_size=1)
    assert docs is not None and docs["id"] == "docs"
    assert find_site_by_scope(adapter, DB, "https://d.example.com/blog", page_size=1) is None
    assert find_site_by_scope(adapter, DB, "http://d.example.com/", page_size=1) is None


def test_site_page_options_apply_the_site_rules() -> None:
    options = site_page_options({"auto_confirm": False, "skip_duplicates": True, "x": 1})
    assert options == {"auto_confirm": True, "skip_duplicates": False, "x": 1}
