# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Worker startup fails the site runs the previous worker left behind.

``import_site`` is not retried after a crash, so a site its dead worker was
importing stayed ``importing`` for good and every refresh answered 409. At
startup nothing of this worker runs yet: a site left importing or refreshing
is failed with a message that says to check for updates (which resumes from
the run's cursor); a queued site keeps its status only while its task still
waits in the queue. Real migrated database; the queue scan is stubbed.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

import chaoscypher_neuron.worker  # noqa: F401  # configure_logging side effect
from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import evict_engine
from chaoscypher_core.database.migrations.runner import upgrade_to_head
from chaoscypher_core.models import SiteStatus
from chaoscypher_core.operations.sources.site_runs import SITE_RUN_INTERRUPTED_MESSAGE
from chaoscypher_neuron.worker import _run_startup_recovery


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


@pytest.mark.asyncio
async def test_startup_fails_site_runs_left_without_a_live_task(adapter: SqliteAdapter) -> None:
    for site_id, status in [
        ("queued-live", SiteStatus.QUEUED),
        ("queued-dead", SiteStatus.QUEUED),
        ("importing", SiteStatus.IMPORTING),
        ("refreshing", SiteStatus.REFRESHING),
        ("done", SiteStatus.IMPORTED),
    ]:
        adapter.create_site(
            site_id=site_id,
            database_name=DB,
            url=f"https://docs.example.com/{site_id}",
            title=site_id,
            status=status,
            options={},
        )

    async def _task_exists(*, site_id: str, database_name: str) -> bool:
        return site_id == "queued-live" and database_name == DB

    ctx: dict[str, Any] = {"settings": object(), "current_database": DB, "storage_adapter": adapter}
    with (
        patch(
            "chaoscypher_neuron.worker.recover_orphaned_extraction_tasks",
            new=AsyncMock(return_value={"recovered": 0, "skipped": 0, "failed": 0}),
        ),
        patch(
            "chaoscypher_neuron.worker.recover_stuck_sources",
            new=AsyncMock(return_value={"reset": 0, "marked_failed": 0}),
        ),
        patch(
            "chaoscypher_neuron.worker.queue_client.task_exists_for_site",
            new=AsyncMock(side_effect=_task_exists),
        ),
    ):
        await _run_startup_recovery(ctx)  # type: ignore[arg-type]

    sites = {s["id"]: s for s in adapter.list_sites(DB, page=1, page_size=10)[0]}
    assert sites["queued-live"]["status"] == SiteStatus.QUEUED
    assert sites["done"]["status"] == SiteStatus.IMPORTED
    for site_id in ("queued-dead", "importing", "refreshing"):
        assert sites[site_id]["status"] == SiteStatus.FAILED, site_id
        assert sites[site_id]["error_message"] == SITE_RUN_INTERRUPTED_MESSAGE


@pytest.mark.asyncio
async def test_startup_fails_no_queued_site_when_the_queue_cannot_say(
    adapter: SqliteAdapter,
) -> None:
    """A queue that cannot be scanned is "unknown": a queued site keeps waiting."""
    adapter.create_site(
        site_id="queued",
        database_name=DB,
        url="https://docs.example.com",
        title="Docs",
        status=SiteStatus.QUEUED,
        options={},
    )
    ctx: dict[str, Any] = {"settings": object(), "current_database": DB, "storage_adapter": adapter}
    with (
        patch(
            "chaoscypher_neuron.worker.recover_orphaned_extraction_tasks",
            new=AsyncMock(return_value={"recovered": 0, "skipped": 0, "failed": 0}),
        ),
        patch(
            "chaoscypher_neuron.worker.recover_stuck_sources",
            new=AsyncMock(return_value={"reset": 0, "marked_failed": 0}),
        ),
        patch(
            "chaoscypher_neuron.worker.queue_client.task_exists_for_site",
            new=AsyncMock(side_effect=RuntimeError("scan blip")),
        ),
    ):
        await _run_startup_recovery(ctx)  # type: ignore[arg-type]

    site = adapter.get_site("queued", DB)
    assert site is not None and site["status"] == SiteStatus.QUEUED
