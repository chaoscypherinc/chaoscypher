# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for LLMSettings environment variable auto-detection."""

import os
from unittest.mock import patch

import pytest

from chaoscypher_core.settings import LexiconSettings, LLMSettings


@pytest.mark.unit
@pytest.mark.core
class TestLLMSettingsEnvVars:
    """LLMSettings should auto-detect API keys from environment."""

    def test_openai_api_key_from_env(self):
        """OPENAI_API_KEY env var populates openai_api_key."""
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test-123"}):
            settings = LLMSettings()
            assert settings.openai_api_key is not None
            assert settings.openai_api_key.get_secret_value() == "sk-test-123"

    def test_anthropic_api_key_from_env(self):
        """ANTHROPIC_API_KEY env var populates anthropic_api_key."""
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-test"}):
            settings = LLMSettings()
            assert settings.anthropic_api_key is not None
            assert settings.anthropic_api_key.get_secret_value() == "sk-ant-test"

    def test_gemini_api_key_from_env(self):
        """GEMINI_API_KEY env var populates gemini_api_key."""
        with patch.dict(os.environ, {"GEMINI_API_KEY": "gem-test"}):
            settings = LLMSettings()
            assert settings.gemini_api_key is not None
            assert settings.gemini_api_key.get_secret_value() == "gem-test"

    def test_explicit_value_overrides_env(self):
        """Explicit constructor value takes precedence over env var."""
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-from-env"}):
            settings = LLMSettings(openai_api_key="sk-explicit")
            assert settings.openai_api_key is not None
            assert settings.openai_api_key.get_secret_value() == "sk-explicit"

    def test_no_env_var_keeps_none(self):
        """Without env var, API key remains None."""
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY")
        }
        with patch.dict(os.environ, env, clear=True):
            settings = LLMSettings()
            assert settings.openai_api_key is None

    def test_chat_provider_from_env(self):
        """CHAOSCYPHER_LLM_PROVIDER env var sets chat_provider."""
        with patch.dict(os.environ, {"CHAOSCYPHER_LLM_PROVIDER": "openai"}):
            settings = LLMSettings()
            assert settings.chat_provider == "openai"

    def test_chat_provider_explicit_overrides_env(self):
        """Explicit chat_provider overrides env var."""
        with patch.dict(os.environ, {"CHAOSCYPHER_LLM_PROVIDER": "openai"}):
            settings = LLMSettings(chat_provider="anthropic")
            assert settings.chat_provider == "anthropic"


@pytest.mark.unit
@pytest.mark.core
class TestOllamaDefaultUrl:
    """Default Ollama base URL is localhost, overridable via CHAOSCYPHER_OLLAMA_URL.

    The library default must work for bare-metal installs (pip/CLI users
    running Ollama on the same host). Docker images opt back into
    ``host.docker.internal`` by exporting CHAOSCYPHER_OLLAMA_URL.
    """

    def test_default_instance_is_localhost_when_env_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without the env var, the seeded default instance targets localhost."""
        monkeypatch.delenv("CHAOSCYPHER_OLLAMA_URL", raising=False)
        settings = LLMSettings()
        assert settings.ollama_instances[0].base_url == "http://localhost:11434"

    def test_default_instance_honors_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """CHAOSCYPHER_OLLAMA_URL overrides the seeded default instance URL."""
        monkeypatch.setenv("CHAOSCYPHER_OLLAMA_URL", "http://gpu-box:11434")
        settings = LLMSettings()
        assert settings.ollama_instances[0].base_url == "http://gpu-box:11434"

    def test_primary_url_fallback_is_localhost_when_env_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The empty-instances fallback also defaults to localhost."""
        monkeypatch.delenv("CHAOSCYPHER_OLLAMA_URL", raising=False)
        settings = LLMSettings(ollama_instances=[])
        assert settings.primary_ollama_url == "http://localhost:11434"

    def test_primary_url_fallback_honors_env_override(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The empty-instances fallback honors CHAOSCYPHER_OLLAMA_URL too."""
        monkeypatch.setenv("CHAOSCYPHER_OLLAMA_URL", "http://10.0.0.5:11434")
        settings = LLMSettings(ollama_instances=[])
        assert settings.primary_ollama_url == "http://10.0.0.5:11434"


@pytest.mark.unit
@pytest.mark.core
class TestLexiconSettingsEnvVars:
    """LexiconSettings.timeout honours CHAOSCYPHER_LEXICON_TIMEOUT.

    The env override used to live in CLIConfig._apply_env_overrides; it
    now lives in LexiconSettings.timeout's default_factory so the engine
    config is the single source of truth.
    """

    def test_timeout_from_env(self):
        """CHAOSCYPHER_LEXICON_TIMEOUT env var sets timeout."""
        with patch.dict(os.environ, {"CHAOSCYPHER_LEXICON_TIMEOUT": "55"}):
            assert LexiconSettings().timeout == 55

    def test_timeout_defaults_to_30_without_env(self):
        """Without the env var, timeout keeps its package default (30)."""
        env = {k: v for k, v in os.environ.items() if k != "CHAOSCYPHER_LEXICON_TIMEOUT"}
        with patch.dict(os.environ, env, clear=True):
            assert LexiconSettings().timeout == 30

    def test_timeout_non_int_env_falls_back_to_default(self):
        """A garbage env value falls back to the default rather than crashing."""
        with patch.dict(os.environ, {"CHAOSCYPHER_LEXICON_TIMEOUT": "not-a-number"}):
            assert LexiconSettings().timeout == 30

    def test_timeout_explicit_overrides_env(self):
        """An explicit constructor value still wins over the env default."""
        with patch.dict(os.environ, {"CHAOSCYPHER_LEXICON_TIMEOUT": "55"}):
            assert LexiconSettings(timeout=99).timeout == 99
