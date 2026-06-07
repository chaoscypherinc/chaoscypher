# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Integration: confirm flips awaiting->indexed BEFORE re-queue.

Edge case (design §7): a confirmed source must become discoverable by the
busy-slot waiting requeue (get_oldest_waiting_extraction, which matches only
INDEXED) the moment confirm runs — i.e. status must be INDEXED at the point
queue_import_analysis is awaited, not after.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.adapters.sqlite.models import SourceRow
from chaoscypher_core.models import SourceStatus
from chaoscypher_core.operations.importing.confirmation_gate import confirm_extraction


_DB = "default"


@pytest.fixture
def adapter(tmp_path: Path) -> Generator[SqliteAdapter]:
    db_path = tmp_path / "app.db"
    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)
    a = SqliteAdapter(str(db_path), database_name=_DB)
    a.connect()
    try:
        yield a
    finally:
        a.disconnect()


@pytest.mark.asyncio
async def test_confirm_makes_source_discoverable_to_waiting_requeue(
    adapter: SqliteAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter.session.add(
        SourceRow(
            id="src-busy",
            database_name=_DB,
            filename="doc.pdf",
            filepath="/tmp/doc.pdf",
            status=SourceStatus.AWAITING_CONFIRMATION,
            indexing_complete=True,
            confirmation_required=True,
            extraction_queued_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
            detection_proposal={
                "ranking": [{"domain": "technical", "score": 1.4}],
                "confidence": 1.4,
                "detected_domain": "technical",
                "low_confidence": False,
            },
        )
    )
    adapter.session.commit()

    captured: dict[str, Any] = {}

    async def _fake_queue(*_args: Any, **_kwargs: Any) -> str:
        # At enqueue time, the waiting requeue (matches only INDEXED) must be
        # able to find this source.
        captured["oldest"] = adapter.get_oldest_waiting_extraction(_DB)
        return "task-1"

    monkeypatch.setattr(
        "chaoscypher_core.operations.importing.confirmation_gate.queue_import_analysis",
        _fake_queue,
    )

    ok = await confirm_extraction(adapter, "src-busy", "technical", {"analysis_depth": "full"})

    assert ok is True
    assert captured["oldest"] is not None
    assert captured["oldest"]["id"] == "src-busy"
    final = adapter.session.get(SourceRow, "src-busy")
    assert final is not None
    assert final.status == SourceStatus.INDEXED
    assert final.forced_domain == "technical"
