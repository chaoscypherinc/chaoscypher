# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Type definitions for the Neuron worker.

Provides a ``WorkerContext`` TypedDict that replaces the untyped
``dict[str, Any]`` previously threaded through setup, handler
registration, settings reload, and recovery code paths.
"""

from typing import TYPE_CHECKING, Any, TypedDict


if TYPE_CHECKING:
    import asyncio

    from sqlmodel import Session

    from chaoscypher_core.adapters.sqlite import SqliteAdapter
    from chaoscypher_core.adapters.sqlite.repos import GraphRepository, SearchRepository
    from chaoscypher_core.app_config import Settings
    from chaoscypher_core.app_config.manager import ConfigManager
    from chaoscypher_core.database.repository import DatabaseRepository
    from chaoscypher_core.llm_queue import LLMProvider
    from chaoscypher_core.llm_queue.queue_service import LLMQueueService
    from chaoscypher_core.services.workflows.triggers.engine.executor import TriggerExecutor
    from chaoscypher_core.settings import EngineSettings


class WorkerContext(TypedDict, total=False):
    """Typed context dictionary passed through all Neuron worker phases.

    Populated by ``setup_shared()`` and extended by ``run_worker()``.
    All keys are optional (``total=False``) because the context is built
    incrementally during startup.
    """

    # Populated by setup_shared()
    database_repository: DatabaseRepository
    config_manager: ConfigManager
    settings: Settings
    engine_settings: EngineSettings
    current_database: str
    search_repository: SearchRepository
    graph_repository: GraphRepository
    worker_session: Session
    llm_provider: LLMProvider
    llm_service: LLMQueueService
    storage_adapter: SqliteAdapter

    # Populated by setup_operations_handlers()
    trigger_dispatcher: TriggerExecutor

    # Populated by run_worker() after setup
    settings_listener_task: asyncio.Task[Any]


__all__ = ["WorkerContext"]
