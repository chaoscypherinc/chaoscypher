# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The one "a model is configured" rule: setup done, and a provider with what it needs."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from chaoscypher_core.services.llm.configured import (
    llm_configured,
    model_configured,
    setup_completed,
)
from chaoscypher_core.settings import LLMSettings


@pytest.mark.parametrize(
    ("provider", "keys", "expected"),
    [
        ("ollama", {}, True),
        ("openai", {}, False),
        ("openai", {"openai_api_key": "sk-test"}, True),  # pragma: allowlist secret
        ("anthropic", {}, False),
        ("anthropic", {"anthropic_api_key": "sk-test"}, True),  # pragma: allowlist secret
        ("gemini", {}, False),
        ("gemini", {"gemini_api_key": "g-test"}, True),  # pragma: allowlist secret
        ("", {}, False),
        ("mystery", {"openai_api_key": "sk-test"}, False),  # pragma: allowlist secret
    ],
)
def test_the_provider_and_key_table(provider: str, keys: dict[str, str], expected: bool) -> None:
    llm = LLMSettings(chat_provider=provider, **keys)  # type: ignore[arg-type]

    assert llm_configured(llm, setup_done=True) is expected


def test_setup_not_done_is_never_configured() -> None:
    assert llm_configured(LLMSettings(chat_provider="ollama"), setup_done=False) is False


@pytest.fixture
def no_env_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CHAOSCYPHER_LLM_PROVIDER", raising=False)


def _write(data_dir: Path, data: object) -> None:
    (data_dir / "settings.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")


@pytest.mark.usefixtures("no_env_provider")
def test_setup_completed_flag(tmp_path: Path) -> None:
    _write(tmp_path, {"setup_completed": True})
    assert setup_completed(tmp_path) is True


@pytest.mark.usefixtures("no_env_provider")
def test_setup_completed_by_an_explicit_chat_provider(tmp_path: Path) -> None:
    _write(tmp_path, {"llm": {"chat_provider": "ollama"}})
    assert setup_completed(str(tmp_path)) is True


@pytest.mark.usefixtures("no_env_provider")
def test_setup_not_completed_with_nothing(tmp_path: Path) -> None:
    assert setup_completed(tmp_path) is False


def test_setup_completed_by_the_env_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CHAOSCYPHER_LLM_PROVIDER", "ollama")
    assert setup_completed(tmp_path) is True


@pytest.mark.usefixtures("no_env_provider")
def test_model_configured_needs_both(tmp_path: Path) -> None:
    llm = LLMSettings(chat_provider="ollama")
    assert model_configured(llm, tmp_path) is False
    _write(tmp_path, {"setup_completed": True})
    assert model_configured(llm, tmp_path) is True
    assert model_configured(LLMSettings(chat_provider="openai"), tmp_path) is False
