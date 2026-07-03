# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Workstream 1: indexing handler reads upload settings from the source row.

Verifies that when the queue payload (``file_info``) omits per-source
settings, ``handle_index_document`` falls back to the persisted row
state. This is the recovery / retry contract: a re-dispatched handler
must use whatever the user actually picked at upload time, not the
hardcoded defaults that the queue payload provided in legacy paths.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


@pytest.mark.asyncio
async def test_indexing_uses_row_settings_when_payload_omits(monkeypatch, tmp_path) -> None:
    """Recovery scenario: queue payload missing settings → row values used.

    The handler builds its working config from ``adapter.get_source(...)``
    and falls back to the queue payload only when the row says nothing.
    """
    from chaoscypher_core.operations.importing import indexing_handler

    captured_kwargs: dict = {}

    async def _fake_run_indexing(**kwargs):
        captured_kwargs.update(kwargs)
        return {
            "chunks_persisted": 0,
            "queued_for_embedding": True,
            "task_id": "tsk_x",
        }

    monkeypatch.setattr(indexing_handler, "_run_indexing", _fake_run_indexing)

    # Bypass the pause guard (it's imported lazily inside the function).
    pause_check = MagicMock()
    pause_check.paused = False
    monkeypatch.setattr(
        "chaoscypher_core.operations.pause_guard.check_paused",
        lambda **kw: pause_check,
    )

    # Bypass the heartbeat context manager.
    class _NullCtx:
        async def __aenter__(self) -> None:
            return None

        async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
            return None

    monkeypatch.setattr(indexing_handler, "source_heartbeat", lambda **kw: _NullCtx())

    # Stub settings + engine_settings dependencies.
    settings = MagicMock()
    settings.current_database = "default"
    settings.priorities.background = 50
    monkeypatch.setattr(
        "chaoscypher_core.app_config.get_settings",
        lambda: settings,
    )

    # Pin the MagicMock's data_dir so any Path(engine_settings.paths.data_dir)
    # construction lands inside tmp_path instead of stringifying the mock into
    # a literal "<MagicMock ...>" directory at the repo root (issue #249).
    engine_settings = MagicMock()
    engine_settings.paths.data_dir = str(tmp_path)

    # Adapter returns a row whose values diverge from any payload defaults
    # so we can prove the handler actually read from the row.
    adapter = MagicMock()
    adapter.get_source.return_value = {
        "id": "src_recovered",
        "database_name": "default",
        "extraction_depth": "quick",
        "enable_normalization": False,
        "enable_vision": False,
        "filtering_mode": "strict",
        "content_filtering": False,
        "auto_analyze": False,
    }

    # Queue payload deliberately omits the user settings — the recovery
    # contract is that the row drives behaviour.
    payload = {
        "file_id": "src_recovered",
        "file_info": {
            "filepath": "/tmp/recovered.txt",
            "filename": "recovered.txt",
        },
    }

    await indexing_handler.handle_index_document(
        data=payload,
        source_repository=adapter,
        chunking_service=MagicMock(),
        engine_settings=engine_settings,
    )

    adapter.get_source.assert_called_once_with("src_recovered", "default")
    assert captured_kwargs["analysis_depth"] == "quick"
    assert captured_kwargs["enable_normalization"] is False
    assert captured_kwargs["enable_vision"] is False
