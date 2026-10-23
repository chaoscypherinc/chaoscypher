# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""The relationship pass is loop-checked against pass 1's entity count.

Regression (section-audit 2026-10-12): ``_StreamLoopDetector`` gates every
``R|`` check on ``entity_count > 0`` and counts only the ``E|`` lines of its
own stream; ``call_llm`` builds a fresh detector per call, and pass 2
streams ``R|`` lines only — so a runaway relationship pass (500 copies of
one pair) was never cut. The detector now starts from the known entity
count, and pass 2 hands it ``len(entities)``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from chaoscypher_core.services.sources.engine.extraction.utils.ai_entities import (
    AIEntityExtractor,
    CallLLMResult,
    _StreamLoopDetector,
    replay_harvest_output,
)
from chaoscypher_core.settings import ExtractionSettings


RUNAWAY_PASS2 = "".join("R|0|1|knows|0.9|S1|x\n" for _ in range(500))


@pytest.mark.asyncio
async def test_pass2_replay_with_known_entities_is_cut_like_a_stream() -> None:
    cfg = ExtractionSettings()
    content, finish, aborted = await replay_harvest_output(RUNAWAY_PASS2, cfg, known_entity_count=2)
    assert aborted is True and finish == "unknown"
    assert content.count("\n") < 500


@pytest.mark.asyncio
async def test_pass2_replay_without_known_entities_was_never_checked() -> None:
    """The shape of the defect: with no known entities, every R| check is skipped."""
    content, finish, aborted = await replay_harvest_output(RUNAWAY_PASS2, ExtractionSettings())
    assert aborted is False and finish == "stop"
    assert content.count("\n") == 500


def test_detector_starts_from_the_known_count() -> None:
    detector = _StreamLoopDetector(ExtractionSettings(), known_entity_count=3)
    assert detector.entity_count == 3
    # An out-of-bounds index is now seen as such.
    assert detector.check_line("R|0|7|knows|0.9|S1|x", 0) is False
    assert detector._oob_total == 1


@pytest.mark.asyncio
async def test_extract_relationships_hands_pass1_count_to_call_llm() -> None:
    extractor = AIEntityExtractor.__new__(AIEntityExtractor)
    captured: dict[str, Any] = {}

    async def _fake_call_llm(prompt: str, **kwargs: Any) -> CallLLMResult:
        captured.update(kwargs)
        return CallLLMResult(
            content="", input_tokens=0, output_tokens=0, finish_reason="stop", aborted_by_loop=False
        )

    extractor.call_llm = _fake_call_llm  # type: ignore[method-assign]
    extractor.settings = MagicMock()
    from chaoscypher_core.services.sources.engine.extraction.utils.filtering_log import (
        FilteringLog,
    )

    entities = [
        {"name": "Alice", "type": "Person", "aliases": []},
        {"name": "Bob", "type": "Person", "aliases": []},
        {"name": "Moscow", "type": "Location", "aliases": []},
    ]
    await extractor._extract_relationships(
        entities=entities,
        numbered_text="S1: Alice met Bob in Moscow.",
        sentences=["Alice met Bob in Moscow."],
        prompt_inputs={
            "edge_templates_formatted": "",
            "relationship_guidance": None,
            "relationship_examples": None,
        },
        extraction_cfg=MagicMock(loop_max_out_of_bounds=10, loop_max_source_type_repeat=5),
        filtering_config=MagicMock(
            enable_type_constraints=False,
            enable_evidence_filter=False,
            enable_implausible_filter=False,
            enable_relationship_evidence_filter=False,
        ),
        evidence_stats={},
        filtering_log=FilteringLog(),
        chunk_content="Alice met Bob in Moscow.",
        _max_entity_count_override=None,
        _min_alias_len=2,
    )
    assert captured["known_entity_count"] == len(entities)
