# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression tests for Phase 3 Task 3: magic numbers lifted to Pydantic settings.

Each test asserts that the consumer reads the configured value rather than a
hardcoded literal. All defaults match the previous literals so behaviour is
unchanged at default settings — these tests verify the wiring only.

Covered settings:
  ExtractionSettings.loop_max_relationship_multiplier  (was * 4)
  ExtractionSettings.loop_max_same_pair               (was = 6)
  ExtractionSettings.empty_output_retry_min_chars     (was 200)
  ExtractionSettings.dedup_type_partition_cutoff      (was 50)
  ExtractionSettings.dedup_no_overlap_boost           (was 0.08)
  ExtractionSettings.dedup_borderline_penalty         (was 0.05)
  NormalizerSettings.ftfy_fix_character_width         (was True)
  NormalizerSettings.ftfy_fix_line_breaks             (was True)
  NormalizerSettings.ocr_page_artifact_min_repeats    (was 3 via > 2)
  NormalizerSettings.ocr_page_artifact_max_line_length  (was 30)
  NormalizerSettings.ocr_page_artifact_candidate_max_length (was 50)
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from chaoscypher_core.services.sources.engine.deduplication.service import EntityProcessor
from chaoscypher_core.services.sources.engine.extraction.utils.ai_entities import (
    _StreamLoopDetector,
)
from chaoscypher_core.services.sources.normalizer.cleaners.ocr_cleaner import OCRCleaner
from chaoscypher_core.services.sources.normalizer.cleaners.text_cleaner import TextCleaner
from chaoscypher_core.settings import ExtractionSettings, NormalizerSettings


# ---------------------------------------------------------------------------
# _StreamLoopDetector: loop_max_relationship_multiplier
# ---------------------------------------------------------------------------


def test_loop_max_relationship_multiplier_default_is_four() -> None:
    """Default multiplier of 4.0 produces max_relationship_count = entity_count * 4."""
    cfg = ExtractionSettings()  # default multiplier = 4.0
    detector = _StreamLoopDetector(extraction_cfg=cfg)
    detector.entity_count = 10
    # Patch max_entity_count to 10 to control the base
    detector._max_entity_count = 10  # type: ignore[misc]
    # Re-derive: multiplier is applied at construction; we check the stored value
    cfg2 = ExtractionSettings(loop_max_relationship_multiplier=4.0)
    det2 = _StreamLoopDetector(extraction_cfg=cfg2)
    det2._max_entity_count = 10  # type: ignore[misc]
    # The actual _max_relationship_count was computed from max_entity_count at
    # __init__ time, so we construct a fresh detector with known entity count.
    cfg3 = ExtractionSettings(loop_max_entity_count=10, loop_max_relationship_multiplier=4.0)
    det3 = _StreamLoopDetector(extraction_cfg=cfg3)
    assert det3._max_relationship_count == 40  # 10 * 4.0


def test_loop_max_relationship_multiplier_custom_value() -> None:
    """Custom multiplier of 2.5 produces max_relationship_count = entity_count * 2.5."""
    cfg = ExtractionSettings(loop_max_entity_count=10, loop_max_relationship_multiplier=2.5)
    detector = _StreamLoopDetector(extraction_cfg=cfg)
    assert detector._max_relationship_count == 25  # int(10 * 2.5)


def test_loop_max_relationship_multiplier_domain_override() -> None:
    """Domain override for max_entity_count propagates to max_relationship_count."""
    # loop_max_entity_count has ge=10 constraint; use 10 as the settings base.
    cfg = ExtractionSettings(loop_max_entity_count=10, loop_max_relationship_multiplier=3.0)
    # max_entity_count_override=20 overrides the entity count but multiplier stays
    detector = _StreamLoopDetector(extraction_cfg=cfg, max_entity_count_override=20)
    assert detector._max_entity_count == 20
    assert detector._max_relationship_count == int(20 * 3.0)  # 60


# ---------------------------------------------------------------------------
# _StreamLoopDetector: loop_max_same_pair
# ---------------------------------------------------------------------------


