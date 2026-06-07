# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""MCP extraction must respect chunking and filtering settings.

Workstream 3, Task 3.5: the MCP path at ``mcp/extraction.py`` historically
hardcoded ``target_tokens=900``, ``overlap=1``, and ``quick_sample_size=5``,
and ignored the source row's ``filtering_mode`` entirely. MCP-driven
extractions silently used defaults, so the same source uploaded with
``filtering_mode=strict`` would behave like ``balanced`` whenever the
client used MCP rather than Cortex.

These tests assert the MCP path now reads:

* ``engine.settings.chunking.target_group_tokens`` for group sizing,
* ``engine.settings.chunking.group_overlap`` for overlap,
* ``engine.settings.analysis.quick_sample_size`` for quick-mode sampling
  (same source as Cortex's ``import_service``),
* the source row's ``filtering_mode`` for cross-chunk filter behavior.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_core.mcp.extraction import ExtractionOrchestrator
from tests.unit.mcp.conftest import install_chunk_indices_shortcut


def _make_engine(
    *,
    target_group_tokens: int = 900,
    group_overlap: int = 1,
    quick_sample_size: int = 5,
) -> MagicMock:
    """Build a mock engine with configurable chunking settings."""
    engine = MagicMock()
    engine.settings.current_database = "default"
    engine.settings.mcp.max_extraction_payload_bytes = 10 * 1024 * 1024
    engine.settings.mcp.extraction_rate_limit_per_minute = 100
    engine.settings.chunking.target_group_tokens = target_group_tokens
    engine.settings.chunking.group_overlap = group_overlap
    engine.settings.analysis.quick_sample_size = quick_sample_size
    engine.storage_adapter = MagicMock()
    engine.graph_repository = MagicMock()
    engine.graph_repository.list_templates.return_value = []
    engine.extraction_service = AsyncMock()
    engine.commit_service = AsyncMock()
    # Stage-progress port methods are async — wire as AsyncMock so awaiting them works
    engine.storage_adapter.start_stage = AsyncMock()
    engine.storage_adapter.tick_stage = AsyncMock()
    engine.storage_adapter.complete_stage = AsyncMock()
    engine.storage_adapter.update_stage_extras = AsyncMock()
    return engine


@pytest.mark.asyncio
async def test_build_source_groups_passes_settings_target_tokens() -> None:
    """``_build_source_groups`` must thread ``target_group_tokens`` from settings."""
    engine = _make_engine(target_group_tokens=2000, group_overlap=3)
    engine.storage_adapter.get_chunks_for_extraction.return_value = [
        {"id": "c1", "chunk_index": 0, "content": "x"},
    ]
    engine.storage_adapter.get_source.return_value = {
        "id": "src_001",
        "filtering_mode": None,
        "extraction_domain": None,
        "forced_domain": None,
    }
    orchestrator = ExtractionOrchestrator(engine=engine)
    install_chunk_indices_shortcut(orchestrator)

    with patch(
        "chaoscypher_core.services.sources.engine.extraction.orchestration.build_extraction_groups"
    ) as mock_build:
        mock_build.return_value = []
        orchestrator._build_source_groups("src_001")

    mock_build.assert_called_once()
    _args, kwargs = mock_build.call_args
    assert kwargs["target_tokens"] == 2000, (
        "MCP must read target_group_tokens from settings.chunking, not hardcode 900"
    )
    assert kwargs["overlap"] == 3, (
        "MCP must read group_overlap from settings.chunking, not hardcode 1"
    )


@pytest.mark.asyncio
async def test_get_expected_indices_quick_uses_settings_sample_size() -> None:
    """Quick-mode sampling must read ``quick_sample_size`` from settings."""
    engine = _make_engine(quick_sample_size=3)
    orchestrator = ExtractionOrchestrator(engine=engine)
    install_chunk_indices_shortcut(orchestrator)

    with patch.object(
        orchestrator,
        "_get_group_indices",
        return_value={0, 1, 2, 3, 4, 5, 6, 7, 8, 9},
    ):
        result = orchestrator._get_expected_indices({"id": "src_001", "extraction_depth": "quick"})

    # With 10 indices and quick_sample_size=3, we expect a sample of size 3
    # (not the hardcoded default of 5).
    assert len(result) == 3, (
        f"expected 3-sample (settings.analysis.quick_sample_size=3), got {len(result)}"
    )


@pytest.mark.asyncio
async def test_get_tasks_reads_filtering_mode_from_source_row() -> None:
    """``get_tasks`` must read ``filtering_mode`` from the source row.

    Pre-fix behaviour: MCP ignored ``filtering_mode`` entirely. The fix is to
    persist a resolved ``FilteringConfig`` on the orchestrator so the
    downstream ``finalize`` step can apply the same filters Cortex does.

    This test mocks the dependencies ``get_tasks`` needs to run end-to-end
    so it can assert the resolved config landed in
    ``orchestrator._filtering_configs[source_id]`` — the behaviour that
    matters — rather than papering over a heavily-mocked failure with a
    blanket ``try/except`` and asserting on call args.
    """
    engine = _make_engine()
    engine.storage_adapter.get_source.return_value = {
        "id": "src_001",
        "filename": "x.pdf",
        "status": "indexed",
        "filtering_mode": "strict",
        "extraction_depth": "full",
        "forced_domain": None,
        "extraction_domain": None,
        "extraction_chunk_indices": None,
        "stage_progress": {},
    }
    engine.storage_adapter.transition_source_status.return_value = True
    engine.storage_adapter.get_chunks_for_extraction.return_value = [
        {"id": "c1", "chunk_index": 0, "content": "x"},
    ]
    engine.storage_adapter.list_chunks.return_value = [
        {"id": "c1", "chunk_index": 0, "content": "x"},
    ]
    # ``get_tasks`` lists existing graph templates for the type-reuse hints
    # block; an empty list is fine here.
    engine.graph_repository.list_templates.return_value = []

    orchestrator = ExtractionOrchestrator(engine=engine)
    install_chunk_indices_shortcut(orchestrator)

    sentinel = MagicMock(name="strict_config")

    # Mock the auto-detect path so we don't reach into the real domain
    # registry. The orchestrator only needs ``detection["domain"]`` to be
    # truthy enough to drive ``format_extraction_templates``; a fully-faked
    # ConfigurableDomain shape covers the methods get_tasks calls.
    fake_domain = MagicMock(name="generic_domain")
    fake_domain.get_entity_exclusions.return_value = []
    fake_domain.get_strict_entity_types.return_value = False

    with (
        patch(
            "chaoscypher_core.services.sources.engine.extraction.utils.filtering_config.resolve_filtering_config",
            return_value=sentinel,
        ),
        patch(
            "chaoscypher_core.services.sources.engine.extraction.orchestration.build_extraction_groups",
            return_value=[],
        ),
        patch("chaoscypher_core.services.sources.engine.extraction.domains.get_domain_registry"),
        patch(
            "chaoscypher_core.services.sources.engine.extraction.orchestration.detect_extraction_domain",
            return_value={
                "detected_domain": "generic",
                "domain": fake_domain,
                "confidence": 0.9,
            },
        ),
        patch(
            "chaoscypher_core.services.sources.engine.extraction.orchestration.format_extraction_templates",
            return_value={
                "node_templates": "",
                "edge_templates": "",
                "entity_examples": "",
                "relationship_examples": "",
            },
        ),
    ):
        await orchestrator.get_tasks("src_001")

    # Behaviour assertion: the resolved config landed in the per-source
    # cache so ``finalize`` can apply the same filters Cortex would.
    assert orchestrator._filtering_configs["src_001"] is sentinel, (
        "MCP get_tasks must persist the row's filtering_mode resolution "
        "into ``_filtering_configs`` so finalize can honour the upload-time preset"
    )


@pytest.mark.asyncio
async def test_finalize_threads_cached_filtering_config_and_file_info() -> None:
    """``finalize`` must thread the cached FilteringConfig and persist the
    row's ``filtering_mode`` into ``file_info`` for the commit phase.

    Pre-fix behaviour (the W3 spec-review gap on Task 3.5): ``get_tasks``
    cached a resolved ``FilteringConfig`` keyed on ``source_id``, but
    ``finalize`` ignored the cache and called
    ``finalize_distributed_extraction`` with neither ``filtering_config``
    nor ``edge_type_constraints``. ``commit_service.commit`` was also called
    with a ``file_info`` that lacked ``filtering_mode``, so the commit-side
    orphan-drop fell back to engine defaults — silently disagreeing with
    the row's preset.
    """
    engine = _make_engine()
    engine.storage_adapter.transition_source_status.return_value = True
    engine.storage_adapter.get_source.return_value = {
        "id": "src_002",
        "filename": "doc.pdf",
        "status": "mcp_extracting",
        "filtering_mode": "strict",
        "extraction_depth": "full",
        "extraction_domain": None,
        "forced_domain": None,
        "extraction_chunk_indices": [0],
        "chunk_count": 1,
    }
    engine.storage_adapter.list_extraction_submissions.return_value = [
        {
            "chunk_group_index": 0,
            "entities_text": "",
            "relationships_text": "",
        }
    ]
    engine.storage_adapter.delete_extraction_submissions.return_value = 1
    engine.extraction_service.finalize_distributed_extraction = AsyncMock(
        return_value={
            "entities": [],
            "relationships": [],
            "suggested_templates": [],
            "suggested_edge_templates": [],
            "metadata": {"total_entities": 0, "total_relationships": 0},
        }
    )
    engine.commit_service.commit = AsyncMock(
        return_value={
            "created_nodes": [],
            "created_edges": [],
            "created_templates": [],
        }
    )

    orchestrator = ExtractionOrchestrator(engine=engine)
    install_chunk_indices_shortcut(orchestrator)

    # Seed the per-source FilteringConfig cache the way ``get_tasks`` would.
    sentinel_filtering_config = MagicMock(name="cached_strict_config")
    orchestrator._filtering_configs["src_002"] = sentinel_filtering_config

    # The orchestrator avoids importing the domain registry when the source
    # has no domain (extraction_domain is None and forced_domain is None),
    # so the heavy mock environment doesn't need to stub it.
    with patch(
        "chaoscypher_core.services.sources.engine.extraction.orchestration.cache_quality_scores"
    ):
        await orchestrator.finalize("src_002")

    # 1. finalize_distributed_extraction was passed the cached config and
    #    edge_type_constraints (None here because no domain in scope).
    fde = engine.extraction_service.finalize_distributed_extraction
    fde.assert_awaited_once()
    fde_kwargs = fde.await_args.kwargs
    assert fde_kwargs["filtering_config"] is sentinel_filtering_config, (
        "finalize must thread the cached FilteringConfig from get_tasks "
        "into finalize_distributed_extraction"
    )
    assert "edge_type_constraints" in fde_kwargs, (
        "finalize must pass edge_type_constraints (worker-path parity)"
    )

    # 2. commit_service.commit was called with file_info carrying the row's
    #    filtering_mode so the commit-side orphan-drop honours strict.
    commit = engine.commit_service.commit
    commit.assert_awaited_once()
    commit_kwargs = commit.await_args.kwargs
    file_info = commit_kwargs["file_info"]
    assert file_info["filtering_mode"] == "strict", (
        f"file_info must carry filtering_mode='strict', got {file_info!r}"
    )


@pytest.mark.asyncio
async def test_finalize_falls_back_to_row_when_cache_misses() -> None:
    """When the cache is empty (e.g. finalize runs in a different process
    than ``get_tasks``), ``finalize`` re-resolves the FilteringConfig from
    the row's ``filtering_mode`` rather than passing ``None`` and silently
    falling back to engine defaults.
    """
    engine = _make_engine()
    engine.storage_adapter.transition_source_status.return_value = True
    engine.storage_adapter.get_source.return_value = {
        "id": "src_003",
        "filename": "doc.pdf",
        "status": "mcp_extracting",
        "filtering_mode": "strict",
        "extraction_depth": "full",
        "extraction_domain": None,
        "forced_domain": None,
        "extraction_chunk_indices": [0],
        "chunk_count": 1,
    }
    engine.storage_adapter.list_extraction_submissions.return_value = [
        {"chunk_group_index": 0, "entities_text": "", "relationships_text": ""}
    ]
    engine.storage_adapter.delete_extraction_submissions.return_value = 1
    engine.extraction_service.finalize_distributed_extraction = AsyncMock(
        return_value={
            "entities": [],
            "relationships": [],
            "suggested_templates": [],
            "suggested_edge_templates": [],
            "metadata": {"total_entities": 0, "total_relationships": 0},
        }
    )
    engine.commit_service.commit = AsyncMock(
        return_value={
            "created_nodes": [],
            "created_edges": [],
            "created_templates": [],
        }
    )

    orchestrator = ExtractionOrchestrator(engine=engine)
    install_chunk_indices_shortcut(orchestrator)
    # Deliberately do not seed orchestrator._filtering_configs — simulate the
    # cross-process / fresh-instance case.
    assert "src_003" not in orchestrator._filtering_configs

    sentinel = MagicMock(name="re_resolved_strict")
    with (
        patch(
            "chaoscypher_core.services.sources.engine.extraction.utils.filtering_config.resolve_filtering_config",
            return_value=sentinel,
        ) as mock_resolve,
        patch(
            "chaoscypher_core.services.sources.engine.extraction.orchestration.cache_quality_scores"
        ),
    ):
        await orchestrator.finalize("src_003")

    # The fallback must have invoked resolve_filtering_config with mode='strict'.
    strict_calls = [
        call for call in mock_resolve.call_args_list if call.kwargs.get("mode") == "strict"
    ]
    assert strict_calls, (
        "finalize must call resolve_filtering_config(mode='strict') when the "
        "cache misses, so the row's mode still drives cross-chunk filtering"
    )

    fde = engine.extraction_service.finalize_distributed_extraction
    fde.assert_awaited_once()
    assert fde.await_args.kwargs["filtering_config"] is sentinel


@pytest.mark.asyncio
async def test_finalize_threads_minimum_alias_length_into_parser() -> None:
    """``finalize`` must thread ``minimum_alias_length`` from the resolved
    FilteringConfig into ``parse_extraction_output`` so MCP submissions
    honour the slider.

    Pre-fix behaviour (W4 review-gap): ``finalize`` resolved a
    ``FilteringConfig`` for the cross-chunk filter step, but the parse
    loop above it called ``parse_extraction_output`` with no
    ``minimum_alias_length``. That meant MCP submissions for a source
    uploaded with ``filtering_mode=maximum`` (which sets
    ``minimum_alias_length=3``) silently kept single-char and 2-char
    aliases — the slider had no effect on the MCP path.

    This test submits a single MCP submission whose entity line carries
    a 1-char alias ('X'), a 2-char alias ('AI'), and a 3-char alias
    ('LLM'). With ``filtering_mode=maximum`` resolved from the row, the
    parsed entity must have only 'LLM' in aliases.
    """
    engine = _make_engine()
    engine.storage_adapter.transition_source_status.return_value = True
    engine.storage_adapter.get_source.return_value = {
        "id": "src_alias",
        "filename": "doc.pdf",
        "status": "mcp_extracting",
        "filtering_mode": "maximum",
        "extraction_depth": "full",
        "extraction_domain": None,
        "forced_domain": None,
        "extraction_chunk_indices": [0],
        "chunk_count": 1,
    }
    # The submission carries a single E| line whose aliases mix lengths.
    engine.storage_adapter.list_extraction_submissions.return_value = [
        {
            "chunk_group_index": 0,
            "entities_text": "E|OpenAI|Organization|X; AI; LLM|0.9|S1|An AI lab",
            "relationships_text": "",
        }
    ]
    engine.storage_adapter.delete_extraction_submissions.return_value = 1
    engine.extraction_service.finalize_distributed_extraction = AsyncMock(
        return_value={
            "entities": [],
            "relationships": [],
            "suggested_templates": [],
            "suggested_edge_templates": [],
            "metadata": {"total_entities": 0, "total_relationships": 0},
        }
    )
    engine.commit_service.commit = AsyncMock(
        return_value={
            "created_nodes": [],
            "created_edges": [],
            "created_templates": [],
        }
    )

    orchestrator = ExtractionOrchestrator(engine=engine)
    install_chunk_indices_shortcut(orchestrator)
    # Cache miss path — finalize must re-resolve from the row's
    # filtering_mode='maximum' and pass minimum_alias_length=3 through to
    # parse_extraction_output.
    assert "src_alias" not in orchestrator._filtering_configs

    with patch(
        "chaoscypher_core.services.sources.engine.extraction.orchestration.cache_quality_scores"
    ):
        await orchestrator.finalize("src_alias")

    # The aggregated entity list passed downstream should contain a single
    # entity whose aliases were filtered by the slider's minimum length (3).
    fde = engine.extraction_service.finalize_distributed_extraction
    fde.assert_awaited_once()
    raw_entities = fde.await_args.kwargs["raw_entities"]
    assert len(raw_entities) == 1, f"expected exactly one parsed entity, got {len(raw_entities)}"
    aliases = raw_entities[0].get("aliases", [])
    assert "X" not in aliases, (
        "single-char alias must be dropped when filtering_mode=maximum (minimum_alias_length=3)"
    )
    assert "AI" not in aliases, (
        "two-char alias must be dropped when filtering_mode=maximum (minimum_alias_length=3)"
    )
    assert "LLM" in aliases, (
        "three-char alias must be kept when filtering_mode=maximum (minimum_alias_length=3)"
    )


@pytest.mark.asyncio
async def test_finalize_persists_entity_embeddings_for_commit() -> None:
    """``finalize`` must persist generated entity embeddings before commit.

    Regression (worker-parity gap): the MCP manual-extraction path computes
    entity embeddings — ``finalize_distributed_extraction(generate_embeddings
    =True)`` returns them under ``result["embeddings"]`` — but then dropped
    them with ``result.pop("embeddings")`` and never called
    ``_store_entity_embeddings``. The worker/distributed finalizer DOES store
    them, so commit's ``EntityCommitHandler._load_embeddings`` can read them
    back by ``entity_id`` and attach real vectors to the graph nodes. Without
    parity the MCP path committed every node with a null embedding, leaving
    ``vec_search_nodes`` empty — node vector search and GraphRAG seeding
    silently degraded to keyword-only matching.
    """
    engine = _make_engine()
    engine.storage_adapter.transition_source_status.return_value = True
    engine.storage_adapter.get_source.return_value = {
        "id": "src_emb",
        "filename": "doc.txt",
        "status": "mcp_extracting",
        "filtering_mode": None,
        "extraction_depth": "full",
        "extraction_domain": None,
        "forced_domain": None,
        "extraction_chunk_indices": [0],
        "chunk_count": 1,
    }
    engine.storage_adapter.list_extraction_submissions.return_value = [
        {"chunk_group_index": 0, "entities_text": "", "relationships_text": ""}
    ]
    engine.storage_adapter.delete_extraction_submissions.return_value = 1

    entities = [{"id": "e0", "name": "Napoleon"}]
    embeddings_payload = {
        "embeddings": [[0.1, 0.2, 0.3]],
        "model": "test-embed",
        "dimensions": 3,
    }
    engine.extraction_service.finalize_distributed_extraction = AsyncMock(
        return_value={
            "entities": entities,
            "relationships": [],
            "suggested_templates": [],
            "suggested_edge_templates": [],
            "metadata": {"total_entities": 1, "total_relationships": 0},
            "embeddings": embeddings_payload,
        }
    )
    engine.commit_service.commit = AsyncMock(
        return_value={"created_nodes": ["n0"], "created_edges": [], "created_templates": []}
    )

    orchestrator = ExtractionOrchestrator(engine=engine)
    install_chunk_indices_shortcut(orchestrator)

    # Snapshot the embeddings payload at store-time: ``finalize`` pops
    # ``result["embeddings"]`` right after persisting, and MagicMock records
    # references (not copies), so a post-call read would see the pop. The
    # side_effect captures the value while the call is in flight.
    captured = {}

    def _record_store(adapter, extraction_results, matched_entities, source_id, database_name):
        captured["embeddings"] = (extraction_results or {}).get("embeddings")
        captured["entities"] = matched_entities
        captured["source_id"] = source_id

    with (
        patch(
            "chaoscypher_core.operations.extraction.extraction_finalizer._store_entity_embeddings",
            side_effect=_record_store,
        ) as mock_store,
        patch(
            "chaoscypher_core.services.sources.engine.extraction.orchestration.cache_quality_scores"
        ),
    ):
        await orchestrator.finalize("src_emb")

    mock_store.assert_called_once()
    assert captured["embeddings"] == embeddings_payload, (
        "MCP finalize must persist the generated entity embeddings (parity with "
        "the worker finalizer) so commit can attach real vectors to the nodes — "
        "it must not discard them before commit"
    )
    assert captured["entities"] == entities, (
        "the final entity list must be passed so embeddings join by entity_id"
    )
    assert captured["source_id"] == "src_emb"
