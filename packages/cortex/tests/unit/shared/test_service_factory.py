# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Unit tests for ServiceFactory (shared/service_factory.py).

ServiceFactory is the boilerplate-free service accessor used by workers and
scripts. These tests verify:

- ``__enter__`` opens the SqliteAdapter and ``__exit__`` disconnects it.
- The lazy ``session`` / ``adapter`` properties raise RuntimeError when used
  outside the context manager.
- ``graph_repository`` / ``search_repository`` are lazily built once and cached.
- ``search_service()`` and ``source_service()`` construct the right service
  wired with the shared adapter / repositories / settings.

All Core/Cortex collaborators are patched at the *source* path the factory
imports them from (eager imports patched on the service_factory module;
lazy in-function imports patched on their defining module) so no real DB or
service is constructed.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from chaoscypher_cortex.shared.service_factory import ServiceFactory


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_adapter() -> MagicMock:
    """A fake SqliteAdapter with a truthy session and a disconnect() spy."""
    adapter = MagicMock(name="SqliteAdapter")
    adapter.session = MagicMock(name="Session")
    return adapter


# ---------------------------------------------------------------------------
# Context manager lifecycle
# ---------------------------------------------------------------------------


def test_enter_opens_adapter_and_records_settings() -> None:
    """__enter__ loads settings, opens the adapter for the given database."""
    adapter = _make_adapter()
    settings = MagicMock(name="Settings")
    with (
        patch(
            "chaoscypher_cortex.shared.service_factory.get_sqlite_adapter",
            return_value=adapter,
        ) as get_adapter,
        patch(
            "chaoscypher_cortex.shared.service_factory.get_settings",
            return_value=settings,
        ),
    ):
        with ServiceFactory("mydb") as factory:
            assert factory is not None
            assert factory.adapter is adapter
            assert factory.settings is settings
        get_adapter.assert_called_once_with(database_name="mydb")


def test_exit_disconnects_adapter() -> None:
    """__exit__ calls adapter.disconnect() and clears the reference."""
    adapter = _make_adapter()
    with (
        patch(
            "chaoscypher_cortex.shared.service_factory.get_sqlite_adapter",
            return_value=adapter,
        ),
        patch("chaoscypher_cortex.shared.service_factory.get_settings"),
    ):
        with ServiceFactory() as factory:
            pass
        adapter.disconnect.assert_called_once_with()
        # Reference cleared on exit.
        assert factory._adapter is None


def test_session_property_outside_context_raises() -> None:
    """Accessing .session before __enter__ raises RuntimeError."""
    factory = ServiceFactory()
    with pytest.raises(RuntimeError, match="context manager"):
        _ = factory.session


def test_adapter_property_outside_context_raises() -> None:
    """Accessing .adapter before __enter__ raises RuntimeError."""
    factory = ServiceFactory()
    with pytest.raises(RuntimeError, match="context manager"):
        _ = factory.adapter


def test_settings_property_lazy_loads_when_not_yet_set() -> None:
    """.settings calls get_settings() lazily if __enter__ never ran."""
    settings = MagicMock(name="Settings")
    factory = ServiceFactory()
    with patch(
        "chaoscypher_cortex.shared.service_factory.get_settings",
        return_value=settings,
    ) as get_settings:
        assert factory.settings is settings
        # Second access reuses the cached value (no second call).
        assert factory.settings is settings
        get_settings.assert_called_once_with()


# ---------------------------------------------------------------------------
# Shared repositories (lazy + cached)
# ---------------------------------------------------------------------------


def test_graph_repository_lazy_and_cached() -> None:
    """graph_repository builds once via get_graph_repository, then caches."""
    adapter = _make_adapter()
    graph_repo = MagicMock(name="GraphRepository")
    with (
        patch(
            "chaoscypher_cortex.shared.service_factory.get_sqlite_adapter",
            return_value=adapter,
        ),
        patch("chaoscypher_cortex.shared.service_factory.get_settings"),
        patch(
            "chaoscypher_cortex.shared.service_factory.get_graph_repository",
            return_value=graph_repo,
        ) as get_graph,
    ):
        with ServiceFactory("db1") as factory:
            first = factory.graph_repository
            second = factory.graph_repository
        assert first is graph_repo
        assert second is graph_repo
        # Built exactly once with the shared session + database name.
        get_graph.assert_called_once_with(adapter.session, "db1")


