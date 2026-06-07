# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for factory cache invalidation."""

from chaoscypher_core.plugins import PluginMetadata, create_registry_factory
from chaoscypher_core.plugins.registry import BaseRegistry


class _FakePlugin:
    _metadata = PluginMetadata(plugin_id="fake", name="Fake", description="")

    @property
    def metadata(self) -> PluginMetadata:
        return self._metadata


class _FakeRegistry(BaseRegistry["_FakePlugin"]):
    created_count = 0

    def __init__(self, settings=None, database_name: str = "default") -> None:
        type(self).created_count += 1
        super().__init__(settings=settings, database_name=database_name)

    def _discover(self) -> None:
        pass


def test_invalidate_cache_forces_new_instance() -> None:
    _FakeRegistry.created_count = 0
    factory = create_registry_factory(_FakeRegistry)

    r1 = factory()
    r2 = factory()
    assert r1 is r2
    assert _FakeRegistry.created_count == 1

    factory.invalidate_cache()

    r3 = factory()
    assert r3 is not r1
    assert _FakeRegistry.created_count == 2


def test_invalidate_cache_scoped_to_settings() -> None:
    _FakeRegistry.created_count = 0
    factory = create_registry_factory(_FakeRegistry)

    s1 = object()
    s2 = object()
    r1a = factory(s1)
    r2a = factory(s2)
    assert _FakeRegistry.created_count == 2

    factory.invalidate_cache(settings=s1)

    r1b = factory(s1)
    r2b = factory(s2)

    assert r1b is not r1a  # s1 entry cleared
    assert r2b is r2a  # s2 entry untouched
    assert _FakeRegistry.created_count == 3
