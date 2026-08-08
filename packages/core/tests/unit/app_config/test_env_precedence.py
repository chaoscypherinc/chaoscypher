# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Env-var precedence for the settings loader.

Documented precedence (highest wins): env var → settings.yaml → package
default. Two historical violations are pinned here:

1. ``CHAOSCYPHER_DATA_DIR``/``CONFIG_DIR``/``CACHE_DIR`` and ``LEXICON_*``
   were wired as Pydantic ``default_factory`` reads, so a value in
   settings.yaml silently beat the env var (inverted precedence).
2. When settings.yaml did not exist (first-run install), the loader skipped
   Dynaconf entirely, dropping every ``CHAOSCYPHER_*`` prefixed env override.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chaoscypher_core.app_config import Settings


_ENV_VARS = (
    "CHAOSCYPHER_DATA_DIR",
    "CHAOSCYPHER_CONFIG_DIR",
    "CHAOSCYPHER_CACHE_DIR",
    "CHAOSCYPHER_DEFAULT_SETTINGS_PATH",
    "CHAOSCYPHER_STATIC_DIR",
    "LEXICON_URL",
    "LEXICON_API_PATH",
    "CHAOSCYPHER_LEXICON_TIMEOUT",
    "CHAOSCYPHER_CURRENT_DATABASE",
    "CHAOSCYPHER_DARK_MODE",
    "CHAOSCYPHER_LLM__OLLAMA_CHAT_MODEL",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start each test without any of the precedence-relevant env vars."""
    for var in _ENV_VARS:
        monkeypatch.delenv(var, raising=False)


class TestEnvBeatsYamlForPathFields:
    """Env var must win over settings.yaml for paths.* fields."""

    def test_data_dir_env_beats_yaml(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        yaml_dir = tmp_path / "yaml-data"
        env_dir = tmp_path / "env-data"
        yaml_path = tmp_path / "settings.yaml"
        yaml_path.write_text(f"paths:\n  data_dir: {yaml_dir.as_posix()}\n", encoding="utf-8")
        monkeypatch.setenv("CHAOSCYPHER_DATA_DIR", str(env_dir))

        settings = Settings.load_from_yaml(yaml_path)

        assert Path(settings.paths.data_dir) == env_dir.resolve()

    def test_config_dir_env_beats_yaml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        yaml_path = tmp_path / "settings.yaml"
        yaml_path.write_text(
            f"paths:\n  config_dir: {(tmp_path / 'yaml-config').as_posix()}\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("CHAOSCYPHER_CONFIG_DIR", str(tmp_path / "env-config"))

        settings = Settings.load_from_yaml(yaml_path)

        assert Path(settings.paths.config_dir) == (tmp_path / "env-config").resolve()

    def test_cache_dir_env_beats_yaml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        yaml_path = tmp_path / "settings.yaml"
        yaml_path.write_text(
            f"paths:\n  cache_dir: {(tmp_path / 'yaml-cache').as_posix()}\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("CHAOSCYPHER_CACHE_DIR", str(tmp_path / "env-cache"))

        settings = Settings.load_from_yaml(yaml_path)

        assert Path(settings.paths.cache_dir) == (tmp_path / "env-cache").resolve()

    def test_deployment_paths_env_beats_yaml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        yaml_path = tmp_path / "settings.yaml"
        yaml_path.write_text(
            "paths:\n"
            "  default_settings_path: /yaml/default_settings.yaml\n"
            "  static_dir: /yaml/static\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("CHAOSCYPHER_DEFAULT_SETTINGS_PATH", "/env/default_settings.yaml")
        monkeypatch.setenv("CHAOSCYPHER_STATIC_DIR", "/env/static")

        settings = Settings.load_from_yaml(yaml_path)

        assert settings.paths.default_settings_path == "/env/default_settings.yaml"
        assert settings.paths.static_dir == "/env/static"

    def test_yaml_still_beats_default_when_env_unset(self, tmp_path: Path) -> None:
        yaml_dir = tmp_path / "yaml-data"
        yaml_path = tmp_path / "settings.yaml"
        yaml_path.write_text(f"paths:\n  data_dir: {yaml_dir.as_posix()}\n", encoding="utf-8")

        settings = Settings.load_from_yaml(yaml_path)

        assert Path(settings.paths.data_dir) == yaml_dir.resolve()


class TestEnvBeatsYamlForLexiconFields:
    """Env var must win over settings.yaml for lexicon.* fields."""

    def test_lexicon_url_env_beats_yaml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        yaml_path = tmp_path / "settings.yaml"
        yaml_path.write_text(
            "lexicon:\n  url: https://lexicon-yaml.example.com\n", encoding="utf-8"
        )
        monkeypatch.setenv("LEXICON_URL", "https://lexicon-env.example.com")

        settings = Settings.load_from_yaml(yaml_path)

        assert settings.lexicon.url == "https://lexicon-env.example.com"

    def test_lexicon_api_path_env_beats_yaml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        yaml_path = tmp_path / "settings.yaml"
        yaml_path.write_text("lexicon:\n  api_path: /yaml/v1\n", encoding="utf-8")
        monkeypatch.setenv("LEXICON_API_PATH", "/env/v1")

        settings = Settings.load_from_yaml(yaml_path)

        assert settings.lexicon.api_path == "/env/v1"

    def test_lexicon_timeout_env_beats_yaml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        yaml_path = tmp_path / "settings.yaml"
        yaml_path.write_text("lexicon:\n  timeout: 77\n", encoding="utf-8")
        monkeypatch.setenv("CHAOSCYPHER_LEXICON_TIMEOUT", "55")

        settings = Settings.load_from_yaml(yaml_path)

        assert settings.lexicon.timeout == 55

    def test_lexicon_timeout_garbage_env_keeps_yaml_value(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A non-integer env value never crashes boot (mirrors the factory)."""
        yaml_path = tmp_path / "settings.yaml"
        yaml_path.write_text("lexicon:\n  timeout: 77\n", encoding="utf-8")
        monkeypatch.setenv("CHAOSCYPHER_LEXICON_TIMEOUT", "not-a-number")

        settings = Settings.load_from_yaml(yaml_path)

        assert settings.lexicon.timeout == 77


class TestPrefixedEnvAppliesWithoutSettingsYaml:
    """CHAOSCYPHER_* env overrides must load even when settings.yaml is absent."""

    def test_current_database_env_applies_without_yaml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CHAOSCYPHER_CURRENT_DATABASE", "envdb")

        settings = Settings.load_from_yaml(tmp_path / "settings.yaml")  # never created

        assert settings.current_database == "envdb"

    def test_dark_mode_env_applies_without_yaml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CHAOSCYPHER_DARK_MODE", "false")

        settings = Settings.load_from_yaml(tmp_path / "settings.yaml")

        assert settings.dark_mode is False

    def test_nested_llm_env_applies_without_yaml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Dynaconf dunder syntax reaches nested settings groups."""
        monkeypatch.setenv("CHAOSCYPHER_LLM__OLLAMA_CHAT_MODEL", "env-model:1b")

        settings = Settings.load_from_yaml(tmp_path / "settings.yaml")

        assert settings.llm.ollama_chat_model == "env-model:1b"

    def test_nested_llm_env_applies_with_yaml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Same dunder override works when a settings.yaml exists too."""
        yaml_path = tmp_path / "settings.yaml"
        yaml_path.write_text("llm:\n  ollama_num_ctx: 12345\n", encoding="utf-8")
        monkeypatch.setenv("CHAOSCYPHER_LLM__OLLAMA_CHAT_MODEL", "env-model:1b")

        settings = Settings.load_from_yaml(yaml_path)

        assert settings.llm.ollama_chat_model == "env-model:1b"
        assert settings.llm.ollama_num_ctx == 12345

    def test_defaults_when_no_yaml_and_no_env(self, tmp_path: Path) -> None:
        settings = Settings.load_from_yaml(tmp_path / "settings.yaml")

        assert settings.current_database == "default"
        assert settings.dark_mode is True
