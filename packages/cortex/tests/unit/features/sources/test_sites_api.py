# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The site import routes on the real Cortex app (spec 2026-10-22, decision 5).

``/sources/sites/previews`` (discovery only), ``/sources/sites`` (create and
enqueue ``import_site``; list), ``/sources/sites/{id}`` (read with computed
progress; delete, optionally with its pages through the source delete).
The service runs over a real adapter on an Alembic-migrated database; the
queue and the discovery are recorded or stubbed, and no request leaves the
process.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import evict_engine
from chaoscypher_core.app_config import get_settings
from chaoscypher_core.database.migrations.runner import upgrade_to_head
from chaoscypher_core.models import SiteStatus, SourceStatus
from chaoscypher_core.services.sources.web import (
    DiscoveryBudget,
    DiscoveryMethod,
    SiteManifest,
    SitePage,
)
from chaoscypher_cortex.features.sources.models import UrlImportOptions
from chaoscypher_cortex.features.sources.sites_api import get_sites_service
from chaoscypher_cortex.features.sources.sites_repository import SitesRepository
from chaoscypher_cortex.features.sources.sites_service import SitesService
from chaoscypher_cortex.shared.auth.dependencies import get_current_username


DB = "default"
SITE = "https://docs.example.com"
_SERVICE = "chaoscypher_cortex.features.sources.sites_service"


class _Harness:
    def __init__(self, tmp_path: Path) -> None:
        db_path = tmp_path / "app.db"
        upgrade_to_head(db_path)
        self.db_path = db_path
        self.adapter = SqliteAdapter(str(db_path), database_name=DB)
        self.adapter.connect()
        self.staging = tmp_path / "sources"
        self.deleted: list[str] = []
        self.delete_kwargs: list[dict[str, Any]] = []
        self.enqueued: list[dict[str, Any]] = []

    def delete_source(self, source_id: str, **kwargs: Any) -> bool:
        self.deleted.append(source_id)
        self.delete_kwargs.append(kwargs)
        return self.adapter.delete_source(source_id, DB)

    def list_sources(self, **filters: Any) -> dict[str, Any]:
        """The source list's shape (``list_sources_enriched``) over the real adapter query."""
        sources, total = self.adapter.list_sources(**filters)
        return {"sources": sources, "total": total}

    def service(self) -> SitesService:
        return SitesService(
            SitesRepository(self.adapter, DB),
            settings=get_settings(),
            delete_source=self.delete_source,
            list_sources=self.list_sources,
        )

    def site(self, site_id: str, status: str = SiteStatus.IMPORTED) -> None:
        self.adapter.create_site(
            site_id=site_id,
            database_name=DB,
            url=SITE,
            title="Example Docs",
            status=status,
            options={"auto_analyze": True},
        )

    def page(self, source_id: str, site_id: str, status: str) -> None:
        self.adapter.upload_source(
            source_id=source_id,
            database_name=DB,
            filename=f"{source_id}.md",
            file_content=b"# page\n",
            staging_dir=str(self.staging),
            origin_url=f"{SITE}/{source_id}.md",
            source_type_override="webpage",
            site_id=site_id,
        )
        self.adapter.update_file(source_id, DB, {"status": status})


@pytest.fixture
def harness(tmp_path: Path) -> Iterator[_Harness]:
    built = _Harness(tmp_path)
    yield built
    built.adapter.disconnect()
    evict_engine(built.db_path)


@pytest.fixture
def client(harness: _Harness) -> Iterator[TestClient]:
    from chaoscypher_cortex.app_factory import create_app

    app = create_app()
    app.dependency_overrides[get_current_username] = lambda: "test-user"
    app.dependency_overrides[get_sites_service] = harness.service

    async def _record(site_id: str, **kwargs: Any) -> str:
        harness.enqueued.append({"site_id": site_id, **kwargs})
        return "task-1"

    with (
        patch(f"{_SERVICE}.queue_utils.queue_import_site", new=AsyncMock(side_effect=_record)),
        patch("chaoscypher_core.services.llm.require_extraction_ready", new=AsyncMock()),
    ):
        yield TestClient(app, base_url="http://localhost")


# --------------------------------------------------------------------------- preview


