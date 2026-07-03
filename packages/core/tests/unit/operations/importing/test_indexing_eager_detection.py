# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the wizard eager-detection step (§3.1 of the wizard design).

Covers:
- Gate-eligible source (confirmation_required=True, no forced_domain):
  detection_proposal is written to the SourceRow without changing status
  (status stays INDEXING).
- Non-gate-eligible source (forced_domain set): NO eager proposal written.
- Non-gate-eligible source (confirmation_required=False): NO eager proposal
  written.
- Analysis-gate dedupe: when detection_proposal already present on the source
  row at park-time, detect_extraction_domain is NOT called a second time and
  the pre-written proposal is reused as the park payload.

Patch targets follow the convention in test_import_analysis_gate.py: symbols
imported function-locally inside the handler are patched at their SOURCE
module (``orchestration.detect_extraction_domain``,
``domains.get_domain_registry``, ``confirmation_gate.write_detection_proposal``,
``confirmation_gate.park_for_confirmation``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_chunking_result(**kw: Any) -> MagicMock:
    defaults = {
        "total_small_chunks": 1,
        "total_groups": 1,
        "chunks_filtered": 0,
        "normalize_drops": 0,
        "prestrip_lines_removed": 0,
        "chunks_skipped_by_depth": 0,
    }
    defaults.update(kw)
    r = MagicMock()
    for k, v in defaults.items():
        setattr(r, k, v)
    return r


def _detect_result(domain: str = "technical") -> dict[str, Any]:
    return {
        "domain": MagicMock(),
        "detected_domain": domain,
        "confidence": 0.9,
        "ranking": [{"domain": domain, "score": 0.9}],
        "low_confidence": False,
        "entity_guidance": "",
        "relationship_guidance": "",
    }


def _settings() -> MagicMock:
    s = MagicMock()
    s.priorities.background = 50
    s.data_dir = "/tmp"
    return s


def _engine_settings(tmp_path: Path) -> MagicMock:
    """MagicMock engine_settings with a real data_dir.

    _run_indexing computes ``Path(engine_settings.paths.data_dir)`` (original-
    text persistence, vision, error-path cleanup); an unpinned MagicMock
    stringifies into a literal ``<MagicMock name='mock.paths.data_dir' ...>``
    directory at the repo root (issue #249).
    """
    engine_settings = MagicMock()
    engine_settings.paths.data_dir = str(tmp_path)
    return engine_settings


# ---------------------------------------------------------------------------
# Part 1: Eager detection in _run_indexing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_eager_detection_written_for_gate_eligible_source(
    monkeypatch, tmp_path: Path
) -> None:
    """Gate-eligible source gets detection_proposal written after store_chunks.

    Invariants:
    - detect_extraction_domain called exactly once (during indexing, not again).
    - write_detection_proposal called with the proposal blob.
    - Status stays INDEXING (no park_for_confirmation call).
    """
    from chaoscypher_core.operations.importing import indexing_handler

    adapter = MagicMock()
    # Source is gate-eligible: confirmation_required=True, no forced_domain.
    adapter.get_source.return_value = {
        "id": "src-1",
        "status": "indexing",
        "confirmation_required": True,
        "forced_domain": None,
    }

    monkeypatch.setattr(
        indexing_handler,
        "_extract_text",
        lambda **kw: (
            "x" * 200,
            {"lines_removed": 0, "paragraphs_deduplicated": 0, "chars_removed": 0},
        ),
    )
    fake_loader_registry = MagicMock()
    fake_loader_registry.load_document.return_value = [{"content": "x", "metadata": {}}]
    monkeypatch.setattr(
        "chaoscypher_core.services.sources.loaders.get_loader_registry",
        lambda *a, **kw: fake_loader_registry,
    )
    monkeypatch.setattr(
        indexing_handler,
        "_apply_vision_processing",
        AsyncMock(side_effect=lambda **kw: (kw["documents"], None)),
    )

    chunking_service = MagicMock()
    chunking_service.create_chunks = AsyncMock(return_value=_make_chunking_result())
    chunking_service.store_chunks = MagicMock()

    monkeypatch.setattr(
        indexing_handler,
        "queue_embed_chunks",
        AsyncMock(return_value="tsk_e1"),
    )
    monkeypatch.setattr(indexing_handler, "event_bus", MagicMock())

    detect_result = _detect_result()

    with (
        patch(
            "chaoscypher_core.services.sources.engine.extraction.domains.get_domain_registry",
            return_value=MagicMock(),
        ),
        patch(
            "chaoscypher_core.services.sources.engine.extraction.orchestration.detect_extraction_domain",
            return_value=detect_result,
        ) as detect_spy,
        patch(
            "chaoscypher_core.operations.importing.confirmation_gate.write_detection_proposal"
        ) as write_spy,
        patch(
            "chaoscypher_core.operations.importing.confirmation_gate.park_for_confirmation"
        ) as park_spy,
    ):
        result = await indexing_handler._run_indexing(
            file_id="src-1",
            file_info={"filename": "doc.txt"},
            filepath="/tmp/doc.txt",
            analysis_depth="full",
            enable_normalization=False,
            enable_vision=False,
            adapter=adapter,
            chunking_service=chunking_service,
            engine_settings=_engine_settings(tmp_path),
            settings=_settings(),
            database_name="default",
        )

    # Detection ran once (eager).
    detect_spy.assert_called_once()
    # Proposal was written (status-preserving write).
    write_spy.assert_called_once()
    # write_detection_proposal is called with positional args: (adapter, file_id, proposal)
    written_proposal = write_spy.call_args[0][2]
    assert written_proposal["detected_domain"] == "technical"
    assert "ranking" in written_proposal
    assert "confidence" in written_proposal
    assert "low_confidence" in written_proposal
    # park_for_confirmation was NOT called (that changes status to AWAITING_CONFIRMATION).
    park_spy.assert_not_called()
    # Status in result is still INDEXING.
    assert result.get("status") == "indexing"


