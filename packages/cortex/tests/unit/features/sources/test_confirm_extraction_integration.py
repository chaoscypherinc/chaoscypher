# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Integration: real SourceService.confirm_extraction -> core gate -> real SqliteAdapter.

The per-task endpoint tests (test_confirm_extraction_endpoint.py) mock the core
``confirm_extraction_gate``, so they never exercise the override write that
crashes on a default confirm. These tests drive the REAL path end to end against
a file-backed SqliteAdapter and only mock the queue enqueue, so they would fail
without:

- the core None-guard in confirmation_gate.confirm_extraction (no NULL into the
  NOT NULL filtering_mode column), and
- the Cortex present-keys-only / tri-state semantics (no silent clobber of an
  upload-time content_filtering=False on a default confirm).
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.adapters.sqlite.models import SourceRow
from chaoscypher_core.models import SourceStatus
from chaoscypher_cortex.features.sources.service import SourceService


_DB = "default"

_PROPOSAL: dict[str, Any] = {
    "ranking": [{"domain": "technical", "score": 1.4}],
    "confidence": 1.4,
    "detected_domain": "technical",
    "low_confidence": False,
}


@pytest.fixture
def adapter(tmp_path: Path) -> Generator[SqliteAdapter]:
    """File-backed SqliteAdapter with all SourceRow columns created (CC040)."""
    db_path = tmp_path / "app.db"
    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)
    a = SqliteAdapter(str(db_path), database_name=_DB)
    a.connect()
    try:
        yield a
    finally:
        a.disconnect()


def _settings() -> MagicMock:
    settings = MagicMock()
    settings.priorities.background = 50
    settings.pagination.default_page_size = 50
    settings.logs.error_message_preview_chars = 200
    return settings


def _make_service(adapter: SqliteAdapter) -> SourceService:
    """SourceService whose get_source AND core gate both hit the same real adapter."""
    engine = MagicMock()
    # get_source delegates to the engine service; route it to the real adapter
    # so the AWAITING_CONFIRMATION precondition check reads real row state.
    engine.get_source.side_effect = lambda sid: adapter.get_source(sid, _DB)
    return SourceService(
        engine_service=engine,
        database_name=_DB,
        settings=_settings(),
        storage_adapter=adapter,
    )


def _seed_awaiting(
    adapter: SqliteAdapter,
    source_id: str,
    *,
    filtering_mode: str = "balanced",
    content_filtering: bool = False,
) -> None:
    adapter.session.add(
        SourceRow(
            id=source_id,
            database_name=_DB,
            filename="doc.pdf",
            filepath="/tmp/doc.pdf",
            status=SourceStatus.AWAITING_CONFIRMATION,
            indexing_complete=True,
            confirmation_required=True,
            detection_proposal=_PROPOSAL,
            filtering_mode=filtering_mode,
            content_filtering=content_filtering,
        )
    )
    adapter.session.commit()


