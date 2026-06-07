# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""VRAM Preset Plugin System.

Provides extensible VRAM-based LLM configuration presets.
Presets are JSON files that can be extended by users dropping files
in the data/plugins/presets/ directory.

Plugin Types:
    - Builtin presets: Shipped with package in services/presets/plugins/
    - User presets: Custom presets in data/plugins/presets/

Example:
    from chaoscypher_core.services.presets import get_preset_registry

    registry = get_preset_registry(settings)
    presets = registry.list_presets()

    # Apply a preset
    preset = registry.get_required("vram_24gb")
    settings_to_apply = preset.get_all_settings()
"""

from chaoscypher_core.services.presets.base import VRAMPreset
from chaoscypher_core.services.presets.configurable import (
    ConfigurableVRAMPreset,
    load_preset_config,
)
from chaoscypher_core.services.presets.factory import (
    get_preset_registry,
)
from chaoscypher_core.services.presets.registry import VRAMPresetRegistry


__all__ = [
    "ConfigurableVRAMPreset",
    "VRAMPreset",
    "VRAMPresetRegistry",
    "get_preset_registry",
    "load_preset_config",
]
