# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""A package import and a compose merge never ask a model for a summary.

A package's documents arrive with whatever summary the package carries; the
import-time summary belongs to a document's own indexing. A model counts as
configured here (``settings.yaml`` says setup is done, the default provider
needs no key), so a regression would reach the summary step: building the
summary call is recorded and the call itself records and raises, as do
``write_summary`` and the shared ``_summarise`` step. Nothing may be recorded.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import evict_engine, get_engine
from chaoscypher_core.adapters.sqlite.models import SourceRow
from chaoscypher_core.app_config import get_settings
from chaoscypher_core.app_config.engine_factory import build_engine_settings
from chaoscypher_core.repo_factories.graph_factory import get_transaction_graph_repository
from chaoscypher_core.services.compose.merger import NamespaceMerger
from chaoscypher_core.services.compose.models import PackageSpec, ResolvedPackage
from chaoscypher_core.services.export import CcxExporter
from chaoscypher_core.services.package.importer import CcxImporter, ImportOptions


class _Guard:
    """Records every way into the summary step; each one also raises."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def chat_factory(self, *_args: Any, **_kwargs: Any) -> Any:
        self.calls.append("import_summary_chat")

        async def chat(_messages: Any) -> str:
            self.calls.append("chat")
            msg = "a package import called the summary model"
            raise AssertionError(msg)

        return chat

    def refuse(self, name: str) -> Any:
        def refused(*_args: Any, **_kwargs: Any) -> Any:
            self.calls.append(name)
            msg = f"a package import reached {name}"
            raise AssertionError(msg)

        return refused


@pytest.fixture
def guard(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[_Guard]:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "settings.yaml").write_text("setup_completed: true\n", encoding="utf-8")
    monkeypatch.setenv("CHAOSCYPHER_DATA_DIR", str(data_dir))
    from chaoscypher_core import app_config

    app_config.get_config_manager.cache_clear()
    app_config.get_settings.cache_clear()
    app_config._settings = None
    recorder = _Guard()
    summaries = "chaoscypher_core.services.sources.summaries"
    with (
        patch(f"{summaries}.import_summary_chat", new=recorder.chat_factory),
        patch(f"{summaries}.write_summary", new=recorder.refuse("write_summary")),
        patch(f"{summaries}._summarise", new=recorder.refuse("_summarise")),
        patch(
            "chaoscypher_core.operations.importing.imported_source_handler.index_imported_package",
            new=AsyncMock(return_value={"sources_indexed": 0, "nodes_indexed": 0}),
        ),
    ):
        yield recorder
    app_config.get_config_manager.cache_clear()
    app_config.get_settings.cache_clear()
    app_config._settings = None
    evict_engine(tmp_path / "out" / "databases" / "default" / "app.db")


def test_a_model_counts_as_configured_here(guard: _Guard) -> None:
    """The guard is live: without it, this setup would summarise."""
    from chaoscypher_core.services.llm.configured import model_configured

    settings = get_settings()
    assert model_configured(settings.llm, settings.paths.data_dir)


def _adapter(db_path: Path) -> SqliteAdapter:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    SQLModel.metadata.create_all(get_engine(str(db_path)), checkfirst=True)
    adapter = SqliteAdapter(str(db_path), database_name="default")
    adapter.connect()
    return adapter


def _two_source_package(tmp_path: Path) -> bytes:
    """A real .ccx exported from a database holding two documents and no summaries."""
    db_path = tmp_path / "src" / "pack-a" / "app.db"
    adapter = _adapter(db_path)
    try:
        session = adapter.session
        assert session is not None
        for source_id, text in (("doc-a", "About falcons."), ("doc-b", "About herons.")):
            session.add(
                SourceRow(
                    id=source_id,
                    database_name="default",
                    filename=f"{source_id}.txt",
                    filepath="",
                    title=source_id,
                    source_type="text",
                    status="committed",
                    full_text=text,
                )
            )
        session.commit()
        return CcxExporter(
            graph_repository=get_transaction_graph_repository(adapter, "default"),
            sources_repository=adapter,
            settings=build_engine_settings(get_settings()),
        ).export(include_embeddings=False, source_ids=["doc-a", "doc-b"])
    finally:
        adapter.disconnect()
        evict_engine(db_path)


@pytest.mark.asyncio
async def test_package_import_and_compose_make_no_model_call(tmp_path: Path, guard: _Guard) -> None:
    data = _two_source_package(tmp_path)

    db_path = tmp_path / "dst" / "app.db"
    adapter = _adapter(db_path)
    try:
        importer = CcxImporter(
            graph_repository=get_transaction_graph_repository(adapter, "default"),
            sources_repository=adapter,
        )
        stats = await importer.import_from_bytes(data, ImportOptions(database_name="default"))
        assert stats.is_success, stats.errors
        assert adapter.count_sources(database_name="default") == 2
        assert all(
            row["summary"] is None
            for row in (
                adapter.get_source_by_ccx_iri(f"urn:ccx:chaoscypher:source/{s}", "default")
                for s in ("doc-a", "doc-b")
            )
            if row is not None
        )
    finally:
        adapter.disconnect()
        evict_engine(db_path)

    archive = tmp_path / "pack-a.ccx"
    archive.write_bytes(data)
    package = ResolvedPackage(
        spec=PackageSpec.parse("pack-a:1.0.0"),
        name="pack-a",
        version="1.0.0",
        path=archive,
        archive_path=archive,
    )
    result = await NamespaceMerger(output_dir=tmp_path / "out").merge([package])

    assert result.success is True, result.errors
    assert guard.calls == []