def test_preview_returns_the_manifest(client: TestClient) -> None:
    manifest = SiteManifest(
        url=SITE,
        title="Example Docs",
        method=DiscoveryMethod.LLMS_TXT,
        pages=[SitePage(url=f"{SITE}/a.md", title="A"), SitePage(url=f"{SITE}/b.md")],
        crawl_delay_seconds=0.5,
        capped=False,
    )
    with (
        patch(f"{_SERVICE}.validate_url_safety", return_value=True),
        patch(f"{_SERVICE}.discover_site", new=AsyncMock(return_value=manifest)) as discover,
    ):
        response = client.post("/api/v1/sources/sites/previews", json={"url": SITE})

    assert response.status_code == 200
    assert response.json() == {
        "url": SITE,
        "title": "Example Docs",
        "method": "llms_txt",
        "page_count": 2,
        "pages": [{"url": f"{SITE}/a.md", "title": "A"}, {"url": f"{SITE}/b.md", "title": ""}],
        "capped": False,
    }
    assert discover.await_args.args[0] == SITE
    # The preview's crawl fallback runs on the settings' preview budget, so it answers in seconds.
    assert discover.await_args.kwargs["budget"] == DiscoveryBudget.for_preview(get_settings().web)


@pytest.mark.parametrize("url", ["http://127.0.0.1/docs", "http://169.254.169.254/latest"])
def test_preview_refuses_an_unsafe_address_before_discovery(client: TestClient, url: str) -> None:
    with patch(f"{_SERVICE}.discover_site", new=AsyncMock()) as discover:
        response = client.post("/api/v1/sources/sites/previews", json={"url": url})

    assert response.status_code == 400
    assert response.json()["error"] == "VALIDATION_ERROR"
    discover.assert_not_awaited()


def test_preview_refuses_a_non_http_address(client: TestClient) -> None:
    response = client.post("/api/v1/sources/sites/previews", json={"url": "file:///etc/passwd"})
    assert response.status_code == 422


# --------------------------------------------------------------------------- create


def test_create_stores_the_site_and_enqueues_its_import(
    client: TestClient, harness: _Harness
) -> None:
    body = {
        "url": SITE,
        "exclude": [f"{SITE}/a.md"],
        "options": {"analysis_depth": "quick", "auto_confirm": True},
    }
    with patch(f"{_SERVICE}.validate_url_safety", return_value=True):
        response = client.post("/api/v1/sources/sites", json=body)

    assert response.status_code == 202
    site = response.json()
    assert site["url"] == SITE
    assert site["status"] == "queued"
    assert site["page_count"] == 0
    assert site["progress"]["total"] == 0
    expected = UrlImportOptions(analysis_depth="quick", auto_confirm=True).upload_options()
    assert site["options"] == expected
    assert site["run_in_progress"] is True
    stored = harness.adapter.get_site(site["id"], DB)
    assert stored is not None and stored["options"] == expected
    # The site remembers the unticked page (its identity), so a refresh keeps it out.
    assert site["excluded_pages"] == [f"{SITE}/a"]
    assert stored["excluded_pages"] == [f"{SITE}/a"]
    assert harness.enqueued == [
        {
            "site_id": site["id"],
            "refresh": False,
            "database_name": DB,
            "priority": get_settings().priorities.background,
        }
    ]


def test_create_refuses_an_unsafe_address(client: TestClient, harness: _Harness) -> None:
    response = client.post("/api/v1/sources/sites", json={"url": "http://10.0.0.7/docs"})
    assert response.status_code == 400
    assert harness.enqueued == []
    assert harness.adapter.list_sites(DB, page=1, page_size=10) == ([], 0)


def test_create_refuses_more_exclusions_than_a_site_may_have(client: TestClient) -> None:
    limit = get_settings().web.site_max_pages
    pages = [f"{SITE}/p{n}.md" for n in range(limit + 1)]
    with patch(f"{_SERVICE}.validate_url_safety", return_value=True):
        response = client.post("/api/v1/sources/sites", json={"url": SITE, "exclude": pages})
    assert response.status_code == 400


def test_create_applies_the_site_rules_whatever_the_dialog_sends(
    client: TestClient, harness: _Harness
) -> None:
    """The dialog's "skip duplicates" and an unticked auto-confirm never reach a site's pages."""
    body = {"url": SITE, "options": {"skip_duplicates": True, "auto_confirm": False}}
    with patch(f"{_SERVICE}.validate_url_safety", return_value=True):
        response = client.post("/api/v1/sources/sites", json=body)

    assert response.status_code == 202
    stored = harness.adapter.get_site(response.json()["id"], DB)
    assert stored is not None and stored["options"] is not None
    assert stored["options"]["skip_duplicates"] is False
    assert stored["options"]["auto_confirm"] is True


