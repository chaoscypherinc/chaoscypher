# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The one "a model is configured" rule.

A model is configured when setup has been done (the wizard ran, ``settings.yaml``
names a chat provider, or ``CHAOSCYPHER_LLM_PROVIDER`` is set) and the chat
provider has what it needs: OpenAI, Anthropic and Gemini their API key, Ollama
nothing. The defaults count: a finished setup on the default Ollama provider is
configured.

The CLI's ``is_llm_configured`` and the import-time summary both ask
:func:`model_configured`. The CLI's start-up gate keeps its own cheap copy of
:func:`setup_completed` (importing Core on every CLI start is too slow); a
parity test pins the two together.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from chaoscypher_core.settings import LLMSettings


__all__ = ["llm_configured", "model_configured", "setup_completed"]

SETUP_ENV_VAR = "CHAOSCYPHER_LLM_PROVIDER"
SETTINGS_FILENAME = "settings.yaml"

# The key each chat provider needs; ``None``: the provider needs none.
_PROVIDER_KEYS: dict[str, str | None] = {
    "openai": "openai_api_key",
    "anthropic": "anthropic_api_key",
    "gemini": "gemini_api_key",
    "ollama": None,
}


def llm_configured(llm: LLMSettings, *, setup_done: bool) -> bool:
    """True when setup is done and the chat provider has what it needs.

    Args:
        llm: The LLM settings.
        setup_done: Whether setup has been done (:func:`setup_completed`).

    Returns:
        False for an empty or unknown provider, or a cloud provider without its key.

    """
    if not setup_done:
        return False
    provider = llm.chat_provider
    if not isinstance(provider, str) or provider not in _PROVIDER_KEYS:
        return False
    key_field = _PROVIDER_KEYS[provider]
    return key_field is None or bool(getattr(llm, key_field, None))


def _read_settings_yaml(data_dir: str | Path) -> dict[str, Any]:
    """Best-effort raw parse of ``data_dir/settings.yaml``: ``{}`` on any error."""
    try:
        import yaml

        raw = yaml.safe_load((Path(data_dir) / SETTINGS_FILENAME).read_text(encoding="utf-8"))
    except Exception:
        return {}
    return raw if isinstance(raw, dict) else {}


def setup_completed(data_dir: str | Path) -> bool:
    """True when the engine has been set up (CLI wizard, web wizard, or environment).

    Satisfied by ``CHAOSCYPHER_LLM_PROVIDER`` in the environment, or by a
    ``settings.yaml`` in ``data_dir`` with a truthy ``setup_completed`` or an
    explicit ``llm.chat_provider``.
    """
    if os.getenv(SETUP_ENV_VAR):
        return True
    data = _read_settings_yaml(data_dir)
    if data.get("setup_completed"):
        return True
    llm = data.get("llm")
    return isinstance(llm, dict) and bool(llm.get("chat_provider"))


def model_configured(llm: LLMSettings, data_dir: str | Path) -> bool:
    """True when a model is configured: setup done, and the provider has what it needs."""
    return llm_configured(llm, setup_done=setup_completed(data_dir))
