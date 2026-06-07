# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for OCR_CLEANER_SKIPPED_BY_PREDICATE counter wiring.

Phase 2 observability (2026-05-08): when ``enable_ocr_cleaning=True`` but
the OCR cleaner's ``applies_to`` predicate returns ``False`` (unknown
extraction_method), the normalizer increments ``ocr_predicate_skips`` on
the returned ``NormalizedContent``.

The indexing handler reads that field and calls
``increment_quality_counter`` with ``QualityCounter.OCR_CLEANER_SKIPPED_BY_PREDICATE``.

These tests verify:
1. ``ocr_predicate_skips`` is 1 for an unknown extraction_method when
   OCR cleaning is enabled.
2. ``ocr_predicate_skips`` is 0 for a known OCR method (not a predicate skip).
3. ``ocr_predicate_skips`` is 0 when OCR cleaning is globally disabled
   (predicate fires but is not considered a "skip" worth counting).
4. ``ocr_predicate_skips`` is 0 when no extraction_method is provided and
   OCR cleaning is disabled.
"""

from __future__ import annotations

from pathlib import Path

from chaoscypher_core.services.sources.normalizer.service import ContentNormalizerService
from chaoscypher_core.settings import EngineSettings, NormalizerSettings, PathSettings


def _engine_settings(tmp_path: Path, *, enable_ocr_cleaning: bool) -> EngineSettings:
    return EngineSettings(
        paths=PathSettings(data_dir=str(tmp_path)),
        normalizer=NormalizerSettings(enable_ocr_cleaning=enable_ocr_cleaning),
    )


_ENOUGH_CONTENT = "Normal prose content that passes all OCR heuristics. " * 30


class TestOCRPredicateSkipCounter:
    """ocr_predicate_skips is set correctly on NormalizedContent."""

    def test_unknown_extraction_method_with_ocr_enabled_yields_skip(self, tmp_path: Path) -> None:
        """Unknown extraction_method + enable_ocr_cleaning=True → ocr_predicate_skips=1."""
        service = ContentNormalizerService(
            settings=_engine_settings(tmp_path, enable_ocr_cleaning=True)
        )
        metadata = {"extraction_method": "my_new_custom_loader"}

        result = service.normalize(content=_ENOUGH_CONTENT, metadata=metadata)

        assert result.ocr_predicate_skips == 1

    def test_known_ocr_method_yields_no_skip(self, tmp_path: Path) -> None:
        """Known OCR method (pypdf_extract) → predicate passes → ocr_predicate_skips=0."""
        service = ContentNormalizerService(
            settings=_engine_settings(tmp_path, enable_ocr_cleaning=True)
        )
        metadata = {"extraction_method": "pypdf_extract"}

        result = service.normalize(content=_ENOUGH_CONTENT, metadata=metadata)

        assert result.ocr_predicate_skips == 0

    def test_ocr_cleaning_disabled_no_skip_recorded(self, tmp_path: Path) -> None:
        """When enable_ocr_cleaning=False the predicate skip is not counted.

        The OCR cleaner's ``clean()`` method gates on the setting, but the
        ``applies_to`` predicate still fires for content filtering. When the
        setting is off the user didn't ask for OCR cleaning, so bypassing it
        is not a silent degradation worth surfacing.
        """
        service = ContentNormalizerService(
            settings=_engine_settings(tmp_path, enable_ocr_cleaning=False)
        )
        metadata = {"extraction_method": "my_new_custom_loader"}

        result = service.normalize(content=_ENOUGH_CONTENT, metadata=metadata)

        assert result.ocr_predicate_skips == 0

    def test_missing_extraction_method_with_ocr_disabled_no_skip(self, tmp_path: Path) -> None:
        """No extraction_method at all + OCR disabled → ocr_predicate_skips=0."""
        service = ContentNormalizerService(
            settings=_engine_settings(tmp_path, enable_ocr_cleaning=False)
        )
        result = service.normalize(content=_ENOUGH_CONTENT, metadata={})

        assert result.ocr_predicate_skips == 0

    def test_missing_extraction_method_with_ocr_enabled_yields_skip(self, tmp_path: Path) -> None:
        """No extraction_method + OCR enabled → predicate fails → ocr_predicate_skips=1.

        ``applies_to`` returns ``False`` for missing extraction_method, which
        is the same silent-skip case as an unknown method.
        """
        service = ContentNormalizerService(
            settings=_engine_settings(tmp_path, enable_ocr_cleaning=True)
        )
        result = service.normalize(content=_ENOUGH_CONTENT, metadata={})

        assert result.ocr_predicate_skips == 1

    def test_ocr_predicate_skips_zero_by_default_on_normalized_content(
        self, tmp_path: Path
    ) -> None:
        """NormalizedContent.ocr_predicate_skips defaults to 0."""
        from chaoscypher_core.services.sources.normalizer.models import (
            ContentType,
            NormalizedContent,
        )

        obj = NormalizedContent(
            content="x",
            original_content="x",
            content_type=ContentType.TEXT,
        )
        assert obj.ocr_predicate_skips == 0
