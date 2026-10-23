# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Smoke tests for ExtractionService orchestration.

Mocks all external dependencies (LLM, embeddings, storage, domain registry)
and exercises the high-level orchestration paths to verify wiring.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_core.services.sources.engine.extraction.service import (
    ExtractionService,
)
from chaoscypher_core.settings import EngineSettings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_settings() -> SimpleNamespace:
    """Build minimal settings for the service."""
    return SimpleNamespace(
        source_processing=SimpleNamespace(
            entity_max_description_length=4000,
            entity_deduplication_mode="exact",
            dedup_require_type_compatibility=False,
        ),
        extraction=SimpleNamespace(
            semantic_dedup_threshold=0.95,
            extraction_filtering_mode="standard",
            dedup_type_partition_cutoff=50,
            dedup_no_overlap_boost=0.08,
            dedup_borderline_penalty=0.05,
        ),
        embedding=SimpleNamespace(model="test-embed-model"),
    )


def _make_service(embedding_service: object | None = None) -> ExtractionService:
    """Create an ExtractionService with mocked dependencies."""
    return ExtractionService(
        graph_repository=MagicMock(name="graph_repository"),
        llm_provider=MagicMock(name="llm_provider"),
        settings=_fake_settings(),
        embedding_service=embedding_service,
    )


# ---------------------------------------------------------------------------
# TestBuildExtractionResults
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
class TestBuildExtractionResults:
    """Smoke tests for build_extraction_results."""

    async def test_empty_entities_yields_empty_results(self) -> None:
        """Empty entity list yields empty suggestions and no embeddings."""
        service = _make_service()
        result = await service.build_extraction_results(
            entities=[],
            relationships=[],
            generate_embeddings=False,
            cached_embeddings=[],
            detected_domain=None,
        )
        assert result["entities"] == []
        assert result["relationships"] == []
        assert result["suggested_templates"] == []
        assert result["suggested_edge_templates"] == []
        assert result["matched_templates"] == []
        assert result["metadata"]["total_entities"] == 0
        assert result["metadata"]["embeddings_generated"] is False
        assert "embeddings" not in result

    async def test_normalizes_and_suggests_templates(self) -> None:
        """Raw entities are normalized and produce node/edge template suggestions."""
        service = _make_service()
        raw_entities = [
            {"id": "e0", "name": "Alice", "type": "Person"},
            {"id": "e1", "name": "Bob", "type": "Person"},
            {"id": "e2", "name": "AcmeCorp", "type": "Organization"},
        ]
        raw_relationships = [
            {"source": 0, "target": 1, "type": "knows"},
            {"source": 0, "target": 2, "type": "works_at"},
        ]
        result = await service.build_extraction_results(
            entities=raw_entities,
            relationships=raw_relationships,
            generate_embeddings=False,
            cached_embeddings=[],
            detected_domain=None,
        )
        # Entities are normalized (have default fields)
        assert len(result["entities"]) == 3
        for entity in result["entities"]:
            assert "confidence" in entity
            assert "aliases" in entity
            assert "properties" in entity

        # Node template suggestions produced for Person and Organization
        node_names = {s["name"] for s in result["suggested_templates"]}
        assert "Person" in node_names
        assert "Organization" in node_names

        # Edge template suggestions produced for knows and works_at
        edge_names = {s["name"] for s in result["suggested_edge_templates"]}
        assert "knows" in edge_names
        assert "works_at" in edge_names

        # Metadata is populated
        assert result["metadata"]["total_entities"] == 3
        assert result["metadata"]["total_relationships"] == 2
        assert result["metadata"]["embeddings_generated"] is False
        assert result["metadata"]["extraction_depth"] == "full"

    async def test_generate_embeddings_calls_embedding_service(self) -> None:
        """Embedding service is invoked when generate_embeddings=True."""
        embedding_service = MagicMock()
        embedding_service.batch_embed = AsyncMock(
            return_value=SimpleNamespace(embeddings=[[0.1, 0.2, 0.3]])
        )
        service = _make_service(embedding_service=embedding_service)
        result = await service.build_extraction_results(
            entities=[{"id": "e0", "name": "Alice", "type": "Person"}],
            relationships=[],
            generate_embeddings=True,
            cached_embeddings=[],
            detected_domain=None,
        )
        embedding_service.batch_embed.assert_awaited_once()
        assert "embeddings" in result
        assert result["embeddings"]["count"] == 1
        assert result["metadata"]["embeddings_generated"] is True

    async def test_embedding_failure_degrades_gracefully(self) -> None:
        """Embedding failure yields metadata.embeddings_generated=False, no raise."""
        embedding_service = MagicMock()
        embedding_service.batch_embed = AsyncMock(side_effect=RuntimeError("boom"))
        service = _make_service(embedding_service=embedding_service)
        result = await service.build_extraction_results(
            entities=[{"id": "e0", "name": "Alice", "type": "Person"}],
            relationships=[],
            generate_embeddings=True,
            cached_embeddings=[],
            detected_domain=None,
        )
        assert "embeddings" not in result
        assert result["metadata"]["embeddings_generated"] is False

    async def test_forced_domain_recorded_in_metadata(self) -> None:
        """forced_domain is recorded in the metadata when provided."""
        service = _make_service()
        result = await service.build_extraction_results(
            entities=[],
            relationships=[],
            generate_embeddings=False,
            cached_embeddings=[],
            detected_domain="literary",
            forced_domain="technical",
            extraction_depth="quick",
        )
        assert result["metadata"]["forced_domain"] == "technical"
        assert result["metadata"]["detected_domain"] == "literary"
        assert result["metadata"]["extraction_depth"] == "quick"


