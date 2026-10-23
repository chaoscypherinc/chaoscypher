# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Publish worker hot-reload notifications over Valkey pub/sub.

Cortex mutates settings on disk; the Neuron worker keeps them live by listening
on ``chaoscypher:settings:changed`` and reloading. Any settings write that the
worker must react to (LLM/model changes, logging level, the active database)
publishes through :func:`publish_settings_change`; a write to settings sections
asks :func:`notify_workers_of`, which publishes when one of
:data:`WORKER_SETTINGS_SECTIONS` changed.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Final

import valkey.asyncio as valkey

from chaoscypher_core.app_config import get_settings
from chaoscypher_core.utils.logging.app_config import get_logger


logger = get_logger(__name__)

_SETTINGS_VERSION_KEY = "chaoscypher:settings:version"
_SETTINGS_CHANNEL = "chaoscypher:settings:changed"

WORKER_SETTINGS_SECTIONS: Final[frozenset[str]] = frozenset(
    {"llm", "search", "embedding", "cache", "graphrag"}
)
"""The ``settings.yaml`` sections a running Neuron worker reads once and must reload to see.

The worker builds its providers and registers its handlers (the chat among them,
which reads ``graphrag``) from the settings it loaded; a change to one of these
sections reaches it only through :func:`publish_settings_change`. Every settings
write path asks :func:`notify_workers_of` with the sections it changed, so a
section added here reaches the workers from all of them (the settings PATCH, a
preset, the Cache section)."""


async def publish_settings_change(reason: str) -> None:
    """Bump the settings version and publish a change so workers hot-reload.

    Uses a direct short-lived Valkey connection (independent of ``queue_client``
    state) so the notification is sent regardless of the queue's connection
    lifecycle. Best-effort: a publish failure is logged, never raised — the
    caller's settings write has already persisted.

    Args:
        reason: A short ``v1:<event>`` tag for the published message. Diagnostic
            only — the worker reloads on any message on the channel.
    """
    try:
        settings = get_settings()
        password_part = (
            f":{settings.queue.queue_password.get_secret_value()}@"
            if settings.queue.queue_password
            else ""
        )
        valkey_url = (
            f"valkey://{password_part}{settings.queue.queue_host}"
            f":{settings.queue.queue_port}/{settings.queue.queue_database}"
        )
        client = valkey.from_url(valkey_url)
        try:
            version = await client.incr(_SETTINGS_VERSION_KEY)
            await client.publish(_SETTINGS_CHANNEL, reason)
            logger.info("settings_change_notification_published", reason=reason, version=version)
        finally:
            await client.aclose()
    except Exception as e:
        logger.warning("settings_notification_failed", error=str(e), error_type=type(e).__name__)


async def notify_workers_of(sections: Iterable[str], reason: str | None = None) -> bool:
    """Publish a settings change when any of ``sections`` is one the workers must reload.

    Args:
        sections: The top-level ``settings.yaml`` sections a write changed.
        reason: The published tag; default ``v1:settings_updated:<sections>``.

    Returns:
        Whether a change was published (a section of :data:`WORKER_SETTINGS_SECTIONS` changed).
    """
    changed = sorted(set(sections) & WORKER_SETTINGS_SECTIONS)
    if not changed:
        return False
    await publish_settings_change(reason or f"v1:settings_updated:{'+'.join(changed)}")
    return True
