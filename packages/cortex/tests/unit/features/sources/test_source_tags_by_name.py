# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Document tags by name and summaries over REST, on the real Cortex app.

The source, tag and upload services run over a real adapter on an
Alembic-migrated database; the indexing and URL-fetch queues are recorded,
and no request leaves the process. Every route reaches the one Core tag rule
(``chaoscypher_core.services.sources.tags``), so the refusals carry its text.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import evict_engine
from chaoscypher_core.app_config import get_settings
from chaoscypher_core.database.migrations.runner import upgrade_to_head
from chaoscypher_core.models import MEMORY_SOURCE_TYPE
from chaoscypher_core.services.graph.management import SourceService as EngineSourceService
from chaoscypher_core.services.sources.management.service import SourceProcessingService
from chaoscypher_core.services.sources.tags import (
    REPLACE_WITH_ADD_OR_REMOVE,
    TagRuleError,
    check_tag_edit,
    edit_source_tags,
    source_tag_names,
)
from chaoscypher_core.settings import BatchingSettings, SourceProcessingSettings
from chaoscypher_cortex.features.sources import models as source_models
from chaoscypher_cortex.features.sources.api import get_source_service, get_upload_service
from chaoscypher_cortex.features.sources.service import SourceService
from chaoscypher_cortex.features.sources.tag_service import TagService
from chaoscypher_cortex.features.sources.tags_api import get_tag_service
from chaoscypher_cortex.features.sources.upload_service import UploadService
from chaoscypher_cortex.shared.auth.dependencies import get_current_username


DB = "default"
BAD_TAG_MESSAGE = (
    "tag \"Work Docs\" is not allowed: use a-z, 0-9, ':', '_' or '-', "
    "starting with a letter or digit, at most 64 characters"
)


class _Harness:
    def __init__(self, tmp_path: Path) -> None:
        self.db_path = tmp_path / "app.db"
        upgrade_to_head(self.db_path)
        self.adapter = SqliteAdapter(str(self.db_path), database_name=DB)
        self.adapter.connect()
        self.tmp_path = tmp_path
        self.staging = tmp_path / "sources"
        self.indexing: list[dict[str, Any]] = []
        self.url_jobs: list[dict[str, Any]] = []

    def engine(self) -> EngineSourceService:
        return EngineSourceService(repository=self.adapter, database_name=DB)

    def source_service(self) -> SourceService:
        return SourceService(
            self.engine(),
            database_name=DB,
            settings=get_settings(),
            storage_adapter=self.adapter,
        )

    def tag_service(self) -> TagService:
        return TagService(self.engine(), database_name=DB)

    def upload_service(self) -> UploadService:
        settings = SimpleNamespace(
            current_database=DB,
            data_dir=self.tmp_path,
            database_dir=self.tmp_path / "databases" / DB,
            batching=BatchingSettings(upload_content_type_allowlist=["*"]),
            source_processing=SourceProcessingSettings(),
            logs=get_settings().logs,
            priorities=SimpleNamespace(background=50),
        )
        config_manager = MagicMock()
        config_manager.get_settings.return_value = settings
        harness = self

        class _Queue:
            async def queue_import_indexing(self, **kwargs: Any) -> dict[str, str]:
                harness.indexing.append(kwargs)
                return {"task_id": f"task-{len(harness.indexing)}", "status": "queued"}

        core = SourceProcessingService(
            source_manager=self.adapter,
            operations_manager=_Queue(),
            config_manager=config_manager,
            validators=MagicMock(),
        )
        service = UploadService(settings=settings, source_processing_service=core)  # type: ignore[arg-type]
        service.preflight_disk_for_upload = MagicMock()  # type: ignore[method-assign]
        return service

    def document(self, source_id: str, *, tags: list[str] | None = None) -> str:
        self.adapter.upload_source(
            source_id=source_id,
            database_name=DB,
            filename=f"{source_id}.md",
            file_content=f"# {source_id}\n".encode(),
            staging_dir=str(self.staging),
        )
        if tags:
            edit_source_tags(self.adapter, DB, source_id, add=tags)
        return source_id


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
    app.dependency_overrides[get_source_service] = harness.source_service
    app.dependency_overrides[get_tag_service] = harness.tag_service
    app.dependency_overrides[get_upload_service] = harness.upload_service

    async def _record_url(**kwargs: Any) -> str:
        harness.url_jobs.append(kwargs)
        return "task-url"

    mappers = "chaoscypher_cortex.features.sources.mappers"
    with (
        patch("chaoscypher_core.services.llm.require_extraction_ready", new=AsyncMock()),
        patch(
            "chaoscypher_cortex.features.sources.api.queue_utils.queue_fetch_url",
            new=AsyncMock(side_effect=_record_url),
        ),
        patch("chaoscypher_cortex.features.sources.api.validate_url_safety", return_value=True),
        patch(f"{mappers}.build_domain_icon_map", return_value={}),
        patch(f"{mappers}.build_domain_fingerprint_map", return_value={}),
    ):
        yield TestClient(app, base_url="http://localhost")


