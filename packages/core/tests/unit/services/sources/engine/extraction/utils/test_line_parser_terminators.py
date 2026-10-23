# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""A trailing pipe closes an empty last field; spaced separators are separators.

Regression (section-audit 2026-10-12): the parser stripped every trailing
``|`` as a line terminator, which deleted an empty description or
justification and dropped the line as malformed, though the prompt tells
the model to keep every field even when empty; and the unescaped-pipe
guard rejected an entity line written as ``E| Pierre | Person | … ``,
with its ``P|`` lines.
"""

from __future__ import annotations

import pytest

from chaoscypher_core.services.sources.engine.extraction.utils.line_parser import (
    _strip_terminator_pipe,
    parse_extraction_output,
)


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("E|Napoleon|Person||1.0|S1|", "E|Napoleon|Person||1.0|S1|"),  # empty description
        ("E|Napoleon|Person||1.0|S1|d|", "E|Napoleon|Person||1.0|S1|d"),  # terminator
        ("E|Napoleon|Person|1.0|S1|", "E|Napoleon|Person|1.0|S1|"),  # no aliases column
        ("R|0|1|knows|0.9|S1|", "R|0|1|knows|0.9|S1|"),  # empty justification
        ("R|0|1|knows|0.9|S1|because|", "R|0|1|knows|0.9|S1|because"),
        ("P|0|age|40|", "P|0|age|40"),
        ("P|0|age|40", "P|0|age|40"),
        ("E|Rostov \\| Sons|Organization||1.0|S1|", "E|Rostov \\| Sons|Organization||1.0|S1|"),
        ("E|Name|Type||1.0|S1|ends with \\|", "E|Name|Type||1.0|S1|ends with \\|"),
    ],
)
def test_terminator_pipe_goes_only_past_a_full_line(line: str, expected: str) -> None:
    assert _strip_terminator_pipe(line) == expected


def test_entity_with_empty_description_and_trailing_pipe_is_kept() -> None:
    entities, _r, _p = parse_extraction_output("E|Napoleon|Person||1.0|S1|")
    assert [e["name"] for e in entities] == ["Napoleon"]
    assert entities[0].get("description", "") == ""


def test_entity_with_aliases_and_trailing_pipe_is_kept() -> None:
    entities, _r, _p = parse_extraction_output("E|Napoleon|Person|Bonaparte|1.0|S1|")
    assert len(entities) == 1 and "Bonaparte" in entities[0]["aliases"]


def test_relationship_with_empty_justification_and_trailing_pipe_is_kept() -> None:
    text = "E|Napoleon|Person||1.0|S1|a general\nE|Moscow|Location||1.0|S1|a city\nR|0|1|visited|0.9|S1|"
    _e, relationships, _p = parse_extraction_output(text)
    assert len(relationships) == 1
    assert relationships[0]["type"] == "visited"


def test_property_with_trailing_pipe_still_strips_it() -> None:
    text = "E|Pierre|Person||1.0|S1|a count\nP|0|age|40|"
    _e, _r, properties = parse_extraction_output(text)
    assert properties[0]["value"] == "40"


def test_spaced_separators_parse_with_their_properties() -> None:
    text = "E| Pierre | Person | Pierre Bezukhov | 1.0 | S1 | a count\nP| 0 | age | 40 "
    entities, _r, properties = parse_extraction_output(text)
    assert [e["name"] for e in entities] == ["Pierre"]
    assert entities[0]["type"] == "Person"
    assert len(properties) == 1


def test_unescaped_pipe_inside_a_name_is_still_rejected() -> None:
    entities, _r, _p = parse_extraction_output(
        "E|Rostov | Bolkonsky & Sons|Organization||1.0|S1|a firm"
    )
    assert entities == []
