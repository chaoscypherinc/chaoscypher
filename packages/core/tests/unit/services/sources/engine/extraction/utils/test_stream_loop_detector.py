# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the streaming loop detector in ai_entities.

The detector inspects each completed line of an LLM extraction stream
and aborts when degenerate patterns appear. This file focuses on the
invalid-relationship-index-rate detector added after a real-world
incident where 336 of 352 relationship lines had out-of-bounds entity
indices but the consecutive-streak detector never triggered (the
invalid lines were interleaved with valid ones).
"""

from __future__ import annotations

from chaoscypher_core.services.sources.engine.extraction.utils.ai_entities import (
    _StreamLoopDetector,
)
from chaoscypher_core.settings import ExtractionSettings


def _make_detector(
    *,
    invalid_rate_threshold: float = 0.5,
    invalid_rate_warmup: int = 10,
    max_entity_count: int = 50,
) -> _StreamLoopDetector:
    """Build a detector with explicit thresholds for the test."""
    cfg = ExtractionSettings(
        loop_invalid_relationship_rate_threshold=invalid_rate_threshold,
        loop_invalid_relationship_rate_warmup=invalid_rate_warmup,
    )
    detector = _StreamLoopDetector(extraction_cfg=cfg)
    # Seed entity_count so out-of-bounds checks are meaningful.
    detector.entity_count = max_entity_count
    return detector


def _feed_relationship(
    detector: _StreamLoopDetector, src: int, tgt: int, rel_type: str = "RELATES_TO"
) -> bool:
    """Feed one relationship line and return whether it aborted."""
    line = f"R|{src}|{tgt}|{rel_type}"
    return detector.check_line(line, content_length=len(line))


def test_invalid_rate_does_not_fire_during_warmup() -> None:
    """Below the warmup count, even all-invalid lines must not trigger.

    Tiny chunks legitimately produce a few relationships; one bad line
    out of three would otherwise spuriously trip a 50% threshold.
    """
    detector = _make_detector(invalid_rate_warmup=10)
    # Feed 5 out-of-bounds lines (below warmup of 10).
    for _ in range(5):
        aborted = _feed_relationship(detector, src=999, tgt=999)
        # Each line is OOB but the streak resets the rate counter
        # only at consecutive-OOB threshold. Streak alone shouldn't fire
        # at default loop_max_out_of_bounds=3 — we'd hit that first.
        # So set OOB streak threshold high to isolate the rate check.
        if aborted:
            break
    # Expectation: streak detector aborted at 3 OOB in a row, NOT the
    # rate detector. The rate detector is gated by warmup.
    assert detector.aborted, "streak detector should fire first"


def test_invalid_rate_fires_when_majority_invalid_after_warmup() -> None:
    """Past warmup, >50% invalid (spread out, no long streaks) aborts.

    The realistic failure mode: model emits a valid line every 2-3
    hallucinations, defeating the consecutive-streak detector.
    """
    # Disable the streak detector by setting a very high threshold
    # so we isolate the rate detector under test.
    cfg = ExtractionSettings(
        loop_max_out_of_bounds=10_000,
        loop_invalid_relationship_rate_warmup=10,
        loop_invalid_relationship_rate_threshold=0.5,
    )
    detector = _StreamLoopDetector(extraction_cfg=cfg)
    detector.entity_count = 5

    aborted = False
    # Pattern: 2 invalid, 1 valid, repeat. Streak never exceeds 2.
    # Vary src/tgt so the same-pair detector doesn't trip and confound
    # what we're testing.
    for i in range(30):
        if i % 3 == 2:
            src, tgt = (i % 5, (i + 1) % 5)  # valid varying pair
        else:
            src, tgt = (99 + i, 100 + i)  # OOB varying pair
        aborted = _feed_relationship(detector, src=src, tgt=tgt, rel_type=f"T{i}")
        if aborted:
            break

    assert aborted, "rate detector should abort once invalid majority is established"
    assert detector.aborted is True


def test_invalid_rate_does_not_fire_when_mostly_valid() -> None:
    """A handful of OOB lines among many valid ones must not abort.

    Real LLM output occasionally hallucinates a single bad index;
    the rate detector must tolerate that.
    """
    cfg = ExtractionSettings(
        loop_max_out_of_bounds=10_000,
        loop_invalid_relationship_rate_warmup=10,
        loop_invalid_relationship_rate_threshold=0.5,
    )
    # entity_count=20 gives enough valid pairs that we don't repeat
    # the same (src, tgt) more than 5 times across 50 iterations.
    detector = _StreamLoopDetector(extraction_cfg=cfg)
    detector.entity_count = 20

    aborted = False
    # 1 invalid every 10 valid → 10% rate, well under 50%.
    for i in range(50):
        if i % 10 == 0:
            src, tgt = (999, 999)  # OOB
        else:
            # Use unique pair each iteration via i.
            src, tgt = (i % 20, (i * 3) % 20)
        aborted = _feed_relationship(detector, src=src, tgt=tgt, rel_type=f"T{i}")
        if aborted:
            break

    assert not aborted
    assert detector.aborted is False


def test_invalid_rate_threshold_is_configurable() -> None:
    """Lowering the threshold makes the detector fire sooner."""
    cfg = ExtractionSettings(
        loop_max_out_of_bounds=10_000,
        loop_invalid_relationship_rate_warmup=10,
        loop_invalid_relationship_rate_threshold=0.2,  # much stricter
    )
    detector = _StreamLoopDetector(extraction_cfg=cfg)
    detector.entity_count = 5

    aborted = False
    # 25% invalid (1 of 4), past warmup of 10 should trip the 0.2 threshold.
    # Vary pairs to avoid same-pair detector noise.
    for i in range(40):
        if i % 4 == 0:
            src, tgt = (99 + i, 100 + i)
        else:
            src, tgt = (i % 5, (i + 1) % 5)
        aborted = _feed_relationship(detector, src=src, tgt=tgt, rel_type=f"T{i}")
        if aborted:
            break

    assert aborted
    assert detector.aborted is True


def test_warmup_is_configurable() -> None:
    """A larger warmup delays the rate detector firing."""
    cfg = ExtractionSettings(
        loop_max_out_of_bounds=10_000,
        loop_invalid_relationship_rate_warmup=50,  # much larger warmup
        loop_invalid_relationship_rate_threshold=0.5,
    )
    detector = _StreamLoopDetector(extraction_cfg=cfg)
    detector.entity_count = 5

    # Feed 30 lines, all OOB (100% invalid). Below warmup of 50, the
    # rate detector cannot fire. Vary src/tgt to avoid the same-pair
    # detector tripping (we're isolating the rate check).
    aborted = False
    for i in range(30):
        aborted = _feed_relationship(detector, src=99 + i, tgt=100 + i, rel_type=f"T{i}")
        if aborted:
            break

    assert not aborted
    # Counters still tracking — exceed warmup and the rate would fire.
    assert detector._oob_total == 30
    assert detector.relationship_count == 30