# --------------------------------------------------------------------------- tags


def test_create_tag_with_bad_name_is_422_with_the_rule_message(client: TestClient) -> None:
    response = client.post("/api/v1/sources/tags", json={"name": "  Work Docs "})

    assert response.status_code == 422
    body = response.json()
    assert body["error"] == "VALIDATION_FAILED"
    messages = [e["msg"] for e in body["details"]["errors"]]
    assert messages == [BAD_TAG_MESSAGE]

    created = client.post("/api/v1/sources/tags", json={"name": "  Alpha "})
    assert created.status_code == 201
    assert created.json()["name"] == "alpha"


def test_rename_to_existing_tag_is_409(client: TestClient) -> None:
    client.post("/api/v1/sources/tags", json={"name": "alpha"})
    beta = client.post("/api/v1/sources/tags", json={"name": "beta"}).json()

    response = client.patch(f"/api/v1/sources/tags/{beta['id']}", json={"name": "Alpha"})

    assert response.status_code == 409
    assert response.json()["message"] == 'a tag named "alpha" already exists'

    bad = client.patch(f"/api/v1/sources/tags/{beta['id']}", json={"name": "Work Docs"})
    assert bad.status_code == 422
    assert [e["msg"] for e in bad.json()["details"]["errors"]] == [BAD_TAG_MESSAGE]


def test_list_sources_by_tags_any_and_all(client: TestClient, harness: _Harness) -> None:
    harness.document("doc-a", tags=["alpha", "beta"])
    harness.document("doc-b", tags=["alpha"])
    harness.document("doc-c", tags=["gamma"])

    both = client.get("/api/v1/sources?tags=alpha&tags=beta&match=all")
    assert both.status_code == 200
    assert [s["id"] for s in both.json()["data"]] == ["doc-a"]
    assert both.json()["pagination"]["total"] == 1

    either = client.get("/api/v1/sources", params={"tags": ["Alpha", "beta"]})
    assert either.status_code == 200
    assert sorted(s["id"] for s in either.json()["data"]) == ["doc-a", "doc-b"]
    assert either.json()["pagination"]["total"] == 2

    none = client.get("/api/v1/sources?tags=delta")
    assert none.json()["data"] == []
    assert none.json()["pagination"]["total"] == 0

    bad = client.get("/api/v1/sources", params={"tags": ["Work Docs"]})
    assert bad.status_code == 400
    assert bad.json()["message"] == BAD_TAG_MESSAGE

    bad_match = client.get("/api/v1/sources?tags=alpha&match=some")
    assert bad_match.status_code == 422


def test_patch_tags_add_remove_replace_and_clear(client: TestClient, harness: _Harness) -> None:
    harness.document("doc-a", tags=["alpha"])
    url = "/api/v1/sources/doc-a/tags"

    added = client.patch(url, json={"add": ["Beta", "gamma"], "remove": ["alpha"]})
    assert added.status_code == 200
    assert added.json() == {"source_id": "doc-a", "tags": ["beta", "gamma"]}

    replaced = client.patch(url, json={"replace": ["delta"]})
    assert replaced.json() == {"source_id": "doc-a", "tags": ["delta"]}

    cleared = client.patch(url, json={"replace": []})
    assert cleared.json() == {"source_id": "doc-a", "tags": []}
    assert source_tag_names(harness.adapter, "doc-a") == []

    missing = client.patch("/api/v1/sources/doc-zz/tags", json={"add": ["alpha"]})
    assert missing.status_code == 404

    bad = client.patch(url, json={"add": ["Work Docs"]})
    assert bad.status_code == 422
    assert [e["msg"] for e in bad.json()["details"]["errors"]] == [BAD_TAG_MESSAGE]


