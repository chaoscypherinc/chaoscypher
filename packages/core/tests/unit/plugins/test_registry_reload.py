# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for BaseRegistry.reload()."""

from chaoscypher_core.plugins import PluginMetadata
from chaoscypher_core.plugins.registry import BaseRegistry


class _FakePlugin:
    def __init__(self, pid: str) -> None:
        self._metadata = PluginMetadata(plugin_id=pid, name=pid, description="")

    @property
    def metadata(self) -> PluginMetadata:
        return self._metadata


class _GrowingRegistry(BaseRegistry["_FakePlugin"]):
    def __init__(self) -> None:
        self._seed = ["a"]
        super().__init__()

    def _discover(self) -> None:
        for pid in self._seed:
            self._register(_FakePlugin(pid))


def test_reload_rediscovers_plugins() -> None:
    r = _GrowingRegistry()
    assert set(r.list_all().keys()) == {"a"}

    # Simulate new plugin appearing on disk.
    r._seed = ["a", "b"]
    r.reload()

    assert set(r.list_all().keys()) == {"a", "b"}


def test_reload_drops_plugins_no_longer_discovered() -> None:
    r = _GrowingRegistry()
    r._seed = ["b"]
    r.reload()
    assert set(r.list_all().keys()) == {"b"}
