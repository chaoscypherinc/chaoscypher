# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""A real site import against the fake web site: a migrated database, the real upload path.

``SiteHarness`` wires the ``import_site`` handler the way the worker does: a
SQLite adapter on an Alembic-migrated database, the real
``SourceProcessingService`` (its indexing queue replaced by a recorder), and
app settings with the web, batching, paths and priorities groups. Fetches go
through the real WebScraper to ``conftest.fake_site``: no network.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import evict_engine
from chaoscypher_core.database.migrations.runner import upgrade_to_head
from chaoscypher_core.models import SiteStatus
from chaoscypher_core.operations.sources.site_import_handler import handle_import_site
from chaoscypher_core.operations.sources.site_runs import begin_site_refresh, exclusion_keys
from chaoscypher_core.services.sources.management.service import SourceProcessingService
from chaoscypher_core.settings import BatchingSettings, WebSettings


if TYPE_CHECKING:
    from chaoscypher_core.services.sources.web import CrawlThrottle


DB = "default"
ROOT = "https://docs.example.com"
BODY = "This page explains one part of the product in enough words to import. "


@dataclass
class _IndexingQueue:
    """Records ``queue_import_indexing`` calls instead of enqueueing."""

    calls: list[dict[str, Any]] = field(default_factory=list)

    async def queue_import_indexing(self, **kwargs: Any) -> dict[str, str]:
        self.calls.append(kwargs)
        return {"task_id": f"task-{len(self.calls)}", "status": "queued"}


@dataclass
class SiteHarness:
    adapter: SqliteAdapter
    service: SourceProcessingService
    queue: _IndexingQueue
    settings: SimpleNamespace

    def create_site(
        self,
        url: str = ROOT,
        *,
        options: dict[str, Any] | None = None,
        exclude: list[str] | None = None,
    ) -> str:
        """Create ``site-1``; ``exclude`` is stored the way the import stores it."""
        self.adapter.create_site(
            site_id="site-1",
            database_name=DB,
            url=url,
            title=url,
            status=SiteStatus.QUEUED,
            options=options if options is not None else {"auto_confirm": True},
            excluded_pages=exclusion_keys(exclude, site_url=url) or [],
        )
        return "site-1"

    async def run(
        self,
        site_id: str = "site-1",
        *,
        refresh: bool = False,
        exclude: list[str] | None = None,
        throttle: CrawlThrottle | None = None,
        requeue: bool = True,
    ) -> dict[str, Any]:
        """Run ``import_site``; a refresh starts the way every surface starts one.

        ``exclude`` (a refresh's new list of pages to leave out) goes through
        :func:`begin_site_refresh`, which replaces the site's stored list.
        """
        if refresh:
            begin_site_refresh(self.adapter, site_id, DB, exclude=exclude)
        return await handle_import_site(
            {"site_id": site_id, "refresh": refresh},
            source_processing_service=self.service,
            search_repository=None,
            engine_settings=None,
            metadata={"database_name": DB},
            throttle=throttle,
            requeue=requeue,
        )

    def pages(self, site_id: str = "site-1") -> dict[str, dict[str, Any]]:
        """The site's page rows (full ``get_source`` dicts) keyed by URL."""
        rows: dict[str, dict[str, Any]] = {}
        for page in self.adapter.list_site_pages(site_id, DB):
            source = self.adapter.get_source(page["id"], DB)
            assert source is not None
            rows[str(source["origin_url"])] = source
        return rows


def markdown_page(title: str) -> str:
    return f"# {title}\n\n{BODY}{title}.\n"


def build_harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **web: Any) -> SiteHarness:
    web.setdefault("crawl_delay_seconds", 0.0)
    db_path = tmp_path / "app.db"
    upgrade_to_head(db_path)
    adapter = SqliteAdapter(str(db_path), database_name=DB)
    adapter.connect()
    settings = SimpleNamespace(
        web=WebSettings(**web),
        batching=BatchingSettings(),
        paths=SimpleNamespace(max_filename_length=80),
        priorities=SimpleNamespace(background=50),
        current_database=DB,
        database_dir=tmp_path / "databases" / DB,
    )
    for module in ("url_fetch_handler", "site_import_handler"):
        monkeypatch.setattr(
            f"chaoscypher_core.operations.sources.{module}.get_settings", lambda: settings
        )
    config_manager = MagicMock()
    config_manager.get_settings.return_value = settings
    queue = _IndexingQueue()
    service = SourceProcessingService(
        source_manager=adapter,
        operations_manager=queue,
        config_manager=config_manager,
        validators=MagicMock(),
    )
    return SiteHarness(adapter=adapter, service=service, queue=queue, settings=settings)


def close_harness(harness: SiteHarness, tmp_path: Path) -> None:
    harness.adapter.disconnect()
    evict_engine(tmp_path / "app.db")