@pytest.mark.asyncio
async def test_eager_detection_skipped_for_forced_domain(monkeypatch, tmp_path: Path) -> None:
    """No eager proposal written when source has forced_domain set."""
    from chaoscypher_core.operations.importing import indexing_handler

    adapter = MagicMock()
    # NOT gate-eligible: forced_domain is set.
    adapter.get_source.return_value = {
        "id": "src-2",
        "status": "indexing",
        "confirmation_required": True,
        "forced_domain": "technical",
    }

    monkeypatch.setattr(
        indexing_handler,
        "_extract_text",
        lambda **kw: (
            "x" * 200,
            {"lines_removed": 0, "paragraphs_deduplicated": 0, "chars_removed": 0},
        ),
    )
    fake_loader_registry = MagicMock()
    fake_loader_registry.load_document.return_value = [{"content": "x", "metadata": {}}]
    monkeypatch.setattr(
        "chaoscypher_core.services.sources.loaders.get_loader_registry",
        lambda *a, **kw: fake_loader_registry,
    )
    monkeypatch.setattr(
        indexing_handler,
        "_apply_vision_processing",
        AsyncMock(side_effect=lambda **kw: (kw["documents"], None)),
    )

    chunking_service = MagicMock()
    chunking_service.create_chunks = AsyncMock(return_value=_make_chunking_result())
    chunking_service.store_chunks = MagicMock()

    monkeypatch.setattr(
        indexing_handler,
        "queue_embed_chunks",
        AsyncMock(return_value="tsk_e1"),
    )
    monkeypatch.setattr(indexing_handler, "event_bus", MagicMock())

    with (
        patch(
            "chaoscypher_core.operations.importing.confirmation_gate.write_detection_proposal"
        ) as write_spy,
    ):
        await indexing_handler._run_indexing(
            file_id="src-2",
            file_info={"filename": "doc.txt"},
            filepath="/tmp/doc.txt",
            analysis_depth="full",
            enable_normalization=False,
            enable_vision=False,
            adapter=adapter,
            chunking_service=chunking_service,
            engine_settings=_engine_settings(tmp_path),
            settings=_settings(),
            database_name="default",
        )

    write_spy.assert_not_called()


