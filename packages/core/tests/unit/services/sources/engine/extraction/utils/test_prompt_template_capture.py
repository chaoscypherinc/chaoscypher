# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Prompt-template capture for the Processing-tab "AI prompts" panel.

Extraction is a two-pass pipeline; the displayed prompts must be the reusable
*templates* (with a placeholder where the per-chunk text / pass-1 entities are
injected) rather than one chunk's filled-in prompt. These tests pin that the
placeholder constants survive formatting against the real harvest templates.
"""

from __future__ import annotations

from chaoscypher_core.services.sources.engine.extraction.utils.ai_entities import (
    PROMPT_CHUNK_TEXT_PLACEHOLDER,
    PROMPT_PASS1_ENTITIES_PLACEHOLDER,
    _build_entity_prompt,
    _build_relationship_prompt,
)
from chaoscypher_core.services.sources.engine.extraction.utils.prompts import (
    ENTITY_HARVEST_TEMPLATE,
    RELATIONSHIP_HARVEST_TEMPLATE,
)


def test_entity_prompt_template_keeps_placeholder_not_chunk_text() -> None:
    """The pass-1 template shows the chunk-text placeholder, not real text."""
    prompt = _build_entity_prompt(
        template=ENTITY_HARVEST_TEMPLATE,
        numbered_text=PROMPT_CHUNK_TEXT_PLACEHOLDER,
        node_templates_formatted="- Person\n- Organization",
        entity_exclusions=None,
        strict_entity_types=False,
        entity_guidance=None,
        entity_examples=None,
    )
    assert PROMPT_CHUNK_TEXT_PLACEHOLDER in prompt
    # Job-static parts (node templates) are still substituted for real.
    assert "Person" in prompt


def test_relationship_prompt_template_keeps_both_placeholders() -> None:
    """The pass-2 template shows both per-chunk placeholders."""
    prompt = _build_relationship_prompt(
        template=RELATIONSHIP_HARVEST_TEMPLATE,
        numbered_sentences=PROMPT_CHUNK_TEXT_PLACEHOLDER,
        entity_list=PROMPT_PASS1_ENTITIES_PLACEHOLDER,
        max_entity_index="N",
        edge_templates="- works_at",
        relationship_guidance=None,
        relationship_examples=None,
    )
    assert PROMPT_CHUNK_TEXT_PLACEHOLDER in prompt
    assert PROMPT_PASS1_ENTITIES_PLACEHOLDER in prompt
    assert "works_at" in prompt


def test_relationship_prompt_appends_guidance_and_examples() -> None:
    """Optional domain guidance/examples are appended to the template."""
    prompt = _build_relationship_prompt(
        template=RELATIONSHIP_HARVEST_TEMPLATE,
        numbered_sentences=PROMPT_CHUNK_TEXT_PLACEHOLDER,
        entity_list=PROMPT_PASS1_ENTITIES_PLACEHOLDER,
        max_entity_index="N",
        edge_templates="- e",
        relationship_guidance="DOMAIN_GUIDANCE_MARKER",
        relationship_examples="DOMAIN_EXAMPLE_MARKER",
    )
    assert "DOMAIN_GUIDANCE_MARKER" in prompt
    assert "DOMAIN_EXAMPLE_MARKER" in prompt


def test_relationship_prompt_matches_inline_formatting_for_real_values() -> None:
    """The helper reproduces the prior inline formatting for real values."""
    expected = RELATIONSHIP_HARVEST_TEMPLATE.format(
        numbered_sentences="1. Alice met Bob.",
        entity_list="0: Alice\n1: Bob",
        max_entity_index=1,
        edge_templates="- knows",
    )
    prompt = _build_relationship_prompt(
        template=RELATIONSHIP_HARVEST_TEMPLATE,
        numbered_sentences="1. Alice met Bob.",
        entity_list="0: Alice\n1: Bob",
        max_entity_index=1,
        edge_templates="- knows",
        relationship_guidance=None,
        relationship_examples=None,
    )
    assert prompt == expected
