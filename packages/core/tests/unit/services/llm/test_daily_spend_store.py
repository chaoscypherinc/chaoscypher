# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Adapter-level persistence for the daily LLM token-spend counter.

Backs the restart-safe daily spend cap (the in-memory `LLMSpendTracker`
daily counter zeroed on worker restart re-armed the budget every crash-loop).
The counter lives in the per-database `app.db` (`llm_daily_spend`), keyed by
``(database_name, UTC spend_date)`` so midnight rollover is automatic and a
worker restart resumes the day's total instead of resetting it.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine


@pytest.fixture
def in_memory_adapter(tmp_path: Path) -> Generator[SqliteAdapter]:
    """Fresh file-backed SqliteAdapter with all tables created via SQLModel."""
    db_dir = tmp_path / "chaoscypher-test"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "app.db"
    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)
    adapter = SqliteAdapter(str(db_path), database_name="default")
    adapter.connect()
    yield adapter
    adapter.disconnect()


def test_get_daily_token_spend_zero_when_absent(in_memory_adapter) -> None:
    """No row for the date → 0 (not None) so the cap comparison is safe."""
    assert (
        in_memory_adapter.get_daily_token_spend(database_name="default", spend_date="2026-05-25")
        == 0
    )


def test_add_daily_token_spend_upserts_and_accumulates(in_memory_adapter) -> None:
    """Repeated adds for the same (db, date) accumulate in one row."""
    in_memory_adapter.add_daily_token_spend(
        database_name="default", spend_date="2026-05-25", tokens=100
    )
    in_memory_adapter.add_daily_token_spend(
        database_name="default", spend_date="2026-05-25", tokens=250
    )
    assert (
        in_memory_adapter.get_daily_token_spend(database_name="default", spend_date="2026-05-25")
        == 350
    )


def test_daily_token_spend_isolated_per_date(in_memory_adapter) -> None:
    """Each UTC date is its own row — yesterday's total never bleeds in."""
    in_memory_adapter.add_daily_token_spend(
        database_name="default", spend_date="2026-05-24", tokens=900
    )
    in_memory_adapter.add_daily_token_spend(
        database_name="default", spend_date="2026-05-25", tokens=5
    )
    assert (
        in_memory_adapter.get_daily_token_spend(database_name="default", spend_date="2026-05-25")
        == 5
    )


def test_daily_token_spend_isolated_per_database(in_memory_adapter) -> None:
    """The counter is per-database (each app.db is one database)."""
    in_memory_adapter.add_daily_token_spend(
        database_name="default", spend_date="2026-05-25", tokens=1000
    )
    in_memory_adapter.add_daily_token_spend(
        database_name="other", spend_date="2026-05-25", tokens=7
    )
    assert (
        in_memory_adapter.get_daily_token_spend(database_name="other", spend_date="2026-05-25") == 7
    )


def test_add_daily_token_spend_ignores_non_positive(in_memory_adapter) -> None:
    """Defensive: zero / negative token deltas are a no-op."""
    in_memory_adapter.add_daily_token_spend(
        database_name="default", spend_date="2026-05-25", tokens=0
    )
    in_memory_adapter.add_daily_token_spend(
        database_name="default", spend_date="2026-05-25", tokens=-10
    )
    assert (
        in_memory_adapter.get_daily_token_spend(database_name="default", spend_date="2026-05-25")
        == 0
    )
