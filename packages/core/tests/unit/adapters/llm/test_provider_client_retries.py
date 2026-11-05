# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""``llm.client_max_retries`` reaches each cloud client; unset, the library's default stands.

The import-time summary is one try (owner's ruling): its provider is built with
``client_max_retries=0`` so the LangChain client does not retry inside the call.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from chaoscypher_core.adapters.llm.factory import ProviderFactory
from chaoscypher_core.settings import EngineSettings, LLMSettings


_BASE_CONFIG: dict[str, Any] = {
    "chat_provider": "test",
    "llm_max_concurrent": 1,
    "llm_reserved_interactive": 0,
    "llm_enable_priority": True,
    "llm_request_timeout": 300,
}

_PROVIDERS = {
    "openai": (
        "chaoscypher_core.adapters.llm.providers.openai_provider",
        "OpenAIProvider",
        "ChatOpenAI",
        {
            "openai_api_key": "sk-test",  # pragma: allowlist secret
            "openai_base_url": "https://api.openai.com/v1",
            "openai_chat_model": "gpt-4",
        },
    ),
    "anthropic": (
        "chaoscypher_core.adapters.llm.providers.anthropic_provider",
        "AnthropicProvider",
        "ChatAnthropic",
        {
            "anthropic_api_key": "sk-ant-test",  # pragma: allowlist secret
            "anthropic_chat_model": "claude-test",
        },
    ),
    "gemini": (
        "chaoscypher_core.adapters.llm.providers.gemini_provider",
        "GeminiProvider",
        "ChatGoogleGenerativeAI",
        {
            "gemini_api_key": "g-test",  # pragma: allowlist secret
            "gemini_chat_model": "gemini-test",
        },
    ),
}


def _client_kwargs(provider: str, **config: Any) -> dict[str, Any]:
    import importlib

    module_name, class_name, client_name, keys = _PROVIDERS[provider]
    module = importlib.import_module(module_name)
    captured: dict[str, Any] = {}

    def fake_client(**kwargs: Any) -> object:
        captured.update(kwargs)
        return object()

    with patch(f"{module_name}.{client_name}", side_effect=fake_client):
        getattr(module, class_name)({**_BASE_CONFIG, **keys, **config})
    return captured


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
def test_client_max_retries_reaches_the_client(provider: str) -> None:
    assert _client_kwargs(provider, client_max_retries=0)["max_retries"] == 0
    assert _client_kwargs(provider, client_max_retries=2)["max_retries"] == 2


def test_gemini_gets_attempts_not_retries() -> None:
    """langchain-google-genai reads ``max_retries`` as total attempts, and 0 as "Google's default".

    So retries N reach it as N + 1 attempts: 0 is one try, as for the other clients.
    """
    assert _client_kwargs("gemini", client_max_retries=0)["max_retries"] == 1
    assert _client_kwargs("gemini", client_max_retries=2)["max_retries"] == 3


@pytest.mark.parametrize("provider", sorted(_PROVIDERS))
def test_unset_client_max_retries_keeps_the_library_default(provider: str) -> None:
    assert "max_retries" not in _client_kwargs(provider, client_max_retries=None)
    assert "max_retries" not in _client_kwargs(provider)


def test_the_factory_passes_client_max_retries() -> None:
    settings = EngineSettings(llm=LLMSettings(chat_provider="openai", client_max_retries=0))
    assert ProviderFactory(settings)._get_provider_config()["client_max_retries"] == 0
    default = EngineSettings(llm=LLMSettings(chat_provider="openai"))
    assert ProviderFactory(default)._get_provider_config()["client_max_retries"] is None
