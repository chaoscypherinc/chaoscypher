# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Shared context initialization for the Neuron worker.

Initializes the shared resources (database, configuration, LLM provider,
Valkey connection, storage adapter) used by both the LLM and Operations
queue handlers.  The resulting services are stored in a mutable context
dictionary that is passed to each setup phase.
"""

import os
from pathlib import Path
from typing import TYPE_CHECKING

from chaoscypher_core.queue import queue_client
from chaoscypher_core.utils.logging.app_config import get_logger


if TYPE_CHECKING:
    from chaoscypher_neuron.types import WorkerContext


logger = get_logger(__name__)

__all__ = ["setup_shared"]


async def setup_shared(ctx: WorkerContext) -> None:
    """Initialize shared resources used by both queues.

    Args:
        ctx: Mutable typed context populated with shared services.

    """
    from chaoscypher_core.adapters.sqlite import SqliteAdapter
    from chaoscypher_core.adapters.sqlite.repos import GraphRepository, SearchRepository
    from chaoscypher_core.app_config import PathSettings
    from chaoscypher_core.app_config.engine_factory import build_engine_settings
    from chaoscypher_core.app_config.manager import (
        ConfigManager,
    )
    from chaoscypher_core.database.repository import (
        DatabaseRepository,
    )
    from chaoscypher_core.llm_queue import LLMProvider
    from chaoscypher_core.llm_queue.queue_service import (
        LLMQueueService,
    )

    # Initialize database repository (path from env or Docker default)
    data_root = os.getenv("CHAOSCYPHER_DATA_DIR", "/data")
    database_repository = DatabaseRepository(data_root=data_root)

    # Initialize configuration using centralized PathSettings
    path_settings = PathSettings()
    settings_path = os.path.join(data_root, path_settings.settings_filename)
    config_manager = ConfigManager(
        settings_path=settings_path,
        default_settings_path=path_settings.default_settings_path,
    )
    settings = config_manager.get_settings()
    current_database = settings.current_database

    # Ensure database exists
    db_info = database_repository.get_database(current_database)
    if not db_info:
        database_repository.create_database(current_database)

    # Initialize search repository (uses app.db engine, not separate search dir)
    from chaoscypher_core.database.engine import get_engine

    search_repository = SearchRepository(
        engine=get_engine(current_database),
        vector_dim=settings.search.vector_dimensions,
        embedding_model=settings.embedding.model,
    )

    # Initialize SqliteAdapter first so its session can be shared with
    # GraphRepository. Separate sessions write through separate SQLite
    # connections, which makes adapter.transaction()'s writer lock
    # contend against the graph repo's own writes and self-deadlock
    # during commit. Sharing the session keeps every write inside one
    # transaction context — the queue worker enforces this per-task via
    # ``register_worker_adapter`` + ``SqliteAdapter.session_scope()``,
    # which installs a fresh ``SafeSession`` in a ContextVar that both
    # ``SqliteAdapter.session`` and ``GraphRepository.session`` resolve
    # to for the duration of each handler dispatch.
    db_path = str(
        Path(settings.data_dir) / "databases" / current_database / settings.paths.app_db_filename
    )
    storage_adapter = SqliteAdapter(db_path=db_path)
    storage_adapter.connect()

    # ``connect()`` initialises the fallback session; the property is
    # typed as ``SafeSession | None`` for adapter lifecycle reasons.
    # After connect it is always populated, so narrow for downstream
    # consumers. The graph repo holds onto this as its **fallback** —
    # actual queue handlers run inside ``session_scope()`` and use the
    # per-task session via the shared ContextVar.
    worker_session = storage_adapter.session
    if worker_session is None:  # pragma: no cover — connect() invariant
        msg = "SqliteAdapter.connect() did not initialise a session"
        raise RuntimeError(msg)

    # Register the adapter so the queue dispatcher enters a fresh
    # ``session_scope()`` around every handler — concurrent handlers
    # never share session state and the 2026-05-20 silent-data-loss
    # race becomes structurally impossible.
    from chaoscypher_core.queue.service import register_worker_adapter

    register_worker_adapter(storage_adapter)

    # Initialize graph repository against the adapter's fallback session.
    # When a handler enters ``session_scope()`` the repo's ``session``
    # property reads the per-task ContextVar instead, so adapter and
    # graph repo stay on one session per dispatch.
    graph_repository = GraphRepository(worker_session, current_database)

    # Initialize LLM provider (shared by both queues)
    engine_settings = build_engine_settings(settings)
    llm_provider = LLMProvider(
        settings=engine_settings,
        managers={
            "graph": graph_repository,
            "search": search_repository,
            "config": config_manager,
        },
    )
    llm_service = LLMQueueService(provider=llm_provider, settings=settings)

    # Connect to Valkey
    await queue_client.connect(settings)

    # Populate context
    ctx["database_repository"] = database_repository
    ctx["config_manager"] = config_manager
    ctx["settings"] = settings
    ctx["engine_settings"] = engine_settings
    ctx["current_database"] = current_database
    ctx["search_repository"] = search_repository
    ctx["graph_repository"] = graph_repository
    ctx["worker_session"] = worker_session
    ctx["llm_provider"] = llm_provider
    ctx["llm_service"] = llm_service
    ctx["storage_adapter"] = storage_adapter
