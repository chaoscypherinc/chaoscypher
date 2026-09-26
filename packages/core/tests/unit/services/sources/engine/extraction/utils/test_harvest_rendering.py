# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The harvest prompts and post-LLM parsing, usable without calling an LLM.

``extract_single_chunk`` was split so a caller that gets the model's answers
some other way (the MCP benchmark) renders the same prompts and parses the
answers the same way. These tests pin that the pieces reproduce the LLM path
for a fixed fake answer.
"""

from __future__ import annotations

from typing import Any

import pytest

from chaoscypher_core.services.sources.engine.extraction.utils.ai_entities import (
    AIEntityExtractor,
    CallLLMResult,
    combine_finish_reasons,
    format_raw_harvest_response,
    replay_harvest_output,
)
from chaoscypher_core.settings import EngineSettings, ExtractionSettings


CHUNK = (
    "Kutuzov commanded the Russian army in 1812. "
    "Bagration led the Second Army under Kutuzov. "
    "The rain did not stop."
)
PASS1 = (
    "E|Kutuzov|Character|Mikhail Kutuzov|1.0|S1|Kutuzov commanded the Russian army "
    "during the campaign of 1812 against the French.\n"
    "P|0|rank|Field Marshal\n"
    "E|Bagration|Character||1.0|S2|Bagration led the Second Army of the Russians "
    "under the command of Kutuzov.\n"
    "not a format line\n"
)
PASS2 = (
    "R|1|0|serves_under|1.0|S2|Bagration led the Second Army under Kutuzov.\n"
    "R|0|7|commands|1.0|S1|Out-of-range index.\n"
)
KW: dict[str, Any] = {
    "node_templates_formatted": "- Character\n- Location",
    "edge_templates_formatted": "- serves_under\n- commands",
    "entity_examples": "EXAMPLES",
    "relationship_examples": "REL EXAMPLES",
}


async def _run_llm_path() -> tuple[AIEntityExtractor, list[str], tuple[Any, ...]]:
    """Run extract_single_chunk with canned answers; return prompts it sent."""
    extractor = AIEntityExtractor(settings=EngineSettings())
    prompts: list[str] = []
    answers = iter([PASS1, PASS2])

    async def _fake_call_llm(prompt: str, **_: Any) -> CallLLMResult:
        """Record the prompt and return the next canned answer."""
        prompts.append(prompt)
        return CallLLMResult(
            content=next(answers),
            input_tokens=10,
            output_tokens=5,
            finish_reason="stop",
            aborted_by_loop=False,
        )

    extractor.call_llm = _fake_call_llm  # type: ignore[method-assign]
    out = await extractor.extract_single_chunk(chunk_content=CHUNK, **KW)
    return extractor, prompts, out


@pytest.mark.asyncio
async def test_rendered_prompts_are_the_prompts_extraction_sends() -> None:
    """render_harvest_prompts + render_relationship_prompt == what call_llm got."""
    extractor, sent, (entities, _rels, _i, _o, metrics) = await _run_llm_path()
    prompts = extractor.render_harvest_prompts(
        CHUNK,
        KW["node_templates_formatted"],
        entity_examples=KW["entity_examples"],
    )
    assert sent[0] == prompts.entity_prompt
    assert prompts.system_prompt == metrics["_prompt_data"]["system_prompt"]
    assert prompts.sentences == metrics["sentences"]
    rel_prompt = extractor.render_relationship_prompt(
        entities,
        prompts.numbered_text,
        KW["edge_templates_formatted"],
        relationship_examples=KW["relationship_examples"],
    )
    assert sent[1] == rel_prompt
    assert "0: Kutuzov (Character) [aliases: Mikhail Kutuzov]" in rel_prompt
    template = extractor.render_relationship_prompt_template(
        KW["edge_templates_formatted"], relationship_examples=KW["relationship_examples"]
    )
    assert template == metrics["_prompt_data"]["relationship_instructions"]


@pytest.mark.asyncio
async def test_parse_harvest_outputs_reproduces_the_llm_path() -> None:
    """Same two answers in, same entities/relationships/counters out."""
    extractor, _sent, (entities, relationships, _i, _o, metrics) = await _run_llm_path()
    prompts = extractor.render_harvest_prompts(CHUNK, KW["node_templates_formatted"])
    parsed = await extractor.parse_harvest_outputs(
        PASS1,
        PASS2,
        sentences=prompts.sentences,
        chunk_content=CHUNK,
        numbered_text=prompts.numbered_text,
        edge_templates_formatted=KW["edge_templates_formatted"],
        relationship_examples=KW["relationship_examples"],
    )
    assert parsed.entities == entities
    assert parsed.relationships == relationships
    assert parsed.parser_lines_dropped == metrics["parser_lines_dropped"]
    assert parsed.invalid_relationship_count == metrics["invalid_relationship_count"]
    assert parsed.invalid_relationship_count == 1  # the out-of-range R| line
    assert len(entities) == 2 and len(relationships) == 1
    assert format_raw_harvest_response(PASS1, PASS2) == metrics["raw_llm_response"]


@pytest.mark.asyncio
async def test_parse_harvest_outputs_skips_pass_two_without_entities() -> None:
    """The pipeline never runs pass 2 on an empty entity list; neither does parsing."""
    extractor = AIEntityExtractor(settings=EngineSettings())
    prompts = extractor.render_harvest_prompts(CHUNK, KW["node_templates_formatted"])
    parsed = await extractor.parse_harvest_outputs(
        "",
        PASS2,
        sentences=prompts.sentences,
        chunk_content=CHUNK,
        numbered_text=prompts.numbered_text,
        edge_templates_formatted=KW["edge_templates_formatted"],
    )
    assert parsed.entities == [] and parsed.relationships == []
    assert parsed.relationship_prompt is None


@pytest.mark.asyncio
async def test_replay_passes_a_clean_answer_through_unchanged() -> None:
    """A normal answer completes with finish_reason stop."""
    content, finish, aborted = await replay_harvest_output(PASS1, ExtractionSettings())
    assert (content, finish, aborted) == (PASS1, "stop", False)


@pytest.mark.asyncio
async def test_replay_cuts_a_runaway_answer_where_the_stream_would() -> None:
    """The loop detector's entity cap aborts the replay like a live stream."""
    cfg = ExtractionSettings()
    runaway = "".join(f"E|N{i}|Character||1.0|S1|d\n" for i in range(500))
    content, finish, aborted = await replay_harvest_output(runaway, cfg, 10)
    assert aborted is True and finish == "unknown"
    assert content.count("\n") == 10  # up to and including the line that tripped it


def test_combine_finish_reasons() -> None:
    """Length wins; otherwise pass 1's non-stop reason, else pass 2's."""
    assert combine_finish_reasons("stop", "length") == "length"
    assert combine_finish_reasons("unknown", "stop") == "unknown"
    assert combine_finish_reasons("stop", "stop") == "stop"
    assert combine_finish_reasons("stop", "unknown") == "unknown"