# ---------------------------------------------------------------------------
# TestDomainHelpers
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDomainHelpers:
    """Smoke tests for the service's domain helper methods."""

    def test_none_domain_name_reads_the_floor(self) -> None:
        """No domain name is the floor alone: its symmetric labels and inverse pairs."""
        service = _make_service()
        floor = _real_stack()
        assert service.get_domain_title_words(None) is None
        assert service.get_domain_symmetric_relationships(None) == floor.symmetric_relationships()
        assert service.get_domain_inverse_relationships(None) == floor.inverse_relationships()

    def test_domain_helpers_use_registry(self) -> None:
        """Domain helpers consult the domain registry when given a name."""
        service = _make_service()
        domain = MagicMock()
        domain.role = "pack"
        domain.name = "literary"
        domain.get_title_words.return_value = ["Mr", "Dr"]
        floor = MagicMock()
        floor.role = "floor"
        floor.name = "generic"
        floor.get_title_words.return_value = []
        registry = MagicMock()
        # the service resolves the source's stack: the floor and the pack (Task 7)
        registry.get_domain.side_effect = {"generic": floor, "literary": domain}.get

        with patch(
            "chaoscypher_core.services.sources.engine.extraction.domains.get_domain_registry",
            return_value=registry,
        ):
            assert service.get_domain_title_words("literary") == frozenset({"mr", "dr"})

    def test_the_stack_helpers_read_the_floor_and_the_pack(self) -> None:
        """The conventions, symmetric labels and inverse map are the stack's: the floor's with literary's.

        Literary declares no ``parent_of``/``child_of`` (the floor does since
        2026-10-07); the finalizer read literary's map alone, so a fact written
        both ways committed as two edges.
        """
        service = _make_service()
        stack = _real_stack("literary")
        inverse = service.get_domain_inverse_relationships("literary")
        assert inverse == stack.inverse_relationships()
        assert inverse["parent_of"] == "child_of"
        assert inverse["godparent_of"] == "godchild_of"
        assert service.get_domain_symmetric_relationships("literary") == (
            stack.symmetric_relationships()
        )
        assert service.get_domain_name_conventions("literary") == stack.conventions()


def _real_stack(*packs: str) -> Any:
    """The real floor plus ``packs``."""
    from chaoscypher_core.services.sources.engine.extraction.domains import get_domain_registry
    from chaoscypher_core.services.sources.engine.extraction.domains.stack import DomainStack

    return DomainStack.resolve(get_domain_registry(EngineSettings()), list(packs))


