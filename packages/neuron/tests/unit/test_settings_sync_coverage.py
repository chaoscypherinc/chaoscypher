# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Additional coverage for settings_sync.

Complements ``test_settings_sync.py``:
- ``_create_pubsub_client`` password branch (queue_password set).
- listener reconnect ``except`` + exponential backoff after a connect failure.
- pub/sub ``aclose`` cleanup failure is swallowed in the ``finally``.
- ``reload_llm_provider`` lock-acquire timeout path.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_neuron.settings_sync import (
    _create_pubsub_client,
    listen_for_settings_changes,
    reload_llm_provider,
)


# ============================================================================
# _create_pubsub_client — password branch
# ============================================================================


class TestCreatePubsubClient:
    """Tests for _create_pubsub_client password handling."""

    def test_password_is_unwrapped_from_secret(self) -> None:
        """When queue_password is set, its secret value is passed to Valkey."""
        settings = MagicMock()
        settings.queue.queue_host = "valkey-host"
        settings.queue.queue_port = 6380
        settings.queue.queue_database = 2
        settings.queue.queue_ssl = True
        secret = MagicMock()
        secret.get_secret_value.return_value = "s3cr3t"
        settings.queue.queue_password = secret

        with patch("chaoscypher_neuron.settings_sync.Valkey") as mock_valkey:
            _create_pubsub_client(settings)

        secret.get_secret_value.assert_called_once()
        kwargs = mock_valkey.call_args.kwargs
        assert kwargs["password"] == "s3cr3t"
        assert kwargs["host"] == "valkey-host"
        assert kwargs["port"] == 6380
        assert kwargs["socket_timeout"] is None

    def test_password_none_when_unset(self) -> None:
        """When queue_password is falsy, password is None (no get_secret_value)."""
        settings = MagicMock()
        settings.queue.queue_host = "h"
        settings.queue.queue_port = 6379
        settings.queue.queue_database = 0
        settings.queue.queue_ssl = False
        settings.queue.queue_password = None

        with patch("chaoscypher_neuron.settings_sync.Valkey") as mock_valkey:
            _create_pubsub_client(settings)

        assert mock_valkey.call_args.kwargs["password"] is None


# ============================================================================
# listen_for_settings_changes — reconnect + backoff
# ============================================================================


def _make_ctx() -> dict[str, Any]:
    """Minimal context with a truthy settings entry."""
    return {"settings": MagicMock()}


class TestListenerReconnect:
    """Tests for the reconnect / backoff path of the listener loop."""

    @pytest.mark.asyncio
    async def test_reconnects_and_backs_off_on_connect_failure(self) -> None:
        """A connect failure is caught, aclose cleanup runs, then backoff sleeps."""
        ctx = _make_ctx()

        # A pubsub client whose subscribe blows up — drives the except branch.
        bad_valkey = MagicMock()
        bad_pubsub = MagicMock()
        bad_pubsub.subscribe = AsyncMock(side_effect=RuntimeError("connect failed"))
        bad_valkey.pubsub.return_value = bad_pubsub
        # aclose itself fails — exercises the finally cleanup-failure branch.
        bad_valkey.aclose = AsyncMock(side_effect=RuntimeError("close failed"))

        sleep_calls: list[float] = []

        async def fake_sleep(delay: float) -> None:
            sleep_calls.append(delay)
            # Let the first backoff sleep happen, then break out of the loop.
            if len(sleep_calls) >= 2:
                raise asyncio.CancelledError

        with (
            patch("chaoscypher_neuron.settings_sync.queue_client") as mock_qc,
            patch(
                "chaoscypher_neuron.settings_sync._create_pubsub_client",
                return_value=bad_valkey,
            ),
            patch("chaoscypher_neuron.settings_sync.asyncio.sleep", side_effect=fake_sleep),
        ):
            mock_qc.client = MagicMock()
            with contextlib.suppress(asyncio.CancelledError):
                await listen_for_settings_changes(ctx)

        # The cleanup (failing aclose) was attempted at least once.
        assert bad_valkey.aclose.await_count >= 1
        # Backoff sleeps happened and the second delay is larger (exponential).
        assert len(sleep_calls) >= 2
        assert sleep_calls[1] >= sleep_calls[0]


# ============================================================================
# reload_llm_provider — lock acquire timeout
# ============================================================================


class TestReloadLockTimeout:
    """Tests for the reload-lock acquisition timeout path."""

    @pytest.mark.asyncio
    async def test_reload_returns_on_lock_timeout(self) -> None:
        """If the reload lock can't be acquired in time, reload returns early."""
        ctx = {"config_manager": MagicMock(), "settings": MagicMock()}

        # Pre-acquire the module lock so the contended acquire would block,
        # and force wait_for to raise TimeoutError to simulate the 30s expiry.
        from chaoscypher_neuron import settings_sync

        await settings_sync._reload_lock.acquire()
        try:
            with patch(
                "chaoscypher_neuron.settings_sync.asyncio.wait_for",
                new=AsyncMock(side_effect=TimeoutError),
            ):
                await reload_llm_provider(ctx)

            # Timed out before touching config_manager — no reload work happened.
            ctx["config_manager"].invalidate_cache.assert_not_called()
        finally:
            settings_sync._reload_lock.release()
