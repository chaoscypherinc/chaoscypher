# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""VRAM Preset Registry with Auto-Discovery.

Auto-discovers VRAM presets from:
1. plugins/ directory - Built-in presets (shipped with package)
2. data/plugins/presets/ - User custom presets

Presets are configured via JSON files (*.json).
No Python code is required for custom presets.

Example:
    from chaoscypher_core.services.presets import get_preset_registry

    registry = get_preset_registry(settings)
    presets = registry.list_presets()

    # Get specific preset
    preset = registry.get_required("vram_24gb")
    settings_to_apply = preset.get_all_settings()
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.plugins import BaseRegistry, PluginMetadata
from chaoscypher_core.services.presets.configurable import ConfigurableVRAMPreset


if TYPE_CHECKING:
    from chaoscypher_core.services.presets.base import VRAMPreset
    from chaoscypher_core.settings import EngineSettings


logger = structlog.get_logger(__name__)


class VRAMPresetRegistry(BaseRegistry["VRAMPreset"]):
    """Registry for VRAM presets with auto-discovery.

    Extends BaseRegistry to provide standardized plugin management.
    Scans two locations for preset JSON files:
    1. plugins/ - Built-in presets (shipped with package)
    2. data/plugins/presets/ - User custom presets

    Attributes:
        settings: Application settings.
        _configs: Cached preset configurations.
    """

    def __init__(self, settings: EngineSettings | None = None) -> None:
        """Initialize preset registry with auto-discovery.

        Args:
            settings: Application settings (optional).
        """
        # Preset-specific state
        self._configs: dict[str, dict[str, Any]] = {}  # name → config

        # Call parent init (triggers _discover)
        super().__init__(settings=settings)

    def _discover(self) -> None:
        """Auto-discover presets from built-in and user plugin directories.

        Scans two locations:
        1. plugins/ - Built-in presets (shipped with package)
        2. data/plugins/presets/ - User custom presets

        Registers presets by loading *.json config files.
        """
        presets_dir = Path(__file__).parent

        # Build search paths
        search_paths: list[tuple[str, Path]] = [
            ("builtin", presets_dir / "plugins"),
        ]

        # Add user plugins path if settings available
        user_plugins_path = self._get_user_plugins_path()
        if user_plugins_path:
            search_paths.append(("user", user_plugins_path))

        logger.info(
            "preset_discovery_started",
            presets_directory=str(presets_dir),
        )

        for path_type, search_dir in search_paths:
            if not search_dir.exists():
                logger.debug(
                    "preset_search_path_missing",
                    path_type=path_type,
                    path=str(search_dir),
                )
                continue

            self._scan_directory(search_dir, path_type)

        logger.info(
            "preset_discovery_complete",
            presets_found=list(self._plugins.keys()),
            total_count=len(self._plugins),
        )

    def _get_user_plugins_path(self) -> Path | None:
        """Get user plugins presets path from settings.

        Returns:
            Path to data/plugins/presets/ directory, or None.
        """
        if self.settings is None:
            return None

        # Try to get data_dir from settings
        data_dir = getattr(self.settings, "data_dir", None)
        if data_dir is None:
            # Try paths.data_dir pattern
            paths = getattr(self.settings, "paths", None)
            if paths:
                data_dir = getattr(paths, "data_dir", None)

        if data_dir is None:
            return None

        return Path(data_dir) / "plugins" / "presets"

    def _scan_directory(self, search_dir: Path, path_type: str) -> None:
        """Scan a directory for preset JSON files.

        Args:
            search_dir: Directory to scan.
            path_type: Type identifier (builtin or user).
        """
        if not search_dir.exists():
            return

        for item in search_dir.iterdir():
            # Only process .json files
            if item.is_file() and item.suffix == ".json":
                try:
                    self._register_preset_from_config(item, path_type)
                except Exception as e:
                    logger.warning(
                        "preset_registration_failed",
                        file=item.name,
                        error=str(e),
                        exc_info=True,
                    )

    def _register_preset_from_config(
        self,
        config_path: Path,
        path_type: str,
    ) -> None:
        """Register a preset from its JSON config file.

        Args:
            config_path: Path to preset .json file.
            path_type: Type identifier (builtin or user).
        """
        # Load JSON config
        content = config_path.read_text(encoding="utf-8")
        config = json.loads(content)

        # Create ConfigurableVRAMPreset instance
        instance = ConfigurableVRAMPreset(config)

        # Register using parent's _register_by_id
        self._register_by_id(instance.name, instance)
        self._configs[instance.name] = config

        logger.info(
            "preset_registered",
            preset=instance.name,
            vram_gb=instance.vram_gb,
            version=config.get("version", "unknown"),
            builtin=config.get("builtin", False),
            path_type=path_type,
        )

    def _get_plugin_id(self, plugin: VRAMPreset) -> str:
        """Extract plugin ID from a preset instance.

        Args:
            plugin: Preset instance.

        Returns:
            Preset name.
        """
        return plugin.name

    def _get_plugin_metadata(self, plugin: VRAMPreset) -> PluginMetadata:
        """Extract metadata from a preset instance.

        Args:
            plugin: Preset instance.

        Returns:
            PluginMetadata for the preset.
        """
        # Try to get metadata from plugin
        if hasattr(plugin, "metadata"):
            try:
                return plugin.metadata
            except (AttributeError, NotImplementedError):  # fmt: skip
                pass

        # Generate metadata from preset info
        config = self._configs.get(plugin.name, {})
        return PluginMetadata(
            plugin_id=plugin.name,
            name=plugin.display_name,
            description=plugin.description,
            version=config.get("version", "1.0.0"),
            author=config.get("author", ""),
            category="preset",
            builtin=config.get("builtin", False),
        )

    def list_presets(self) -> list[dict[str, Any]]:
        """List all registered presets sorted by VRAM size.

        Returns:
            List of preset dictionaries sorted by vram_gb.
        """
        result = []
        for name, preset in self._plugins.items():
            if hasattr(preset, "to_dict"):
                result.append(preset.to_dict())
            else:
                # Fallback for non-configurable presets
                config = self._configs.get(name, {})
                result.append(
                    {
                        "name": name,
                        "display_name": preset.display_name,
                        "description": preset.description,
                        "vram_gb": preset.vram_gb,
                        "gpu_examples": preset.gpu_examples,
                        "version": config.get("version", "unknown"),
                        "builtin": config.get("builtin", False),
                    }
                )

        # Sort by VRAM size ascending
        return sorted(result, key=lambda x: x.get("vram_gb", 0))

    def get_preset(self, name: str) -> VRAMPreset | None:
        """Get a specific preset by name.

        Args:
            name: Preset identifier.

        Returns:
            Preset instance or None if not found.
        """
        return self.get(name)


__all__ = ["VRAMPresetRegistry"]
