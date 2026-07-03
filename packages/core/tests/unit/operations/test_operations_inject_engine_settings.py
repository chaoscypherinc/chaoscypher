# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tier-2 config unification: operations services consume EngineSettings.

These tests pin the contract that the operation handlers no longer feed the
app ``Settings`` singleton straight into engine collaborators. Instead they
build ``EngineSettings`` at the operation boundary (or reuse the cached
worker-context engine settings) and read engine-relevant groups from that
view. App-only reads (``priorities``) are intentionally still sourced from
the app singleton — those are asserted to keep working, not removed.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_core.settings import EngineSettings


# ===========================================================================
# export_operations_service
# ===========================================================================


def _export_service() -> Any:
    from chaoscypher_core.operations.export_operations_service import (
        ExportOperationsService,
    )

    return ExportOperationsService(workflow_db=MagicMock())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("handler_name", "data"),
    [
        ("_export_graph_handler", {}),
        ("_export_by_sources_handler", {"source_ids": ["a"]}),
    ],
)
async def test_export_handler_hands_engine_settings_to_exporter(
    handler_name: str, data: dict[str, Any]
) -> None:
    """CcxExporter (typed EngineSettings) gets the engine view, not the app singleton."""
    service = _export_service()

    app_settings = MagicMock(name="app_settings")
    engine_settings = EngineSettings(current_database="engine-db")

    exporter = MagicMock()
    exporter.export.return_value = b"ccx-bytes"
    exporter.get_export_filename.return_value = "f.ccx"
    exporter_cls = MagicMock(return_value=exporter)
    get_adapter = MagicMock(return_value=MagicMock())
    build_engine = MagicMock(return_value=engine_settings)

    with (
        patch(
            "chaoscypher_core.app_config.get_settings",
            MagicMock(return_value=app_settings),
        ),
        patch(
            "chaoscypher_core.app_config.engine_factory.build_engine_settings",
            build_engine,
        ),
        patch(
            "chaoscypher_core.database.adapter_factory.get_sqlite_adapter",
            get_adapter,
        ),
        patch(
            "chaoscypher_core.repo_factories.get_graph_repository",
            MagicMock(),
        ),
        patch("chaoscypher_core.services.export.CcxExporter", exporter_cls),
        patch(
            "chaoscypher_core.operations.export_operations_service.event_bus",
            MagicMock(),
        ),
    ):
        await getattr(service, handler_name)(data=data, metadata=None, task_id="t")

    # The boundary build runs, converting the app singleton to the engine view.
    build_engine.assert_called_once_with(app_settings)

    # CcxExporter must receive the engine view, never the raw app singleton.
    _, exporter_kwargs = exporter_cls.call_args
    assert exporter_kwargs["settings"] is engine_settings
    assert exporter_kwargs["settings"] is not app_settings

    # The adapter is scoped by the engine settings' current_database.
    get_adapter.assert_called_once_with("engine-db")


# ===========================================================================
# indexing_handler
# ===========================================================================


def _make_chunking_result(**kw: Any) -> MagicMock:
    defaults = {
        "total_small_chunks": 1,
        "total_groups": 1,
        "chunks_filtered": 0,
        "normalize_drops": 0,
        "prestrip_lines_removed": 0,
        "chunks_skipped_by_depth": 0,
    }
    defaults.update(kw)
    r = MagicMock()
    for k, v in defaults.items():
        setattr(r, k, v)
    return r


