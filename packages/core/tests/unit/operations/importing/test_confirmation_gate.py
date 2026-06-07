# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Unit tests for the shared confirmation-gate primitives.

Covers:
- gate_decision truth table (forced/auto x bypass x confirmed x status)
- park_for_confirmation single atomic SourceRow write
- confirm_extraction CAS win/idempotent-loss + write-once extraction_confirmed_at
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.adapters.sqlite.models import SourceRow
from chaoscypher_core.models import SourceStatus
from chaoscypher_core.operations.importing.confirmation_gate import (
    gate_decision,
    park_for_confirmation,
)


def _source(
    *,
    status: str = SourceStatus.INDEXED,
    forced_domain: str | None = None,
    confirmation_required: bool = False,
    extraction_confirmed_at: Any = None,
) -> dict[str, Any]:
    """Build a get_source-shaped dict carrying only the gate-relevant fields."""
    return {
        "status": status,
        "forced_domain": forced_domain,
        "confirmation_required": confirmation_required,
        "extraction_confirmed_at": extraction_confirmed_at,
    }


class TestGateDecision:
    """gate_decision reads ONLY persisted fields and returns 'proceed' | 'park'."""

    def test_forced_domain_proceeds(self) -> None:
        src = _source(forced_domain="technical", confirmation_required=True)
        assert gate_decision(src) == "proceed"

    def test_auto_unconfirmed_parks(self) -> None:
        src = _source(forced_domain=None, confirmation_required=True)
        assert gate_decision(src) == "park"

    def test_auto_not_required_proceeds(self) -> None:
        # confirmation_required=False is how the persisted bypass (auto_confirm)
        # is encoded at upload — an auto domain that should NOT be gated.
        src = _source(forced_domain=None, confirmation_required=False)
        assert gate_decision(src) == "proceed"

    def test_live_bypass_kwarg_proceeds(self) -> None:
        # In-process callers (CLI) pass a live bypass even when the row says
        # confirmation_required=True.
        src = _source(forced_domain=None, confirmation_required=True)
        assert gate_decision(src, bypass=True) == "proceed"

    def test_confirmed_short_circuits_to_proceed(self) -> None:
        # extraction_confirmed_at set => already confirmed, never re-park,
        # even though confirmation_required is still True.
        src = _source(
            forced_domain=None,
            confirmation_required=True,
            extraction_confirmed_at="2026-05-28T00:00:00+00:00",
        )
        assert gate_decision(src) == "proceed"

    @pytest.mark.parametrize(
        "status",
        [
            SourceStatus.EXTRACTING,
            SourceStatus.MCP_EXTRACTING,
            SourceStatus.EXTRACTED,
            SourceStatus.COMMITTING,
            SourceStatus.COMMITTED,
        ],
    )
    def test_past_indexed_short_circuits_to_proceed(self, status: str) -> None:
        # A re-dispatch of a source already past INDEXED must never re-park.
        src = _source(status=status, forced_domain=None, confirmation_required=True)
        assert gate_decision(src) == "proceed"

    def test_awaiting_confirmation_status_parks(self) -> None:
        # A parked source re-evaluated stays parked (not "past INDEXED").
        src = _source(
            status=SourceStatus.AWAITING_CONFIRMATION,
            forced_domain=None,
            confirmation_required=True,
        )
        assert gate_decision(src) == "park"


# ---------------------------------------------------------------------------
# park_for_confirmation — atomic SourceRow write tests
# ---------------------------------------------------------------------------

_DB = "default"


@pytest.fixture
def adapter(tmp_path: Path) -> Generator[SqliteAdapter]:
    """File-backed SqliteAdapter with all SourceRow columns created."""
    db_path = tmp_path / "app.db"
    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)
    a = SqliteAdapter(str(db_path), database_name=_DB)
    a.connect()
    try:
        yield a
    finally:
        a.disconnect()


def _seed_indexed(adapter: SqliteAdapter, source_id: str) -> None:
    """Insert a minimal INDEXED SourceRow via the adapter session."""
    adapter.session.add(
        SourceRow(
            id=source_id,
            database_name=_DB,
            filename="doc.pdf",
            filepath="/tmp/doc.pdf",
            status=SourceStatus.INDEXED,
            indexing_complete=True,
            confirmation_required=True,
        )
    )
    adapter.session.commit()


