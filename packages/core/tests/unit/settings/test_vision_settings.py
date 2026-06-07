# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Smoke test for the new vision-specific LLMSettings fields."""

from __future__ import annotations

from chaoscypher_core.settings import LLMSettings


def test_vision_max_output_tokens_defaults() -> None:
    s = LLMSettings()
    assert s.ollama_vision_max_output_tokens == 8192
    assert s.openai_vision_max_output_tokens == 8192
    assert s.anthropic_vision_max_output_tokens == 8192
    assert s.gemini_vision_max_output_tokens == 8192


def test_vision_split_on_truncation_default_off() -> None:
    s = LLMSettings()
    assert s.vision_split_on_truncation is False


def test_vision_max_output_tokens_can_be_overridden() -> None:
    s = LLMSettings(ollama_vision_max_output_tokens=16384)
    assert s.ollama_vision_max_output_tokens == 16384


def test_vision_max_output_tokens_can_be_none() -> None:
    """None means unbounded — the docstring discourages but allows it."""
    s = LLMSettings(ollama_vision_max_output_tokens=None)
    assert s.ollama_vision_max_output_tokens is None
