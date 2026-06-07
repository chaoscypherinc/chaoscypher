# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Coverage tests for the VRAM preset registry and configurable preset.

Targets:
- chaoscypher_core.services.presets.configurable (ConfigurableVRAMPreset,
  load_preset_config)
- chaoscypher_core.services.presets.registry (VRAMPresetRegistry)
- chaoscypher_core.services.presets.base (VRAMPreset protocol)

These are pure config-driven classes; tests instantiate them directly and
exercise lookups, resolution, registration, JSON load and error branches.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from chaoscypher_core.services.presets.base import VRAMPreset
from chaoscypher_core.services.presets.configurable import (
    ConfigurableVRAMPreset,
    load_preset_config,
)
from chaoscypher_core.services.presets.registry import VRAMPresetRegistry


# ----------------------------------------------------------------------
# ConfigurableVRAMPreset
# ----------------------------------------------------------------------
def _full_config() -> dict[str, Any]:
    return {
        "name": "vram_test",
        "display_name": "Test VRAM",
        "description": "A test preset",
        "version": "2.1.0",
        "author": "Tester",
        "builtin": False,
        "vram_gb": 12,
        "gpu_examples": ["RTX 3060", "RTX 4070"],
        "ollama_settings": {"ollama_chat_model": "qwen3:8b", "ollama_num_ctx": 4096},
        "llm_settings": {"ai_max_tokens": 8192, "thinking_for_chat": False},
    }


class TestConfigurableVRAMPresetFullConfig:
    """A fully-populated config maps cleanly onto every accessor."""

    def test_scalar_properties(self) -> None:
        preset = ConfigurableVRAMPreset(_full_config())
        assert preset.name == "vram_test"
        assert preset.display_name == "Test VRAM"
        assert preset.description == "A test preset"
        assert preset.vram_gb == 12
        assert preset.gpu_examples == ["RTX 3060", "RTX 4070"]

    def test_settings_accessors(self) -> None:
        preset = ConfigurableVRAMPreset(_full_config())
        assert preset.get_ollama_settings() == {
            "ollama_chat_model": "qwen3:8b",
            "ollama_num_ctx": 4096,
        }
        assert preset.get_llm_settings() == {
            "ai_max_tokens": 8192,
            "thinking_for_chat": False,
        }

    def test_get_all_settings_merges(self) -> None:
        preset = ConfigurableVRAMPreset(_full_config())
        merged = preset.get_all_settings()
        # Contains keys from both sub-dicts.
        assert merged["ollama_chat_model"] == "qwen3:8b"
        assert merged["ai_max_tokens"] == 8192
        assert set(merged) == {
            "ollama_chat_model",
            "ollama_num_ctx",
            "ai_max_tokens",
            "thinking_for_chat",
        }

    def test_metadata_built_from_config(self) -> None:
        preset = ConfigurableVRAMPreset(_full_config())
        md = preset.metadata
        # metadata_from_dict derives plugin_id from the "name" key, which the
        # preset populates with the display_name.
        assert md.plugin_id == "Test VRAM"
        assert md.name == "Test VRAM"
        assert md.version == "2.1.0"
        assert md.author == "Tester"
        assert md.category == "preset"

    def test_to_dict_round_trip(self) -> None:
        preset = ConfigurableVRAMPreset(_full_config())
        result = preset.to_dict()
        assert result["name"] == "vram_test"
        assert result["vram_gb"] == 12
        assert result["author"] == "Tester"
        assert result["builtin"] is False
        assert result["ollama_settings"]["ollama_num_ctx"] == 4096
        assert result["llm_settings"]["ai_max_tokens"] == 8192

    def test_satisfies_protocol(self) -> None:
        """ConfigurableVRAMPreset is a runtime VRAMPreset."""
        preset = ConfigurableVRAMPreset(_full_config())
        assert isinstance(preset, VRAMPreset)