_PROPOSAL: dict[str, Any] = {
    "ranking": [{"domain": "technical", "score": 1.4}, {"domain": "news", "score": 1.1}],
    "confidence": 1.4,
    "detected_domain": "technical",
    "low_confidence": False,
}


class TestParkForConfirmation:
    """One atomic write flips status + detection_proposal + confirmation_required."""

    def test_park_persists_all_three_fields(self, adapter: SqliteAdapter) -> None:
        _seed_indexed(adapter, "src-park-1")

        park_for_confirmation(adapter, "src-park-1", _PROPOSAL)

        row = adapter.session.get(SourceRow, "src-park-1")
        assert row is not None
        assert row.status == SourceStatus.AWAITING_CONFIRMATION
        assert row.detection_proposal == _PROPOSAL
        assert row.confirmation_required is True

    def test_park_does_not_set_extraction_started_at(self, adapter: SqliteAdapter) -> None:
        # Slot-leak guard: parking must not look like an extraction start.
        _seed_indexed(adapter, "src-park-2")
        park_for_confirmation(adapter, "src-park-2", _PROPOSAL)
        row = adapter.session.get(SourceRow, "src-park-2")
        assert row is not None
        assert row.extraction_started_at is None

    def test_park_missing_source_is_noop(self, adapter: SqliteAdapter) -> None:
        # No exception when the row is gone (deleted between detect and park).
        park_for_confirmation(adapter, "does-not-exist", _PROPOSAL)
        assert adapter.session.get(SourceRow, "does-not-exist") is None


# ---------------------------------------------------------------------------
# confirm_extraction — CAS win/loss + write-once + re-queue tests
# ---------------------------------------------------------------------------

from unittest.mock import AsyncMock

from chaoscypher_core.operations.importing.confirmation_gate import (
    confirm_extraction,
)


def _seed_awaiting(adapter: SqliteAdapter, source_id: str) -> None:
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
        )
    )
    adapter.session.commit()


_OVERRIDES: dict[str, Any] = {
    "analysis_depth": "full",
    "filtering_mode": "strict",
    "content_filtering": True,
    "enable_direction_correction": True,
    "protect_orphans": False,
    "enable_inverse_relationships": True,
    "max_entity_degree_override": 25,
}


