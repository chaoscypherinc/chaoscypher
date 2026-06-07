# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""run_deduplication uses FilteringConfig.semantic_dedup_threshold.

The W4 wiring promise: when the caller passes a resolved
``FilteringConfig`` to ``run_deduplication`` (or sets it indirectly via
the upstream filtering mode), the cosine-similarity threshold for
semantic dedup must come from that config — not from the extraction
settings default.

Domain extraction limits remain the highest-priority override (they
already shipped before the FilteringConfig path), and the extraction
settings serve as the final fallback when no FilteringConfig is in
scope (legacy callers).
"""

from __future__ import annotations

import pytest

from chaoscypher_core.services.sources.engine.extraction.extractor import (
    run_deduplication,
)
from chaoscypher_core.services.sources.engine.extraction.utils.filtering_config import (
    resolve_filtering_config,
)
from chaoscypher_core.settings import EngineSettings


class _StubDomainResolver:
    """Minimal DomainResolver double — no domain-specific behavior."""

    def get_domain_title_words(self, _domain: str | None) -> set[str]:
        return set()

    def get_domain_type_compatibility(self, _domain: str | None) -> dict[str, list[str]] | None:
        return None

    def get_domain_symmetric_relationships(self, _domain: str | None) -> list[str] | None:
        return None

    def get_domain_inverse_relationships(self, _domain: str | None) -> dict[str, str] | None:
        return None


class _ThresholdRecordingEmbeddingService:
    """Embedding service double that records the threshold it was called with.

    ``EntityProcessor.deduplicate_entities_semantic`` is patched at module
    import time in the run; for this test we just want to confirm the
    threshold value flows through. We capture it via a lightweight stub
    on EntityProcessor instead of touching internals.
    """

    def __init__(self) -> None:
        self.calls: list[float] = []


@pytest.mark.asyncio
async def test_threshold_resolved_from_filtering_config(monkeypatch) -> None:
    """run_deduplication reads semantic_dedup_threshold from FilteringConfig."""
    captured: dict[str, float] = {}

    async def _fake_semantic_dedup(
        self,
        entities,
        embedding_service,
        threshold,
        **kwargs,
    ):
        captured["threshold"] = threshold
        return entities, list(range(len(entities))), []

    from chaoscypher_core.services.sources.engine.deduplication.service import (
        EntityProcessor,
    )

    monkeypatch.setattr(
        EntityProcessor,
        "deduplicate_entities_semantic",
        _fake_semantic_dedup,
    )

    cfg = resolve_filtering_config("strict")  # semantic_dedup_threshold=0.87
    settings = EngineSettings()
    settings.source_processing.entity_deduplication_mode = "semantic"

    await run_deduplication(
        entities=[
            {"name": "Alice", "type": "Person", "description": "a"},
            {"name": "Bob", "type": "Person", "description": "b"},
        ],
        relationships=[],
        detected_domain=None,
        settings=settings,
        embedding_service=object(),  # truthy sentinel
        domain_resolver=_StubDomainResolver(),
        filtering_config=cfg,
    )

    assert captured["threshold"] == pytest.approx(cfg.semantic_dedup_threshold)
    # And the slider-driven 0.87 is *not* the settings default (0.95).
    assert captured["threshold"] != settings.extraction.semantic_dedup_threshold


@pytest.mark.asyncio
async def test_domain_limits_override_filtering_config(monkeypatch) -> None:
    """Domain extraction limits keep their highest-precedence override."""
    captured: dict[str, float] = {}

    async def _fake_semantic_dedup(
        self,
        entities,
        embedding_service,
        threshold,
        **kwargs,
    ):
        captured["threshold"] = threshold
        return entities, list(range(len(entities))), []

    from chaoscypher_core.services.sources.engine.deduplication.service import (
        EntityProcessor,
    )

    monkeypatch.setattr(
        EntityProcessor,
        "deduplicate_entities_semantic",
        _fake_semantic_dedup,
    )

    cfg = resolve_filtering_config("strict")  # 0.87
    settings = EngineSettings()
    settings.source_processing.entity_deduplication_mode = "semantic"

    await run_deduplication(
        entities=[
            {"name": "Alice", "type": "Person", "description": "a"},
            {"name": "Bob", "type": "Person", "description": "b"},
        ],
        relationships=[],
        detected_domain=None,
        settings=settings,
        embedding_service=object(),
        domain_resolver=_StubDomainResolver(),
        filtering_config=cfg,
        domain_extraction_limits={"semantic_dedup_threshold": 0.42},
    )

    assert captured["threshold"] == pytest.approx(0.42)


@pytest.mark.asyncio
async def test_threshold_falls_back_to_settings_when_no_filtering_config(
    monkeypatch,
) -> None:
    """Legacy callers without filtering_config keep the previous behavior."""
    captured: dict[str, float] = {}

    async def _fake_semantic_dedup(
        self,
        entities,
        embedding_service,
        threshold,
        **kwargs,
    ):
        captured["threshold"] = threshold
        return entities, list(range(len(entities))), []

    from chaoscypher_core.services.sources.engine.deduplication.service import (
        EntityProcessor,
    )

    monkeypatch.setattr(
        EntityProcessor,
        "deduplicate_entities_semantic",
        _fake_semantic_dedup,
    )

    settings = EngineSettings()
    settings.source_processing.entity_deduplication_mode = "semantic"

    await run_deduplication(
        entities=[
            {"name": "Alice", "type": "Person", "description": "a"},
            {"name": "Bob", "type": "Person", "description": "b"},
        ],
        relationships=[],
        detected_domain=None,
        settings=settings,
        embedding_service=object(),
        domain_resolver=_StubDomainResolver(),
    )

    assert captured["threshold"] == pytest.approx(settings.extraction.semantic_dedup_threshold)
