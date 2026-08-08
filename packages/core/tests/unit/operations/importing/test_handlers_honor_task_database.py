# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Chunk-pipeline handlers honor the task metadata's target database.

Regression tests for the switch-then-upload race (found 2026-06-11,
demo-video capture): a task enqueued for database A must be executed
against database A even when the worker's process-global
``settings.current_database`` (and its boot/rebuild-bound adapter)
already point at database B. Mirrors the ``7e667d5`` export-handler
fix: the target database is the one captured in the task metadata at
ENQUEUE time, and when it differs from the worker's bound adapter the
handler builds task-scoped replacements so source-row reads and chunk
writes land in the task's database instead of raising FK errors (or
silently mis-routing) into the active one.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from chaoscypher_core.settings import EngineSettings


class _NullCtx:
    """Stand-in for the source_heartbeat async context manager."""

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        return None


def _stub_common(monkeypatch, handler_module) -> None:
    """Bypass the pause guard and heartbeat for a handler module."""
    pause_check = MagicMock()
    pause_check.paused = False
    monkeypatch.setattr(
        "chaoscypher_core.operations.pause_guard.check_paused",
        lambda **kw: pause_check,
    )
    monkeypatch.setattr(handler_module, "source_heartbeat", lambda **kw: _NullCtx())


def _stub_settings(monkeypatch, current_database: str) -> MagicMock:
    settings = MagicMock()
    settings.current_database = current_database
    monkeypatch.setattr(
        "chaoscypher_core.app_config.get_settings",
        lambda: settings,
    )
    return settings


@pytest.mark.asyncio
async def test_index_document_honors_task_metadata_database(monkeypatch, tmp_path) -> None:
    """Task metadata names ``scratch``; worker global/adapter say ``default``.

    The handler must run against ``scratch``: database_name from the
    metadata, a fresh adapter bound to ``scratch`` for the row lookup and
    all writes, a chunking service rebuilt on that adapter, and the
    engine settings re-scoped — never the worker's stale binding.
    """
    from chaoscypher_core.operations.importing import indexing_handler

    captured: dict = {}

    async def _fake_run_indexing(**kwargs):
        captured.update(kwargs)
        return {
            "chunks_persisted": 0,
            "queued_for_embedding": True,
            "task_id": "tsk_x",
        }

    monkeypatch.setattr(indexing_handler, "_run_indexing", _fake_run_indexing)
    _stub_common(monkeypatch, indexing_handler)
    _stub_settings(monkeypatch, "default")

    engine_settings = EngineSettings(current_database="default")

    worker_adapter = MagicMock()
    worker_adapter.database_name = "default"

    fresh_adapter = MagicMock()
    fresh_adapter.database_name = "scratch"
    fresh_adapter.get_source.return_value = None

    factory_calls: list = []

    def _fake_get_sqlite_adapter(database_name=None, *, settings=None):
        factory_calls.append(database_name)
        return fresh_adapter

    monkeypatch.setattr(
        "chaoscypher_core.database.adapter_factory.get_sqlite_adapter",
        _fake_get_sqlite_adapter,
    )

    chunking_builds: list[dict] = []

    class _FakeChunkingService:
        def __init__(self, settings=None, repository=None):
            chunking_builds.append({"settings": settings, "repository": repository})

    monkeypatch.setattr(
        "chaoscypher_core.utils.chunk.ChunkingService",
        _FakeChunkingService,
    )

    payload = {
        "file_id": "src_x",
        "file_info": {
            "filepath": str(tmp_path / "f.txt"),
            "filename": "f.txt",
        },
    }

    await indexing_handler.handle_index_document(
        data=payload,
        source_repository=worker_adapter,
        chunking_service=MagicMock(),
        metadata={"database_name": "scratch"},
        engine_settings=engine_settings,
    )

    assert captured["database_name"] == "scratch"
    assert factory_calls == ["scratch"]
    assert captured["adapter"] is fresh_adapter
    fresh_adapter.get_source.assert_called_once_with("src_x", "scratch")
    worker_adapter.get_source.assert_not_called()
    assert isinstance(captured["chunking_service"], _FakeChunkingService)
    assert chunking_builds[0]["repository"] is fresh_adapter
    assert captured["engine_settings"].current_database == "scratch"