@pytest.mark.asyncio
async def test_default_confirm_preserves_persisted_overrides(
    adapter: SqliteAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A default confirm (no filtering_mode / content_filtering supplied) must
    NOT crash and must PRESERVE the persisted upload-time columns.

    Regression for the BLOCKER: the buggy service built an all-keys overrides
    dict (filtering_mode=None, content_filtering=True default), and the core
    gate wrote them unconditionally → IntegrityError on the NOT NULL
    filtering_mode column + silent clobber of content_filtering=False.
    """
    _seed_awaiting(adapter, "src-int-1", filtering_mode="balanced", content_filtering=False)

    fake_queue = AsyncMock(return_value="task-int-1")
    monkeypatch.setattr(
        "chaoscypher_core.operations.importing.confirmation_gate.queue_import_analysis",
        fake_queue,
    )

    service = _make_service(adapter)

    # NO filtering_mode / content_filtering in the request: pure default confirm.
    result = await service.confirm_extraction(source_id="src-int-1")

    # (a) No IntegrityError raised (we got here) + the indexed envelope back.
    assert result == {"source_id": "src-int-1", "status": SourceStatus.INDEXED}

    adapter.session.expire_all()
    row = adapter.session.get(SourceRow, "src-int-1")
    assert row is not None
    # (b) Persisted overrides PRESERVED, not overwritten to None / True.
    assert row.filtering_mode == "balanced"
    assert row.content_filtering is False
    # (c) Status flipped + write-once timestamp set + forced_domain from proposal.
    assert row.status == SourceStatus.INDEXED
    assert row.extraction_confirmed_at is not None
    assert row.forced_domain == "technical"
    # (d) Queue enqueue awaited.
    fake_queue.assert_awaited_once()


@pytest.mark.asyncio
async def test_confirm_with_explicit_overrides_applies_them(
    adapter: SqliteAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An explicit override IS applied (the present-keys path forwards it)."""
    _seed_awaiting(adapter, "src-int-2", filtering_mode="balanced", content_filtering=False)

    fake_queue = AsyncMock(return_value="task-int-2")
    monkeypatch.setattr(
        "chaoscypher_core.operations.importing.confirmation_gate.queue_import_analysis",
        fake_queue,
    )

    service = _make_service(adapter)

    await service.confirm_extraction(
        source_id="src-int-2",
        domain="medical",
        filtering_mode="strict",
        content_filtering=True,
    )

    adapter.session.expire_all()
    row = adapter.session.get(SourceRow, "src-int-2")
    assert row is not None
    assert row.filtering_mode == "strict"
    assert row.content_filtering is True
    assert row.forced_domain == "medical"


def _seed_pre_gate(
    adapter: SqliteAdapter,
    source_id: str,
    *,
    status: str = SourceStatus.INDEXED,
    filtering_mode: str = "balanced",
    content_filtering: bool = False,
) -> None:
    """A pre-gate source: confirmation_required, no forced_domain, unconfirmed."""
    adapter.session.add(
        SourceRow(
            id=source_id,
            database_name=_DB,
            filename="doc.pdf",
            filepath="/tmp/doc.pdf",
            status=status,
            indexing_complete=status == SourceStatus.INDEXED,
            confirmation_required=True,
            detection_proposal=_PROPOSAL,
            filtering_mode=filtering_mode,
            content_filtering=content_filtering,
        )
    )
    adapter.session.commit()


@pytest.mark.asyncio
async def test_pre_gate_confirm_then_gate_proceeds_real_adapter(
    adapter: SqliteAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DECISIVE (spec §5): the full Cortex service path on a PRE-GATE source
    records the domain WITHOUT parking/requeueing, and the real analysis-stage
    ``gate_decision`` then PROCEEDS over the persisted row (no mocked gate).
    """
    from chaoscypher_core.operations.importing.confirmation_gate import gate_decision

    _seed_pre_gate(adapter, "src-int-pre", status=SourceStatus.INDEXED)

    # Sanity: BEFORE confirm the real gate would PARK this source.
    before = adapter.get_source("src-int-pre", _DB)
    assert before is not None
    assert gate_decision(before) == "park"

    fake_queue = AsyncMock(return_value="task-pre")
    monkeypatch.setattr(
        "chaoscypher_core.operations.importing.confirmation_gate.queue_import_analysis",
        fake_queue,
    )

    service = _make_service(adapter)
    result = await service.confirm_extraction(source_id="src-int-pre", domain="legal")

    # Envelope reports the unchanged pre-gate status (not a forced INDEXED flip).
    assert result == {"source_id": "src-int-pre", "status": SourceStatus.INDEXED}

    adapter.session.expire_all()
    # No park (status unchanged), decision recorded, NO premature requeue.
    after = adapter.get_source("src-int-pre", _DB)
    assert after is not None
    assert after.get("status") == SourceStatus.INDEXED
    assert after.get("forced_domain") == "legal"
    assert after.get("extraction_confirmed_at") is not None
    # The real analysis-stage gate now PROCEEDS (the whole point of the wizard).
    assert gate_decision(after) == "proceed"
    fake_queue.assert_not_awaited()


@pytest.mark.asyncio
async def test_past_gate_confirm_raises_conflict_real_adapter(
    adapter: SqliteAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A source already extracting → ConflictError (HTTP 409) end to end."""
    from chaoscypher_core.exceptions import ConflictError

    _seed_pre_gate(adapter, "src-int-late", status=SourceStatus.EXTRACTING)
    fake_queue = AsyncMock(return_value="task-late")
    monkeypatch.setattr(
        "chaoscypher_core.operations.importing.confirmation_gate.queue_import_analysis",
        fake_queue,
    )
    service = _make_service(adapter)

    with pytest.raises(ConflictError):
        await service.confirm_extraction(source_id="src-int-late", domain="legal")
    fake_queue.assert_not_awaited()


@pytest.mark.asyncio
async def test_bulk_confirm_default_preserves_overrides(
    adapter: SqliteAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bulk confirm uses the default no-override path; must not crash.

    Must preserve persisted columns for every item.
    """
    _seed_awaiting(adapter, "src-bulk-1", filtering_mode="minimal", content_filtering=False)
    _seed_awaiting(adapter, "src-bulk-2", filtering_mode="strict", content_filtering=False)

    fake_queue = AsyncMock(return_value="task-bulk")
    monkeypatch.setattr(
        "chaoscypher_core.operations.importing.confirmation_gate.queue_import_analysis",
        fake_queue,
    )

    service = _make_service(adapter)

    resp = await service.confirm_extraction_bulk(source_ids=["src-bulk-1", "src-bulk-2"])

    assert resp.confirmed == 2
    assert resp.failed == 0

    adapter.session.expire_all()
    r1 = adapter.session.get(SourceRow, "src-bulk-1")
    r2 = adapter.session.get(SourceRow, "src-bulk-2")
    assert r1 is not None and r2 is not None
    assert r1.filtering_mode == "minimal"
    assert r1.content_filtering is False
    assert r1.status == SourceStatus.INDEXED
    assert r2.filtering_mode == "strict"
    assert r2.content_filtering is False
    assert r2.status == SourceStatus.INDEXED
    assert fake_queue.await_count == 2
