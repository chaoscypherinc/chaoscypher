# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Injected-settings contract for ``StructuredExtractor`` quality thresholds.

Tier 2 unified ``entity_desc_min_length`` and
``entity_desc_incomplete_threshold`` onto ``ExtractionSettings``. The
extractor must read these from its *injected* ``ExtractionSettings`` (held
as ``self._extraction_settings``) rather than the global app-config
singleton.

These tests construct the extractor with bespoke ExtractionSettings and a
``get_settings`` patched to *blow up* — so any lingering singleton read in
the quality / auto-fix paths fails loudly instead of silently passing on
defaults.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from chaoscypher_core.adapters.llm.schema.extractor import StructuredExtractor
from chaoscypher_core.settings import ExtractionSettings


def _exploding_get_settings() -> object:
    msg = "extractor must not read the app-config singleton"
    raise AssertionError(msg)


def _extractor(extraction_settings: ExtractionSettings) -> StructuredExtractor:
    return StructuredExtractor(MagicMock(), extraction_settings=extraction_settings)


def test_min_length_uses_injected_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    """A high injected entity_desc_min_length flags otherwise-fine descriptions.

    With min_length=42, a 30-char (well-punctuated) description is now 'too
    short' and must produce a quality issue.
    """
    monkeypatch.setattr(
        "chaoscypher_core.adapters.llm.schema.extractor.get_settings",
        _exploding_get_settings,
        raising=False,
    )
    ext = _extractor(
        ExtractionSettings(entity_desc_min_length=42, entity_desc_incomplete_threshold=20)
    )
    desc = "A thirty character description."  # 31 chars, ends with a period
    issues = ext._check_entity_quality([{"name": "X", "type": "P", "description": desc}])
    assert any("very short description" in i for i in issues)


def test_min_length_low_setting_accepts_short_desc(monkeypatch: pytest.MonkeyPatch) -> None:
    """A low injected min_length stops flagging short descriptions as too short."""
    monkeypatch.setattr(
        "chaoscypher_core.adapters.llm.schema.extractor.get_settings",
        _exploding_get_settings,
        raising=False,
    )
    ext = _extractor(
        ExtractionSettings(entity_desc_min_length=3, entity_desc_incomplete_threshold=20)
    )
    issues = ext._check_entity_quality([{"name": "X", "type": "P", "description": "short."}])
    assert not any("very short description" in i for i in issues)


def test_incomplete_threshold_uses_injected_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    """entity_desc_incomplete_threshold from the injected settings drives the
    auto-fix punctuation gate.

    With threshold=5, a 10-char unterminated description is now 'long enough'
    to auto-complete with a period.
    """
    monkeypatch.setattr(
        "chaoscypher_core.adapters.llm.schema.extractor.get_settings",
        _exploding_get_settings,
        raising=False,
    )
    ext = _extractor(
        ExtractionSettings(entity_desc_min_length=1, entity_desc_incomplete_threshold=5)
    )
    entities = [{"name": "X", "type": "P", "description": "ten charss"}]  # 10 chars, no punct
    fixes = ext._auto_fix_entity_descriptions(entities)
    assert fixes == 1
    assert entities[0]["description"].endswith(".")


def test_incomplete_threshold_high_skips_autofix(monkeypatch: pytest.MonkeyPatch) -> None:
    """A high injected incomplete threshold leaves shortish descriptions untouched."""
    monkeypatch.setattr(
        "chaoscypher_core.adapters.llm.schema.extractor.get_settings",
        _exploding_get_settings,
        raising=False,
    )
    ext = _extractor(
        ExtractionSettings(entity_desc_min_length=1, entity_desc_incomplete_threshold=500)
    )
    entities = [{"name": "X", "type": "P", "description": "a medium length description no period"}]
    fixes = ext._auto_fix_entity_descriptions(entities)
    assert fixes == 0
    assert not entities[0]["description"].endswith(".")


def test_incomplete_threshold_drives_quality_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """The incomplete-description quality issue uses the injected threshold."""
    monkeypatch.setattr(
        "chaoscypher_core.adapters.llm.schema.extractor.get_settings",
        _exploding_get_settings,
        raising=False,
    )
    ext = _extractor(
        ExtractionSettings(entity_desc_min_length=1, entity_desc_incomplete_threshold=5)
    )
    # 10 chars, no end punctuation, above the threshold of 5 -> incomplete issue.
    issues = ext._check_entity_quality([{"name": "X", "type": "P", "description": "ten charss"}])
    assert any("incomplete description" in i for i in issues)
