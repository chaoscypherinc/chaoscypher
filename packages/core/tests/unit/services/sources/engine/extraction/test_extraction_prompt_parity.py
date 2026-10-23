# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Extraction sends the bytes the shared renderer renders, for every registered domain's plan.

``render_entity_pass`` / ``render_relationship_pass`` are the one mapping of a
plan's prompt inputs onto the prompt builders. The MCP instructions, the
recipe digest and the prompt budget render through them; this pins that
``extract_single_chunk`` (what a local model is actually sent, and the
templates its metrics record) does too.
"""

from __future__ import annotations

import pytest

from chaoscypher_core.services.sources.engine.extraction.domains.registry import DomainRegistry
from chaoscypher_core.services.sources.engine.extraction.domains.stack import DomainStack
from chaoscypher_core.services.sources.engine.extraction.extraction_plan import (
    CHUNK_KWARG_NAMES,
    build_extraction_plan,
)
from chaoscypher_core.services.sources.engine.extraction.utils.ai_entities import (
    AIEntityExtractor,
    render_plan_templates,
)
from chaoscypher_core.settings import EngineSettings
from chaoscypher_core.testing.extraction_llm import ScriptedExtractionLLM


_REGISTRY = DomainRegistry()

CHUNK = (
    "Captain Orsolya Darnell walked the harbour wall of Vellmarsh at dusk. "
    "The lamplighter Bram Darnell was her father."
)
PASS1 = (
    "E|Orsolya Darnell|Person|Orsolya; Captain Orsolya|1.0|S1|Harbour captain of Vellmarsh. "
    "She walks the harbour wall at dusk.\n"
    "P|0|title|Captain\n"
    "E|Vellmarsh|Location||1.0|S1|Walled harbour town. Its harbour wall faces the sea.\n"
    "E|Bram Darnell|Person||1.0|S2|Lamplighter of Vellmarsh. Father of Orsolya Darnell.\n"
)
PASS2 = (
    "R|0|1|located_in|1.0|S1|Captain Orsolya Darnell walked the harbour wall of Vellmarsh.\n"
    "R|2|0|parent_of|1.0|S2|The lamplighter Bram Darnell was her father.\n"
)


@pytest.mark.asyncio
@pytest.mark.parametrize("name", _REGISTRY.names())
async def test_extraction_sends_the_shared_renderers_bytes(name: str) -> None:
    """Both passes sent, and both templates recorded, are the shared renderer's for the plan."""
    settings = EngineSettings()
    kwargs = build_extraction_plan(
        DomainStack.from_domain_name(_REGISTRY, name), settings
    ).chunk_kwargs
    assert set(kwargs) == CHUNK_KWARG_NAMES
    extractor = AIEntityExtractor(settings=settings)
    llm = ScriptedExtractionLLM(
        pass1_answers=[PASS1], pass2_answers=[PASS2], input_tokens=10, output_tokens=5
    )
    sent = llm.prompts
    extractor.call_llm = llm  # type: ignore[method-assign]
    entities, _rels, _i, _o, metrics = await extractor.extract_single_chunk(
        chunk_content=CHUNK, **kwargs
    )

    assert entities, name  # pass 2 ran
    expected = extractor.render_chunk_prompts(kwargs, CHUNK, entities)
    assert sent == [expected.entity_prompt, expected.relationship_prompt]
    recorded = metrics["_prompt_data"]
    assert recorded["system_prompt"] == expected.system_prompt
    assert (recorded["user_instructions"], recorded["relationship_instructions"]) == (
        render_plan_templates(kwargs)
    )
