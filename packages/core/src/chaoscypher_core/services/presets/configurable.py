# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Configurable VRAM Preset.

Preset implementation driven entirely by JSON configuration files.
No Python code needed for custom presets - just create a .json file.

The JSON format provides:
- Preset identification (name, display_name, description)
- VRAM tier info (vram_gb, gpu_examples)
- Ollama settings (model, context size, batch size)
- LLM behavior settings (max tokens, thinking mode)

Example JSON structure:
    {
        "name": "vram_24gb",
        "display_name": "24GB VRAM",
        "vram_gb": 24,
        "gpu_examples": ["RTX 4090", "RTX 3090"],
        "ollama_settings": {
            "ollama_chat_model": "qwen3:30b-instruct",
            "ollama_num_ctx": 32768
        },
        "llm_settings": {
            "ai_max_tokens": 16384,
            "thinking_for_chat": false
        }
    }
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.plugins import PluginMetadata, metadata_from_dict


if TYPE_CHECKING:
    from pathlib import Path


logger = structlog.get_logger(__name__)


class ConfigurableVRAMPreset:
    """VRAM preset driven entirely by JSON configuration.

    Implements the VRAMPreset protocol by reading all behavior from
    a JSON config file. This enables users to create custom presets
    without writing Python code.

    Attributes:
        config: The loaded JSON configuration.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        """Initialize preset from configuration.

        Args:
            config: Parsed JSON configuration dictionary.
        """
        self.config = config

    @property
    def name(self) -> str:
        """Unique preset identifier.

        Returns:
            Preset name from config.
        """
        name_value: str = self.config.get("name", "unknown")
        return name_value

    @property
    def display_name(self) -> str:
        """Human-readable display name.

        Returns:
            Display name from config, or formatted name.
        """
        display: str = self.config.get("display_name", self.name.replace("_", " ").title())
        return display

    @property
    def description(self) -> str:
        """Detailed preset description.

        Returns:
            Description from config, or empty string.
        """
        desc: str = self.config.get("description", "")
        return desc

    @property
    def vram_gb(self) -> int:
        """Target VRAM size in gigabytes.

        Returns:
            VRAM size from config.
        """
        vram: int = self.config.get("vram_gb", 0)
        return vram

    @property
    def gpu_examples(self) -> list[str]:
        """Example GPUs in this VRAM tier.

        Returns:
            GPU examples from config.
        """
        examples: list[str] = self.config.get("gpu_examples", [])
        return examples

    @property
    def metadata(self) -> PluginMetadata:
        """Get plugin metadata from JSON config.

        Returns:
            PluginMetadata instance generated from config.
        """
        return metadata_from_dict(
            {
                "plugin_id": self.name,
                "name": self.display_name,
                "description": self.description,
                "version": self.config.get("version", "1.0.0"),
                "author": self.config.get("author", ""),
                "category": "preset",
                "builtin": self.config.get("builtin", False),
            }
        )

    def get_ollama_settings(self) -> dict[str, Any]:
        """Get Ollama-specific settings.

        Returns:
            Dict with ollama_* settings from config.
        """
        settings: dict[str, Any] = self.config.get("ollama_settings", {})
        return settings

    def get_llm_settings(self) -> dict[str, Any]:
        """Get general LLM settings.

        Returns:
            Dict with ai_* settings from config.
        """
        llm_settings: dict[str, Any] = self.config.get("llm_settings", {})
        return llm_settings

    def get_all_settings(self) -> dict[str, Any]:
        """Get all settings merged for application.

        Merges ollama_settings and llm_settings into a single dict
        ready to be applied to the LLM configuration.

        Returns:
            Merged dict of ollama_settings + llm_settings.
        """
        return {
            **self.get_ollama_settings(),
            **self.get_llm_settings(),
        }

    def to_dict(self) -> dict[str, Any]:
        """Convert preset to dictionary representation.

        Returns:
            Dict with all preset data for API responses.
        """
        return {
            "name": self.name,
            "display_name": self.display_name,
            "description": self.description,
            "vram_gb": self.vram_gb,
            "gpu_examples": self.gpu_examples,
            "version": self.config.get("version", "1.0.0"),
            "author": self.config.get("author", ""),
            "builtin": self.config.get("builtin", False),
            "ollama_settings": self.get_ollama_settings(),
            "llm_settings": self.get_llm_settings(),
        }


def load_preset_config(path: Path) -> dict[str, Any]:
    """Load preset configuration from JSON file.

    Args:
        path: Path to preset .json file.

    Returns:
        Parsed configuration dictionary.

    Raises:
        FileNotFoundError: If config file doesn't exist.
        json.JSONDecodeError: If config file is invalid JSON.
    """
    import json

    content = path.read_text(encoding="utf-8")
    config: dict[str, Any] = json.loads(content)
    return config


__all__ = ["ConfigurableVRAMPreset", "load_preset_config"]
