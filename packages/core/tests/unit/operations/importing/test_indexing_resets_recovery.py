# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression: indexing entry must reset recovery_attempts."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_indexing_handler_resets_recovery_attempts(monkeypatch, tmp_path) -> None:
    from chaoscypher_core.operations.importing import indexing_handler

    adapter = MagicMock()

    # Stub out everything past start_indexing so we can assert the call ordering.
    monkeypatch.setattr(
        indexing_handler,
        "_extract_text",
        lambda **kw: (
            "x" * 200,
            {"lines_removed": 0, "paragraphs_deduplicated": 0, "chars_removed": 0},
        ),
    )
    fake_registry = MagicMock()
    fake_registry.load_document.return_value = [{"content": "x", "metadata": {}}]
    # get_loader_registry is imported lazily inside _run_indexing, so patch at source
    monkeypatch.setattr(
        "chaoscypher_core.services.sources.loaders.get_loader_registry",
        lambda *a, **kw: fake_registry,
    )
    monkeypatch.setattr(
        indexing_handler,
        "_apply_vision_processing",
        AsyncMock(side_effect=lambda **kw: (kw["documents"], None)),
    )

    chunking_service = MagicMock()
    chunking_result = MagicMock(
        total_small_chunks=1,
        total_groups=1,
        chunks_filtered=0,
        normalize_drops=0,
        prestrip_lines_removed=0,
        chunks_skipped_by_depth=0,
    )
    chunking_service.create_chunks = AsyncMock(return_value=chunking_result)
    chunking_service.store_chunks = MagicMock()

    monkeypatch.setattr(
        indexing_handler,
        "queue_embed_chunks",
        AsyncMock(return_value="tsk_e1"),
    )

    # Stub out event_bus so emit() doesn't fail
    monkeypatch.setattr(indexing_handler, "event_bus", MagicMock())

    settings = MagicMock()
    settings.priorities.background = 50
    settings.data_dir = "/tmp"

    # Pin the MagicMock's data_dir so _run_indexing's
    # Path(engine_settings.paths.data_dir) writes land inside tmp_path instead
    # of a literal "<MagicMock ...>" directory at the repo root (issue #249).
    engine_settings = MagicMock()
    engine_settings.paths.data_dir = str(tmp_path)

    await indexing_handler._run_indexing(
        file_id="src_x",
        file_info={"filename": "x.txt"},
        filepath="/tmp/x.txt",
        analysis_depth="full",
        enable_normalization=False,
        enable_vision=False,
        adapter=adapter,
        chunking_service=chunking_service,
        engine_settings=engine_settings,
        settings=settings,
        database_name="default",
    )

    adapter.reset_source_recovery_attempts.assert_called_once_with(
        source_id="src_x", database_name="default"
    )
