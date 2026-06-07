# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Operator's normalizer settings reach the cleaners during indexing.

Workstream 5.1 (2026-05-07): when the indexing handler builds a
:class:`ContentNormalizerService`, it must pass ``engine_settings`` so the
operator's flags (``enable_ocr_cleaning=False`` etc.) actually take effect.
Before this fix, the service was constructed with no arguments and silently
fell back to defaults — short identifiers like ``git``, ``npm``, ``K8s``
were dropped as "gibberish" by the OCR cleaner regardless of user setting.
"""

from __future__ import annotations

from chaoscypher_core.operations.importing.indexing_handler import _extract_text
from chaoscypher_core.settings import EngineSettings, NormalizerSettings


def test_extract_text_honors_disabled_ocr_cleaning_via_engine_settings(
    monkeypatch,
) -> None:
    """When the indexing handler builds a normalizer with ``enable_ocr_cleaning=False``,
    short identifiers must survive normalization.

    The previous behaviour (no settings threaded into ContentNormalizerService)
    let the OCR cleaner drop short tokens like "git" because it ran
    unconditionally with default settings.
    """
    # Build engine settings with OCR cleaning disabled.
    engine_settings = EngineSettings(
        normalizer=NormalizerSettings(
            enable_ocr_cleaning=False,
            enable_duplicate_removal=False,
        )
    )

    text_with_short_identifiers = "word " * 100 + "git\nnpm\nK8s\n" + "more text " * 100
    documents = [{"content": text_with_short_identifiers, "metadata": {}}]

    # Capture the kwargs the handler passes when building ContentNormalizerService.
    constructed_with: dict = {}

    from chaoscypher_core.services.sources.normalizer import (
        service as normalizer_service_module,
    )

    real_init = normalizer_service_module.ContentNormalizerService.__init__

    def _spy_init(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        constructed_with["args"] = args
        constructed_with["kwargs"] = dict(kwargs)
        return real_init(self, *args, **kwargs)

    monkeypatch.setattr(
        normalizer_service_module.ContentNormalizerService,
        "__init__",
        _spy_init,
    )

    result, _counts = _extract_text(
        documents=documents,
        enable_normalization=True,
        filepath="/tmp/identifiers.txt",
        file_id="src_test",
        engine_settings=engine_settings,
    )

    # The handler must have threaded engine_settings through. Either as the
    # first positional argument or via a keyword — both shapes are accepted
    # by the new ContentNormalizerService.__init__.
    threaded = (
        constructed_with["kwargs"].get("settings") is engine_settings
        or constructed_with["kwargs"].get("engine_settings") is engine_settings
        or (constructed_with["args"] and constructed_with["args"][0] is engine_settings)
    )
    assert threaded, (
        "Indexing handler must pass engine_settings into ContentNormalizerService "
        f"(got args={constructed_with['args']!r}, kwargs={constructed_with['kwargs']!r})"
    )

    # And the disabled OCR cleaner must not have eaten the short identifiers.
    assert "git" in result
    assert "npm" in result
    assert "K8s" in result


def test_normalizer_service_tolerates_non_normalizer_settings_object() -> None:
    """``_build_engine_settings`` must not raise when ``self.settings`` is not a real
    :class:`NormalizerSettings`.

    Workstream 5 wired ``ContentNormalizerService._build_engine_settings`` to call
    ``EngineSettings(normalizer=self.settings)``. Pydantic v2 rejects anything that
    isn't a real ``NormalizerSettings`` (or a dict that validates) — including the
    ``MagicMock`` callers commonly use in unit tests. The service must fall back to
    a default ``NormalizerSettings`` rather than crash deep inside cleaner-registry
    initialization.
    """
    from unittest.mock import MagicMock

    from chaoscypher_core.services.sources.normalizer.service import (
        ContentNormalizerService,
    )

    # Pass a MagicMock through the legacy ``settings`` arg (truthy, not a
    # NormalizerSettings). Pre-fix this raised ValidationError on first
    # access of the cleaner registry.
    service = ContentNormalizerService(settings=MagicMock())

    # Touching ``cleaner_registry`` triggers ``_build_engine_settings``.
    registry = service.cleaner_registry
    assert registry is not None
