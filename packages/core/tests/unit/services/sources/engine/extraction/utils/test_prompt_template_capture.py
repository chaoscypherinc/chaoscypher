# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Prompt-template capture for the Processing-tab "AI prompts" panel.

Extraction is a two-pass pipeline; the displayed prompts must be the reusable
*templates* (with a placeholder where the per-chunk text / pass-1 entities are
injected) rather than one chunk's filled-in prompt. These tests pin that the
placeholder constants survive formatting against the real harvest templates.
"""

from __future__ import annotations

from typing import Any

from chaoscypher_core.services.sources.engine.extraction.utils.ai_entities import (
    PROMPT_CHUNK_TEXT_PLACEHOLDER,
    PROMPT_PASS1_ENTITIES_PLACEHOLDER,
    _build_entity_prompt,
    _build_relationship_prompt,
)
from chaoscypher_core.services.sources.engine.extraction.utils.known_entities import (
    KNOWN_ENTITIES_HEADER,
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
        edge_templates_formatted="- works_at",
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
        edge_templates_formatted="- e",
        relationship_guidance="DOMAIN_GUIDANCE_MARKER",
        relationship_examples="DOMAIN_EXAMPLE_MARKER",
    )
    assert "DOMAIN_GUIDANCE_MARKER" in prompt
    assert "DOMAIN_EXAMPLE_MARKER" in prompt


def test_relationship_prompt_matches_inline_formatting_for_real_values() -> None:
    """The helper reproduces the prior inline formatting for real values."""
    from chaoscypher_core.services.sources.engine.extraction.utils.prompts import (
        render_relationship_format_example,
    )

    expected = RELATIONSHIP_HARVEST_TEMPLATE.format(
        numbered_sentences="1. Alice met Bob.",
        entity_list="0: Alice\n1: Bob",
        max_entity_index=1,
        edge_templates="- knows",
        relationship_format_example=render_relationship_format_example("- knows"),
    )
    prompt = _build_relationship_prompt(
        template=RELATIONSHIP_HARVEST_TEMPLATE,
        numbered_sentences="1. Alice met Bob.",
        entity_list="0: Alice\n1: Bob",
        max_entity_index=1,
        edge_templates_formatted="- knows",
        relationship_guidance=None,
        relationship_examples=None,
    )
    assert prompt == expected


def test_known_entities_block_renders_between_sentences_and_rules_or_not_at_all() -> None:
    """The block sits after the sentences; an empty block leaves no header and no blank slot."""
    common: dict[str, Any] = {
        "template": ENTITY_HARVEST_TEMPLATE,
        "numbered_text": "[S1] x",
        "node_templates_formatted": "- Person",
        "entity_exclusions": None,
        "strict_entity_types": False,
        "entity_guidance": None,
        "entity_examples": None,
    }
    block = f"{KNOWN_ENTITIES_HEADER}\n- Oswin Kell (Person)"
    without = _build_entity_prompt(**common)
    with_block = _build_entity_prompt(**common, known_entities=block)
    # The rules name KNOWN ENTITIES and the template has blank slots of its own
    # (an empty strict line, no exclusions): no block means no header and no slot of the block's.
    assert KNOWN_ENTITIES_HEADER not in without
    assert "</numbered_sentences>\n\nOUTPUT FORMAT:" in without
    assert (
        with_block.index("</numbered_sentences>")
        < with_block.index(KNOWN_ENTITIES_HEADER)
        < with_block.index("OUTPUT FORMAT:")
    )
    assert with_block.replace(block + "\n\n", "") == without


def test_the_recorded_pass_one_template_shows_the_known_entities_slot() -> None:
    """The job's template marks where the per-group block goes, and is no block itself (M2).

    The block is per group (group 1 has none), so the template, like the chunk text, shows a
    placeholder in its slot, wrapped as a block is; the MCP instructions keep the slot empty,
    since that path serves no block.
    """
    from chaoscypher_core.mcp.extraction import _render_extraction_instructions
    from chaoscypher_core.services.sources.engine.extraction.domains.registry import (
        DomainRegistry,
    )
    from chaoscypher_core.services.sources.engine.extraction.domains.stack import DomainStack
    from chaoscypher_core.services.sources.engine.extraction.extraction_plan import (
        build_extraction_plan,
    )
    from chaoscypher_core.services.sources.engine.extraction.utils.ai_entities import (
        PROMPT_KNOWN_ENTITIES_PLACEHOLDER,
        render_entity_pass,
        render_plan_templates,
    )
    from chaoscypher_core.settings import EngineSettings

    settings = EngineSettings()
    plan = build_extraction_plan(
        DomainStack.from_domain_name(DomainRegistry(), "generic"), settings
    )
    entity_template, relationship_template = render_plan_templates(plan.chunk_kwargs)
    assert KNOWN_ENTITIES_HEADER not in entity_template
    assert entity_template == render_entity_pass(
        plan.chunk_kwargs,
        numbered_text=PROMPT_CHUNK_TEXT_PLACEHOLDER,
        known_entities=PROMPT_KNOWN_ENTITIES_PLACEHOLDER,
    )
    assert (
        f"</numbered_sentences>\n\n{PROMPT_KNOWN_ENTITIES_PLACEHOLDER}\n\nOUTPUT FORMAT:"
        in entity_template
    )
    assert PROMPT_KNOWN_ENTITIES_PLACEHOLDER not in relationship_template  # pass 2 gets no block
    mcp_entity, _ = _render_extraction_instructions(settings, plan)
    assert PROMPT_KNOWN_ENTITIES_PLACEHOLDER not in mcp_entity
    assert mcp_entity == entity_template.replace(PROMPT_KNOWN_ENTITIES_PLACEHOLDER + "\n\n", "")


def test_the_templates_are_the_one_two_pass_renderer_with_placeholders(
    monkeypatch: Any,
) -> None:
    """``render_plan_templates`` is ``render_plan_prompts`` with placeholders, block slot included."""
    from chaoscypher_core.services.sources.engine.extraction.domains.registry import (
        DomainRegistry,
    )
    from chaoscypher_core.services.sources.engine.extraction.domains.stack import DomainStack
    from chaoscypher_core.services.sources.engine.extraction.extraction_plan import (
        build_extraction_plan,
    )
    from chaoscypher_core.services.sources.engine.extraction.utils import ai_entities
    from chaoscypher_core.settings import EngineSettings

    kwargs = build_extraction_plan(
        DomainStack.from_domain_name(DomainRegistry(), "generic"), EngineSettings()
    ).chunk_kwargs
    block = f"{KNOWN_ENTITIES_HEADER}\n- Oswin Kell (Person)"
    entity, _ = ai_entities.render_plan_prompts(
        kwargs, numbered_text="S1: x", entity_list="0: x", max_entity_index=0, known_entities=block
    )
    assert f"{block}\n\nOUTPUT FORMAT:" in entity
    calls: list[dict[str, Any]] = []
    real = ai_entities.render_plan_prompts

    def spy(*args: Any, **kw: Any) -> tuple[str, str]:
        calls.append(kw)
        return real(*args, **kw)

    monkeypatch.setattr(ai_entities, "render_plan_prompts", spy)
    templates = ai_entities.render_plan_templates(kwargs, known_entities="")
    assert [c["known_entities"] for c in calls] == [""]
    assert templates == real(
        kwargs,
        numbered_text=PROMPT_CHUNK_TEXT_PLACEHOLDER,
        entity_list=PROMPT_PASS1_ENTITIES_PLACEHOLDER,
        max_entity_index="N",
    )
