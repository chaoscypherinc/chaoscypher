# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""VRAM Preset Protocol.

Defines the interface that all VRAM presets implement.
Presets provide pre-configured Ollama LLM settings optimized
for specific GPU VRAM sizes.

Example:
    from chaoscypher_core.services.presets import VRAMPreset

    class MyPreset:
        @property
        def name(self) -> str:
            return "my_preset"

        def get_all_settings(self) -> dict[str, Any]:
            return {
                "ollama_chat_model": "qwen3:8b-instruct",
                "ollama_num_ctx": 4096,
            }
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable


if TYPE_CHECKING:
    from chaoscypher_core.plugins import PluginMetadata


@runtime_checkable
class VRAMPreset(Protocol):
    """Protocol for VRAM-based LLM configuration presets.

    Presets provide pre-configured settings optimized for specific
    GPU VRAM sizes. Users can select a preset matching their hardware
    to automatically configure optimal LLM settings.

    Attributes:
        name: Unique preset identifier (e.g., "vram_24gb").
        display_name: Human-readable name (e.g., "24GB VRAM").
        description: Detailed description of this preset.
        vram_gb: Target VRAM size in gigabytes.
        gpu_examples: List of example GPUs in this tier.
        metadata: Plugin metadata for registry integration.
    """

    @property
    def name(self) -> str:
        """Unique preset identifier.

        Returns:
            Preset name (e.g., "vram_24gb").
        """
        ...

    @property
    def display_name(self) -> str:
        """Human-readable display name.

        Returns:
            Display name (e.g., "24GB VRAM").
        """
        ...

    @property
    def description(self) -> str:
        """Detailed preset description.

        Returns:
            Description of what this preset is optimized for.
        """
        ...

    @property
    def vram_gb(self) -> int:
        """Target VRAM size in gigabytes.

        Returns:
            VRAM size (e.g., 24 for RTX 4090).
        """
        ...

    @property
    def gpu_examples(self) -> list[str]:
        """Example GPUs in this VRAM tier.

        Returns:
            List of GPU names (e.g., ["RTX 4090", "RTX 3090"]).
        """
        ...

    @property
    def metadata(self) -> PluginMetadata:
        """Plugin metadata for registry integration.

        Returns:
            PluginMetadata instance.
        """
        ...

    def get_ollama_settings(self) -> dict[str, Any]:
        """Get Ollama-specific settings.

        Returns:
            Dict with ollama_* settings (model, context, batch, etc.).
        """
        ...

    def get_llm_settings(self) -> dict[str, Any]:
        """Get general LLM settings.

        Returns:
            Dict with ai_* settings (max_tokens, chunk_max_tokens, etc.).
        """
        ...

    def get_all_settings(self) -> dict[str, Any]:
        """Get all settings merged for application.

        Returns:
            Merged dict of ollama_settings + llm_settings.
        """
        ...


__all__ = ["VRAMPreset"]
