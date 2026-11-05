# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Every way a document arrives in Core applies the caller's tags and summary.

Each path is driven the way its own tests drive it: the MCP pipeline closure
and ``Engine.process_document`` on a real engine (the embedder faked, the
extraction model scripted: ``tests/unit/mcp/memory_world.py``), ``upload_file``
on a real adapter with its indexing queue recorded, and a URL page through
``import_url_page`` against the fake web site (``site_harness.py``).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest

from chaoscypher_core.mcp.server import _create_pipeline_callback
from chaoscypher_core.operations.sources.site_runs import site_page_options
from chaoscypher_core.operations.sources.url_fetch_handler import import_url_page
from chaoscypher_core.operations.sources.url_import_options import url_upload_options
from chaoscypher_core.services.sources.management.service import SourceProcessingService
from chaoscypher_core.services.sources.tags import source_tag_names
from chaoscypher_core.settings import BatchingSettings, SourceProcessingSettings
from chaoscypher_core.testing.fake_web import fake_site  # noqa: F401 — the fake web
from chaoscypher_core.utils.id import generate_id
from tests.unit.mcp.memory_world import DOCUMENT_TEXT, build_world
from tests.unit.services.sources.web.site_harness import (
    DB as SITE_DB,
)
from tests.unit.services.sources.web.site_harness import (
    ROOT,
    build_harness,
    close_harness,
    markdown_page,
)


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
    from chaoscypher_core.testing.fake_web import FakeSite


Create = Callable[..., Awaitable[str]]


@dataclass
class Rig:
    """One creation path: how to create through it, and the storage it lands in."""

    create: Create
    storage: SqliteAdapter
    database_name: str

    def count_sources(self) -> int:
        return self.storage.count_sources(database_name=self.database_name)

    def summary(self, source_id: str) -> str | None:
        row = self.storage.get_source(source_id, self.database_name)
        assert row is not None
        return row["summary"]


def _mcp_rig(world: SimpleNamespace, *, extract_entities: bool) -> Rig:
    engine = world.engine
    sandbox = Path(engine.settings.paths.mcp_dir)
    sandbox.mkdir(parents=True, exist_ok=True)
    doc = sandbox / "doc-a.txt"
    doc.write_text(DOCUMENT_TEXT)
    pipeline = _create_pipeline_callback(engine, extract_entities=extract_entities)

    async def create(**labels: Any) -> str:
        result = await pipeline(
            file_path=str(doc), file_id=generate_id(), auto_confirm=True, **labels
        )
        assert result.get("tags") == source_tag_names(engine.storage_adapter, result["source_id"])
        return str(result["source_id"])

    return Rig(create, engine.storage_adapter, engine.settings.current_database)


def _engine_rig(world: SimpleNamespace) -> Rig:
    engine = world.engine

    async def create(**labels: Any) -> str:
        result = await engine.process_document(DOCUMENT_TEXT, extract=False, **labels)
        assert result.tags == source_tag_names(engine.storage_adapter, result.source_id)
        return result.source_id

    return Rig(create, engine.storage_adapter, engine.settings.current_database)


@dataclass
class _IndexingQueue:
    calls: list[dict[str, Any]]

    async def queue_import_indexing(self, **kwargs: Any) -> dict[str, str]:
        self.calls.append(kwargs)
        return {"task_id": f"task-{len(self.calls)}", "status": "queued"}


def _upload_rig(adapter: SqliteAdapter, tmp_path: Path) -> Rig:
    settings = SimpleNamespace(
        current_database="default",
        database_dir=tmp_path / "databases" / "default",
        batching=BatchingSettings(),
        source_processing=SourceProcessingSettings(),
        priorities=SimpleNamespace(background=50),
    )
    config_manager = MagicMock()
    config_manager.get_settings.return_value = settings
    service = SourceProcessingService(
        source_manager=adapter,
        operations_manager=_IndexingQueue([]),
        config_manager=config_manager,
        validators=MagicMock(),
    )

    async def create(**labels: Any) -> str:
        result = await service.upload_file(
            file_content=DOCUMENT_TEXT.encode(), filename="doc-a.txt", **labels
        )
        assert result["tags"] == source_tag_names(adapter, result["id"])
        return str(result["id"])

    return Rig(create, adapter, "default")


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[SimpleNamespace]:
    w = build_world(tmp_path, monkeypatch)
    yield w
    w.engine.close()


