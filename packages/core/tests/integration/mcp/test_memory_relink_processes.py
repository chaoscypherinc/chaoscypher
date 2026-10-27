# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""The relink across real processes: a memory outlives its process, two sweeps link it once.

Threads would share the process's locks and sessions and prove nothing (spec D10), so each
server here is a ``spawn`` process of its own on one database file, with a scripted model
(``tests/unit/mcp/memory_processes.py``).
"""

from __future__ import annotations

import os
import signal
import sqlite3
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from chaoscypher_core.mcp.memory import TIMED_OUT_LINE
from tests.unit.mcp.memory_processes import (
    JOIN_SECONDS,
    ServerProcesses,
    barrier,
    collect,
)


LYON = "Pierre Bezukhov moved to Lyon."
GUESTS = (
    "Natasha Rostova",
    "Nikolai Rostov",
    "Sonya Rostova",
    "Andrei Bolkonsky",
    "Marya Bolkonskaya",
    "Helene Kuragina",
    "Anatole Kuragin",
    "Vasily Denisov",
    "Fyodor Dolokhov",
    "Platon Karataev",
)
BALL = {f"{name} came to the ball.": (f"E|{name}|Person||0.9|S1|A guest\n", "") for name in GUESTS}


def _db(home: Path) -> sqlite3.Connection:
    return sqlite3.connect(home / "databases" / "test" / "app.db")


def _memory_rows(home: Path) -> list[tuple[str, str, str | None]]:
    with _db(home) as db:
        return db.execute(
            "SELECT id, memory_link_state, memory_link_claim FROM sources "
            "WHERE source_type = 'memory' ORDER BY created_at"
        ).fetchall()


def _finish(process: Any, out: Any) -> list[tuple[Any, ...]]:
    messages = collect(out, until="done", process=process)
    process.join(JOIN_SECONDS)
    assert process.exitcode == 0, process.exitcode
    return messages


def test_a_pending_memory_survives_its_process_and_the_next_start_links_it(
    tmp_path: Path, servers: ServerProcesses
) -> None:
    first, out = servers.spawn(
        tmp_path, [("remember", {"text": LYON})], model="hold", wait_seconds=1
    )
    try:
        messages = collect(out, until="held", also="call", process=first)
    finally:
        if first.is_alive():
            os.kill(first.pid, signal.SIGKILL)
        first.join(JOIN_SECONDS)
    assert first.exitcode == -signal.SIGKILL
    [(_, answer, is_error)] = [m for m in messages if m[0] == "call"]
    assert not is_error
    assert answer.splitlines()[1:] == [TIMED_OUT_LINE]
    [(source_id, state, claim)] = _memory_rows(tmp_path)
    assert state == "linking" and claim is not None

    # standing in for the 5 minutes after which a claim with no heartbeat is stale
    stale = (datetime.now(UTC) - timedelta(minutes=6)).replace(tzinfo=None).isoformat(" ")
    with _db(tmp_path) as db:
        db.execute("UPDATE sources SET last_activity_at = ? WHERE id = ?", (stale, source_id))

    second, out = servers.spawn(
        tmp_path,
        [("recall", {"query": "Where does Pierre live?"})],
        model="ok",
        sweep=True,
    )
    messages = _finish(second, out)
    [(_, report)] = [m for m in messages if m[0] == "sweep"]
    assert (report["linked"], report["left"]) == (1, 0)
    [(_, recalled, is_error)] = [m for m in messages if m[0] == "call"]
    assert not is_error
    assert "Lyon" in recalled
    assert _memory_rows(tmp_path) == [(source_id, "linked", None)]


def test_two_sweeps_link_each_memory_once(tmp_path: Path, servers: ServerProcesses) -> None:
    remember = [("remember", {"text": text}) for text in BALL]
    stored, out = servers.spawn(tmp_path, remember, model="none", script=BALL)
    calls = [m for m in _finish(stored, out) if m[0] == "call"]
    assert len(calls) == 10 and not any(is_error for _, _, is_error in calls)
    assert {state for _, state, _ in _memory_rows(tmp_path)} == {"no_model"}

    start = barrier(2)
    sweeps = [
        servers.spawn(tmp_path, [], model="ok", sweep=True, start=start, script=BALL)
        for _ in range(2)
    ]
    results = [_finish(process, out) for process, out in sweeps]
    reports = [next(m[1] for m in messages if m[0] == "sweep") for messages in results]
    model_calls = [
        next(m[1] for m in messages if m[0] == "done")["model_calls"] for messages in results
    ]

    assert sum(r["linked"] for r in reports) == 10
    assert [r["failed"] for r in reports] == [0, 0], reports
    # the sweep that ends last finds nothing left (the other may still be linking at its end)
    assert min(r["left"] for r in reports) == 0, reports
    for mine, theirs in (reports, reports[::-1]):
        # what one sweep did not link itself, it saw linked or being linked by the other
        assert mine["linked"] + mine["elsewhere"] + theirs["linked"] >= 10
    # each memory was extracted once: two model calls (entities, relationships) per memory
    assert sum(model_calls) == 2 * 10
    assert {state for _, state, _ in _memory_rows(tmp_path)} == {"linked"}
    with _db(tmp_path) as db:
        labels = Counter(label for (label,) in db.execute("SELECT label FROM graph_nodes"))
        memberships = db.execute(
            "SELECT node_id, source_id, COUNT(*) FROM graph_node_sources "
            "GROUP BY node_id, source_id HAVING COUNT(*) > 1"
        ).fetchall()
    assert {label for label, n in labels.items() if n > 1} == set()
    assert set(GUESTS) <= set(labels)
    assert memberships == []