# ---------------------------------------------------------------------------
# TestFinalizeDistributedExtraction
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
class TestFinalizeDistributedExtraction:
    """Smoke test for finalize_distributed_extraction with run_deduplication patched."""

    async def test_calls_run_deduplication_and_returns_results(self) -> None:
        """The finalization wires run_deduplication output through to results."""
        service = _make_service()
        dedup_entities = [{"id": "e0", "name": "Alice", "type": "Person"}]
        dedup_rels = [{"source": 0, "target": 0, "type": "knows"}]

        async def _fake_run_dedup(**_: object) -> tuple:
            return (dedup_entities, dedup_rels, [], {})

        with patch(
            "chaoscypher_core.services.sources.engine.extraction.service.run_deduplication",
            side_effect=_fake_run_dedup,
        ):
            result = await service.finalize_distributed_extraction(
                raw_entities=[{"name": "Alice", "type": "Person"}],
                raw_relationships=[{"source": 0, "target": 0, "type": "knows"}],
                generate_embeddings=False,
                detected_domain="literary",
            )

        assert len(result["entities"]) == 1
        assert result["entities"][0]["name"] == "Alice"
        assert result["metadata"]["total_entities"] == 1
        assert result["metadata"]["total_relationships"] == 1
        assert result["metadata"]["detected_domain"] == "literary"
        assert result["metadata"]["extraction_depth"] == "distributed"

    async def test_skips_cross_chunk_filters_when_config_omitted(self) -> None:
        """No filter pass when both filtering_config and edge_type_constraints are None.

        Backward-compat for callers like ``Engine.process_document`` that
        already ran cross-chunk filtering inside ``extract_entities_from_groups``.
        """
        service = _make_service()
        dedup_entities = [{"id": "e0", "name": "Alice", "type": "Person"}]
        dedup_rels = [{"source": 0, "target": 0, "type": "knows"}]

        async def _fake_run_dedup(**_: object) -> tuple:
            return (dedup_entities, dedup_rels, [], {})

        with (
            patch(
                "chaoscypher_core.services.sources.engine.extraction.service.run_deduplication",
                side_effect=_fake_run_dedup,
            ),
            patch(
                "chaoscypher_core.services.sources.engine.extraction.service."
                "apply_cross_chunk_relationship_filters"
            ) as mock_filter,
        ):
            await service.finalize_distributed_extraction(
                raw_entities=[{"name": "Alice", "type": "Person"}],
                raw_relationships=[{"source": 0, "target": 0, "type": "knows"}],
                generate_embeddings=False,
            )

        mock_filter.assert_not_called()

    async def test_runs_cross_chunk_filters_when_constraints_provided(self) -> None:
        """Cross-chunk filter is invoked once, fed dedup output, when constraints are set.

        Regression: the CLI extraction path silently lost type-constraint and
        relationship-limit enforcement before constraints/config were threaded
        into ``finalize_distributed_extraction``.
        """
        from chaoscypher_core.services.sources.engine.extraction.utils.filtering_config import (
            resolve_filtering_config,
        )

        service = _make_service()
        dedup_entities = [
            {"id": "e0", "name": "Alice", "type": "Person"},
            {"id": "e1", "name": "AcmeCorp", "type": "Organization"},
        ]
        dedup_rels = [{"source": 0, "target": 1, "type": "works_at"}]
        edge_type_constraints = {
            "works_at": {"source_types": ["Person"], "target_types": ["Organization"]}
        }
        filtering_config = resolve_filtering_config(mode="balanced")

        async def _fake_run_dedup(**_: object) -> tuple:
            return (dedup_entities, dedup_rels, [], {})

        # Filter passthrough: returns (entities, relationships) unchanged so we
        # can assert it was invoked with the expected inputs without modeling
        # the full filter pipeline.
        def _fake_filter(
            *,
            entities: list,
            relationships: list,
            edge_type_constraints: dict | None,
            filtering_config: object,
            filtering_log: object | None = None,
        ) -> tuple[list, list]:
            return entities, relationships

        with (
            patch(
                "chaoscypher_core.services.sources.engine.extraction.service.run_deduplication",
                side_effect=_fake_run_dedup,
            ),
            patch(
                "chaoscypher_core.services.sources.engine.extraction.service."
                "apply_cross_chunk_relationship_filters",
                side_effect=_fake_filter,
            ) as mock_filter,
        ):
            result = await service.finalize_distributed_extraction(
                raw_entities=[{"name": "Alice", "type": "Person"}],
                raw_relationships=[{"source": 0, "target": 1, "type": "works_at"}],
                generate_embeddings=False,
                edge_type_constraints=edge_type_constraints,
                filtering_config=filtering_config,
            )

        mock_filter.assert_called_once()
        kwargs = mock_filter.call_args.kwargs
        assert kwargs["entities"] is dedup_entities
        assert kwargs["relationships"] is dedup_rels
        assert kwargs["edge_type_constraints"] == edge_type_constraints
        assert kwargs["filtering_config"] is filtering_config
        # And the result still flows through normalization.
        assert result["metadata"]["total_relationships"] == 1

    async def test_forced_domain_recorded_in_metadata_finalize(self) -> None:
        """Forced-domain string value propagates into the result metadata.

        Regression guard for metadata propagation through
        finalize_distributed_extraction — independent of the _resolve_domain
        forced-parameter behavior added in Task 6, but adjacent in scope and
        worth keeping as a wiring guard.
        """
        service = _make_service()
        dedup_entities = [{"id": "e0", "name": "Alice", "type": "Person"}]
        dedup_rels: list[Any] = []

        async def _fake_run_dedup(**_: object) -> tuple:
            return (dedup_entities, dedup_rels, [], {})

        with patch(
            "chaoscypher_core.services.sources.engine.extraction.service.run_deduplication",
            side_effect=_fake_run_dedup,
        ):
            result = await service.finalize_distributed_extraction(
                raw_entities=[{"name": "Alice", "type": "Person"}],
                raw_relationships=[],
                generate_embeddings=False,
                detected_domain="auto_detected",
                forced_domain="technical",
            )

        assert result["metadata"]["forced_domain"] == "technical"
        assert result["metadata"]["detected_domain"] == "auto_detected"

    async def test_resolves_default_filtering_config_when_only_constraints_given(self) -> None:
        """If only edge_type_constraints is passed, a balanced FilteringConfig is resolved.

        The cross-chunk filter requires a ``FilteringConfig`` so the helper
        falls back to ``resolve_filtering_config()`` defaults rather than
        crashing.
        """
        service = _make_service()
        dedup_entities = [{"id": "e0", "name": "Alice", "type": "Person"}]
        dedup_rels = [{"source": 0, "target": 0, "type": "knows"}]

        async def _fake_run_dedup(**_: object) -> tuple:
            return (dedup_entities, dedup_rels, [], {})

        captured_config: list[Any] = []

        def _fake_filter(
            *,
            entities: list,
            relationships: list,
            edge_type_constraints: dict | None,
            filtering_config: object,
            filtering_log: object | None = None,
        ) -> tuple[list, list]:
            captured_config.append(filtering_config)
            return entities, relationships

        with (
            patch(
                "chaoscypher_core.services.sources.engine.extraction.service.run_deduplication",
                side_effect=_fake_run_dedup,
            ),
            patch(
                "chaoscypher_core.services.sources.engine.extraction.service."
                "apply_cross_chunk_relationship_filters",
                side_effect=_fake_filter,
            ),
        ):
            await service.finalize_distributed_extraction(
                raw_entities=[{"name": "Alice", "type": "Person"}],
                raw_relationships=[{"source": 0, "target": 0, "type": "knows"}],
                generate_embeddings=False,
                edge_type_constraints={"knows": {"source_types": [], "target_types": []}},
            )

        assert captured_config, "filter helper was not invoked"
        # A real FilteringConfig dataclass instance was constructed.
        cfg = captured_config[0]
        assert hasattr(cfg, "enable_type_constraints")
        assert hasattr(cfg, "enable_relationship_limits")