@pytest.mark.parametrize("spelling", [SITE, f"{SITE}/", f"{SITE}/index.html"])
def test_create_answers_409_with_the_site_already_imported(
    client: TestClient, harness: _Harness, spelling: str
) -> None:
    harness.site("site-1")

    with patch(f"{_SERVICE}.validate_url_safety", return_value=True):
        response = client.post("/api/v1/sources/sites", json={"url": spelling})

    assert response.status_code == 409
    body = response.json()
    assert body["error"] == "CONFLICT"
    assert body["details"]["site"]["id"] == "site-1"
    assert body["details"]["site"]["url"] == SITE
    assert harness.enqueued == []
    assert harness.adapter.list_sites(DB, page=1, page_size=10)[1] == 1


# --------------------------------------------------------------------------- read


def test_get_counts_the_pages_by_status(client: TestClient, harness: _Harness) -> None:
    harness.site("site-1")
    harness.page("p1", "site-1", SourceStatus.COMMITTED)
    harness.page("p2", "site-1", SourceStatus.ERROR)
    harness.page("p3", "site-1", SourceStatus.EXTRACTING)

    response = client.get("/api/v1/sources/sites/site-1")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "site-1" and body["status"] == "imported"
    assert body["progress"] == {
        "total": 3,
        "done": 1,
        "failed": 1,
        "awaiting_confirmation": 0,
        "in_progress": 1,
        "gone": 0,
        "by_status": {"committed": 1, "error": 1, "extracting": 1},
    }


def test_pages_lists_the_site_pages_and_marks_the_gone(
    client: TestClient, harness: _Harness
) -> None:
    harness.site("site-1")
    harness.site("site-2")
    harness.page("p1", "site-1", SourceStatus.COMMITTED)
    harness.page("p2", "site-1", SourceStatus.COMMITTED)
    harness.page("p3", "site-2", SourceStatus.COMMITTED)
    harness.adapter.set_site_pages_gone(["p2"], DB, datetime.now(UTC))

    response = client.get("/api/v1/sources/sites/site-1/pages", params={"page_size": 10})

    assert response.status_code == 200
    body = response.json()
    assert body["pagination"]["total"] == 2
    pages = {p["id"]: p for p in body["data"]}
    assert set(pages) == {"p1", "p2"}
    assert pages["p1"]["site_id"] == "site-1"
    assert pages["p1"]["origin_url"] == f"{SITE}/p1.md"
    assert pages["p1"]["site_gone_at"] is None
    assert pages["p2"]["site_gone_at"] is not None
    assert pages["p1"]["site_left_out"] is False


def test_pages_marks_a_page_the_site_leaves_out(client: TestClient, harness: _Harness) -> None:
    """A left-out page the site already has is not checked for updates: the list says so."""
    harness.site("site-1")
    harness.page("p1", "site-1", SourceStatus.COMMITTED)
    harness.page("p2", "site-1", SourceStatus.COMMITTED)
    harness.adapter.update_site("site-1", DB, {"excluded_pages": [f"{SITE}/p2"]})

    response = client.get("/api/v1/sources/sites/site-1/pages")

    pages = {p["id"]: p for p in response.json()["data"]}
    assert pages["p2"]["site_left_out"] is True
    assert pages["p1"]["site_left_out"] is False


def test_pages_of_a_missing_site_is_404(client: TestClient) -> None:
    response = client.get("/api/v1/sources/sites/nope/pages")
    assert response.status_code == 404


def test_get_a_missing_site_is_404(client: TestClient) -> None:
    response = client.get("/api/v1/sources/sites/nope")
    assert response.status_code == 404
    assert response.json()["error"] == "NOT_FOUND"