@pytest.mark.asyncio
async def test_embed_chunks_honors_task_metadata_database(monkeypatch) -> None:
    """The embedding stage of the same pipeline honors the task database too.

    A task chunked into ``scratch`` must also embed against ``scratch``:
    database_name from metadata, fresh adapter, and the IndexingService
    rebuilt on that adapter (keeping the worker's embedding provider).
    """
    from chaoscypher_core.operations.importing import embedding_handler

    captured: dict = {}

    async def _fake_run_embedding(**kwargs):
        captured.update(kwargs)
        return {"status": "ok"}

    monkeypatch.setattr(embedding_handler, "_run_embedding", _fake_run_embedding)
    _stub_common(monkeypatch, embedding_handler)
    _stub_settings(monkeypatch, "default")

    worker_adapter = MagicMock()
    worker_adapter.database_name = "default"

    fresh_adapter = MagicMock()
    fresh_adapter.database_name = "scratch"

    def _fake_get_sqlite_adapter(database_name=None, *, settings=None):
        return fresh_adapter

    monkeypatch.setattr(
        "chaoscypher_core.database.adapter_factory.get_sqlite_adapter",
        _fake_get_sqlite_adapter,
    )

    embedding_provider = object()
    worker_indexing_service = MagicMock()
    worker_indexing_service.settings = EngineSettings(current_database="default")
    worker_indexing_service.embedding_service = embedding_provider

    rebuilt: dict = {}

    class _FakeIndexingService:
        def __init__(self, repository=None, settings=None, embedding_service=None):
            rebuilt.update(
                repository=repository,
                settings=settings,
                embedding_service=embedding_service,
            )

    monkeypatch.setattr(
        "chaoscypher_core.services.search.engine.index.IndexingService",
        _FakeIndexingService,
    )

    await embedding_handler.handle_embed_chunks(
        data={"source_id": "src_x", "file_info": {"filename": "f.txt"}},
        source_repository=worker_adapter,
        indexing_service=worker_indexing_service,
        metadata={"database_name": "scratch"},
        task_id="tsk_1",
    )

    assert captured["database_name"] == "scratch"
    assert captured["adapter"] is fresh_adapter
    assert isinstance(captured["indexing_service"], _FakeIndexingService)
    assert rebuilt["repository"] is fresh_adapter
    assert rebuilt["embedding_service"] is embedding_provider
    assert rebuilt["settings"].current_database == "scratch"


@pytest.mark.asyncio
async def test_index_document_defaults_to_worker_database_without_metadata(
    monkeypatch, tmp_path
) -> None:
    """No metadata database → worker global used, no task-scoped rebuild."""
    from chaoscypher_core.operations.importing import indexing_handler

    captured: dict = {}

    async def _fake_run_indexing(**kwargs):
        captured.update(kwargs)
        return {
            "chunks_persisted": 0,
            "queued_for_embedding": True,
            "task_id": "tsk_x",
        }

    monkeypatch.setattr(indexing_handler, "_run_indexing", _fake_run_indexing)
    _stub_common(monkeypatch, indexing_handler)
    _stub_settings(monkeypatch, "default")

    def _explode(*args, **kwargs):
        raise AssertionError("get_sqlite_adapter must not be called without a metadata database")

    monkeypatch.setattr(
        "chaoscypher_core.database.adapter_factory.get_sqlite_adapter",
        _explode,
    )

    worker_adapter = MagicMock()
    worker_adapter.database_name = "default"
    worker_adapter.get_source.return_value = None

    chunking_service = MagicMock()
    payload = {
        "file_id": "src_y",
        "file_info": {
            "filepath": str(tmp_path / "g.txt"),
            "filename": "g.txt",
        },
    }

    await indexing_handler.handle_index_document(
        data=payload,
        source_repository=worker_adapter,
        chunking_service=chunking_service,
        metadata=None,
        engine_settings=EngineSettings(current_database="default"),
    )

    assert captured["database_name"] == "default"
    assert captured["adapter"] is worker_adapter
    assert captured["chunking_service"] is chunking_service
    worker_adapter.get_source.assert_called_once_with("src_y", "default")
