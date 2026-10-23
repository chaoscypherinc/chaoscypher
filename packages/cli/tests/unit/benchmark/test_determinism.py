# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The decoding a result row records is the decoding every stage runs with."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from chaoscypher_cli.benchmark.chat_dataset import GraphRAGChatDataset
from chaoscypher_cli.benchmark.determinism import PINNED_FIELDS, pin_llm_determinism
from chaoscypher_cli.benchmark.extraction_dataset import ExtractionDataset
from chaoscypher_core.settings import LLMSettings


def test_pins_seed_both_temperatures_and_thinking() -> None:
    llm = LLMSettings(seed=None, ai_temperature=0.3, extraction_temperature=0.1)
    pin_llm_determinism(llm, seed=42, temperature=0.0, thinking=False)
    assert (llm.seed, llm.ai_temperature, llm.extraction_temperature) == (42, 0.0, 0.0)
    assert llm.thinking_for_extraction is False
    pin_llm_determinism(llm, seed=1, temperature=0.5)  # thinking untouched when not given
    assert (llm.seed, llm.ai_temperature, llm.thinking_for_extraction) == (1, 0.5, False)


def test_a_settings_schema_without_a_pinned_field_fails_loudly() -> None:
    stub = SimpleNamespace(**{f: None for f in PINNED_FIELDS if f != "seed"})
    with pytest.raises(AttributeError, match="seed"):
        pin_llm_determinism(stub, seed=42, temperature=0.0)


def test_both_benchmark_datasets_carry_the_decoding_the_runner_records() -> None:
    """``run_benchmark`` copies seed and temperature onto a dataset that has the attributes: both must."""
    for cls in (GraphRAGChatDataset, ExtractionDataset):
        fields = {f.name: f for f in cls.__dataclass_fields__.values()}
        assert fields["seed"].default == 42 and fields["temperature"].default == 0.0, cls.__name__
