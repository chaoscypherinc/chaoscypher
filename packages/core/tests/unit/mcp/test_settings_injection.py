# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""MCP server + extraction must read engine settings, not the app singleton.

Tier 2 config-unification (Task C3): ``mcp/extraction.py`` and
``mcp/server.py`` previously reached for the process-wide app settings via
``chaoscypher_core.app_config.get_settings()`` to read domain-detection
sampling and the sanitized-filename length. Post-union those fields live on
the SAME settings classes that ``EngineSettings`` carries, so the MCP objects
must read them from the engine instance they already hold
(``self.engine.settings`` / ``engine.settings``).

These tests inject a custom value through the engine settings and assert the
production read honors it. Before the refactor they fail because the code
ignores the engine value and reads the app singleton's default instead. The
domain-detection sample no longer reads those knobs: since audit wave 3 the
MCP queue samples a source the way the import path does (``source_domain_sample``).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_core.mcp.extraction import ExtractionOrchestrator
from chaoscypher_core.services.sources.engine.extraction.domains import create_domain_sample_text
from chaoscypher_core.services.sources.engine.extraction.domains.registry import DomainRegistry


def _make_engine() -> MagicMock:
    """Build a mock engine whose settings carry what the orchestrator reads."""
    engine = MagicMock()
    engine.settings.current_database = "default"
    engine.settings.mcp.max_extraction_payload_bytes = 10 * 1024 * 1024
    engine.settings.mcp.extraction_rate_limit_per_minute = 100
    engine.settings.chunking.target_group_tokens = 900
    engine.settings.chunking.group_overlap = 1
    engine.settings.analysis.quick_sample_size = 5
    engine.storage_adapter = MagicMock()
    engine.storage_adapter.start_stage = AsyncMock()
    engine.storage_adapter.tick_stage = AsyncMock()
    engine.storage_adapter.complete_stage = AsyncMock()
    engine.storage_adapter.update_stage_extras = AsyncMock()
    engine.graph_repository = MagicMock()
    engine.graph_repository.list_templates.return_value = []
    engine.extraction_service = AsyncMock()
    engine.commit_service = AsyncMock()
    return engine


@pytest.mark.asyncio
async def test_get_tasks_park_branch_samples_like_the_import_path() -> None:
    """The park-gate detection proposal samples the source the way every path does.

    ``source_domain_sample``: ``create_domain_sample_text`` over the source's
    chunks for extraction, the import worker's sample. Until audit wave 3 the
    MCP queue sliced the first ``domain_detection_sample_chars`` of the first
    chunks, so a novel's opening 8,000 characters auto-detected as generic
    where the import path read it as literary.
    """
    engine = _make_engine()
    engine.storage_adapter.get_source.return_value = {
        "id": "src_park",
        "filename": "doc.pdf",
        "status": "indexed",
        "forced_domain": None,
        "extraction_domain": None,
        "extraction_confirmed_at": None,
        "confirmation_required": True,
    }
    chunks = [
        {"id": f"c{i}", "chunk_index": i, "content": f"chunk {i} " + "word " * 400}
        for i in range(12)
    ]
    engine.storage_adapter.get_chunks_for_extraction.return_value = chunks

    detection = {
        "detected_domain": "generic",
        "confidence": 0.5,
        "low_confidence": True,
        "ranking": [],
    }
    proposal = {
        "detected_domain": "generic",
        "confidence": 0.5,
        "ranking": [],
        "low_confidence": True,
    }

    orchestrator = ExtractionOrchestrator(engine=engine)

    with (
        patch("chaoscypher_core.mcp.extraction.gate_decision", return_value="park"),
        # The plan's stack reads the shipped floor (the detected domain is "generic").
        patch("chaoscypher_core.mcp.extraction.get_domain_registry", return_value=DomainRegistry()),
        patch(
            "chaoscypher_core.mcp.extraction.detect_extraction_domain",
            return_value=detection,
        ) as mock_detect,
        patch(
            "chaoscypher_core.mcp.extraction.proposal_from_detection",
            return_value=proposal,
        ),
        patch("chaoscypher_core.mcp.extraction.park_for_confirmation"),
    ):
        await orchestrator.get_tasks("src_park")

    engine.storage_adapter.list_chunks.assert_not_called()
    sample_text = mock_detect.call_args.kwargs["sample_text"]
    assert sample_text == create_domain_sample_text(chunks, content_key="content")
    assert "chunk 4 " in sample_text and "chunk 5 " in sample_text  # not just the opening


