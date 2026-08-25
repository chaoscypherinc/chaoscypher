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

from chaoscypher_core.plugins.factory import register_registry_cache


if TYPE_CHECKING:
    from chaoscypher_core.services.presets.registry import VRAMPresetRegistry
    from chaoscypher_core.settings import EngineSettings


# Cache for registry instances, keyed by the resolved user-plugin root.
# Using a value-based key — not ``id(settings)`` — so callers that build a
# fresh EngineSettings per request (e.g. the Cortex preset endpoints via
# ``build_engine_settings``) still hit the cache instead of re-running
# preset discovery and leaking one dead entry per settings object (same
# fix as the domain registry factory). Registered with the plugins factory
# so invalidate_all_caches (the /admin/plugins/reload backend) clears it;
# mutate in place, never rebind.
_registry_cache: dict[str, VRAMPresetRegistry] = register_registry_cache("VRAMPresetRegistry", {})


def _cache_key(settings: EngineSettings | None) -> str:
    """Build the registry cache key.

    The only settings field discovery depends on is the user-plugin root,
    which ``VRAMPresetRegistry._get_user_plugins_path`` resolves from a
    flat ``data_dir`` attribute falling back to ``paths.data_dir`` —
    mirror that resolution order here.
    """
    if settings is None:
        return ""
    data_dir = getattr(settings, "data_dir", None)
    if data_dir is None:
        data_dir = getattr(getattr(settings, "paths", None), "data_dir", None)
    return "" if data_dir is None else str(data_dir)


def get_preset_registry(settings: EngineSettings | None = None) -> VRAMPresetRegistry:
    """Get cached preset registry.

    Uses singleton pattern - returns the same registry instance
    for the same resolved user-plugin root (``data_dir``).

    Args:
        settings: Application settings. If None, uses default key.

    Returns:
        VRAMPresetRegistry instance with auto-discovered presets.
    """
    # Import here to avoid circular imports
    from chaoscypher_core.services.presets.registry import VRAMPresetRegistry

    settings_key = _cache_key(settings)

    if settings_key not in _registry_cache:
        _registry_cache[settings_key] = VRAMPresetRegistry(settings)

    return _registry_cache[settings_key]


def clear_preset_registry_cache() -> None:
    """Clear the registry cache.

    Useful for testing or when presets are added/removed at runtime.
    Mutates in place — the dict is registered with the plugins factory,
    so rebinding would orphan the /admin/plugins/reload wiring.
    """
    _registry_cache.clear()


__all__ = ["clear_preset_registry_cache", "get_preset_registry"]
