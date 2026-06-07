# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for QueueRecoverySettings defaults and overrides."""

from chaoscypher_core.app_config import QueueRecoverySettings, Settings


def test_queue_recovery_defaults() -> None:
    """Default values match the spec."""
    s = QueueRecoverySettings()
    assert s.heartbeat_ttl_seconds == 30
    assert s.heartbeat_refresh_interval_seconds == 10
    assert s.worker_reconcile_interval_seconds == 30
    assert s.cortex_reconcile_interval_seconds == 150


def test_queue_recovery_overrides_applied() -> None:
    """Environment-style overrides are accepted."""
    s = QueueRecoverySettings(
        heartbeat_ttl_seconds=60,
        heartbeat_refresh_interval_seconds=20,
        worker_reconcile_interval_seconds=90,
        cortex_reconcile_interval_seconds=600,
    )
    assert s.heartbeat_ttl_seconds == 60
    assert s.worker_reconcile_interval_seconds == 90


def test_settings_has_queue_recovery_field() -> None:
    """Top-level Settings exposes queue_recovery with correct type."""
    settings = Settings()
    assert isinstance(settings.queue_recovery, QueueRecoverySettings)
    assert settings.queue_recovery.heartbeat_ttl_seconds == 30


def test_refresh_interval_must_be_less_than_half_ttl() -> None:
    """Config sanity check: refresh must fire at least twice per TTL."""
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        QueueRecoverySettings(heartbeat_ttl_seconds=10, heartbeat_refresh_interval_seconds=10)
