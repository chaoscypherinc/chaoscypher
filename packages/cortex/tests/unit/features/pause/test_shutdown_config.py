# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for ShutdownSettings config group."""

from chaoscypher_core.app_config import Settings, ShutdownSettings


def test_defaults() -> None:
    s = ShutdownSettings()
    assert s.worker_shutdown_grace_seconds == 30
    assert s.cortex_shutdown_grace_seconds == 30


def test_settings_has_shutdown_field() -> None:
    settings = Settings()
    assert isinstance(settings.shutdown, ShutdownSettings)
