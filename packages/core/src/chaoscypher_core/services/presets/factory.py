# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""VRAM Preset Registry Factory.

Provides cached access to the VRAMPresetRegistry.
Uses singleton pattern to avoid re-discovering presets on every call.

Example:
    from chaoscypher_core.services.presets import get_preset_registry

    registry = get_preset_registry(settings)
    presets = registry.list_presets()

    # Get and apply a preset
    preset = registry.get_required("vram_24gb")
    settings_to_apply = preset.get_all_settings()
"""

from __future__ import annotations

from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from chaoscypher_core.services.presets.registry import VRAMPresetRegistry
    from chaoscypher_core.settings import EngineSettings


# Cache for registry instances (keyed by settings_id)
_registry_cache: dict[int, VRAMPresetRegistry] = {}


def get_preset_registry(settings: EngineSettings | None = None) -> VRAMPresetRegistry:
    """Get cached preset registry.

    Uses singleton pattern - returns the same registry instance
    for the same settings object.

    Args:
        settings: Application settings. If None, uses default key.

    Returns:
        VRAMPresetRegistry instance with auto-discovered presets.
    """
    # Import here to avoid circular imports
    from chaoscypher_core.services.presets.registry import VRAMPresetRegistry

    settings_key = id(settings) if settings else 0

    if settings_key not in _registry_cache:
        _registry_cache[settings_key] = VRAMPresetRegistry(settings)

    return _registry_cache[settings_key]


__all__ = ["get_preset_registry"]