def test_patch_tags_replace_with_add_is_422(client: TestClient, harness: _Harness) -> None:
    harness.document("doc-a", tags=["alpha"])

    response = client.patch(
        "/api/v1/sources/doc-a/tags", json={"replace": ["beta"], "add": ["gamma"]}
    )

    assert response.status_code == 422
    assert [e["msg"] for e in response.json()["details"]["errors"]] == [REPLACE_WITH_ADD_OR_REMOVE]
    assert source_tag_names(harness.adapter, "doc-a") == ["alpha"]


def test_patch_tags_refuses_exactly_as_core_does(
    client: TestClient, harness: _Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness.document("doc-a", tags=["alpha"])
    url = "/api/v1/sources/doc-a/tags"

    for body in ({"replace": ["beta"], "add": ["gamma"]}, {"replace": [], "remove": ["alpha"]}):
        with pytest.raises(TagRuleError) as core:
            edit_source_tags(harness.adapter, DB, "doc-a", **body)
        rest = client.patch(url, json=body)
        assert rest.status_code == 422
        assert [e["msg"] for e in rest.json()["details"]["errors"]] == [core.value.message]

    # The model's check is Core's check_tag_edit, not a copy of its condition.
    seen: list[dict[str, Any]] = []

    def spy(**edit: Any) -> None:
        seen.append(edit)
        check_tag_edit(**edit)
        if edit["add"] == ["delta"]:
            raise TagRuleError("refused by the spy")

    monkeypatch.setattr(source_models, "check_tag_edit", spy)
    spied = client.patch(url, json={"add": ["Delta"]})
    assert spied.status_code == 422
    assert [e["msg"] for e in spied.json()["details"]["errors"]] == ["refused by the spy"]
    assert seen == [{"add": ["delta"], "remove": [], "replace": None}]
    assert source_tag_names(harness.adapter, "doc-a") == ["alpha"]


def test_assign_by_id_goes_through_the_one_tag_edit(client: TestClient, harness: _Harness) -> None:
    """The old POST /sources/{id}/tags/{tag_id} route keeps the cap and the memory rule."""
    full = [f"t{i:02d}" for i in range(32)]
    harness.document("doc-a", tags=full)
    extra = client.post("/api/v1/sources/tags", json={"name": "extra"}).json()

    over = client.post(f"/api/v1/sources/doc-a/tags/{extra['id']}")
    assert over.status_code == 400
    assert over.json()["message"] == "a document can carry at most 32 tags; this would give it 33"
    assert source_tag_names(harness.adapter, "doc-a") == sorted(full)

    harness.document("mem-a")
    harness.adapter.update_source("mem-a", {"source_type": MEMORY_SOURCE_TYPE})
    memory = client.post(f"/api/v1/sources/mem-a/tags/{extra['id']}")
    assert memory.status_code == 400
    assert memory.json()["message"] == '"mem-a" is a memory; memories carry no tags.'
    assert source_tag_names(harness.adapter, "mem-a") == []

    harness.document("doc-b")
    assert client.post(f"/api/v1/sources/doc-b/tags/{extra['id']}").status_code == 204
    assert client.post(f"/api/v1/sources/doc-b/tags/{extra['id']}").status_code == 204  # idempotent
    assert source_tag_names(harness.adapter, "doc-b") == ["extra"]

    assert client.post("/api/v1/sources/doc-b/tags/tag-missing").status_code == 404
    assert client.post(f"/api/v1/sources/doc-zz/tags/{extra['id']}").status_code == 404


# --------------------------------------------------------------------------- creation


def test_upload_with_tags_and_summary(client: TestClient, harness: _Harness) -> None:
    response = client.post(
        "/api/v1/sources",
        files={"file": ("doc-a.txt", b"Alpha and beta.", "text/plain")},
        data={"tags": ["Alpha", "beta", "alpha"], "summary": '  "A short note."  '},
    )

    assert response.status_code == 202, response.text
    source_id = response.json()["id"]
    assert source_tag_names(harness.adapter, source_id) == ["alpha", "beta"]
    row = harness.adapter.get_source(source_id, DB)
    assert row is not None
    assert row["summary"] == "A short note."
    assert response.json()["summary"] == "A short note."

    count = harness.adapter.count_sources(database_name=DB)
    bad = client.post(
        "/api/v1/sources",
        files={"file": ("doc-b.txt", b"Gamma.", "text/plain")},
        data={"tags": ["Work Docs"]},
    )
    assert bad.status_code == 400
    assert bad.json()["message"] == BAD_TAG_MESSAGE
    assert harness.adapter.count_sources(database_name=DB) == count

    batch = client.post(
        "/api/v1/sources/batch",
        files=[
            ("files", ("doc-c.txt", b"Gamma.", "text/plain")),
            ("files", ("doc-d.txt", b"Delta.", "text/plain")),
        ],
        data={"tags": ["Gamma"]},
    )
    assert batch.status_code == 202, batch.text
    assert batch.json()["uploaded"] == 2
    for uploaded in batch.json()["files"]:
        assert source_tag_names(harness.adapter, uploaded["id"]) == ["gamma"]

    bad_batch = client.post(
        "/api/v1/sources/batch",
        files=[("files", ("doc-e.txt", b"Epsilon.", "text/plain"))],
        data={"tags": ["Work Docs"]},
    )
    assert bad_batch.status_code == 400
    assert bad_batch.json()["message"] == BAD_TAG_MESSAGE
    assert harness.adapter.count_sources(database_name=DB) == count + 2


def test_duplicate_upload_reports_the_stored_summary(client: TestClient, harness: _Harness) -> None:
    def upload(summary: str) -> dict[str, Any]:
        response = client.post(
            "/api/v1/sources",
            files={"file": ("doc-a.txt", b"Alpha.", "text/plain")},
            data={"skip_duplicates": "true", "summary": summary},
        )
        assert response.status_code == 202, response.text
        return response.json()

    first = upload("First note.")
    again = upload('  "Second   note."  ')

    assert again["id"] == first["id"]
    assert harness.adapter.count_sources(database_name=DB) == 1
    assert harness.adapter.get_source(first["id"], DB)["summary"] == "Second note."  # type: ignore[index]
    assert again["summary"] == "Second note."


def test_url_import_stores_tags_and_summary_in_the_job_options(
    client: TestClient, harness: _Harness
) -> None:
    response = client.post(
        "/api/v1/sources/url",
        json={"url": "https://docs.example.com/a", "tags": ["Alpha"], "summary": "A page."},
    )

    assert response.status_code == 202, response.text
    options = harness.url_jobs[0]["options"]
    assert list(options["tags"]) == ["alpha"]
    assert options["summary"] == "A page."

    bad = client.post(
        "/api/v1/sources/url", json={"url": "https://docs.example.com/b", "tags": ["Work Docs"]}
    )
    assert bad.status_code == 422
    assert [e["msg"] for e in bad.json()["details"]["errors"]] == [BAD_TAG_MESSAGE]
    assert len(harness.url_jobs) == 1

    too_long = client.post(
        "/api/v1/sources/url", json={"url": "https://docs.example.com/c", "summary": "x" * 5000}
    )
    assert too_long.status_code == 400
    assert len(harness.url_jobs) == 1


# --------------------------------------------------------------------------- summaries


def test_source_responses_carry_summary(client: TestClient, harness: _Harness) -> None:
    harness.document("doc-a")
    harness.adapter.update_source("doc-a", {"summary": "About alpha."})

    detail = client.get("/api/v1/sources/doc-a")
    assert detail.status_code == 200
    assert detail.json()["summary"] == "About alpha."

    listing = client.get("/api/v1/sources")
    assert [s["summary"] for s in listing.json()["data"]] == ["About alpha."]


def test_patch_source_sets_and_clears_summary(client: TestClient, harness: _Harness) -> None:
    harness.document("doc-a")

    set_ = client.patch("/api/v1/sources/doc-a", json={"summary": "  About   alpha. "})
    assert set_.status_code == 200
    assert set_.json()["summary"] == "About alpha."
    assert harness.adapter.get_source("doc-a", DB)["summary"] == "About alpha."  # type: ignore[index]

    kept = client.patch("/api/v1/sources/doc-a", json={"title": "Doc A"})
    assert kept.json()["summary"] == "About alpha."
    assert kept.json()["title"] == "Doc A"

    too_long = client.patch("/api/v1/sources/doc-a", json={"summary": "x" * 5000})
    assert too_long.status_code == 400
    assert harness.adapter.get_source("doc-a", DB)["summary"] == "About alpha."  # type: ignore[index]

    cleared = client.patch("/api/v1/sources/doc-a", json={"summary": ""})
    assert cleared.status_code == 200
    assert cleared.json()["summary"] is None
    assert harness.adapter.get_source("doc-a", DB)["summary"] is None  # type: ignore[index]

    missing = client.patch("/api/v1/sources/doc-zz", json={"summary": "x"})
    assert missing.status_code == 404
