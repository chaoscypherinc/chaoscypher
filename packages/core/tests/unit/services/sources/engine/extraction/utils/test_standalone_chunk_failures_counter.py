# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for STANDALONE_CHUNK_FAILURES counter wiring in extract_from_chunks.

The standalone extraction path (AIEntityExtractor.extract_from_chunks) catches
per-chunk exceptions and continues rather than aborting the whole extraction.
Every such swallow must increment QualityCounter.STANDALONE_CHUNK_FAILURES when
an adapter + source_id are provided.

When adapter or source_id is None the increment is a no-op — callers without a
source row (pure CLI / notebook) pass None and no DB write is attempted.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from chaoscypher_core.services.quality.counters import QualityCounter
from chaoscypher_core.services.sources.engine.extraction.utils import (
    ai_entities,
)
from chaoscypher_core.services.sources.engine.extraction.utils.ai_entities import (
    AIEntityExtractor,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_extractor(monkeypatch: pytest.MonkeyPatch) -> AIEntityExtractor:
    """Build a minimal AIEntityExtractor bypassing __init__.

    - Monkeypatches ``detect_domain`` so no domain registry I/O is needed.
    - Monkeypatches ``AIEntityExtractor.settings`` with a MagicMock so
      attribute access inside the method doesn't fail.
    """
    extractor = AIEntityExtractor.__new__(AIEntityExtractor)

    # Provide a minimal settings stub — only the paths touched by
    # extract_from_chunks before extract_single_chunk is called.
    settings_mock = MagicMock()
    settings_mock.llm.extraction_examples_enabled = False
    extractor.settings = settings_mock  # type: ignore[attr-defined]

    # Stub detect_domain to return a minimal domain object
    domain_mock = MagicMock()
    domain_mock.name = "generic"
    domain_mock.get_entity_guidance.return_value = ""
    domain_mock.get_relationship_guidance.return_value = ""
    domain_mock.get_templates.return_value = {"node_templates": [], "edge_templates": []}
    domain_mock.get_examples.return_value = []
    domain_mock.get_extraction_limits.return_value = {}
    domain_mock.get_filtering_mode.return_value = None
    domain_mock.get_entity_exclusions.return_value = []
    domain_mock.get_normalization_rules.return_value = {}
    domain_mock.get_property_type_mapping.return_value = {}
    domain_mock.get_edge_type_constraints.return_value = {}
    domain_mock.get_strict_entity_types.return_value = False
    domain_mock.get_evidence_validation_mode.return_value = None

    monkeypatch.setattr(ai_entities, "detect_domain", lambda *_a, **_kw: (domain_mock, 1.0))
    monkeypatch.setattr(ai_entities, "format_domain_node_templates", lambda *_a, **_kw: "")
    monkeypatch.setattr(ai_entities, "format_domain_edge_templates", lambda *_a, **_kw: "")
    monkeypatch.setattr(ai_entities, "calculate_density_stats", lambda *_a, **_kw: {})
    monkeypatch.setattr(ai_entities, "_merge_within_task_relationships", lambda rels: rels)

    return extractor


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestExtractFromChunksStandaloneFailuresCounter:
    """STANDALONE_CHUNK_FAILURES must be incremented on each swallowed error."""

    @pytest.mark.asyncio
    async def test_increments_once_per_failing_chunk(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """One failing chunk → one STANDALONE_CHUNK_FAILURES increment."""
        bumps: list[QualityCounter] = []

        async def fake_increment(
            *, adapter: Any, source_id: str, database_name: str, counter: QualityCounter, n: int = 1
        ) -> None:
            bumps.extend([counter] * n)

        monkeypatch.setattr(ai_entities, "increment_quality_counter", fake_increment)

        extractor = _make_extractor(monkeypatch)

        call_count = 0

        async def _failing_extract_single_chunk(**_kwargs: Any) -> Any:
            nonlocal call_count
            call_count += 1
            raise RuntimeError("simulated chunk failure")

        extractor.extract_single_chunk = _failing_extract_single_chunk  # type: ignore[method-assign]

        adapter = MagicMock()
        result = await extractor.extract_from_chunks(
            chunks=["chunk one"],
            adapter=adapter,
            source_id="src-abc",
            database_name="default",
        )

        assert call_count == 1
        assert bumps.count(QualityCounter.STANDALONE_CHUNK_FAILURES) == 1
        # Result is still a valid (empty) dict — extraction continues after failure
        assert result["entities"] == []
        assert result["relationships"] == []

    @pytest.mark.asyncio
    async def test_increments_per_failing_chunk_with_multiple_chunks(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Two failing chunks out of three → two counter increments."""
        bumps: list[QualityCounter] = []

        async def fake_increment(
            *, adapter: Any, source_id: str, database_name: str, counter: QualityCounter, n: int = 1
        ) -> None:
            bumps.extend([counter] * n)

        monkeypatch.setattr(ai_entities, "increment_quality_counter", fake_increment)

        extractor = _make_extractor(monkeypatch)

        call_count = 0

        async def _selective_failure(**_kwargs: Any) -> Any:
            nonlocal call_count
            call_count += 1
            # First and third chunks fail; second succeeds
            if call_count in (1, 3):
                raise ValueError("simulated failure")
            # Successful call returns (entities, relationships, tok_in, tok_out, metrics)
            return ([], [], 0, 0, {})

        extractor.extract_single_chunk = _selective_failure  # type: ignore[method-assign]

        adapter = MagicMock()
        result = await extractor.extract_from_chunks(
            chunks=["chunk one", "chunk two", "chunk three"],
            adapter=adapter,
            source_id="src-xyz",
            database_name="mydb",
        )

        assert call_count == 3
        assert bumps.count(QualityCounter.STANDALONE_CHUNK_FAILURES) == 2
        # Successful chunk (index 1) produces no entities/rels in our stub
        assert result["entities"] == []
        assert result["relationships"] == []

    @pytest.mark.asyncio
    async def test_no_increment_when_adapter_is_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Counter is skipped when adapter=None (true-standalone / no source row)."""
        bumps: list[QualityCounter] = []

        async def fake_increment(
            *, adapter: Any, source_id: str, database_name: str, counter: QualityCounter, n: int = 1
        ) -> None:
            bumps.extend([counter] * n)

        monkeypatch.setattr(ai_entities, "increment_quality_counter", fake_increment)

        extractor = _make_extractor(monkeypatch)

        async def _always_fail(**_kwargs: Any) -> Any:
            raise RuntimeError("failure")

        extractor.extract_single_chunk = _always_fail  # type: ignore[method-assign]

        await extractor.extract_from_chunks(
            chunks=["only chunk"],
            adapter=None,  # no adapter → no counter write
            source_id="src-001",
            database_name="default",
        )

        assert bumps == []

    @pytest.mark.asyncio
    async def test_no_increment_when_source_id_is_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Counter is skipped when source_id=None even if adapter is provided."""
        bumps: list[QualityCounter] = []

        async def fake_increment(
            *, adapter: Any, source_id: str, database_name: str, counter: QualityCounter, n: int = 1
        ) -> None:
            bumps.extend([counter] * n)

        monkeypatch.setattr(ai_entities, "increment_quality_counter", fake_increment)

        extractor = _make_extractor(monkeypatch)

        async def _always_fail(**_kwargs: Any) -> Any:
            raise RuntimeError("failure")

        extractor.extract_single_chunk = _always_fail  # type: ignore[method-assign]

        adapter = MagicMock()
        await extractor.extract_from_chunks(
            chunks=["only chunk"],
            adapter=adapter,
            source_id=None,  # no source_id → no counter write
            database_name="default",
        )

        assert bumps == []

    @pytest.mark.asyncio
    async def test_database_name_defaults_to_default_when_omitted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When database_name is None the increment uses 'default' as fallback."""
        captured_db_names: list[str] = []

        async def fake_increment(
            *, adapter: Any, source_id: str, database_name: str, counter: QualityCounter, n: int = 1
        ) -> None:
            captured_db_names.append(database_name)

        monkeypatch.setattr(ai_entities, "increment_quality_counter", fake_increment)

        extractor = _make_extractor(monkeypatch)

        async def _always_fail(**_kwargs: Any) -> Any:
            raise RuntimeError("failure")

        extractor.extract_single_chunk = _always_fail  # type: ignore[method-assign]

        adapter = MagicMock()
        await extractor.extract_from_chunks(
            chunks=["only chunk"],
            adapter=adapter,
            source_id="src-999",
            database_name=None,  # omitted → fallback to "default"
        )

        assert captured_db_names == ["default"]