def test_search_repository_lazy_and_cached() -> None:
    """search_repository builds once via get_search_repository, then caches."""
    adapter = _make_adapter()
    search_repo = MagicMock(name="SearchRepository")
    with (
        patch(
            "chaoscypher_cortex.shared.service_factory.get_sqlite_adapter",
            return_value=adapter,
        ),
        patch("chaoscypher_cortex.shared.service_factory.get_settings"),
        patch(
            "chaoscypher_cortex.shared.service_factory.get_search_repository",
            return_value=search_repo,
        ) as get_search,
    ):
        with ServiceFactory("db2") as factory:
            first = factory.search_repository
            second = factory.search_repository
        assert first is search_repo
        assert second is search_repo
        get_search.assert_called_once_with(database_name="db2")


# ---------------------------------------------------------------------------
# Service factories
# ---------------------------------------------------------------------------


def test_search_service_wires_dependencies() -> None:
    """search_service() builds a SearchService with all shared deps wired."""
    adapter = _make_adapter()
    graph_repo = MagicMock(name="GraphRepository")
    search_repo = MagicMock(name="SearchRepository")
    settings = MagicMock(name="Settings")
    built_service = MagicMock(name="SearchService")

    with (
        patch(
            "chaoscypher_cortex.shared.service_factory.get_sqlite_adapter",
            return_value=adapter,
        ),
        patch(
            "chaoscypher_cortex.shared.service_factory.get_settings",
            return_value=settings,
        ),
        patch(
            "chaoscypher_cortex.shared.service_factory.get_graph_repository",
            return_value=graph_repo,
        ),
        patch(
            "chaoscypher_cortex.shared.service_factory.get_search_repository",
            return_value=search_repo,
        ),
        patch(
            "chaoscypher_cortex.features.search.service.SearchService",
            return_value=built_service,
        ) as search_service_cls,
    ):
        with ServiceFactory("db") as factory:
            result = factory.search_service()

    assert result is built_service
    search_service_cls.assert_called_once_with(
        search_repository=search_repo,
        graph_repository=graph_repo,
        indexing_repository=adapter,
        source_repository=adapter,
        sources_repository=adapter,
        settings=settings,
    )


def test_source_service_wires_engine_and_backend_service() -> None:
    """source_service() builds the engine SourceService and wraps it.

    The factory:
      1. converts backend settings to engine settings,
      2. constructs the engine SourceService with the adapter + db name,
      3. wraps it in the Cortex SourceService with shared repos.
    """
    adapter = _make_adapter()
    graph_repo = MagicMock(name="GraphRepository")
    search_repo = MagicMock(name="SearchRepository")
    settings = MagicMock(name="Settings")
    engine_settings = MagicMock(name="EngineSettings")
    engine_service = MagicMock(name="EngineSourceService")
    backend_service = MagicMock(name="CortexSourceService")

    with (
        patch(
            "chaoscypher_cortex.shared.service_factory.get_sqlite_adapter",
            return_value=adapter,
        ),
        patch(
            "chaoscypher_cortex.shared.service_factory.get_settings",
            return_value=settings,
        ),
        patch(
            "chaoscypher_cortex.shared.service_factory.get_graph_repository",
            return_value=graph_repo,
        ),
        patch(
            "chaoscypher_cortex.shared.service_factory.get_search_repository",
            return_value=search_repo,
        ),
        patch(
            "chaoscypher_core.app_config.engine_factory.build_engine_settings",
            return_value=engine_settings,
        ) as build,
        patch(
            "chaoscypher_core.services.graph.management.SourceService",
            return_value=engine_service,
        ) as engine_source_service_cls,
        patch(
            "chaoscypher_cortex.features.sources.service.SourceService",
            return_value=backend_service,
        ) as cortex_source_service_cls,
    ):
        with ServiceFactory("srcdb") as factory:
            result = factory.source_service()

    assert result is backend_service
    build.assert_called_once_with(settings)
    engine_source_service_cls.assert_called_once_with(
        repository=adapter,
        database_name="srcdb",
        settings=engine_settings,
    )
    cortex_source_service_cls.assert_called_once_with(
        engine_service,
        database_name="srcdb",
        settings=settings,
        storage_adapter=adapter,
        graph_repository=graph_repo,
        search_repository=search_repo,
    )
