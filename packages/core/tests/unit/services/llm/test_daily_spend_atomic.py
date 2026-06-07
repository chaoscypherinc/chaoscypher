# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The daily spend increment must be atomic across processes/connections.

``add_daily_token_spend`` used to read the row, ``+=`` in Python, then write.
Its docstring justified that as safe because "every token-spending operation
runs on QUEUE_LLM (concurrency 1)" — but the Cortex streaming-chat handler
records spend on the FastAPI process while the Neuron worker records on its
own process, both writing the same per-database ``app.db`` row. Two
read-modify-write cycles can interleave and lose an increment.

The fix replaces it with a single atomic SQLite UPSERT
(``INSERT ... ON CONFLICT DO UPDATE SET total = total + :delta``) so the
increment is correct regardless of which connection/process writes.
"""

from __future__ import annotations

import threading
from collections.abc import Generator
from pathlib import Path

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine


@pytest.fixture
def two_adapters(tmp_path: Path) -> Generator[tuple[SqliteAdapter, SqliteAdapter]]:
    """Two independent adapters/connections over the same app.db (two 'processes')."""
    db_dir = tmp_path / "chaoscypher-test"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "app.db"
    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)
    a = SqliteAdapter(str(db_path), database_name="default")
    b = SqliteAdapter(str(db_path), database_name="default")
    a.connect()
    b.connect()
    yield a, b
    a.disconnect()
    b.disconnect()


def test_concurrent_increments_do_not_lose_updates(
    two_adapters: tuple[SqliteAdapter, SqliteAdapter],
) -> None:
    """Two connections incrementing in parallel must accumulate every token.

    Read-modify-write loses increments when both connections read the same
    total before either commits. The atomic UPSERT (``total = total + :delta``
    inside one statement) cannot lose an increment regardless of interleaving.
    """
    a, b = two_adapters
    date = "2026-05-25"
    per_thread = 150
    barrier = threading.Barrier(2)

    def hammer(adapter: SqliteAdapter) -> None:
        barrier.wait()
        for _ in range(per_thread):
            adapter.add_daily_token_spend(database_name="default", spend_date=date, tokens=1)

    t1 = threading.Thread(target=hammer, args=(a,))
    t2 = threading.Thread(target=hammer, args=(b,))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert a.get_daily_token_spend(database_name="default", spend_date=date) == 2 * per_thread
