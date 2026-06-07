# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the unified PluginMetadata Pydantic model."""

from chaoscypher_core.plugins.base import PluginMetadata


def test_plugin_metadata_defaults() -> None:
    """All fields except name default to sensible values; plugin_id derives from name."""
    md = PluginMetadata(name="test_plugin")
    assert md.name == "test_plugin"
    assert md.plugin_id == "test_plugin"
    assert md.version == "1.0.0"
    assert md.description == ""
    assert md.priority == 0
    assert md.applies_to is None
    assert md.origin == "builtin"


def test_plugin_metadata_explicit_fields() -> None:
    """Explicit field values are preserved."""
    md = PluginMetadata(
        plugin_id="sphinx_html",
        name="sphinx",
        version="1.2.3",
        description="Handles Sphinx HTML",
        priority=10,
    )
    assert md.plugin_id == "sphinx_html"
    assert md.priority == 10
    assert md.version == "1.2.3"


def test_plugin_metadata_applies_to_callable() -> None:
    """applies_to callable is accepted (arbitrary_types_allowed)."""
    md = PluginMetadata(
        name="filter_plugin",
        applies_to=lambda source: bool(source),
    )
    assert md.applies_to is not None
    assert md.applies_to("anything") is True