# ---------------------------------------------------------------------------
# TestResolveDomain (forced vs. auto-detect behavior)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestResolveDomain:
    """Tests for _resolve_domain forced vs. auto-detection failure modes."""

    def test_forced_domain_registry_failure_raises(self) -> None:
        """When the user explicitly forces a domain and the registry blows up,
        the service must fail loud rather than silently fall back to None
        (which would proceed as a generic-domain extraction without telling
        the user).
        """
        service = _make_service()

        def _broken_get_domain(name: str) -> None:
            raise RuntimeError("registry corrupted")

        with patch(
            "chaoscypher_core.services.sources.engine.extraction.domains.get_domain_registry",
            return_value=type("R", (), {"get_domain": staticmethod(_broken_get_domain)})(),
        ):
            with pytest.raises(RuntimeError, match="registry corrupted"):
                service._resolve_domain("legal_contracts", forced=True)

    def test_forced_domain_registry_failure_does_not_cache_none(self) -> None:
        """After a forced-domain failure, the cache must not contain None —
        a future retry (e.g. after a transient error) must re-attempt the
        registry lookup rather than returning a stale None.
        """
        service = _make_service()

        def _broken_get_domain(name: str) -> None:
            raise RuntimeError("transient failure")

        with patch(
            "chaoscypher_core.services.sources.engine.extraction.domains.get_domain_registry",
            return_value=type("R", (), {"get_domain": staticmethod(_broken_get_domain)})(),
        ):
            with pytest.raises(RuntimeError):
                service._resolve_domain("legal_contracts", forced=True)

        cache = service._stack_resolver()._stacks
        assert cache.get("legal_contracts") is None, (
            "_resolve_domain must not cache None when forced=True raises"
        )

    def test_auto_detected_domain_still_swallows(self) -> None:
        """Auto-detection is best-effort: a registry failure during auto-detect
        must continue to swallow + cache None so extraction can proceed
        (current behavior, regression guard).
        """
        service = _make_service()

        def _broken_get_domain(name: str) -> None:
            raise RuntimeError("transient")

        with patch(
            "chaoscypher_core.services.sources.engine.extraction.domains.get_domain_registry",
            return_value=type("R", (), {"get_domain": staticmethod(_broken_get_domain)})(),
        ):
            # Default forced=False: should NOT raise; should return None.
            result = service._resolve_domain("legal_contracts")

        assert result is None
