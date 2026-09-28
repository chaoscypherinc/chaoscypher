# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""``normalize_ollama_tag`` / ``ollama_model_present`` — the one rule both the
LLM health snapshot and the Ollama embedding probe use to compare a
configured model name against ``/api/tags`` output.
"""

from __future__ import annotations

import pytest

from chaoscypher_core.utils.ollama_model_tags import (
    normalize_ollama_tag,
    ollama_model_present,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("llama3", "llama3:latest"),
        ("llama3:latest", "llama3:latest"),
        ("qwen3:30b-instruct", "qwen3:30b-instruct"),
        ("  bge-m3 ", "bge-m3:latest"),
        ("library/llama3", "library/llama3:latest"),
        ("hf.co/org/model:Q4_K_M", "hf.co/org/model:Q4_K_M"),
        ("", ""),
    ],
)
def test_normalize_ollama_tag(raw: str, expected: str) -> None:
    assert normalize_ollama_tag(raw) == expected


def test_ollama_model_present_matches_across_tag_forms() -> None:
    installed = {"llama3:latest", "qwen3:30b-instruct"}
    assert ollama_model_present("llama3", installed)
    assert ollama_model_present("llama3:latest", installed)
    assert ollama_model_present("qwen3:30b-instruct", installed)
    assert not ollama_model_present("qwen3", installed)  # a different tag is a different model
    assert not ollama_model_present("mistral", installed)
