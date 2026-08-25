# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for factory cache invalidation."""

from typing import Any, cast

from chaoscypher_core.plugins import PluginMetadata, create_registry_factory
from chaoscypher_core.plugins.factory import invalidate_all_caches
from chaoscypher_core.plugins.registry import BaseRegistry
from chaoscypher_core.services.presets.factory import (
    _registry_cache as preset_cache,
)
from chaoscypher_core.services.sources.engine.extraction.domains import (
    factory as domains_factory,
)
from chaoscypher_core.services.sources.loaders.factory import (
    _registry_cache as loader_cache,
)


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


def test_invalidate_all_caches_clears_registered_module_caches() -> None:
    """The three module-owned registry caches are wired into the central map.

    This is the /admin/plugins/reload backend: before the wiring existed the
    endpoint cleared a permanently-empty dict and every real registry cache
    survived (the reload was a silent no-op).
    """
    domain_cache = domains_factory._registry_cache
    loader_cache[id(object())] = cast("Any", object())
    preset_cache[id(object())] = cast("Any", object())
    domain_cache[("sentinel-dir", "default")] = cast("Any", object())
    try:
        counts = invalidate_all_caches()
    finally:
        loader_cache.clear()
        preset_cache.clear()
        domain_cache.clear()

    assert counts["LoaderRegistry"] >= 1
    assert counts["VRAMPresetRegistry"] >= 1
    assert counts["DomainRegistry"] >= 1
    assert not loader_cache
    assert not preset_cache
    assert not domain_cache


def test_clear_domain_registry_cache_clears_in_place() -> None:
    """clear_domain_registry_cache must mutate, never rebind, the module dict.

    A rebind would orphan the reference held by the plugins factory's
    central invalidation map, silently re-breaking /admin/plugins/reload.
    """
    before = domains_factory._registry_cache
    domains_factory._registry_cache[("sentinel-dir", "default")] = cast("Any", object())
    domains_factory.clear_domain_registry_cache()

    assert domains_factory._registry_cache is before
    assert not domains_factory._registry_cache
