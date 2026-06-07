# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for entry-point plugin discovery in BaseRegistry."""

from unittest.mock import MagicMock, patch

from chaoscypher_core.plugins import BaseRegistry, PluginMetadata


class _FakePlugin:
    """Minimal plugin for testing."""

    def __init__(self, plugin_id: str) -> None:
        self._metadata = PluginMetadata(
            plugin_id=plugin_id,
            name=plugin_id,
            description="test",
        )

    @property
    def metadata(self) -> PluginMetadata:
        return self._metadata


class _TestRegistry(BaseRegistry["_FakePlugin"]):
    """Concrete registry for testing entry-point discovery."""

    def __init__(self, entry_point_group: str = "chaoscypher.plugins.test") -> None:
        self._entry_point_group = entry_point_group
        super().__init__()

    @property
    def plugin_entry_point_group(self) -> str | None:
        return self._entry_point_group

    def _discover(self) -> None:
        pass


class TestEntryPointDiscovery:
    """Test that BaseRegistry scans entry-point groups."""

    def test_no_entry_points_installed(self) -> None:
        """Registry works normally when no entry points exist."""
        with patch("chaoscypher_core.plugins.registry.entry_points", return_value=[]):
            registry = _TestRegistry()
        assert registry.count() == 0

    def test_entry_point_plugin_registered(self) -> None:
        """Plugins from entry points are registered."""
        fake_plugin = _FakePlugin("enterprise.provider")

        mock_ep = MagicMock()
        mock_ep.name = "enterprise.provider"
        mock_ep.load.return_value = lambda: fake_plugin

        with patch(
            "chaoscypher_core.plugins.registry.entry_points",
            return_value=[mock_ep],
        ):
            registry = _TestRegistry()

        assert registry.count() == 1
        assert registry.get("enterprise.provider") is fake_plugin

    def test_entry_point_failure_does_not_crash(self) -> None:
        """A broken entry point logs a warning and continues."""
        mock_ep = MagicMock()
        mock_ep.name = "broken"
        mock_ep.load.side_effect = ImportError("missing")

        with patch(
            "chaoscypher_core.plugins.registry.entry_points",
            return_value=[mock_ep],
        ):
            registry = _TestRegistry()

        assert registry.count() == 0

    def test_no_entry_point_group_skips_discovery(self) -> None:
        """Registries that return None for group skip entry-point scanning."""

        class _NoGroupRegistry(BaseRegistry["_FakePlugin"]):
            @property
            def plugin_entry_point_group(self) -> str | None:
                return None

            def _discover(self) -> None:
                pass

        registry = _NoGroupRegistry()
        assert registry.count() == 0