@pytest.mark.asyncio
async def test_get_tasks_autodetect_branch_samples_like_the_import_path() -> None:
    """The auto-detect branch (no forced/stored domain, gate proceeds) samples as the import path does."""
    engine = _make_engine()
    engine.storage_adapter.get_source.return_value = {
        "id": "src_auto",
        "filename": "doc.pdf",
        "status": "indexed",
        "filtering_mode": None,
        "extraction_depth": "full",
        "forced_domain": None,
        "extraction_domain": None,
        "_test_expected_chunk_indices": None,
        "stage_progress": {},
    }
    engine.storage_adapter.transition_source_status.return_value = True
    chunks = [{"id": f"c{i}", "chunk_index": i, "content": f"alpha {i} " * 300} for i in range(9)]
    engine.storage_adapter.get_chunks_for_extraction.return_value = chunks

    fake_domain = MagicMock(name="generic_domain")
    fake_domain.get_entity_exclusions.return_value = []
    fake_domain.get_strict_entity_types.return_value = False

    orchestrator = ExtractionOrchestrator(engine=engine)
    # ``build_extraction_groups`` is patched to [] below; a source with no
    # groups is now rejected after the CAS, and this test is about sampling.
    orchestrator._get_group_indices = lambda _sid: {0}

    with (
        patch("chaoscypher_core.mcp.extraction.gate_decision", return_value="proceed"),
        patch(
            "chaoscypher_core.services.sources.engine.extraction.orchestration.build_extraction_groups",
            return_value=[],
        ),
        patch("chaoscypher_core.services.sources.engine.extraction.domains.get_domain_registry"),
        # The plan's stack reads the shipped floor (the detected domain is "generic").
        patch("chaoscypher_core.mcp.extraction.get_domain_registry", return_value=DomainRegistry()),
        patch(
            "chaoscypher_core.mcp.extraction.detect_extraction_domain",
            return_value={
                "detected_domain": "generic",
                "domain": fake_domain,
                "confidence": 0.9,
            },
        ) as mock_detect,
        patch(
            "chaoscypher_core.services.sources.engine.extraction.utils.filtering_config.resolve_filtering_config",
            return_value=MagicMock(),
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
        await orchestrator.get_tasks("src_auto")

    engine.storage_adapter.list_chunks.assert_not_called()
    sample_text = mock_detect.call_args.kwargs["sample_text"]
    assert sample_text == create_domain_sample_text(chunks, content_key="content")


@pytest.mark.asyncio
async def test_fetch_url_source_truncates_filename_by_engine_setting() -> None:
    """``_fetch_url_source`` must truncate the sanitized filename using the
    engine's ``paths.max_filename_length``, not the app singleton default.
    """
    from chaoscypher_core.mcp.server import _fetch_url_source

    engine = MagicMock()
    engine.settings.current_database = "default"
    engine.settings.paths.max_filename_length = 12
    engine.settings.batching.upload_content_type_allowlist = ["text/html"]
    engine.settings.batching.max_upload_bytes = 5 * 1024 * 1024

    long_title = "A" * 200

    fake_page = MagicMock()
    fake_page.is_binary = False
    fake_page.content = "hello body"
    fake_page.title = long_title
    fake_page.content_type = "text/html"

    fake_scraper = MagicMock()
    fake_scraper.extract_full_content = AsyncMock(return_value=fake_page)

    with patch(
        "chaoscypher_core.adapters.web.search.WebScraper",
        return_value=fake_scraper,
    ):
        result = await _fetch_url_source(engine, "https://example.com")

    # filename is "<safe_title>.md"; safe_title truncated to 12 chars.
    safe_title = result["filename"][:-3]  # strip ".md"
    assert len(safe_title) == 12, (
        "filename stem must be truncated to engine.settings.paths."
        f"max_filename_length (12), got {len(safe_title)}: {result['filename']!r}"
    )
