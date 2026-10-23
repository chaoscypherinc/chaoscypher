# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Interrupted site-import runs, failed on worker startup.

``import_site`` is not retried after a crash (``OPERATION_RETRY_ON_CRASH``),
so a site its dead worker was importing or refreshing would stay that way and
its refresh would be refused. At startup nothing of this worker runs yet: a
site left importing or refreshing is failed, and a queued one too unless its
task still waits in the queue. The message says to check for updates, which
resumes from the run's cursor.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from chaoscypher_core.models import SiteStatus
from chaoscypher_core.operations.sources.site_runs import fail_interrupted_runs
from chaoscypher_core.queue import queue_client
from chaoscypher_core.utils.logging.app_config import get_logger


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite import SqliteAdapter


logger = get_logger(__name__)

__all__ = ["recover_interrupted_site_runs"]


async def recover_interrupted_site_runs(adapter: SqliteAdapter, database_name: str) -> list[str]:
    """Fail every site whose run the previous worker left behind.

    Args:
        adapter: SQLite adapter for the database.
        database_name: The database to recover.

    Returns:
        The ids of the sites marked failed.
    """
    waiting: set[str] = set()
    for site in adapter.list_running_sites(database_name):
        if site["status"] != SiteStatus.QUEUED:
            continue  # importing / refreshing: its worker was the one that died
        try:
            if await queue_client.task_exists_for_site(
                site_id=site["id"], database_name=database_name
            ):
                waiting.add(site["id"])
        except Exception as exc:
            # Unknown is not "no task": a queued site keeps waiting.
            logger.warning(
                "site_run_recovery_queue_unknown",
                site_id=site["id"],
                error_type=type(exc).__name__,
                error=str(exc),
            )
            waiting.add(site["id"])
    return fail_interrupted_runs(
        adapter, database_name, has_live_task=lambda site: site["id"] in waiting
    )
