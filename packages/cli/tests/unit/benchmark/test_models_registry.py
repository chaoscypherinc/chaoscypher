# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the benchmark model registry loader."""

from __future__ import annotations

from chaoscypher_cli.benchmark.models_registry import RegistryEntry, load_registry


def test_load_registry_has_anthropic_opus(tmp_path):
    reg = load_registry()
    entry = reg["anthropic/claude-opus-4-8"]
    assert isinstance(entry, RegistryEntry)
    assert entry.price == {"input": 5.00, "output": 25.00}
    assert entry.open_weight is False


def test_load_registry_from_path(tmp_path):
    p = tmp_path / "r.yaml"
    p.write_text(
        "ollama/m:\n  provider: ollama\n  model: m\n  label: M\n  open_weight: true\n",
        encoding="utf-8",
    )
    reg = load_registry(path=p)
    assert reg["ollama/m"].open_weight is True
    assert reg["ollama/m"].price is None


def test_parse_raises_on_price_block_missing_input(tmp_path):
    """A price block that omits 'input' must raise ValueError at load time."""
    p = tmp_path / "bad.yaml"
    p.write_text(
        "openai/m:\n  provider: openai\n  model: m\n  label: M\n  price:\n    output: 10.00\n",
        encoding="utf-8",
    )
    import pytest

    with pytest.raises(ValueError, match="price block missing key 'input'"):
        load_registry(path=p)


def test_tools_capability_is_read_and_defaults_to_unknown(tmp_path):
    """``tools`` mirrors the Ollama manifest; an entry that never checked it stays None."""
    p = tmp_path / "r.yaml"
    p.write_text(
        "ollama/a:\n  provider: ollama\n  model: a\n  label: A\n  tools: false\n"
        "ollama/b:\n  provider: ollama\n  model: b\n  label: B\n",
        encoding="utf-8",
    )
    reg = load_registry(path=p)
    assert reg["ollama/a"].tools is False
    assert reg["ollama/b"].tools is None


def test_every_measured_local_model_states_its_tool_support():
    """Models with a VRAM figure were pulled, so their manifest was available to check."""
    reg = load_registry()
    unchecked = [
        e.model_id
        for e in reg.values()
        if e.provider == "ollama" and e.vram_gb and e.tools is None and "embedding" not in e.model
    ]
    # The two XL models were never pulled on the benchmark box; embedders never chat.
    assert unchecked == ["ollama/llama3.1:70b", "ollama/gpt-oss:120b"]
    assert reg["ollama/phi4:14b"].tools is False
    assert reg["ollama/olmo-3.1:32b"].tools is False
