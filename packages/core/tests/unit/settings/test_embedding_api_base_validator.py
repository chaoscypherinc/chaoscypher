# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""``EmbeddingSettings.api_base`` must ride the same URL-safety guard as its siblings.

Every other operator-settable custom-endpoint field
(``LexiconSettings.url``, ``OllamaInstance.base_url``,
``LLMSettings.openai_base_url``) validates through
``validate_url_safety``. ``api_base`` outranks the validated Ollama
``base_url`` in the embedding factory and attaches the embedding API key
as a Bearer token to every request, so leaving it unvalidated made it
the one field where a mis-set endpoint silently exfiltrates the key.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from chaoscypher_core.settings import EmbeddingSettings


def test_rejects_cloud_metadata_endpoint() -> None:
    with pytest.raises(ValidationError, match="safety policy"):
        EmbeddingSettings(api_base="http://169.254.169.254/latest/meta-data")


def test_rejects_non_http_scheme() -> None:
    with pytest.raises(ValidationError, match="safety policy"):
        EmbeddingSettings(api_base="file:///etc/passwd")


def test_none_and_empty_pass_through_as_unset() -> None:
    """Falsy values mean "no override" to the factory and must survive round-trips."""
    assert EmbeddingSettings(api_base=None).api_base is None
    assert EmbeddingSettings(api_base="").api_base == ""


def test_local_and_lan_http_urls_stay_allowed() -> None:
    """Permissive policy — local Ollama / LAN endpoints keep working."""
    for url in (
        "http://localhost:11434",
        "http://127.0.0.1:11434",
        "http://192.168.1.100:8080",
        "https://api.openai.com",
    ):
        assert EmbeddingSettings(api_base=url).api_base == url
