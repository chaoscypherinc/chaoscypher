# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for Engine lazy property initialization."""

import pytest

from chaoscypher_core import Engine


@pytest.fixture
def engine(tmp_path):
    """Create an Engine instance with a temporary database directory."""
    db_dir = tmp_path / "databases" / "test"
    db_dir.mkdir(parents=True)
    eng = Engine(str(db_dir), initialize_db=True)
    yield eng
    eng.close()


class TestEngineLazyProperties:
    """Test that llm_provider, extraction_service, and commit_service are lazy."""

    def test_llm_provider_not_created_at_init(self, engine):
        """LLMProvider should not be instantiated until first access."""
        assert engine._llm_provider is None

    def test_llm_provider_created_on_access(self, engine):
        """Accessing llm_provider should create and cache the instance."""
        provider = engine.llm_provider
        assert provider is not None
        assert engine._llm_provider is provider

    def test_llm_provider_cached(self, engine):
        """Repeated access should return the same instance."""
        first = engine.llm_provider
        second = engine.llm_provider
        assert first is second

    def test_extraction_service_not_created_at_init(self, engine):
        """ExtractionService should not be instantiated until first access."""
        assert engine._extraction_service is None

    def test_extraction_service_created_on_access(self, engine):
        """Accessing extraction_service should create and cache the instance."""
        service = engine.extraction_service
        assert service is not None
        assert engine._extraction_service is service

    def test_extraction_service_cached(self, engine):
        """Repeated access should return the same instance."""
        first = engine.extraction_service
        second = engine.extraction_service
        assert first is second

    def test_extraction_service_uses_engine_llm_provider(self, engine):
        """ExtractionService should use the engine's llm_provider."""
        service = engine.extraction_service
        assert service.llm_provider is engine.llm_provider

    def test_commit_service_not_created_at_init(self, engine):
        """SourceCommitService should not be instantiated until first access."""
        assert engine._commit_service is None

    def test_commit_service_created_on_access(self, engine):
        """Accessing commit_service should create and cache the instance."""
        service = engine.commit_service
        assert service is not None
        assert engine._commit_service is service

    def test_commit_service_cached(self, engine):
        """Repeated access should return the same instance."""
        first = engine.commit_service
        second = engine.commit_service
        assert first is second

    def test_commit_service_uses_engine_repos(self, engine):
        """SourceCommitService should be wired with engine repositories.

        ``graph_repository`` is intentionally a SEPARATE ``GraphRepository``
        instance bound to ``storage_adapter.session`` (not ``engine._graph_session``)
        so storage-side writes and graph-side writes share one SQLite writer
        lock during ``adapter.transaction()`` — see commit 39b094a01
        ("bind engine.commit_service to storage_adapter.session"). What
        matters is that both repos point at the same database and that the
        commit_service's repo shares the storage adapter's session.
        """
        from chaoscypher_core.adapters.sqlite.repos.graph.sqlite_repository import (
            GraphRepository,
        )

        service = engine.commit_service
        # Wired to its own GraphRepository on the storage adapter's session.
        assert isinstance(service.graph_repository, GraphRepository)
        assert service.graph_repository.session is engine.storage_adapter.session
        # The rest of the wiring is unchanged: shared adapters / repos.
        assert service.search_repository is engine.search_repository
        assert service.source_repository is engine.storage_adapter
        assert service.sources_repository is engine.storage_adapter
        assert service.indexing_repository is engine.storage_adapter


class TestEngineAlembicStamp:
    """Engine(initialize_db=True) must build the schema through Alembic.

    Regression for the bootstrap bug: ``initialize_database(run_migrations=
    False)`` ran a bare ``create_all`` with no ``alembic_version`` stamp, so a
    CLI-first-created DB later opened by Cortex/Neuron got ``ensure_stamped``
    at the baseline and then replayed 0002→HEAD against already-present schema
    → migration crash / cross-tool drift. A bootstrapped DB must instead be
    stamped at HEAD, exactly like ``init_database``.
    """

    def test_initialize_db_leaves_db_stamped_at_head(self, tmp_path) -> None:
        from chaoscypher_core.database.migrations.runner import (
            current_revision,
            head_revision,
        )

        db_dir = tmp_path / "databases" / "stamped"
        db_dir.mkdir(parents=True)
        eng = Engine(str(db_dir), initialize_db=True)
        try:
            db_path = db_dir / "app.db"
            assert db_path.exists(), "Engine did not create the DB file"
            assert current_revision(db_path) == head_revision(), (
                "bootstrap left the DB unstamped (bare create_all) instead of at Alembic HEAD"
            )
        finally:
            eng.close()


class TestEngineOptionalDataDir:
    """Test Engine works without explicit data_dir."""

    def test_engine_with_database_kwarg(self, tmp_path, monkeypatch):
        """Engine(database='test') auto-resolves path."""
        monkeypatch.setenv("CHAOSCYPHER_DATA_DIR", str(tmp_path))
        with Engine(database="test") as engine:
            assert engine.database_name == "test"
            assert "test" in str(engine.data_dir)

    def test_engine_default_database(self, tmp_path, monkeypatch):
        """Engine() uses 'default' database when no args provided."""
        monkeypatch.setenv("CHAOSCYPHER_DATA_DIR", str(tmp_path))
        with Engine() as engine:
            assert engine.database_name == "default"

    def test_engine_explicit_data_dir_still_works(self, tmp_path):
        """Existing Engine(data_dir=...) pattern still works."""
        db_dir = tmp_path / "databases" / "explicit"
        db_dir.mkdir(parents=True)
        with Engine(str(db_dir)) as engine:
            assert engine.database_name == "explicit"

    def test_engine_database_kwarg_with_settings(self, tmp_path, monkeypatch):
        """Engine(database='x', settings=s) uses settings paths."""
        from chaoscypher_core import EngineSettings

        monkeypatch.setenv("CHAOSCYPHER_DATA_DIR", str(tmp_path))
        settings = EngineSettings()
        with Engine(database="custom", settings=settings) as engine:
            assert engine.database_name == "custom"


@pytest.mark.asyncio
class TestEngineAsyncContextManager:
    """Test that Engine works as an async context manager."""

    async def test_async_with_returns_engine(self, tmp_path):
        """Async with Engine(...) should return the engine instance."""
        db_dir = tmp_path / "databases" / "test_async"
        db_dir.mkdir(parents=True)
        async with Engine(str(db_dir), initialize_db=True) as engine:
            assert isinstance(engine, Engine)
            assert not engine._closed

    async def test_async_with_closes_on_exit(self, tmp_path):
        """Async with Engine(...) should close on exit."""
        db_dir = tmp_path / "databases" / "test_async2"
        db_dir.mkdir(parents=True)
        async with Engine(str(db_dir), initialize_db=True) as engine:
            pass
        assert engine._closed