@pytest.fixture
def rig_for(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Callable[[str], Rig]]:
    """Build the rig for a path name; whatever it opened is closed after the test."""
    closers: list[Callable[[], None]] = []

    def build(path: str) -> Rig:
        if path in {"mcp_index_only", "mcp_extracting", "engine_process_document"}:
            w = build_world(tmp_path, monkeypatch)
            closers.append(w.engine.close)
            if path == "engine_process_document":
                return _engine_rig(w)
            return _mcp_rig(w, extract_entities=path == "mcp_extracting")
        if path == "upload_file":
            adapter = request.getfixturevalue("in_memory_adapter")
            return _upload_rig(adapter, tmp_path)
        if path == "url_page":
            site: FakeSite = request.getfixturevalue("fake_site")
            site.add(f"{ROOT}/guide.md", markdown_page("Guide"), content_type="text/markdown")
            harness = build_harness(tmp_path, monkeypatch)
            closers.append(lambda: close_harness(harness, tmp_path))

            async def create(**labels: Any) -> str:
                options = url_upload_options(
                    auto_confirm=True,
                    tags=labels.get("tags") or (),
                    summary=labels.get("summary"),
                )
                result = await import_url_page(
                    f"{ROOT}/guide.md",
                    options,
                    source_processing_service=harness.service,
                    database_name=SITE_DB,
                )
                return str(result["id"])

            return Rig(create, harness.adapter, SITE_DB)
        raise AssertionError(path)

    yield build
    for close in reversed(closers):
        close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    ["mcp_index_only", "mcp_extracting", "engine_process_document", "upload_file", "url_page"],
)
async def test_every_creation_path_applies_tags_and_summary(
    path: str, rig_for: Callable[[str], Rig]
) -> None:
    rig = rig_for(path)

    source_id = await rig.create(tags=["Alpha", "beta"], summary="A short note.")

    assert source_tag_names(rig.storage, source_id) == ["alpha", "beta"]
    assert rig.summary(source_id) == "A short note."


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["mcp_index_only", "mcp_extracting", "upload_file"])
async def test_readd_with_skip_duplicates_tags_the_existing_document(
    path: str, rig_for: Callable[[str], Rig]
) -> None:
    rig = rig_for(path)

    first = await rig.create(tags=["alpha"])
    again = await rig.create(tags=["gamma"], skip_duplicates=True)

    assert again == first and rig.count_sources() == 1
    assert source_tag_names(rig.storage, first) == ["alpha", "gamma"]

    await rig.create(tags=["delta"], skip_duplicates=True, replace_tags=True)
    assert source_tag_names(rig.storage, first) == ["delta"]


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["mcp_index_only", "mcp_extracting", "upload_file"])
async def test_a_refused_tag_leaves_no_document_behind(
    path: str, rig_for: Callable[[str], Rig]
) -> None:
    from chaoscypher_core.services.sources.tags import TagRuleError

    rig = rig_for(path)

    with pytest.raises(TagRuleError):
        await rig.create(tags=["Work Docs"])

    assert rig.count_sources() == 0


def test_a_sites_pages_share_its_tags_but_not_a_summary() -> None:
    stored = url_upload_options(tags=["alpha"], summary="One page's note.")

    assert stored["tags"] == ["alpha"]
    assert stored["summary"] == "One page's note."
    page = site_page_options(stored)
    assert page["tags"] == ["alpha"]
    assert "summary" not in page


@pytest.mark.asyncio
async def test_every_page_of_a_site_carries_the_sites_tags(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_site: FakeSite,  # noqa: F811
) -> None:
    from chaoscypher_core.models import SiteStatus
    from chaoscypher_core.operations.sources.site_import_handler import handle_import_site

    fake_site.page(ROOT, "Example Docs", [])
    links = "".join(f"- [{n}]({ROOT}/{n}.md)\n" for n in ("install", "usage"))
    fake_site.add(f"{ROOT}/llms.txt", f"# Example Docs\n\n{links}", content_type="text/markdown")
    for name in ("install", "usage"):
        fake_site.add(
            f"{ROOT}/{name}.md", markdown_page(name.title()), content_type="text/markdown"
        )
    harness = build_harness(tmp_path, monkeypatch)
    try:
        harness.adapter.create_site(
            site_id="site-1",
            database_name=SITE_DB,
            url=ROOT,
            title=ROOT,
            status=SiteStatus.QUEUED,
            options=url_upload_options(
                auto_confirm=True, tags=["alpha", "beta"], summary="Site note."
            ),
            excluded_pages=[],
        )
        await handle_import_site(
            {"site_id": "site-1", "refresh": False},
            source_processing_service=harness.service,
            search_repository=None,
            engine_settings=None,
            metadata={"database_name": SITE_DB},
            requeue=True,
        )
        pages = harness.pages("site-1")
        assert len(pages) == 2
        for source in pages.values():
            assert source_tag_names(harness.adapter, source["id"]) == ["alpha", "beta"]
            assert source["summary"] is None
    finally:
        close_harness(harness, tmp_path)


_LABELS_AT = {
    "mcp_index_only": "chaoscypher_core.mcp.server.apply_source_labels",
    "upload_file": "chaoscypher_core.services.sources.labels.apply_source_labels",
}


@pytest.mark.asyncio
@pytest.mark.parametrize("path", sorted(_LABELS_AT))
async def test_a_labels_failure_after_the_row_exists_marks_it_failed(
    path: str, rig_for: Callable[[str], Rig], monkeypatch: pytest.MonkeyPatch
) -> None:
    from chaoscypher_core.exceptions import ConflictError
    from chaoscypher_core.models import SourceErrorStage, SourceStatus

    rig = rig_for(path)

    def lost_race(*_args: Any, **_kwargs: Any) -> list[str]:
        msg = 'another writer created tag "alpha" at the same time; try again'
        raise ConflictError(msg)

    monkeypatch.setattr(_LABELS_AT[path], lost_race)

    with pytest.raises(ConflictError):
        await rig.create(tags=["alpha"])

    rows, total = rig.storage.list_sources()
    assert total == 1
    row = rig.storage.get_source(rows[0]["id"], rig.database_name)
    assert row is not None
    assert row["status"] == SourceStatus.ERROR
    assert row["error_stage"] == SourceErrorStage.INDEXING