@pytest.mark.asyncio
async def test_eager_detection_skipped_when_confirmation_not_required(
    monkeypatch, tmp_path: Path
) -> None:
    """No eager proposal written when confirmation_required=False."""
    from chaoscypher_core.operations.importing import indexing_handler

    adapter = MagicMock()
    # NOT gate-eligible: confirmation_required=False.
    adapter.get_source.return_value = {
        "id": "src-3",
        "status": "indexing",
        "confirmation_required": False,
        "forced_domain": None,
    }

    monkeypatch.setattr(
        indexing_handler,
        "_extract_text",
        lambda **kw: (
            "x" * 200,
            {"lines_removed": 0, "paragraphs_deduplicated": 0, "chars_removed": 0},
        ),
    )
    fake_loader_registry = MagicMock()
    fake_loader_registry.load_document.return_value = [{"content": "x", "metadata": {}}]
    monkeypatch.setattr(
        "chaoscypher_core.services.sources.loaders.get_loader_registry",
        lambda *a, **kw: fake_loader_registry,
    )
    monkeypatch.setattr(
        indexing_handler,
        "_apply_vision_processing",
        AsyncMock(side_effect=lambda **kw: (kw["documents"], None)),
    )

    chunking_service = MagicMock()
    chunking_service.create_chunks = AsyncMock(return_value=_make_chunking_result())
    chunking_service.store_chunks = MagicMock()

    monkeypatch.setattr(
        indexing_handler,
        "queue_embed_chunks",
        AsyncMock(return_value="tsk_e1"),
    )
    monkeypatch.setattr(indexing_handler, "event_bus", MagicMock())

    with (
        patch(
            "chaoscypher_core.operations.importing.confirmation_gate.write_detection_proposal"
        ) as write_spy,
    ):
        await indexing_handler._run_indexing(
            file_id="src-3",
            file_info={"filename": "doc.txt"},
            filepath="/tmp/doc.txt",
            analysis_depth="full",
            enable_normalization=False,
            enable_vision=False,
            adapter=adapter,
            chunking_service=chunking_service,
            engine_settings=_engine_settings(tmp_path),
            settings=_settings(),
            database_name="default",
        )

    write_spy.assert_not_called()


# ---------------------------------------------------------------------------
# Part 2: Analysis-gate dedupe (import_service park path reuses pre-written
# detection_proposal instead of calling detect_extraction_domain again)
# ---------------------------------------------------------------------------


def _make_analysis_service(source_repository: object) -> Any:
    from chaoscypher_core.operations.importing.import_service import (
        ImportOperationsService,
    )
    from chaoscypher_core.settings import EngineSettings

    return ImportOperationsService(
        graph_repository=MagicMock(),
        config_manager=MagicMock(),
        source_manager=MagicMock(),
        trigger_service=MagicMock(),
        llm_service=AsyncMock(),
        source_repository=source_repository,
        chunking_service=MagicMock(),
        indexing_service=MagicMock(),
        engine_settings=EngineSettings(current_database="default"),
    )


def _make_settings_for_service() -> MagicMock:
    settings = MagicMock()
    settings.current_database = "default"
    return settings


def _indexed_source_with_proposal() -> dict[str, Any]:
    return {
        "id": "src-1",
        "status": "indexed",
        "forced_domain": None,
        "confirmation_required": True,
        "extraction_confirmed_at": None,
        "filename": "doc.pdf",
        "filepath": "/tmp/doc.pdf",
        "detection_proposal": {
            "ranking": [{"domain": "legal", "score": 0.85}],
            "confidence": 0.85,
            "detected_domain": "legal",
            "low_confidence": False,
        },
    }