def _run_indexing_with_spies(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Wire ``_run_indexing`` dependencies and return captured collaborator calls.

    Returns a dict of MagicMock spies the caller asserts against.
    """
    from chaoscypher_core.operations.importing import indexing_handler

    monkeypatch.setattr(
        indexing_handler,
        "_extract_text",
        lambda **kw: (
            "x" * 200,
            {"lines_removed": 0, "paragraphs_deduplicated": 0, "chars_removed": 0},
        ),
    )
    fake_loader_registry = MagicMock()
    fake_loader_registry.load_document.return_value = [{"content": "x", "metadata": {}}]
    monkeypatch.setattr(
        "chaoscypher_core.services.sources.loaders.get_loader_registry",
        lambda *a, **kw: fake_loader_registry,
    )

    captured: dict[str, Any] = {}

    async def _fake_vision(**kw: Any) -> tuple[Any, None]:
        captured["vision_data_dir"] = kw.get("data_dir")
        return kw["documents"], None

    monkeypatch.setattr(indexing_handler, "_apply_vision_processing", _fake_vision)
    monkeypatch.setattr(indexing_handler, "queue_embed_chunks", AsyncMock(return_value="tsk_e1"))
    monkeypatch.setattr(indexing_handler, "event_bus", MagicMock())
    monkeypatch.setattr(
        indexing_handler,
        "_persist_original_text",
        lambda **kw: captured.setdefault("persist_data_dir", kw.get("data_dir")) or None,
    )

    return captured


def _indexing_settings() -> MagicMock:
    """App settings sentinel. data_dir is a distinct sentinel that must NOT be read."""
    s = MagicMock(name="app_settings")
    s.priorities.background = 50
    s.data_dir = "/APP_DATA_DIR_SHOULD_NOT_BE_USED"
    return s


@pytest.mark.asyncio
async def test_indexing_eager_detection_uses_engine_settings_not_app(monkeypatch) -> None:
    """Eager-detection domain registry is keyed off engine settings, not the app singleton.

    ``get_domain_registry`` is typed ``EngineSettings``; before unification the
    indexing handler passed the app ``settings`` singleton (a latent type bug).
    """
    from chaoscypher_core.operations.importing import indexing_handler

    _run_indexing_with_spies(monkeypatch)

    adapter = MagicMock()
    adapter.get_source.return_value = {
        "id": "src-1",
        "status": "indexing",
        "confirmation_required": True,
        "forced_domain": None,
    }

    chunking_service = MagicMock()
    chunking_service.create_chunks = AsyncMock(return_value=_make_chunking_result())
    chunking_service.store_chunks = MagicMock()

    engine_settings = EngineSettings(current_database="engine-db")
    app_settings = _indexing_settings()

    registry_settings_seen: dict[str, Any] = {}

    def _fake_get_domain_registry(settings: Any = None, database_name: str = "default") -> Any:
        registry_settings_seen["settings"] = settings
        return MagicMock()

    with (
        patch(
            "chaoscypher_core.services.sources.engine.extraction.domains.get_domain_registry",
            _fake_get_domain_registry,
        ),
        patch(
            "chaoscypher_core.services.sources.engine.extraction.orchestration.detect_extraction_domain",
            return_value={
                "domain": MagicMock(),
                "detected_domain": "technical",
                "confidence": 0.9,
                "ranking": [],
                "low_confidence": False,
                "entity_guidance": "",
                "relationship_guidance": "",
            },
        ),
        patch("chaoscypher_core.operations.importing.confirmation_gate.write_detection_proposal"),
    ):
        await indexing_handler._run_indexing(
            file_id="src-1",
            file_info={"filename": "doc.txt"},
            filepath="/tmp/doc.txt",
            analysis_depth="full",
            enable_normalization=False,
            enable_vision=False,
            adapter=adapter,
            chunking_service=chunking_service,
            engine_settings=engine_settings,
            settings=app_settings,
            database_name="engine-db",
        )

    # The registry must be built from the ENGINE settings, never the app singleton.
    assert registry_settings_seen["settings"] is engine_settings
    assert registry_settings_seen["settings"] is not app_settings


@pytest.mark.asyncio
async def test_indexing_data_dir_comes_from_engine_paths(monkeypatch) -> None:
    """``data_dir`` threaded into vision/persist comes from engine_settings.paths, not app."""
    from chaoscypher_core.operations.importing import indexing_handler

    captured = _run_indexing_with_spies(monkeypatch)

    adapter = MagicMock()
    adapter.get_source.return_value = {
        "id": "src-1",
        "status": "indexing",
        "confirmation_required": False,
        "forced_domain": None,
    }

    chunking_service = MagicMock()
    chunking_service.create_chunks = AsyncMock(return_value=_make_chunking_result())
    chunking_service.store_chunks = MagicMock()

    engine_settings = EngineSettings(current_database="engine-db")
    engine_settings.paths.data_dir = "/ENGINE_DATA_DIR"
    # preserve_original_text_for_citations defaults True → _persist_original_text runs.

    app_settings = _indexing_settings()

    await indexing_handler._run_indexing(
        file_id="src-1",
        file_info={"filename": "doc.txt"},
        filepath="/tmp/doc.txt",
        analysis_depth="full",
        enable_normalization=False,
        enable_vision=False,
        adapter=adapter,
        chunking_service=chunking_service,
        engine_settings=engine_settings,
        settings=app_settings,
        database_name="engine-db",
    )

    # Both the vision hand-off and the original-text persistence must source
    # data_dir from engine_settings.paths.data_dir, NOT the app singleton.
    assert captured.get("vision_data_dir") == "/ENGINE_DATA_DIR"
    assert captured.get("persist_data_dir") == "/ENGINE_DATA_DIR"


# ===========================================================================
# import_service
# ===========================================================================


def _make_import_service(source_repository: object, engine_settings: Any = None) -> Any:
    from chaoscypher_core.operations.importing.import_service import (
        ImportOperationsService,
    )

    return ImportOperationsService(
        graph_repository=MagicMock(),
        config_manager=MagicMock(),
        source_manager=MagicMock(),
        trigger_service=MagicMock(),
        llm_service=AsyncMock(),
        source_repository=source_repository,
        chunking_service=MagicMock(),
        indexing_service=MagicMock(),
        engine_settings=engine_settings,
    )


@pytest.mark.asyncio
async def test_commit_search_repository_built_from_engine_settings(monkeypatch) -> None:
    """SearchRepository vector_dim/model come from engine settings, not the app singleton.

    The app singleton's ``search``/``embedding`` groups are sentinels that must
    NOT reach SearchRepository — the engine view supplies the real values.
    """
    from chaoscypher_core.operations.importing import import_service

    adapter = MagicMock()
    # Not already committed (so the commit path runs).
    adapter.get_source.return_value = {"commit_complete": False}

    service = _make_import_service(adapter)
    service.search_repository = None  # force the SearchRepository construction branch
    service.graph_repository = MagicMock()

    app_settings = MagicMock(name="app_settings")
    app_settings.current_database = "engine-db"
    app_settings.priorities.background = 50
    # Sentinels that must NOT reach SearchRepository.
    app_settings.search.vector_dimensions = "APP_VECTOR_DIM"
    app_settings.embedding.model = "APP_EMBED_MODEL"

    engine_settings = EngineSettings(current_database="engine-db")
    engine_settings.search.vector_dimensions = 1536
    engine_settings.embedding.model = "engine-embed-model"

    search_repo_seen: dict[str, Any] = {}

    def _fake_search_repo(**kw: Any) -> MagicMock:
        search_repo_seen.update(kw)
        return MagicMock()

    commit_service = MagicMock()
    commit_service.commit = AsyncMock(return_value={"created_nodes": [], "created_edges": []})

    class _NullCtx:
        async def __aenter__(self) -> None:
            return None

        async def __aexit__(self, *a: Any) -> None:
            return None

    monkeypatch.setattr(import_service, "source_heartbeat", lambda **kw: _NullCtx())

    with (
        patch(
            "chaoscypher_core.app_config.get_settings",
            return_value=app_settings,
        ),
        patch(
            "chaoscypher_core.app_config.engine_factory.build_engine_settings",
            return_value=engine_settings,
        ),
        patch(
            "chaoscypher_core.operations.pause_guard.check_paused",
            return_value=MagicMock(paused=False),
        ),
        patch(
            "chaoscypher_core.adapters.sqlite.repos.SearchRepository",
            _fake_search_repo,
        ),
        patch(
            "chaoscypher_core.database.engine.get_engine",
            return_value=MagicMock(),
        ),
        patch(
            "chaoscypher_core.services.sources.engine.commit.SourceCommitService",
            return_value=commit_service,
        ),
        patch.object(import_service, "queue_client", MagicMock(enqueue_task=AsyncMock())),
    ):
        await service._import_commit_handler(
            data={
                "file_id": "src-1",
                "file_info": {"filename": "doc.txt"},
                "commit_data": {},
            }
        )

    assert search_repo_seen.get("vector_dim") == 1536
    assert search_repo_seen.get("embedding_model") == "engine-embed-model"
    assert search_repo_seen.get("vector_dim") != "APP_VECTOR_DIM"
    assert search_repo_seen.get("embedding_model") != "APP_EMBED_MODEL"
