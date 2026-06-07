# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""OCR cleaner only fires for OCR-derived content.

Workstream 5.5 (2026-05-07): cleaners gain an instance-level
``applies_to(metadata)`` predicate. The OCR cleaner overrides it to fire
only when the source's ``extraction_method`` indicates the content came
through an OCR-style pipeline (PDF text extraction, image OCR, vision
LLM). Plain ``.txt`` and ``.md`` files used to lose short identifiers
like ``git`` / ``npm`` / ``K8s`` because the OCR cleaner ran on them
unconditionally.
"""

from __future__ import annotations

from pathlib import Path

from chaoscypher_core.services.sources.normalizer.cleaners.ocr_cleaner import (
    OCRCleaner,
)
from chaoscypher_core.services.sources.normalizer.service import (
    ContentNormalizerService,
)
from chaoscypher_core.settings import EngineSettings, NormalizerSettings, PathSettings


def _engine_settings(tmp_path: Path) -> EngineSettings:
    return EngineSettings(
        paths=PathSettings(data_dir=str(tmp_path)),
        normalizer=NormalizerSettings(
            enable_ocr_cleaning=True,
            enable_duplicate_removal=True,
        ),
    )


def test_ocr_cleaner_applies_to_returns_false_for_plain_text() -> None:
    """The OCR cleaner skips content whose extraction_method is read_text."""
    cleaner = OCRCleaner(NormalizerSettings(enable_ocr_cleaning=True))
    metadata = {"extraction_method": "read_text", "content_type": "text/plain"}
    assert cleaner.applies_to(metadata) is False


def test_ocr_cleaner_applies_to_returns_true_for_pdf_extract() -> None:
    """The OCR cleaner fires for PDF-extracted content."""
    cleaner = OCRCleaner(NormalizerSettings(enable_ocr_cleaning=True))
    metadata = {"extraction_method": "pypdf_extract", "content_type": "application/pdf"}
    assert cleaner.applies_to(metadata) is True


def test_ocr_cleaner_applies_to_returns_true_for_each_ocr_method() -> None:
    """All four OCR-style extraction methods fire the OCR cleaner."""
    cleaner = OCRCleaner(NormalizerSettings(enable_ocr_cleaning=True))
    for method in ("pypdf_extract", "ocr_tesseract", "vision_llm", "image_ocr"):
        assert cleaner.applies_to({"extraction_method": method}) is True, (
            f"OCR cleaner must fire for extraction_method={method!r}"
        )


def test_ocr_cleaner_applies_to_returns_false_for_unknown_method() -> None:
    """Unknown / missing extraction_method skips the OCR cleaner."""
    cleaner = OCRCleaner(NormalizerSettings(enable_ocr_cleaning=True))
    assert cleaner.applies_to({}) is False
    assert cleaner.applies_to({"extraction_method": "html_loader"}) is False


def test_normalize_skips_ocr_cleaner_for_plain_text(tmp_path: Path) -> None:
    """End-to-end: plain .txt content keeps short identifiers like git/npm."""
    service = ContentNormalizerService(settings=_engine_settings(tmp_path))

    text = "git npm K8s\n" + "lots of words " * 100
    metadata = {"extraction_method": "read_text", "content_type": "text/plain"}

    result = service.normalize(content=text, metadata=metadata)

    # Short identifiers must survive — the OCR cleaner would have dropped
    # them as "gibberish lines" when run unconditionally.
    assert "git" in result.content
    assert "npm" in result.content
    assert "K8s" in result.content


def test_normalize_runs_ocr_cleaner_for_pdf_extract(tmp_path: Path) -> None:
    """End-to-end: PDF-extracted content still gets OCR cleanup."""
    service = ContentNormalizerService(settings=_engine_settings(tmp_path))

    text = (
        "8 Introduction\n\n"
        "Some real content here that talks about things in detail. "
        "This paragraph has enough length to clear the alpha-ratio "
        "checks the OCR cleaner runs on long lines.\n"
        "Page 12\n"
        "More real content with enough length to be a meaningful "
        "paragraph that survives the gibberish-detection pass cleanly.\n"
    )
    metadata = {"extraction_method": "pypdf_extract", "content_type": "application/pdf"}

    result = service.normalize(content=text, metadata=metadata)

    # Page-number / structural artifacts stripped.
    assert "Page 12" not in result.content
    # Real content survived.
    assert "Some real content" in result.content


# ---------------------------------------------------------------------------
# Phase 7 audit-remediation (2026-05-09): enable_ocr_cleaning=False kill switch
# ---------------------------------------------------------------------------


def test_ocr_cleaner_applies_to_false_when_flag_disabled() -> None:
    """enable_ocr_cleaning=False makes applies_to return False even for OCR content.

    Phase 7 audit fix: the flag check moved from clean() into applies_to() so
    the normalizer service skips the cleaner entirely. Previously setting the
    flag had no visible effect on documents whose extraction_method matched the
    OCR predicate.
    """
    cleaner = OCRCleaner(NormalizerSettings(enable_ocr_cleaning=False))

    # Every OCR-derived extraction method must be blocked by the flag.
    for method in ("pypdf_extract", "ocr_tesseract", "vision_llm", "image_ocr", "pypdf", "vision"):
        metadata = {"extraction_method": method}
        assert cleaner.applies_to(metadata) is False, (
            f"applies_to must return False when enable_ocr_cleaning=False "
            f"(extraction_method={method!r})"
        )


def test_ocr_cleaner_applies_to_true_when_flag_enabled_and_ocr_method() -> None:
    """enable_ocr_cleaning=True + OCR extraction_method → applies_to returns True.

    Positive counterpart to the kill-switch test: the flag and the method-set
    predicate both need to agree before the cleaner fires.
    """
    cleaner = OCRCleaner(NormalizerSettings(enable_ocr_cleaning=True))

    for method in ("pypdf_extract", "ocr_tesseract", "vision_llm", "image_ocr"):
        metadata = {"extraction_method": method}
        assert cleaner.applies_to(metadata) is True, (
            f"applies_to must return True when enable_ocr_cleaning=True "
            f"and extraction_method={method!r}"
        )


def test_ocr_cleaner_applies_to_false_when_flag_disabled_missing_method() -> None:
    """enable_ocr_cleaning=False blocks the cleaner even when metadata is empty."""
    cleaner = OCRCleaner(NormalizerSettings(enable_ocr_cleaning=False))
    assert cleaner.applies_to({}) is False
    assert cleaner.applies_to(None) is False