@pytest.mark.asyncio
async def test_analysis_gate_park_reuses_prewritten_proposal() -> None:
    """When detection_proposal already set, detect is NOT called on park.

    The analysis-handler gate must reuse the eagerly-written proposal instead
    of calling detect_extraction_domain a second time — no drift, no double
    heuristic run.
    """
    adapter = MagicMock(unsafe=True)
    adapter.get_source.return_value = _indexed_source_with_proposal()
    adapter.assert_extractable.return_value = None
    # chunks ARE available (but detect must NOT be called when proposal present)
    adapter.get_chunks_for_extraction.return_value = [{"content": "legal text here"}]

    service = _make_analysis_service(adapter)

    with (
        patch(
            "chaoscypher_core.app_config.get_settings",
            return_value=_make_settings_for_service(),
        ),
        patch(
            "chaoscypher_core.operations.pause_guard.check_paused",
            return_value=MagicMock(paused=False),
        ),
        patch(
            "chaoscypher_core.services.sources.engine.extraction.domains.get_domain_registry",
            return_value=MagicMock(),
        ) as registry_spy,
        patch(
            "chaoscypher_core.services.sources.engine.extraction.orchestration.detect_extraction_domain",
        ) as detect_spy,
        patch(
            "chaoscypher_core.operations.importing.confirmation_gate.park_for_confirmation"
        ) as park_spy,
    ):
        result = await service._import_analysis_handler(
            data={"file_id": "src-1", "file_info": {"filename": "doc.pdf"}}
        )

    # detect must NOT have been called — proposal was pre-written at chunk time.
    detect_spy.assert_not_called()
    # get_domain_registry must NOT have been called — the early-return path
    # skips detection entirely when a pre-written proposal is present.
    registry_spy.assert_not_called()
    # Park was called with the pre-written proposal (same dict).
    park_spy.assert_called_once()
    parked_proposal = park_spy.call_args[0][2]  # positional: (adapter, file_id, proposal)
    assert parked_proposal["detected_domain"] == "legal"
    assert parked_proposal["confidence"] == 0.85
    assert result.get("status") == "parked"


@pytest.mark.asyncio
async def test_analysis_gate_park_detects_when_no_prewritten_proposal() -> None:
    """When no detection_proposal present, detect_extraction_domain is called.

    Non-wizard paths (CLI/MCP/bulk) have no eager proposal; the gate must
    still detect as before.
    """
    adapter = MagicMock(unsafe=True)
    source = _indexed_source_with_proposal()
    source["detection_proposal"] = None  # no pre-written proposal
    adapter.get_source.return_value = source
    adapter.assert_extractable.return_value = None
    adapter.get_chunks_for_extraction.return_value = [{"content": "legal text here"}]

    service = _make_analysis_service(adapter)

    detect_result = {
        "domain": MagicMock(),
        "detected_domain": "legal",
        "confidence": 0.85,
        "ranking": [{"domain": "legal", "score": 0.85}],
        "low_confidence": False,
        "entity_guidance": "",
        "relationship_guidance": "",
    }

    with (
        patch(
            "chaoscypher_core.app_config.get_settings",
            return_value=_make_settings_for_service(),
        ),
        patch(
            "chaoscypher_core.operations.pause_guard.check_paused",
            return_value=MagicMock(paused=False),
        ),
        patch(
            "chaoscypher_core.services.sources.engine.extraction.domains.get_domain_registry",
            return_value=MagicMock(),
        ),
        patch(
            "chaoscypher_core.services.sources.engine.extraction.orchestration.detect_extraction_domain",
            return_value=detect_result,
        ) as detect_spy,
        patch(
            "chaoscypher_core.operations.importing.confirmation_gate.park_for_confirmation"
        ) as park_spy,
    ):
        result = await service._import_analysis_handler(
            data={"file_id": "src-1", "file_info": {"filename": "doc.pdf"}}
        )

    # detect WAS called (no pre-written proposal).
    detect_spy.assert_called_once()
    park_spy.assert_called_once()
    assert result.get("status") == "parked"