class TestConfigurableVRAMPresetDefaults:
    """An empty/minimal config falls back to sensible defaults."""

    def test_name_defaults_to_unknown(self) -> None:
        preset = ConfigurableVRAMPreset({})
        assert preset.name == "unknown"

    def test_display_name_derived_from_name(self) -> None:
        # No display_name key -> name.replace("_", " ").title()
        preset = ConfigurableVRAMPreset({"name": "vram_big_gpu"})
        assert preset.display_name == "Vram Big Gpu"

    def test_empty_defaults(self) -> None:
        preset = ConfigurableVRAMPreset({})
        assert preset.description == ""
        assert preset.vram_gb == 0
        assert preset.gpu_examples == []
        assert preset.get_ollama_settings() == {}
        assert preset.get_llm_settings() == {}
        assert preset.get_all_settings() == {}

    def test_metadata_defaults(self) -> None:
        preset = ConfigurableVRAMPreset({"name": "x"})
        md = preset.metadata
        assert md.version == "1.0.0"
        assert md.author == ""
        # config.get("builtin", False) defaults to False when the key is absent.
        assert md.builtin is False

    def test_to_dict_defaults(self) -> None:
        preset = ConfigurableVRAMPreset({"name": "x"})
        result = preset.to_dict()
        assert result["version"] == "1.0.0"
        assert result["author"] == ""
        assert result["builtin"] is False


# ----------------------------------------------------------------------
# load_preset_config
# ----------------------------------------------------------------------
class TestLoadPresetConfig:
    """load_preset_config reads and parses JSON files."""

    def test_loads_valid_json(self, tmp_path: Any) -> None:
        cfg = {"name": "vram_loaded", "vram_gb": 8}
        path = tmp_path / "preset.json"
        path.write_text(json.dumps(cfg), encoding="utf-8")
        loaded = load_preset_config(path)
        assert loaded == cfg

    def test_invalid_json_raises(self, tmp_path: Any) -> None:
        path = tmp_path / "bad.json"
        path.write_text("{not valid json", encoding="utf-8")
        with pytest.raises(json.JSONDecodeError):
            load_preset_config(path)

    def test_missing_file_raises(self, tmp_path: Any) -> None:
        with pytest.raises(FileNotFoundError):
            load_preset_config(tmp_path / "nope.json")


# ----------------------------------------------------------------------
# VRAMPresetRegistry
# ----------------------------------------------------------------------
class TestVRAMPresetRegistryBuiltinDiscovery:
    """Default registry auto-discovers the shipped built-in presets."""

    def test_discovers_builtin_presets(self) -> None:
        registry = VRAMPresetRegistry()
        # The package ships several vram_*.json built-in presets.
        assert registry.count() > 0
        # A known built-in id is present.
        assert registry.get("vram_24gb") is not None

    def test_get_preset_returns_instance(self) -> None:
        registry = VRAMPresetRegistry()
        preset = registry.get_preset("vram_24gb")
        assert preset is not None
        assert preset.name == "vram_24gb"
        assert preset.vram_gb == 24

    def test_get_preset_missing_returns_none(self) -> None:
        registry = VRAMPresetRegistry()
        assert registry.get_preset("does_not_exist") is None

    def test_get_required_raises_for_missing(self) -> None:
        registry = VRAMPresetRegistry()
        with pytest.raises(KeyError):
            registry.get_required("does_not_exist")

    def test_list_presets_sorted_by_vram(self) -> None:
        registry = VRAMPresetRegistry()
        listed = registry.list_presets()
        assert len(listed) == registry.count()
        vrams = [p["vram_gb"] for p in listed]
        assert vrams == sorted(vrams)
        # Each entry is a to_dict() shape.
        assert all("ollama_settings" in p for p in listed)

    def test_metadata_for_builtin_preset(self) -> None:
        registry = VRAMPresetRegistry()
        preset = registry.get_required("vram_24gb")
        md = registry._get_plugin_metadata(preset)
        # ConfigurableVRAMPreset exposes .metadata, so the registry returns it
        # directly. Its plugin_id derives from the display_name.
        assert md.category == "preset"
        assert md.name == "24GB VRAM"

    def test_get_plugin_id(self) -> None:
        registry = VRAMPresetRegistry()
        preset = registry.get_required("vram_24gb")
        assert registry._get_plugin_id(preset) == "vram_24gb"


