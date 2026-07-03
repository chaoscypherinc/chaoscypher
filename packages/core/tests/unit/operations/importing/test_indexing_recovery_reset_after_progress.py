# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression: indexing must reset recovery_attempts only after forward progress.

Diagnosis (2026-05-12): a 184-page image-only PDF with the qwen3-vl:30b
vision model created an infinite recovery loop. The 1-hour
``operations_worker.timeout`` (``packages/neuron/src/chaoscypher_neuron/config.py``)
cancelled the in-flight indexing task via ``asyncio.wait_for``; the source
sat in ``status='indexing'`` until the recovery worker re-enqueued it 10
minutes later; the new task ran the same work from scratch; the same
1-hour timeout fired again; etc.

The recovery-attempts exhaustion guard
(``services/sources/recovery.py:331`` — ``mark_source_exhausted`` after
``DEFAULT_MAX_RECOVERY_ATTEMPTS`` cycles) is supposed to break loops like
this. It was bypassed because ``indexing_handler._run_indexing`` reset
``recovery_attempts`` to 0 on stage ENTRY — before any forward progress —
so the cycle was always counter=0 → recovery=1 → reset=0 → cancel → ...
never reaching the cap of 10.

The reset must only fire after the source has made *verified* forward
progress. ``chunking_service.store_chunks(...)`` is the milestone — once
chunks are durably persisted, the next attempt won't re-do the load /
vision / chunk work, so prior recovery counts are no longer relevant.
A cancellation BEFORE that milestone means no real progress, and the
counter must survive so the exhaustion guard can fire on the next pass.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest


def _make_settings() -> MagicMock:
    settings = MagicMock()
    settings.priorities.background = 50
    settings.data_dir = "/tmp"
    return settings


def _make_engine_settings(tmp_path: Path) -> MagicMock:
    """MagicMock engine_settings with a real data_dir.

    _run_indexing computes ``Path(engine_settings.paths.data_dir) / "sources"
    / <id> / "original.txt"``; an unpinned MagicMock stringifies into a
    literal ``<MagicMock name='mock.paths.data_dir' ...>`` directory at the
    repo root (issue #249).
    """
    engine_settings = MagicMock()
    engine_settings.paths.data_dir = str(tmp_path)
    return engine_settings


@pytest.mark.asyncio
async def test_recovery_counter_not_reset_when_cancelled_before_chunks_persisted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A timeout cancellation mid-vision must leave recovery_attempts intact.

    Simulates the production failure mode: the operations worker's
    ``asyncio.wait_for(timeout=3600)`` cancels the handler while
    ``_apply_vision_processing`` is still running, before chunks are
    persisted. The recovery counter must survive so the exhaustion
    guard can eventually mark the source ``error/recovery_exhausted``
    instead of letting it loop forever.
    """
    from chaoscypher_core.operations.importing import indexing_handler

    adapter = MagicMock()

    fake_registry = MagicMock()
    fake_registry.load_document.return_value = [{"content": "x", "metadata": {}}]
    monkeypatch.setattr(
        "chaoscypher_core.services.sources.loaders.get_loader_registry",
        lambda *a, **kw: fake_registry,
    )

    async def _cancelled_vision(**_kwargs: object) -> tuple[object, str | None]:
        # Mirrors what asyncio.wait_for does to the inner coroutine when
        # the operations worker's 1-hour timeout fires while
        # _apply_vision_processing is creating vision_jobs / enqueueing
        # per-page tasks (Task 12, 2026-05-13).
        raise asyncio.CancelledError

    monkeypatch.setattr(indexing_handler, "_apply_vision_processing", _cancelled_vision)

    chunking_service = MagicMock()
    chunking_service.create_chunks = AsyncMock()
    chunking_service.store_chunks = MagicMock()

    monkeypatch.setattr(indexing_handler, "event_bus", MagicMock())

    with pytest.raises(asyncio.CancelledError):
        await indexing_handler._run_indexing(
            file_id="src_cancelled",
            file_info={"filename": "huge.pdf"},
            filepath="/tmp/huge.pdf",
            analysis_depth="full",
            enable_normalization=False,
            enable_vision=True,
            adapter=adapter,
            chunking_service=chunking_service,
            engine_settings=_make_engine_settings(tmp_path),
            settings=_make_settings(),
            database_name="default",
        )

    # No chunks were persisted — no forward progress — counter must survive.
    adapter.reset_source_recovery_attempts.assert_not_called()
    chunking_service.store_chunks.assert_not_called()


@pytest.mark.asyncio
async def test_recovery_counter_reset_only_after_store_chunks_on_happy_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """On a successful run, reset must fire AFTER chunks are stored.

    Order matters: if the reset stays at stage entry, a future cancellation
    before chunking re-introduces the infinite-loop bug. Locking the order
    "store_chunks → reset" via this test prevents the regression.
    """
    from chaoscypher_core.operations.importing import indexing_handler

    call_order: list[str] = []

    adapter = MagicMock()
    adapter.reset_source_recovery_attempts.side_effect = lambda **_kw: call_order.append(
        "reset_recovery"
    )

    fake_registry = MagicMock()
    fake_registry.load_document.return_value = [{"content": "x" * 200, "metadata": {}}]
    monkeypatch.setattr(
        "chaoscypher_core.services.sources.loaders.get_loader_registry",
        lambda *a, **kw: fake_registry,
    )

    monkeypatch.setattr(
        indexing_handler,
        "_extract_text",
        lambda **_kw: (
            "x" * 200,
            {"lines_removed": 0, "paragraphs_deduplicated": 0, "chars_removed": 0},
        ),
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
    chunking_service.store_chunks = MagicMock(
        side_effect=lambda *_a, **_kw: call_order.append("store_chunks")
    )

    monkeypatch.setattr(indexing_handler, "queue_embed_chunks", AsyncMock(return_value="tsk_e1"))
    monkeypatch.setattr(indexing_handler, "event_bus", MagicMock())

    await indexing_handler._run_indexing(
        file_id="src_happy",
        file_info={"filename": "x.txt"},
        filepath="/tmp/x.txt",
        analysis_depth="full",
        enable_normalization=False,
        enable_vision=False,
        adapter=adapter,
        chunking_service=chunking_service,
        engine_settings=_make_engine_settings(tmp_path),
        settings=_make_settings(),
        database_name="default",
    )

    # Both calls happened, and the reset must come AFTER persistence — so
    # a cancellation before store_chunks leaves the counter intact for the
    # exhaustion guard to fire on the next reconcile.
    assert call_order == ["store_chunks", "reset_recovery"], (
        f"Expected ['store_chunks', 'reset_recovery'] but got {call_order!r}. "
        "The recovery_attempts reset must follow chunk persistence so a "
        "cancellation before store_chunks does not reset the exhaustion counter."
    )
    adapter.reset_source_recovery_attempts.assert_called_once_with(
        source_id="src_happy", database_name="default"
    )
