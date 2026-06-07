# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""parse_extraction_output respects FilteringConfig.minimum_alias_length.

The W4 wiring promise: when a caller passes a resolved
``FilteringConfig`` whose ``minimum_alias_length`` is N to the
extraction pipeline, the line parser must drop aliases shorter than N
characters.

The parser already accepts a ``min_alias_length`` keyword; this file
pins the contract on the public name (``minimum_alias_length`` —
matching the FilteringConfig field) and the harvest call site that
threads it through.
"""

from __future__ import annotations

from chaoscypher_core.services.sources.engine.extraction.utils.line_parser import (
    parse_extraction_output,
)


def test_parse_keeps_two_char_alias_when_minimum_is_two() -> None:
    """minimum_alias_length=2 (lenient mode) keeps the 'AI' alias."""
    line = "E|OpenAI|Organization|AI; ML; LLM|0.9|S1|An AI lab"
    entities, _, _ = parse_extraction_output(line, minimum_alias_length=2)
    assert entities, "expected at least one entity to parse"
    aliases = entities[0]["aliases"]
    assert "AI" in aliases
    assert "ML" in aliases
    assert "LLM" in aliases


def test_parse_drops_two_char_alias_when_minimum_is_three() -> None:
    """minimum_alias_length=3 (maximum mode) drops the 'AI' alias."""
    line = "E|OpenAI|Organization|AI; ML; LLM|0.9|S1|An AI lab"
    entities, _, _ = parse_extraction_output(line, minimum_alias_length=3)
    assert entities, "expected at least one entity to parse"
    aliases = entities[0]["aliases"]
    assert "AI" not in aliases
    assert "ML" not in aliases
    assert "LLM" in aliases  # 3 chars, kept


def test_parse_keeps_one_char_alias_when_minimum_is_one() -> None:
    """minimum_alias_length=1 (unfiltered/minimal modes) keeps single chars."""
    line = "E|OpenAI|Organization|X; AI; ML|0.9|S1|description"
    entities, _, _ = parse_extraction_output(line, minimum_alias_length=1)
    assert entities, "expected at least one entity to parse"
    aliases = entities[0]["aliases"]
    # Single-char alias kept in unfiltered/minimal modes.
    assert "X" in aliases
    assert "AI" in aliases
    assert "ML" in aliases
