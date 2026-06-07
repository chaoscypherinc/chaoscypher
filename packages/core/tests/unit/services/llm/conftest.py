# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Shared fixtures for LLM service unit tests."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from chaoscypher_core.app_config import Settings


@pytest.fixture
def ollama_settings_factory() -> Callable[..., Settings]:
    """Return a factory that builds an ollama-provider Settings instance.

    Keyword arguments map to LLM fields:
        chat_model: str — overrides ollama_chat_model (default: "qwen3:30b-instruct")
        extraction_model: str | None — overrides ollama_extraction_model (default: None)
        vision_model: str | None — overrides ollama_vision_model (default: None)
    """

    def _factory(
        chat_model: str = "qwen3:30b-instruct",
        extraction_model: str | None = None,
        vision_model: str | None = None,
    ) -> Settings:
        return Settings(
            llm={
                "chat_provider": "ollama",
                "ollama_chat_model": chat_model,
                "ollama_extraction_model": extraction_model,
                "ollama_vision_model": vision_model,
            }
        )

    return _factory


@pytest.fixture
def openai_settings() -> Settings:
    """Return a Settings instance configured for the OpenAI provider."""
    from pydantic import SecretStr

    return Settings(
        llm={
            "chat_provider": "openai",
            "openai_api_key": SecretStr("sk-test-key"),
        }
    )
