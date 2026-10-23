# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""The standalone path keeps the running list: each group is shown what the earlier ones named.

``AIEntityExtractor.extract_from_chunks`` builds one ``KnownEntities`` per
source (``KnownEntities.for_plan``), hands it to every group's
``extract_single_chunk`` and folds each successful group in afterwards (spec
2026-10-11). The LLM is scripted at ``call_llm``: pass 1 is answered per
group, pass 2 with no ``R|`` lines. The CLI and benchmark path is pinned in
``packages/cli/tests/unit/sources/test_extraction_path_parity.py``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from chaoscypher_core.services.sources.engine.extraction.domains.registry import DomainRegistry
from chaoscypher_core.services.sources.engine.extraction.utils import ai_entities
from chaoscypher_core.services.sources.engine.extraction.utils.ai_entities import (
    AIEntityExtractor,
)
from chaoscypher_core.services.sources.engine.extraction.utils.known_entities import (
    KNOWN_ENTITIES_HEADER,
)
from chaoscypher_core.settings import EngineSettings
from chaoscypher_core.testing.extraction_llm import ScriptedExtractionLLM


_CHUNKS = ["Count Ilyá Rostóv, the count, laughed.", "The count laughed."]
_PASS1_ANSWERS = (
    "E|Count Ilyá Rostóv|Person|the count|1.0|S1|A count.\n",
    "E|the count|Person||0.8|S1|The count laughs.\n",
)


@pytest.fixture
def fake_llm() -> ScriptedExtractionLLM:
    return ScriptedExtractionLLM(pass1_answers=_PASS1_ANSWERS)


def _extractor(settings: EngineSettings, llm: ScriptedExtractionLLM) -> AIEntityExtractor:
    extractor = AIEntityExtractor(settings=settings)
    extractor.call_llm = llm  # type: ignore[method-assign]
    return extractor


@pytest.mark.asyncio
async def test_group_two_is_shown_what_group_one_named_and_group_one_sees_no_block(
    fake_llm: ScriptedExtractionLLM,
) -> None:
    extractor = _extractor(EngineSettings(), fake_llm)
    result = await extractor.extract_from_chunks(list(_CHUNKS))
    prompts = fake_llm.pass1_prompts
    assert len(prompts) == 2
    assert KNOWN_ENTITIES_HEADER not in prompts[0]
    assert f"{KNOWN_ENTITIES_HEADER}\n- Count Ilyá Rostóv (Person) — also: the count" in prompts[1]
    # Group 2's "the count" is a reference: never listed, so one entity across two groups.
    assert result["known_entities"] == {"groups": 2, "entities": 1}


@pytest.mark.asyncio
async def test_limit_zero_renders_no_block(fake_llm: ScriptedExtractionLLM) -> None:
    settings = EngineSettings()
    settings.extraction.known_entities_limit = 0
    extractor = _extractor(settings, fake_llm)
    result = await extractor.extract_from_chunks(list(_CHUNKS))
    assert len(fake_llm.pass1_prompts) == 2
    assert all(KNOWN_ENTITIES_HEADER not in p for p in fake_llm.pass1_prompts)
    assert result["known_entities"] == {"groups": 2, "entities": 0}


@pytest.mark.asyncio
async def test_a_failed_group_adds_nothing_and_the_next_group_still_runs() -> None:
    llm = ScriptedExtractionLLM(
        pass1_answers=(
            _PASS1_ANSWERS[0],
            "E|Pierre Bezúkhov|Person||1.0|S1|A man who laughs.\n",
            _PASS1_ANSWERS[1],
        ),
        pass2_failing=frozenset({1}),
    )
    extractor = _extractor(EngineSettings(), llm)
    chunks = [_CHUNKS[0], "Pierre Bezúkhov laughed.", _CHUNKS[1]]
    result = await extractor.extract_from_chunks(chunks)
    assert len(llm.pass1_prompts) == 3
    assert len(llm.pass2_prompts) == 3  # group 2 named Pierre, then its pass 2 failed
    assert "- Count Ilyá Rostóv (Person)" in llm.pass1_prompts[2]
    assert "Pierre" not in llm.pass1_prompts[2]
    assert [e["name"] for e in result["entities"]] == ["Count Ilyá Rostóv", "the count"]
    assert result["known_entities"] == {"groups": 2, "entities": 1}


async def _group_two_prompt(domain_name: str) -> tuple[str, dict[str, Any]]:
    """Group 2's pass-1 prompt and the result, extracting under the ``domain_name`` plan."""
    registry = DomainRegistry()
    domain = registry.get_domain(domain_name)
    packs = () if domain_name == "generic" else (domain_name,)
    llm = ScriptedExtractionLLM(
        pass1_answers=(
            "E|Dowager|Person||1.0|S1|A dowager who laughs.\n"
            "E|Pierre Bezúkhov|Person||1.0|S2|A man who weeps.\n",
            "E|Pierre Bezúkhov|Person||1.0|S1|A man who laughs.\n",
        )
    )
    extractor = _extractor(EngineSettings(), llm)
    with (
        patch.object(ai_entities, "detect_domain", return_value=(domain, 1.0, packs)),
        patch(
            "chaoscypher_core.services.sources.engine.extraction.domains.get_domain_registry",
            return_value=registry,
        ),
    ):
        result = await extractor.extract_from_chunks(
            ["Dowager laughed. Pierre Bezúkhov wept.", "Pierre Bezúkhov laughed."]
        )
    assert len(llm.pass1_prompts) == 2
    return llm.pass1_prompts[1], result


@pytest.mark.asyncio
async def test_the_list_reads_labels_by_the_plans_conventions() -> None:
    """A bare "Dowager" is a reference under the literary pack, a name under the floor.

    "Dowager" is a title only the literary pack declares (its ``title_words``),
    not a universal one, so the contrast is the plan's conventions alone.
    """
    literary, literary_result = await _group_two_prompt("literary")
    assert "- Pierre Bezúkhov (Person)" in literary
    assert "- Dowager (Person)" not in literary
    assert literary_result["known_entities"] == {"groups": 2, "entities": 1}

    generic, generic_result = await _group_two_prompt("generic")
    assert "- Dowager (Person)" in generic
    assert generic_result["known_entities"] == {"groups": 2, "entities": 2}