def test_list_uses_the_house_pagination_envelope(client: TestClient, harness: _Harness) -> None:
    for n in range(3):
        harness.site(f"site-{n}")
    harness.page("p1", "site-2", SourceStatus.COMMITTED)

    first = client.get("/api/v1/sources/sites", params={"page": 1, "page_size": 2}).json()
    second = client.get("/api/v1/sources/sites", params={"page": 2, "page_size": 2}).json()

    assert first["pagination"] == {
        "total": 3,
        "page": 1,
        "page_size": 2,
        "total_pages": 2,
        "has_next": True,
        "has_prev": False,
    }
    assert [s["id"] for s in first["data"] + second["data"]] == ["site-2", "site-1", "site-0"]
    assert first["data"][0]["progress"]["done"] == 1
    assert second["pagination"]["has_prev"] is True


# --------------------------------------------------------------------------- delete


def test_delete_keeps_the_pages_unlinked(client: TestClient, harness: _Harness) -> None:
    harness.site("site-1")
    harness.page("p1", "site-1", SourceStatus.COMMITTED)

    response = client.delete("/api/v1/sources/sites/site-1")

    assert response.status_code == 204
    assert harness.adapter.get_site("site-1", DB) is None
    source = harness.adapter.get_source("p1", DB)
    assert source is not None and source["site_id"] is None
    assert harness.deleted == []


def test_delete_with_pages_removes_them_through_the_source_delete(
    client: TestClient, harness: _Harness
) -> None:
    harness.site("site-1")
    harness.page("p1", "site-1", SourceStatus.COMMITTED)
    harness.page("p2", "site-1", SourceStatus.ERROR)

    response = client.delete("/api/v1/sources/sites/site-1", params={"delete_pages": True})

    assert response.status_code == 204
    assert sorted(harness.deleted) == ["p1", "p2"]
    # The site row goes next: its pages are not written to its list first.
    assert harness.delete_kwargs == [{"leave_out_of_site": False}] * 2
    assert harness.adapter.get_source("p1", DB) is None
    assert harness.adapter.get_site("site-1", DB) is None


def test_delete_refuses_a_site_with_a_live_run(client: TestClient, harness: _Harness) -> None:
    """The run would die on the missing site row; a stale run does not hold the delete."""
    harness.site("site-1", status=SiteStatus.IMPORTING)
    harness.page("p1", "site-1", SourceStatus.COMMITTED)

    refused = client.delete("/api/v1/sources/sites/site-1", params={"delete_pages": True})

    assert refused.status_code == 409
    assert harness.adapter.get_site("site-1", DB) is not None
    assert harness.deleted == []

    _go_stale(harness, "site-1", SiteStatus.IMPORTING)
    assert client.delete("/api/v1/sources/sites/site-1").status_code == 204


def test_delete_a_missing_site_is_404(client: TestClient) -> None:
    assert client.delete("/api/v1/sources/sites/nope").status_code == 404


# --------------------------------------------------------------------------- refresh


def test_refresh_enqueues_a_refresh_run(client: TestClient, harness: _Harness) -> None:
    harness.site("site-1")
    harness.page("p1", "site-1", SourceStatus.COMMITTED)

    response = client.post("/api/v1/sources/sites/site-1/refreshes")

    assert response.status_code == 202
    body = response.json()
    assert body["id"] == "site-1" and body["status"] == "queued"
    assert body["progress"]["done"] == 1
    assert harness.enqueued == [
        {
            "site_id": "site-1",
            "refresh": True,
            "database_name": DB,
            "priority": get_settings().priorities.background,
        }
    ]
    stored = harness.adapter.get_site("site-1", DB)
    assert stored is not None and stored["status"] == "queued"


def test_refresh_keeps_the_pages_the_site_leaves_out_unless_given_a_list(
    client: TestClient, harness: _Harness
) -> None:
    """No body keeps the stored list; ``exclude`` replaces it; ``[]`` brings every page back."""
    harness.site("site-1")
    harness.adapter.update_site("site-1", DB, {"excluded_pages": [f"{SITE}/a", f"{SITE}/b"]})

    kept = client.post("/api/v1/sources/sites/site-1/refreshes")
    assert kept.status_code == 202
    assert kept.json()["excluded_pages"] == [f"{SITE}/a", f"{SITE}/b"]

    harness.adapter.update_site("site-1", DB, {"status": SiteStatus.IMPORTED})
    replaced = client.post(
        "/api/v1/sources/sites/site-1/refreshes", json={"exclude": [f"{SITE}/b.md"]}
    )
    assert replaced.status_code == 202
    assert replaced.json()["excluded_pages"] == [f"{SITE}/b"]
    assert client.get("/api/v1/sources/sites/site-1").json()["excluded_pages"] == [f"{SITE}/b"]

    harness.adapter.update_site("site-1", DB, {"status": SiteStatus.IMPORTED})
    cleared = client.post("/api/v1/sources/sites/site-1/refreshes", json={"exclude": []})
    assert cleared.json()["excluded_pages"] == []
    assert len(harness.enqueued) == 3