class TestVRAMPresetRegistryUserPlugins:
    """User plugin directory discovery via settings.data_dir."""

    def test_user_plugins_path_from_data_dir(self, tmp_path: Any) -> None:
        settings = SimpleNamespace(data_dir=str(tmp_path))
        registry = VRAMPresetRegistry(settings=settings)  # type: ignore[arg-type]
        expected = tmp_path / "plugins" / "presets"
        assert registry._get_user_plugins_path() == expected

    def test_user_plugins_path_from_paths_attr(self, tmp_path: Any) -> None:
        settings = SimpleNamespace(data_dir=None, paths=SimpleNamespace(data_dir=str(tmp_path)))
        registry = VRAMPresetRegistry(settings=settings)  # type: ignore[arg-type]
        assert registry._get_user_plugins_path() == tmp_path / "plugins" / "presets"

    def test_user_plugins_path_none_without_settings(self) -> None:
        registry = VRAMPresetRegistry()
        assert registry._get_user_plugins_path() is None

    def test_user_plugins_path_none_when_no_data_dir(self) -> None:
        settings = SimpleNamespace(data_dir=None, paths=None)
        registry = VRAMPresetRegistry(settings=settings)  # type: ignore[arg-type]
        assert registry._get_user_plugins_path() is None

    def test_user_preset_discovered_and_overrides(self, tmp_path: Any) -> None:
        """A user preset JSON in data/plugins/presets/ is registered."""
        presets_dir = tmp_path / "plugins" / "presets"
        presets_dir.mkdir(parents=True)
        user_cfg = {
            "name": "vram_user_custom",
            "display_name": "Custom",
            "vram_gb": 6,
            "gpu_examples": ["GTX 1660"],
            "ollama_settings": {"ollama_chat_model": "qwen3:4b"},
            "llm_settings": {},
        }
        (presets_dir / "custom.json").write_text(json.dumps(user_cfg), encoding="utf-8")

        settings = SimpleNamespace(data_dir=str(tmp_path))
        registry = VRAMPresetRegistry(settings=settings)  # type: ignore[arg-type]

        preset = registry.get("vram_user_custom")
        assert preset is not None
        assert preset.vram_gb == 6
        # Built-ins still present alongside the user preset.
        assert registry.get("vram_24gb") is not None

    def test_bad_user_json_is_skipped_not_fatal(self, tmp_path: Any) -> None:
        """A malformed user JSON logs a warning but does not break discovery."""
        presets_dir = tmp_path / "plugins" / "presets"
        presets_dir.mkdir(parents=True)
        (presets_dir / "broken.json").write_text("{not json", encoding="utf-8")

        settings = SimpleNamespace(data_dir=str(tmp_path))
        # Should not raise despite the broken file.
        registry = VRAMPresetRegistry(settings=settings)  # type: ignore[arg-type]
        assert registry.get("vram_24gb") is not None  # builtins still loaded

    def test_non_json_files_ignored(self, tmp_path: Any) -> None:
        presets_dir = tmp_path / "plugins" / "presets"
        presets_dir.mkdir(parents=True)
        (presets_dir / "README.txt").write_text("not a preset", encoding="utf-8")
        (presets_dir / "subdir").mkdir()  # directories ignored

        settings = SimpleNamespace(data_dir=str(tmp_path))
        registry = VRAMPresetRegistry(settings=settings)  # type: ignore[arg-type]
        # No crash; only builtins registered (no extra user presets).
        count_with_empty_dir = registry.count()

        registry_builtin_only = VRAMPresetRegistry()
        assert count_with_empty_dir == registry_builtin_only.count()


class _FakePreset:
    """A non-Configurable preset lacking .metadata and .to_dict for fallbacks."""

    name = "fake_preset"
    display_name = "Fake Preset"
    description = "fallback test"
    vram_gb = 99
    gpu_examples = ["FakeGPU"]


class TestVRAMPresetRegistryFallbackBranches:
    """Cover the non-Configurable fallback paths in metadata/list_presets."""

    def test_metadata_fallback_generated(self) -> None:
        registry = VRAMPresetRegistry()
        fake = _FakePreset()
        # No .metadata attr -> metadata generated from preset info.
        md = registry._get_plugin_metadata(fake)  # type: ignore[arg-type]
        assert md.plugin_id == "fake_preset"
        assert md.name == "Fake Preset"
        assert md.category == "preset"

    def test_list_presets_fallback_for_non_configurable(self) -> None:
        registry = VRAMPresetRegistry()
        fake = _FakePreset()
        registry._plugins["fake_preset"] = fake  # type: ignore[assignment]
        registry._configs["fake_preset"] = {"version": "9.9.9", "builtin": False}

        listed = registry.list_presets()
        fake_entry = next(p for p in listed if p["name"] == "fake_preset")
        assert fake_entry["display_name"] == "Fake Preset"
        assert fake_entry["vram_gb"] == 99
        assert fake_entry["version"] == "9.9.9"
        assert fake_entry["builtin"] is False


class TestVRAMPresetRegistryReload:
    """reload() re-runs discovery on the same instance."""

    def test_reload_repopulates(self) -> None:
        registry = VRAMPresetRegistry()
        before = registry.count()
        registry.reload()
        assert registry.count() == before
        assert registry.get("vram_24gb") is not None