def test_loop_max_same_pair_default_is_six() -> None:
    """Default loop_max_same_pair of 6 is stored on the detector."""
    cfg = ExtractionSettings()  # default = 6
    detector = _StreamLoopDetector(extraction_cfg=cfg)
    assert detector._max_same_pair == 6


def test_loop_max_same_pair_custom_value() -> None:
    """Custom loop_max_same_pair is wired through to the detector."""
    cfg = ExtractionSettings(loop_max_same_pair=3)
    detector = _StreamLoopDetector(extraction_cfg=cfg)
    assert detector._max_same_pair == 3


def test_loop_max_same_pair_triggers_abort() -> None:
    """With loop_max_same_pair=2, abort fires on the 2nd identical (src,tgt) pair.

    The check is `pair_count >= max_same_pair`, so a limit of 2 means the
    abort fires when the pair appears for the second time.
    """
    cfg = ExtractionSettings(loop_max_entity_count=10, loop_max_same_pair=2)
    detector = _StreamLoopDetector(extraction_cfg=cfg)
    detector.entity_count = 10

    def feed(src: int, tgt: int) -> bool:
        line = f"R|{src}|{tgt}|RELATES_TO"
        return detector.check_line(line, content_length=len(line))

    # First occurrence: no abort yet
    assert not feed(0, 1)
    # Second occurrence: pair_count == max_same_pair == 2, abort fires
    aborted = feed(0, 1)
    assert aborted
    assert detector.aborted


# ---------------------------------------------------------------------------
# EntityProcessor: dedup_type_partition_cutoff
# ---------------------------------------------------------------------------


def test_dedup_type_partition_cutoff_stored() -> None:
    """Custom cutoff value is stored on the EntityProcessor instance."""
    ep = EntityProcessor(dedup_type_partition_cutoff=25)
    assert ep._dedup_type_partition_cutoff == 25  # type: ignore[misc]


def test_dedup_type_partition_cutoff_default_is_fifty() -> None:
    """Default dedup_type_partition_cutoff is 50."""
    ep = EntityProcessor()
    assert ep._dedup_type_partition_cutoff == 50  # type: ignore[misc]


