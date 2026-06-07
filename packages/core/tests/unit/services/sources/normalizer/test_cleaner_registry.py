# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for CleanerRegistry discovery and selection."""

from __future__ import annotations

from pathlib import Path

from chaoscypher_core.services.sources.normalizer.cleaners.registry import (
    CleanerRegistry,
)
from chaoscypher_core.settings import EngineSettings, PathSettings


def _make_settings(data_dir: Path) -> EngineSettings:
    """Build an EngineSettings whose ``paths.data_dir`` points at ``data_dir``."""
    return EngineSettings(paths=PathSettings(data_dir=str(data_dir)))


def test_registry_discovers_built_in_cleaners(tmp_path: Path) -> None:
    """Registry discovers the three built-in cleaners."""
    registry = CleanerRegistry(settings=_make_settings(tmp_path))
    ids = set(registry.list_all().keys())
    assert {"ocr_cleaner", "text_cleaner", "web_cleaner"} <= ids


def test_registry_discovers_user_plugin(tmp_path: Path) -> None:
    """A cleaner dropped into data/plugins/cleaners/ is discovered."""
    user_plugin_dir = tmp_path / "plugins" / "cleaners"
    user_plugin_dir.mkdir(parents=True)
    plugin_file = user_plugin_dir / "custom_cleaner.py"
    plugin_file.write_text(
        "from chaoscypher_core.plugins.base import PluginMetadata\n"
        "class CustomCleaner:\n"
        "    metadata = PluginMetadata(name='custom_cleaner', version='0.1.0')\n"
        "    def clean(self, content, metadata=None):\n"
        "        return content, []\n"
    )

    registry = CleanerRegistry(settings=_make_settings(tmp_path))
    assert "custom_cleaner" in registry.list_all()


def test_registry_user_plugin_overrides_built_in(tmp_path: Path) -> None:
    """User plugin with same name as a built-in wins."""
    user_plugin_dir = tmp_path / "plugins" / "cleaners"
    user_plugin_dir.mkdir(parents=True)
    plugin_file = user_plugin_dir / "ocr_override.py"
    plugin_file.write_text(
        "from chaoscypher_core.plugins.base import PluginMetadata\n"
        "class OCRCleaner:\n"
        "    metadata = PluginMetadata(name='ocr_cleaner', version='override-1')\n"
        "    def clean(self, content, metadata=None):\n"
        "        return content, ['overridden']\n"
    )

    registry = CleanerRegistry(settings=_make_settings(tmp_path))
    ocr = registry.get("ocr_cleaner")
    assert ocr is not None
    assert ocr.metadata.version == "override-1"


def test_registry_lists_applicable_in_priority_order(tmp_path: Path) -> None:
    """list_applicable orders by metadata.priority descending, filters by applies_to."""
    registry = CleanerRegistry(settings=_make_settings(tmp_path))
    applicable = registry.list_applicable(source_metadata={"file_type": "txt"})
    priorities = [c.metadata.priority for c in applicable]
    assert priorities == sorted(priorities, reverse=True)
