# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""SettingsService.update_settings must sync the module-global singleton.

``HostHeaderCheckMiddleware`` (and any future hot-reloading middleware)
resolves its config via ``get_settings()`` per-request. ``get_settings`` is
``@lru_cache``'d on top of a module-global ``_settings``. ``ConfigManager``
updates only its own instance cache when ``update_settings`` is called —
without an explicit ``set_settings(new_settings)`` plumbed through, the
global singleton stays stale and the middleware never sees the toggle flip.

These tests pin the contract.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from chaoscypher_core.app_config import (
    ConfigManager,
    SecuritySettings,
    Settings,
    get_settings,
    set_settings,
)
from chaoscypher_cortex.features.settings.service import SettingsService


class _FakeLoggingService:
    """Stub — SettingsService requires a logging_service for construction."""


@pytest.fixture
def isolated_settings(tmp_path: Path) -> Path:
    """Yield a fresh settings.yaml in a tmpdir and reset the global cache."""
    yaml_path = tmp_path / "settings.yaml"
    set_settings(Settings())
    yield yaml_path
    set_settings(Settings())


def test_update_settings_propagates_to_global_get_settings(
    isolated_settings: Path,
) -> None:
    """PATCH-equivalent flow must invalidate the module-global cache.

    Without this, ``HostHeaderCheckMiddleware``'s
    ``settings_provider = lambda: get_settings().security``
    will continue to return the pre-PATCH value indefinitely.
    """
    manager = ConfigManager(settings_path=str(isolated_settings))
    # Re-anchor both caches to the same baseline so the assertion below
    # tests propagation, not just initial-load ordering.
    set_settings(manager.get_settings())

    service = SettingsService(
        settings_manager=manager,
        database_name="test",
        logging_service=_FakeLoggingService(),  # type: ignore[arg-type]
    )

    baseline = get_settings()
    assert baseline.security.allow_external_access is False

    asyncio.run(service.update_settings({"security": {"allow_external_access": True}}))

    after = get_settings()
    assert after.security.allow_external_access is True, (
        "SettingsService.update_settings did not invalidate the module-global "
        "_settings / get_settings cache. HostHeaderCheckMiddleware will serve "
        "stale allow-list policy until process restart."
    )


def test_set_settings_clears_lru_cache() -> None:
    """Smoke test for the set_settings contract used by the fix."""
    seeded = Settings()
    set_settings(seeded)
    first = get_settings()
    assert first is seeded

    replacement = Settings(security=SecuritySettings(allow_external_access=True))
    set_settings(replacement)
    second = get_settings()
    assert second is replacement
    assert second.security.allow_external_access is True