@pytest.mark.parametrize(
    ("bad", "code"),
    [
        ("docs.example.com/faq", 422),  # not a web address: the request model refuses it
        ("/faq", 422),
        ("https://other.example.com/a", 400),  # a web address, not a page of the site
    ],
)
def test_refresh_refuses_an_exclusion_that_names_no_page_of_the_site(
    client: TestClient, harness: _Harness, bad: str, code: int
) -> None:
    harness.site("site-1")
    harness.adapter.update_site("site-1", DB, {"excluded_pages": [f"{SITE}/kept"]})

    response = client.post("/api/v1/sources/sites/site-1/refreshes", json={"exclude": [bad]})

    assert response.status_code == code
    assert bad in response.text
    assert harness.enqueued == []
    stored = harness.adapter.get_site("site-1", DB)
    assert stored is not None and stored["excluded_pages"] == [f"{SITE}/kept"]


def test_refresh_refuses_more_exclusions_than_a_site_may_have(
    client: TestClient, harness: _Harness
) -> None:
    harness.site("site-1")
    limit = get_settings().web.site_max_pages
    pages = [f"{SITE}/p{n}.md" for n in range(limit + 1)]

    response = client.post("/api/v1/sources/sites/site-1/refreshes", json={"exclude": pages})

    assert response.status_code == 400
    assert harness.enqueued == []
    stored = harness.adapter.get_site("site-1", DB)
    assert stored is not None and stored["status"] == SiteStatus.IMPORTED


@pytest.mark.parametrize("status", [SiteStatus.QUEUED, SiteStatus.IMPORTING, SiteStatus.REFRESHING])
def test_refresh_refuses_a_site_already_running(
    client: TestClient, harness: _Harness, status: SiteStatus
) -> None:
    harness.site("site-1", status=status)

    response = client.post("/api/v1/sources/sites/site-1/refreshes")

    assert response.status_code == 409
    assert response.json()["error"] == "CONFLICT"
    assert harness.enqueued == []


def _go_stale(harness: _Harness, site_id: str, status: str) -> None:
    """The run's worker was killed: its status stayed, its start is past the timeout."""
    from datetime import timedelta

    from chaoscypher_core.operations.sources.site_runs import run_stale_after_seconds

    old = datetime.now(UTC) - timedelta(seconds=run_stale_after_seconds() + 60)
    harness.adapter.update_site(site_id, DB, {"status": status, "run_started_at": old})


@pytest.mark.parametrize("status", [SiteStatus.QUEUED, SiteStatus.IMPORTING, SiteStatus.REFRESHING])
def test_refresh_runs_a_site_whose_run_went_stale(
    client: TestClient, harness: _Harness, status: SiteStatus
) -> None:
    harness.site("site-1")
    _go_stale(harness, "site-1", status)

    response = client.post("/api/v1/sources/sites/site-1/refreshes")

    assert response.status_code == 202
    assert response.json()["run_in_progress"] is True
    assert [e["site_id"] for e in harness.enqueued] == ["site-1"]


def test_a_stale_run_is_not_reported_in_progress(client: TestClient, harness: _Harness) -> None:
    """The UI disables refresh and delete on `run_in_progress`, never on the bare status."""
    harness.site("live", status=SiteStatus.IMPORTING)
    harness.site("stale")
    _go_stale(harness, "stale", SiteStatus.IMPORTING)

    assert client.get("/api/v1/sources/sites/live").json()["run_in_progress"] is True
    assert client.get("/api/v1/sources/sites/stale").json()["run_in_progress"] is False


def test_refresh_of_a_failed_site_runs_again(client: TestClient, harness: _Harness) -> None:
    harness.site("site-1", status=SiteStatus.FAILED)
    assert client.post("/api/v1/sources/sites/site-1/refreshes").status_code == 202


def test_refresh_of_a_missing_site_is_404(client: TestClient) -> None:
    assert client.post("/api/v1/sources/sites/nope/refreshes").status_code == 404
