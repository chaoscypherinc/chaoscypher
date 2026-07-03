# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Publish worker hot-reload notifications over Valkey pub/sub.

Cortex mutates settings on disk; the Neuron worker keeps them live by listening
on ``chaoscypher:settings:changed`` and reloading. Any settings write that the
worker must react to (LLM/model changes, logging level, the active database)
publishes through :func:`publish_settings_change`.
"""

from __future__ import annotations

import valkey.asyncio as valkey

from chaoscypher_core.app_config import get_settings
from chaoscypher_core.utils.logging.app_config import get_logger


logger = get_logger(__name__)

_SETTINGS_VERSION_KEY = "chaoscypher:settings:version"
_SETTINGS_CHANNEL = "chaoscypher:settings:changed"


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
