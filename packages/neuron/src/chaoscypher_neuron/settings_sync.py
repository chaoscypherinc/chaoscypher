# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Settings change listener and hot-reload logic.

Listens on a Valkey pub/sub channel for settings-change notifications
and applies updates to the LLM provider, chunk extraction service,
and load balancer without restarting the worker.
"""

import asyncio
import logging
from typing import TYPE_CHECKING

from valkey.asyncio import Valkey

from chaoscypher_core.queue import queue_client
from chaoscypher_core.utils.logging.app_config import get_logger
from chaoscypher_neuron.config import get_neuron_settings


if TYPE_CHECKING:
    from chaoscypher_core.app_config import Settings
    from chaoscypher_neuron.types import WorkerContext


logger = get_logger(__name__)

_CHANNEL = "chaoscypher:settings:changed"
_VERSION_KEY = "chaoscypher:settings:version"

_reload_lock = asyncio.Lock()
_known_version: int = 0


def _apply_logging_level(level: str) -> None:
    """Apply a log level change to this process's root logger.

    Args:
        level: Log level string (DEBUG, INFO, WARNING, ERROR, CRITICAL).

    """
    level_upper = level.upper()
    numeric = getattr(logging, level_upper, None)
    if numeric is None:
        logger.warning("invalid_logging_level_received", level=level)
        return

    root = logging.getLogger()
    old_level = logging.getLevelName(root.level)
    root.setLevel(numeric)
    logger.info("logging_level_changed", old_level=old_level, new_level=level_upper)


__all__ = [
    "listen_for_settings_changes",
    "reload_llm_provider",
]


def _create_pubsub_client(settings: Settings) -> Valkey:
    """Create a dedicated Valkey client for pub/sub with no socket timeout.

    Pub/sub connections block indefinitely waiting for messages, so
    the default socket timeout would cause spurious reconnections.

    Args:
        settings: Application settings with queue configuration.

    Returns:
        Valkey client configured for long-lived pub/sub.
    """
    return Valkey(
        host=settings.queue.queue_host,
        port=settings.queue.queue_port,
        db=settings.queue.queue_database,
        password=(
            settings.queue.queue_password.get_secret_value()
            if settings.queue.queue_password
            else None
        ),
        ssl=settings.queue.queue_ssl,
        socket_timeout=None,
        socket_keepalive=True,
        decode_responses=False,
    )


async def _reconcile_version(client: Valkey, ctx: WorkerContext) -> None:
    """Reload settings if the live version counter is ahead of what this worker knows.

    Called immediately after subscribing so workers that start after a publish
    automatically pick up the missed settings change.

    Args:
        client: Valkey client used for the pub/sub connection (also used for GET).
        ctx: Typed worker context dictionary passed through to reload_llm_provider.

    """
    global _known_version

    try:
        raw = await client.get(_VERSION_KEY)
        live_version = int(raw) if raw else 0
    except Exception:
        logger.warning("settings_version_check_failed", exc_info=True)
        return

    if live_version > _known_version:
        logger.info(
            "settings_reload_catchup",
            known=_known_version,
            live=live_version,
        )
        await reload_llm_provider(ctx)
        _known_version = live_version
    else:
        logger.debug(
            "settings_version_current",
            known=_known_version,
            live=live_version,
        )


async def listen_for_settings_changes(ctx: WorkerContext) -> None:
    """Background task that listens for settings change notifications via Valkey pub/sub.

    Uses a dedicated Valkey connection with no socket timeout so the
    pub/sub subscription blocks cleanly without spurious timeout errors.
    Automatically reconnects on real connection errors. On each connect,
    reconciles against the durable version counter so workers that start
    after a publish automatically reload stale settings.

    Args:
        ctx: Typed worker context dictionary.

    """
    if not queue_client.client:
        logger.warning("queue_not_connected_for_settings_listener")
        return

    settings = ctx.get("settings")
    if not settings:
        logger.warning("settings_not_available_for_pubsub_client")
        return

    neuron_settings = get_neuron_settings()
    pubsub_valkey: Valkey | None = None
    reconnect_delay = neuron_settings.settings_sync_reconnect_delay

    while True:
        try:
            pubsub_valkey = _create_pubsub_client(settings)
            pubsub = pubsub_valkey.pubsub()
            await pubsub.subscribe(_CHANNEL)
            logger.info("settings_change_listener_started")
            reconnect_delay = neuron_settings.settings_sync_reconnect_delay

            # Reconcile against the durable version counter so any publishes
            # that happened before this worker connected are not missed.
            await _reconcile_version(pubsub_valkey, ctx)

            async for message in pubsub.listen():
                if message["type"] == "message":
                    data = message.get("data", b"")
                    if isinstance(data, bytes):
                        data = data.decode("utf-8", errors="replace")

                    logger.info(
                        "settings_change_notification_received",
                        data=data,
                    )

                    if not data.startswith("v1:"):
                        logger.warning(
                            "settings_pubsub_unknown_version",
                            body=data[:50],
                        )
                        continue

                    payload = data.removeprefix("v1:")

                    if payload.startswith("logging_level:"):
                        new_level = payload.split(":", 1)[1]
                        _apply_logging_level(new_level)
                    else:
                        await reload_llm_provider(ctx)

                    # Sync known_version after each live message so reconnects
                    # don't trigger a redundant reload.
                    await _reconcile_version(pubsub_valkey, ctx)
        except asyncio.CancelledError:
            logger.info("settings_change_listener_cancelled")
            if pubsub_valkey:
                await pubsub_valkey.aclose()
            return
        except Exception:
            logger.warning(
                "settings_change_listener_reconnecting",
                reconnect_delay=reconnect_delay,
                exc_info=True,
            )
        finally:
            if pubsub_valkey:
                try:
                    await pubsub_valkey.aclose()
                except Exception:
                    logger.debug("settings_pubsub_cleanup_failed", exc_info=True)
                pubsub_valkey = None

        await asyncio.sleep(reconnect_delay)
        reconnect_delay = min(
            reconnect_delay * 2, neuron_settings.settings_sync_max_reconnect_delay
        )


async def reload_llm_provider(ctx: WorkerContext) -> None:
    """Reload the LLM provider with fresh settings.

    Uses an asyncio lock to prevent concurrent reloads from racing
    when multiple settings-change notifications arrive in quick
    succession.

    Args:
        ctx: Typed worker context dictionary.

    """
    from chaoscypher_core.app_config.engine_factory import build_engine_settings
    from chaoscypher_core.llm_queue import LLMProvider
    from chaoscypher_core.llm_queue.queue_service import LLMQueueService

    from .setup.llm_handlers import setup_llm_handlers
    from .setup.ops_handlers import setup_operations_handlers

    try:
        await asyncio.wait_for(_reload_lock.acquire(), timeout=30.0)
    except TimeoutError:
        logger.warning(
            "settings_reload_lock_timeout",
            message="Could not acquire reload lock within 30s, skipping reload",
        )
        return

    try:
        config_manager = ctx.get("config_manager")
        if not config_manager:
            logger.warning("config_manager_not_found_for_reload")
            return

        # Snapshot current context so we can restore on failure
        prev_settings = ctx.get("settings")
        prev_llm_provider = ctx.get("llm_provider")
        prev_llm_service = ctx.get("llm_service")
        prev_engine_settings = ctx.get("engine_settings")

        try:
            # Force reload settings from disk via ConfigManager
            config_manager.invalidate_cache()
            settings = config_manager.get_settings()

            # Sync the cortex get_settings() cache
            from chaoscypher_core.app_config import get_settings, set_settings

            get_settings.cache_clear()
            set_settings(settings)
            logger.info(
                "settings_caches_synced",
                chat_model=settings.llm.ollama_chat_model,
                embedding_model=settings.embedding.model,
                extraction_model=settings.llm.ollama_extraction_model,
            )

            # Recreate SearchRepository if vector dimensions changed
            from chaoscypher_core.adapters.sqlite.repos import SearchRepository
            from chaoscypher_core.database.engine import get_engine

            new_vector_dim = settings.search.vector_dimensions
            current_search_repo: SearchRepository | None = ctx.get("search_repository")
            if current_search_repo and current_search_repo.vector_dim != new_vector_dim:
                logger.info(
                    "search_repository_recreating",
                    old_dimensions=current_search_repo.vector_dim,
                    new_dimensions=new_vector_dim,
                )
                search_repository = SearchRepository(
                    engine=get_engine(settings.current_database),
                    vector_dim=new_vector_dim,
                    embedding_model=settings.embedding.model,
                )
                ctx["search_repository"] = search_repository
            else:
                search_repository = current_search_repo  # type: ignore[assignment]

            # Recreate LLM provider with new settings
            engine_settings = build_engine_settings(settings)
            llm_provider = LLMProvider(
                settings=engine_settings,
                managers={
                    "graph": ctx.get("graph_repository"),
                    "search": search_repository,
                    "config": config_manager,
                },
            )
            llm_service = LLMQueueService(provider=llm_provider, settings=settings)

            # Update context before re-registering handlers (setup_llm_handlers reads from ctx)
            ctx["settings"] = settings
            ctx["llm_provider"] = llm_provider
            ctx["llm_service"] = llm_service
            ctx["engine_settings"] = engine_settings  # reuse the build above

            # Re-register ALL handlers so services (IndexingService, etc.) pick up
            # the new engine_settings including the updated embedding model.
            await setup_llm_handlers(ctx)
            await setup_operations_handlers(ctx)

            logger.info(
                "llm_provider_reloaded",
                chat_provider=settings.llm.chat_provider,
                chat_model=settings.llm.ollama_chat_model,
                embedding_model=settings.embedding.model,
                extraction_model=settings.llm.ollama_extraction_model,
                ollama_instances=len(settings.llm.ollama_instances or []),
            )
        except Exception:
            logger.error("llm_provider_reload_failed", exc_info=True)
            # Restore previous context so handlers keep working with old settings
            if prev_settings is not None:
                ctx["settings"] = prev_settings
            if prev_llm_provider is not None:
                ctx["llm_provider"] = prev_llm_provider
            if prev_llm_service is not None:
                ctx["llm_service"] = prev_llm_service
            if prev_engine_settings is not None:
                ctx["engine_settings"] = prev_engine_settings
    finally:
        _reload_lock.release()