def test_dedup_type_partition_uses_cutoff_for_partitioning() -> None:
    """With cutoff=5 and 6 entities, _build_type_groups is called (partitioned path)."""
    ep = EntityProcessor(dedup_type_partition_cutoff=5)

    # 6 entities exceed the cutoff of 5
    entities = [{"name": f"Entity{i}", "type": "Person", "description": ""} for i in range(6)]

    with patch.object(ep, "_build_type_groups", wraps=ep._build_type_groups) as mock_btg:
        # Calling _find_semantic_duplicates requires embeddings; stub them.
        import numpy as np

        embeddings = [[float(i), 0.0] for i in range(6)]
        emb_np = np.array(embeddings, dtype=float)
        # normalise rows manually
        norms = np.linalg.norm(emb_np, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        emb_norm = emb_np / norms

        ep._find_semantic_duplicates(  # type: ignore[misc]
            entities=entities,
            embeddings=embeddings,
            embeddings_normalized=emb_norm,
            similarity_threshold=0.95,
            require_type_compatibility=True,
        )

    mock_btg.assert_called_once()


# ---------------------------------------------------------------------------
# EntityProcessor: dedup_no_overlap_boost
# ---------------------------------------------------------------------------


def test_dedup_no_overlap_boost_stored() -> None:
    """Custom boost value is stored on the EntityProcessor instance."""
    ep = EntityProcessor(dedup_no_overlap_boost=0.15)
    assert ep._dedup_no_overlap_boost == pytest.approx(0.15)  # type: ignore[misc]


def test_dedup_no_overlap_boost_default_is_point_zero_eight() -> None:
    """Default dedup_no_overlap_boost is 0.08."""
    ep = EntityProcessor()
    assert ep._dedup_no_overlap_boost == pytest.approx(0.08)  # type: ignore[misc]


# ---------------------------------------------------------------------------
# EntityProcessor: dedup_borderline_penalty
# ---------------------------------------------------------------------------


def test_dedup_borderline_penalty_stored() -> None:
    """Custom penalty value is stored on the EntityProcessor instance."""
    ep = EntityProcessor(dedup_borderline_penalty=0.10)
    assert ep._dedup_borderline_penalty == pytest.approx(0.10)  # type: ignore[misc]


def test_dedup_borderline_penalty_default_is_point_zero_five() -> None:
    """Default dedup_borderline_penalty is 0.05."""
    ep = EntityProcessor()
    assert ep._dedup_borderline_penalty == pytest.approx(0.05)  # type: ignore[misc]


# ---------------------------------------------------------------------------
# TextCleaner: ftfy_fix_character_width / ftfy_fix_line_breaks
# ---------------------------------------------------------------------------


def test_ftfy_fix_character_width_default_is_true() -> None:
    """Default ftfy_fix_character_width=True is passed to ftfy.fix_text."""
    settings = NormalizerSettings()
    assert settings.ftfy_fix_character_width is True


def test_ftfy_fix_line_breaks_default_is_true() -> None:
    """Default ftfy_fix_line_breaks=True is passed to ftfy.fix_text."""
    settings = NormalizerSettings()
    assert settings.ftfy_fix_line_breaks is True


def test_text_cleaner_passes_ftfy_options_from_settings() -> None:
    """TextCleaner passes ftfy options from NormalizerSettings to ftfy.fix_text."""
    settings = NormalizerSettings(
        ftfy_fix_character_width=False,
        ftfy_fix_line_breaks=False,
    )
    cleaner = TextCleaner(settings)

    captured_kwargs: dict = {}

    def fake_fix_text(text: str, **kwargs: object) -> str:
        captured_kwargs.update(kwargs)
        return text

    with patch("ftfy.fix_text", side_effect=fake_fix_text):
        cleaner._fix_encoding("hello")

    assert captured_kwargs.get("fix_character_width") is False
    assert captured_kwargs.get("fix_line_breaks") is False


def test_text_cleaner_passes_ftfy_default_options() -> None:
    """TextCleaner passes ftfy default options (True/True) from NormalizerSettings."""
    settings = NormalizerSettings()  # defaults: True, True
    cleaner = TextCleaner(settings)

    captured_kwargs: dict = {}

    def fake_fix_text(text: str, **kwargs: object) -> str:
        captured_kwargs.update(kwargs)
        return text

    with patch("ftfy.fix_text", side_effect=fake_fix_text):
        cleaner._fix_encoding("hello")

    assert captured_kwargs.get("fix_character_width") is True
    assert captured_kwargs.get("fix_line_breaks") is True


# ---------------------------------------------------------------------------
# OCRCleaner: page-artifact thresholds
# ---------------------------------------------------------------------------


def test_ocr_page_artifact_min_repeats_default_is_three() -> None:
    """Default ocr_page_artifact_min_repeats=3 matches the previous `> 2` literal."""
    settings = NormalizerSettings()
    assert settings.ocr_page_artifact_min_repeats == 3


def test_ocr_page_artifact_max_line_length_default_is_thirty() -> None:
    """Default ocr_page_artifact_max_line_length=30 matches the previous literal."""
    settings = NormalizerSettings()
    assert settings.ocr_page_artifact_max_line_length == 30


def test_ocr_page_artifact_candidate_max_length_default_is_fifty() -> None:
    """Default ocr_page_artifact_candidate_max_length=50 matches the previous literal."""
    settings = NormalizerSettings()
    assert settings.ocr_page_artifact_candidate_max_length == 50


def test_ocr_cleaner_uses_min_repeats_setting() -> None:
    """OCRCleaner uses ocr_page_artifact_min_repeats to detect artifacts.

    With min_repeats=4, a line appearing 3 times should NOT be treated as an
    artifact (contrast with the default of 3 where it would be).
    """
    # Line shorter than max_line_length=30, longer than candidate threshold allows
    artifact_candidate = "Header Line"  # length=11, appears 3 times

    text = "\n".join(
        ["Intro paragraph with real content."]
        + [artifact_candidate] * 3
        + ["More real content here."]
    )

    # Default settings: min_repeats=3, so "Header Line" IS an artifact
    default_settings = NormalizerSettings()
    default_cleaner = OCRCleaner(default_settings)
    _, default_removed = default_cleaner._remove_page_artifacts(text)  # type: ignore[misc]
    assert default_removed == 3, "default settings: 3 repeats should be treated as artifact"

    # min_repeats=4: "Header Line" appears 3 times, below new threshold → not removed
    lenient_settings = NormalizerSettings(ocr_page_artifact_min_repeats=4)
    lenient_cleaner = OCRCleaner(lenient_settings)
    _, lenient_removed = lenient_cleaner._remove_page_artifacts(text)  # type: ignore[misc]
    assert lenient_removed == 0, "with min_repeats=4, 3 repeats should not be treated as artifact"


def test_ocr_cleaner_uses_max_line_length_setting() -> None:
    """OCRCleaner uses ocr_page_artifact_max_line_length to filter artifact candidates.

    With max_line_length=10, a line of 15 chars should not become an artifact even
    if it repeats 3+ times.
    """
    long_artifact = "A" * 15  # 15 chars, repeats 3 times
    text = "\n".join(["Real content."] + [long_artifact] * 3 + ["More real content."])

    # Default max_line_length=30: 15-char line IS in range → becomes artifact
    default_settings = NormalizerSettings()
    default_cleaner = OCRCleaner(default_settings)
    _, default_removed = default_cleaner._remove_page_artifacts(text)  # type: ignore[misc]
    assert default_removed == 3

    # max_line_length=10: 15-char line is too long → not an artifact
    strict_settings = NormalizerSettings(ocr_page_artifact_max_line_length=10)
    strict_cleaner = OCRCleaner(strict_settings)
    _, strict_removed = strict_cleaner._remove_page_artifacts(text)  # type: ignore[misc]
    assert strict_removed == 0


def test_ocr_cleaner_uses_candidate_max_length_setting() -> None:
    """OCRCleaner uses ocr_page_artifact_candidate_max_length to tally candidates.

    With candidate_max_length=10, a 15-char line is never tallied at all, so it
    cannot reach the min_repeats threshold.
    """
    medium_line = "B" * 15  # 15 chars
    text = "\n".join(
        ["Real content."]
        + [medium_line] * 5  # repeats 5 times (would exceed min_repeats=3)
        + ["More real content."]
    )

    # Default candidate_max_length=50: 15-char line IS tallied → becomes artifact
    default_settings = NormalizerSettings()
    default_cleaner = OCRCleaner(default_settings)
    _, default_removed = default_cleaner._remove_page_artifacts(text)  # type: ignore[misc]
    assert default_removed == 5

    # candidate_max_length=10: 15-char line not tallied → cannot become artifact
    strict_settings = NormalizerSettings(ocr_page_artifact_candidate_max_length=10)
    strict_cleaner = OCRCleaner(strict_settings)
    _, strict_removed = strict_cleaner._remove_page_artifacts(text)  # type: ignore[misc]
    assert strict_removed == 0


# ---------------------------------------------------------------------------
# Settings defaults: verify all 11 lifted values match prior literals exactly
# ---------------------------------------------------------------------------


def test_extraction_settings_defaults_match_prior_literals() -> None:
    """All ExtractionSettings defaults for lifted fields match the previous literals."""
    cfg = ExtractionSettings()
    assert cfg.loop_max_relationship_multiplier == pytest.approx(4.0)
    assert cfg.loop_max_same_pair == 6
    assert cfg.empty_output_retry_min_chars == 200
    assert cfg.dedup_type_partition_cutoff == 50
    assert cfg.dedup_no_overlap_boost == pytest.approx(0.08)
    assert cfg.dedup_borderline_penalty == pytest.approx(0.05)


def test_normalizer_settings_defaults_match_prior_literals() -> None:
    """All NormalizerSettings defaults for lifted fields match the previous literals."""
    ns = NormalizerSettings()
    assert ns.ftfy_fix_character_width is True
    assert ns.ftfy_fix_line_breaks is True
    assert ns.ocr_page_artifact_min_repeats == 3
    assert ns.ocr_page_artifact_max_line_length == 30
    assert ns.ocr_page_artifact_candidate_max_length == 50
