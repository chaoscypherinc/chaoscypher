# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression: vision auto-skip without model must log a distinct event (M4)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import structlog


@pytest.mark.asyncio
async def test_vision_disabled_logs_disabled_event(monkeypatch) -> None:
    from chaoscypher_core.operations.importing import indexing_handler

    # Vision is explicitly disabled — _get_active_vision_model is not even called.
    documents = [{"content": "x", "metadata": {}}]
    with structlog.testing.capture_logs() as logs:
        result_docs, vision_job_id = await indexing_handler._apply_vision_processing(
            documents=documents,
            file_id="src_1",
            filepath="/tmp/x.pdf",
            enable_vision=False,
            engine_settings=MagicMock(),
            database_name="default",
            data_dir="/tmp",
            adapter=MagicMock(),
        )

    assert result_docs == documents
    assert vision_job_id is None
    assert any(rec.get("event") == "vision_disabled_by_request" for rec in logs)


@pytest.mark.asyncio
async def test_vision_auto_with_no_model_logs_skipped(monkeypatch) -> None:
    from chaoscypher_core.operations.importing import indexing_handler

    monkeypatch.setattr(indexing_handler, "_get_active_vision_model", lambda s: None)

    documents = [{"content": "x", "metadata": {}}]
    with structlog.testing.capture_logs() as logs:
        result_docs, vision_job_id = await indexing_handler._apply_vision_processing(
            documents=documents,
            file_id="src_1",
            filepath="/tmp/x.pdf",
            enable_vision=None,  # auto
            engine_settings=MagicMock(),
            database_name="default",
            data_dir="/tmp",
            adapter=MagicMock(),
        )

    assert result_docs == documents
    assert vision_job_id is None
    assert any(rec.get("event") == "vision_skipped_no_model_configured" for rec in logs)


@pytest.mark.asyncio
async def test_vision_requested_but_no_model_logs_warning(monkeypatch) -> None:
    from chaoscypher_core.operations.importing import indexing_handler

    monkeypatch.setattr(indexing_handler, "_get_active_vision_model", lambda s: None)

    documents = [{"content": "x", "metadata": {}}]
    with structlog.testing.capture_logs() as logs:
        result_docs, vision_job_id = await indexing_handler._apply_vision_processing(
            documents=documents,
            file_id="src_1",
            filepath="/tmp/x.pdf",
            enable_vision=True,  # explicitly requested
            engine_settings=MagicMock(),
            database_name="default",
            data_dir="/tmp",
            adapter=MagicMock(),
        )

    assert result_docs == documents
    assert vision_job_id is None
    assert any(rec.get("event") == "vision_requested_but_no_model_configured" for rec in logs)
