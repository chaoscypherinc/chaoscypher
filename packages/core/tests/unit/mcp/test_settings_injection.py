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
ignores the engine value and reads the app singleton's default instead.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_core.mcp.extraction import ExtractionOrchestrator


def _make_engine(
    *,
    domain_detection_sample_count: int = 5,
    domain_detection_sample_chars: int = 2000,
) -> MagicMock:
    """Build a mock engine whose settings carry custom domain-detection knobs."""
    engine = MagicMock()
    engine.settings.current_database = "default"
    engine.settings.mcp.max_extraction_payload_bytes = 10 * 1024 * 1024
    engine.settings.mcp.extraction_rate_limit_per_minute = 100
    engine.settings.chunking.target_group_tokens = 900
    engine.settings.chunking.group_overlap = 1
    engine.settings.analysis.quick_sample_size = 5
    # The fields under test — explicitly set so a MagicMock auto-attr never
    # masks a regression where the code reads the app singleton instead.
    engine.settings.extraction.domain_detection_sample_count = domain_detection_sample_count
    engine.settings.extraction.domain_detection_sample_chars = domain_detection_sample_chars
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
async def test_get_tasks_park_branch_samples_from_engine_settings() -> None:
    """The park-gate detection proposal must sample using the engine's
    ``extraction.domain_detection_sample_count`` / ``_sample_chars``.

    With a custom count of 7 and 50 chars, ``list_chunks`` must be called with
    ``limit=7`` and the sample text truncated to 50 chars — values that come
    from ``engine.settings``, not the app-singleton defaults (5 / 2000).
    """
    engine = _make_engine(
        domain_detection_sample_count=7,
        domain_detection_sample_chars=50,
    )
    engine.storage_adapter.get_source.return_value = {
        "id": "src_park",
        "filename": "doc.pdf",
        "status": "indexed",
        "forced_domain": None,
        "extraction_domain": None,
        "extraction_confirmed_at": None,
        "confirmation_required": True,
    }
    long_content = "word " * 100  # 500 chars, well past the 50-char window
    engine.storage_adapter.list_chunks.return_value = [
        {"id": "c1", "chunk_index": 0, "content": long_content},
    ]

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
        patch("chaoscypher_core.mcp.extraction.get_domain_registry"),
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

    # 1. list_chunks limit comes from engine settings (7), not the default 5.
    list_kwargs = engine.storage_adapter.list_chunks.call_args.kwargs
    assert list_kwargs["limit"] == 7, (
        "park-branch detection must read domain_detection_sample_count from "
        "engine.settings.extraction, not the app singleton"
    )
    # 2. sample_text truncated to 50 chars before detection.
    sample_text = mock_detect.call_args.kwargs["sample_text"]
    assert len(sample_text) == 50, (
        "sample_text must be sliced to domain_detection_sample_chars from "
        f"engine.settings (50), got {len(sample_text)}"
    )


@pytest.mark.asyncio
async def test_get_tasks_autodetect_branch_samples_from_engine_settings() -> None:
    """The auto-detect branch (no forced/stored domain, gate proceeds) must
    also sample using the engine's domain-detection settings.
    """
    engine = _make_engine(
        domain_detection_sample_count=3,
        domain_detection_sample_chars=40,
    )
    engine.storage_adapter.get_source.return_value = {
        "id": "src_auto",
        "filename": "doc.pdf",
        "status": "indexed",
        "filtering_mode": None,
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
    long_content = "alpha " * 100
    engine.storage_adapter.list_chunks.return_value = [
        {"id": "c1", "chunk_index": 0, "content": long_content},
    ]

    fake_domain = MagicMock(name="generic_domain")
    fake_domain.get_entity_exclusions.return_value = []
    fake_domain.get_strict_entity_types.return_value = False

    orchestrator = ExtractionOrchestrator(engine=engine)

    with (
        patch("chaoscypher_core.mcp.extraction.gate_decision", return_value="proceed"),
        patch(
            "chaoscypher_core.services.sources.engine.extraction.orchestration.build_extraction_groups",
            return_value=[],
        ),
        patch("chaoscypher_core.services.sources.engine.extraction.domains.get_domain_registry"),
        patch("chaoscypher_core.mcp.extraction.get_domain_registry"),
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

    list_kwargs = engine.storage_adapter.list_chunks.call_args.kwargs
    assert list_kwargs["limit"] == 3, (
        "auto-detect branch must read domain_detection_sample_count from "
        "engine.settings.extraction, not the app singleton"
    )
    sample_text = mock_detect.call_args.kwargs["sample_text"]
    assert len(sample_text) == 40, (
        "sample_text must be sliced to domain_detection_sample_chars from "
        f"engine.settings (40), got {len(sample_text)}"
    )


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