class TestConfirmExtraction:
    """CAS awaiting->indexed, persist overrides + write-once, then re-queue."""

    @pytest.mark.asyncio
    async def test_confirm_wins_persists_and_requeues(
        self, adapter: SqliteAdapter, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _seed_awaiting(adapter, "src-c-1")
        fake_queue = AsyncMock(return_value="task-123")
        monkeypatch.setattr(
            "chaoscypher_core.operations.importing.confirmation_gate.queue_import_analysis",
            fake_queue,
        )

        ok = await confirm_extraction(adapter, "src-c-1", "technical", _OVERRIDES)

        assert ok is True
        row = adapter.session.get(SourceRow, "src-c-1")
        assert row is not None
        assert row.status == SourceStatus.INDEXED
        assert row.forced_domain == "technical"
        assert row.filtering_mode == "strict"
        assert row.max_entity_degree_override == 25
        assert row.extraction_confirmed_at is not None
        fake_queue.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_concurrent_cas_loss_is_noop_false(
        self, adapter: SqliteAdapter, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Genuine race: two confirms both observe ``awaiting_confirmation`` but
        the CAS serializes them — the loser is a benign no-op (False), no
        re-queue, no forced_domain overwrite. Simulated by forcing the CAS to
        lose while the row is still parked.
        """
        _seed_awaiting(adapter, "src-c-2")
        fake_queue = AsyncMock(return_value="task-1")
        monkeypatch.setattr(
            "chaoscypher_core.operations.importing.confirmation_gate.queue_import_analysis",
            fake_queue,
        )
        # Force the CAS to lose (the other racer already flipped the row).
        monkeypatch.setattr(adapter, "transition_source_status", lambda *a, **k: False)

        lost = await confirm_extraction(adapter, "src-c-2", "news", _OVERRIDES)

        assert lost is False
        # The loser must not re-queue and must not overwrite forced_domain.
        fake_queue.assert_not_awaited()
        row = adapter.session.get(SourceRow, "src-c-2")
        assert row is not None
        assert row.forced_domain is None

    @pytest.mark.asyncio
    async def test_sequential_reconfirm_after_confirm_is_conflict(
        self, adapter: SqliteAdapter, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """State-aware contract: once confirmed (status moved INDEXED + write-once
        timestamp set), a second confirm under a different domain is too late →
        ConflictError. The first call still wins + re-queues exactly once.
        """
        _seed_awaiting(adapter, "src-c-2b")
        fake_queue = AsyncMock(return_value="task-1")
        monkeypatch.setattr(
            "chaoscypher_core.operations.importing.confirmation_gate.queue_import_analysis",
            fake_queue,
        )

        first = await confirm_extraction(adapter, "src-c-2b", "technical", _OVERRIDES)
        assert first is True

        with pytest.raises(ConflictError):
            await confirm_extraction(adapter, "src-c-2b", "news", _OVERRIDES)

        # Only the winner re-queued; forced_domain is the first (winning) choice.
        assert fake_queue.await_count == 1
        row = adapter.session.get(SourceRow, "src-c-2b")
        assert row is not None
        assert row.forced_domain == "technical"

    @pytest.mark.asyncio
    async def test_confirm_writes_once_then_requeues_after_cas(
        self, adapter: SqliteAdapter, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Ordering invariant: status is INDEXED BEFORE queue_import_analysis
        # is awaited, so the busy-slot waiting requeue (matches only INDEXED)
        # can find it.
        _seed_awaiting(adapter, "src-c-3")
        seen_status: dict[str, Any] = {}

        async def _capture(*_args: Any, **_kwargs: Any) -> str:
            r = adapter.session.get(SourceRow, "src-c-3")
            seen_status["status"] = r.status if r else None
            return "task-1"

        monkeypatch.setattr(
            "chaoscypher_core.operations.importing.confirmation_gate.queue_import_analysis",
            _capture,
        )

        ok = await confirm_extraction(adapter, "src-c-3", "technical", _OVERRIDES)

        assert ok is True
        assert seen_status["status"] == SourceStatus.INDEXED

    @pytest.mark.asyncio
    async def test_none_override_does_not_clobber_persisted_columns(
        self, adapter: SqliteAdapter, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A None-valued override must NOT be written to the column.

        Regression for the BLOCKER: a default confirm passes
        ``filtering_mode=None`` / ``content_filtering=None`` in the overrides
        dict. Without the None-guard the loop writes ``row.filtering_mode =
        None`` into a NOT NULL column → IntegrityError, and (for the boolean)
        silently overwrites the upload-time value. The guard means None ==
        "leave column as-is / use the persisted upload value".
        """
        # Seed an awaiting source with a non-default persisted filtering_mode
        # and content_filtering=False (an upload-time choice we must preserve).
        adapter.session.add(
            SourceRow(
                id="src-none-1",
                database_name=_DB,
                filename="doc.pdf",
                filepath="/tmp/doc.pdf",
                status=SourceStatus.AWAITING_CONFIRMATION,
                indexing_complete=True,
                confirmation_required=True,
                detection_proposal=_PROPOSAL,
                filtering_mode="minimal",
                content_filtering=False,
            )
        )
        adapter.session.commit()

        fake_queue = AsyncMock(return_value="task-none")
        monkeypatch.setattr(
            "chaoscypher_core.operations.importing.confirmation_gate.queue_import_analysis",
            fake_queue,
        )

        # Mirror the all-keys-present dict the buggy service path builds for a
        # default confirm (no filtering_mode / content_filtering supplied).
        none_overrides: dict[str, Any] = {
            "analysis_depth": "full",
            "filtering_mode": None,
            "content_filtering": None,
            "enable_direction_correction": None,
            "protect_orphans": None,
            "enable_inverse_relationships": None,
            "max_entity_degree_override": None,
        }

        ok = await confirm_extraction(adapter, "src-none-1", None, none_overrides)

        assert ok is True
        adapter.session.expire_all()
        row = adapter.session.get(SourceRow, "src-none-1")
        assert row is not None
        # NOT NULL column preserved (no IntegrityError, no None write).
        assert row.filtering_mode == "minimal"
        # Upload-time boolean preserved (not silently flipped to a default).
        assert row.content_filtering is False
        # chosen_domain=None => forced_domain falls back to detected proposal.
        assert row.forced_domain == "technical"
        assert row.extraction_confirmed_at is not None
        fake_queue.assert_awaited_once()


# ---------------------------------------------------------------------------
# confirm_extraction — STATE-AWARE branch (wizard §3.2): pre-gate vs past-gate
# ---------------------------------------------------------------------------

from chaoscypher_core.exceptions import ConflictError


def _seed_status(
    adapter: SqliteAdapter,
    source_id: str,
    *,
    status: str,
    filtering_mode: str = "balanced",
    content_filtering: bool = False,
    forced_domain: str | None = None,
    extraction_confirmed_at: Any = None,
) -> None:
    """Insert a SourceRow at an arbitrary lifecycle status for the gate tests."""
    adapter.session.add(
        SourceRow(
            id=source_id,
            database_name=_DB,
            filename="doc.pdf",
            filepath="/tmp/doc.pdf",
            status=status,
            indexing_complete=status not in (SourceStatus.PENDING, SourceStatus.INDEXING),
            confirmation_required=True,
            detection_proposal=_PROPOSAL,
            filtering_mode=filtering_mode,
            content_filtering=content_filtering,
            forced_domain=forced_domain,
            extraction_confirmed_at=extraction_confirmed_at,
        )
    )
    adapter.session.commit()


class TestConfirmExtractionPreGate:
    """Confirm arriving BEFORE the analysis gate parks (the wizard race).

    Records the user's domain decision WITHOUT changing status and WITHOUT
    re-queueing — the analysis stage will run on its own and gate_decision
    then PROCEEDS because forced_domain + extraction_confirmed_at are set.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "status",
        [
            SourceStatus.PENDING,
            SourceStatus.INDEXING,
            SourceStatus.VISION_PENDING,
            SourceStatus.INDEXED,
        ],
    )
    async def test_pre_gate_confirm_sets_fields_no_status_change_no_requeue(
        self, adapter: SqliteAdapter, monkeypatch: pytest.MonkeyPatch, status: str
    ) -> None:
        _seed_status(adapter, "src-pre-1", status=status)
        fake_queue = AsyncMock(return_value="task-x")
        monkeypatch.setattr(
            "chaoscypher_core.operations.importing.confirmation_gate.queue_import_analysis",
            fake_queue,
        )

        ok = await confirm_extraction(adapter, "src-pre-1", "medical", _OVERRIDES)

        assert ok is True
        adapter.session.expire_all()
        row = adapter.session.get(SourceRow, "src-pre-1")
        assert row is not None
        # Status UNCHANGED (no premature flip to INDEXED).
        assert row.status == status
        # Decision recorded: forced_domain + write-once timestamp + overrides.
        assert row.forced_domain == "medical"
        assert row.extraction_confirmed_at is not None
        assert row.filtering_mode == "strict"
        assert row.max_entity_degree_override == 25
        # No premature requeue — the analysis stage runs on its own.
        fake_queue.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_pre_gate_confirm_then_gate_proceeds_real_adapter(
        self, adapter: SqliteAdapter, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """DECISIVE real-adapter test (spec §5): confirm pre-gate, then the
        analysis-stage gate_decision PROCEEDS (no park) over the persisted row.
        """
        # Pre-gate: indexed, confirmation_required, no forced_domain, unconfirmed.
        adapter.session.add(
            SourceRow(
                id="src-proceed-1",
                database_name=_DB,
                filename="doc.pdf",
                filepath="/tmp/doc.pdf",
                status=SourceStatus.INDEXED,
                indexing_complete=True,
                confirmation_required=True,
                detection_proposal=_PROPOSAL,
                filtering_mode="balanced",
                content_filtering=False,
            )
        )
        adapter.session.commit()

        # Sanity: BEFORE confirm the real gate would PARK this source.
        before = adapter.get_source("src-proceed-1", _DB)
        assert before is not None
        assert gate_decision(before) == "park"

        fake_queue = AsyncMock(return_value="task-x")
        monkeypatch.setattr(
            "chaoscypher_core.operations.importing.confirmation_gate.queue_import_analysis",
            fake_queue,
        )

        ok = await confirm_extraction(adapter, "src-proceed-1", "legal", _OVERRIDES)
        assert ok is True

        # AFTER confirm the real gate PROCEEDS (forced/confirmed fields set).
        adapter.session.expire_all()
        after = adapter.get_source("src-proceed-1", _DB)
        assert after is not None
        assert gate_decision(after) == "proceed"
        assert after.get("forced_domain") == "legal"
        assert after.get("extraction_confirmed_at") is not None
        # No requeue on the pre-gate path.
        fake_queue.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_pre_gate_none_overrides_do_not_clobber_columns(
        self, adapter: SqliteAdapter, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """BLOCKER-1 regression on the pre-gate branch: None overrides dropped."""
        _seed_status(
            adapter,
            "src-pre-none",
            status=SourceStatus.INDEXED,
            filtering_mode="minimal",
            content_filtering=False,
        )
        fake_queue = AsyncMock(return_value="task-x")
        monkeypatch.setattr(
            "chaoscypher_core.operations.importing.confirmation_gate.queue_import_analysis",
            fake_queue,
        )
        none_overrides: dict[str, Any] = {
            "analysis_depth": "full",
            "filtering_mode": None,
            "content_filtering": None,
            "enable_direction_correction": None,
            "protect_orphans": None,
            "enable_inverse_relationships": None,
            "max_entity_degree_override": None,
        }

        ok = await confirm_extraction(adapter, "src-pre-none", None, none_overrides)

        assert ok is True
        adapter.session.expire_all()
        row = adapter.session.get(SourceRow, "src-pre-none")
        assert row is not None
        # NOT NULL column preserved (no None write).
        assert row.filtering_mode == "minimal"
        assert row.content_filtering is False
        # chosen_domain=None => forced_domain falls back to detected proposal.
        assert row.forced_domain == "technical"
        assert row.extraction_confirmed_at is not None


class TestConfirmExtractionPastGate:
    """Confirm arriving too late (extraction already started/finished) → 409."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "status",
        [
            SourceStatus.EXTRACTING,
            SourceStatus.MCP_EXTRACTING,
            SourceStatus.EXTRACTED,
            SourceStatus.COMMITTING,
            SourceStatus.COMMITTED,
        ],
    )
    async def test_past_gate_raises_conflict(
        self, adapter: SqliteAdapter, monkeypatch: pytest.MonkeyPatch, status: str
    ) -> None:
        _seed_status(adapter, "src-past-1", status=status)
        fake_queue = AsyncMock(return_value="task-x")
        monkeypatch.setattr(
            "chaoscypher_core.operations.importing.confirmation_gate.queue_import_analysis",
            fake_queue,
        )

        with pytest.raises(ConflictError):
            await confirm_extraction(adapter, "src-past-1", "medical", _OVERRIDES)

        fake_queue.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_already_confirmed_raises_conflict(
        self, adapter: SqliteAdapter, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A pre-gate source that was already confirmed (write-once timestamp
        set) cannot be re-confirmed under a different domain → 409.
        """
        _seed_status(
            adapter,
            "src-confirmed",
            status=SourceStatus.INDEXED,
            forced_domain="technical",
            extraction_confirmed_at=datetime.now(UTC),
        )
        fake_queue = AsyncMock(return_value="task-x")
        monkeypatch.setattr(
            "chaoscypher_core.operations.importing.confirmation_gate.queue_import_analysis",
            fake_queue,
        )

        with pytest.raises(ConflictError):
            await confirm_extraction(adapter, "src-confirmed", "news", _OVERRIDES)

        fake_queue.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_error_status_raises_conflict(
        self, adapter: SqliteAdapter, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An errored source is neither parked nor pre-gate → 409 (not confirmable)."""
        _seed_status(adapter, "src-error", status=SourceStatus.ERROR)
        fake_queue = AsyncMock(return_value="task-x")
        monkeypatch.setattr(
            "chaoscypher_core.operations.importing.confirmation_gate.queue_import_analysis",
            fake_queue,
        )

        with pytest.raises(ConflictError):
            await confirm_extraction(adapter, "src-error", "medical", _OVERRIDES)

        fake_queue.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_missing_source_raises_conflict(self, adapter: SqliteAdapter) -> None:
        """A confirm against a vanished source raises (no silent True)."""
        with pytest.raises(ConflictError):
            await confirm_extraction(adapter, "does-not-exist", "medical", _OVERRIDES)
