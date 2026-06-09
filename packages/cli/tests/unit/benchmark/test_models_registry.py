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
