# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The extraction prompts document the pipe escape the parser accepts."""

from chaoscypher_core.services.sources.engine.extraction.utils.prompts import (
    ENTITY_HARVEST_TEMPLATE,
    EXTRACTION_RULES_TEMPLATE,
    RELATIONSHIP_HARVEST_TEMPLATE,
)


def test_every_pipe_format_prompt_documents_the_escape() -> None:
    r"""A model can only follow an escape rule it has been given.

    ``unescape_field`` has accepted ``\|`` for a long time, but until
    2026-09-24 no prompt mentioned it, so a name containing ``|`` broke the
    line for 13/16 models on probe A2-hard.
    """
    for template in (
        ENTITY_HARVEST_TEMPLATE,
        RELATIONSHIP_HARVEST_TEMPLATE,
        EXTRACTION_RULES_TEMPLATE,
    ):
        assert "\\|" in template, template[:80]
        assert "\\\\" in template, template[:80]


def test_entity_prompt_shows_an_empty_aliases_field_and_every_example_parses() -> None:
    """Models copy the examples: one keeps ``||`` for no aliases, and all of them parse.

    Local models omitted the aliases slot when the examples only showed
    populated aliases (32 entity lines lost in War and Peace Book One).
    """
    from chaoscypher_core.services.sources.engine.extraction.utils.line_parser import (
        parse_entity_line,
    )

    assert "never omit a field" in ENTITY_HARVEST_TEMPLATE
    examples = [ln for ln in ENTITY_HARVEST_TEMPLATE.splitlines() if ln.startswith("E|")]
    parsed = [parse_entity_line(ln) for ln in examples]
    assert all(p is not None for p in parsed)
    assert any(ln.split("|")[3] == "" for ln in examples)
