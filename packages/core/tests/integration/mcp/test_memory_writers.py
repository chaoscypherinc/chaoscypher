# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Several writers on one memory database, each a real process (spec 2026-10-24 § 3).

Threads would share the process's locks and sessions and prove nothing (D10), so every
writer here is a ``spawn`` process of its own on one database file, with a scripted model,
started together on a barrier (``tests/unit/mcp/memory_processes.py``).
"""

from __future__ import annotations

import random
import signal
import sqlite3
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from chaoscypher_core.mcp.memory import NOTHING_YET
from tests.unit.mcp.memory_processes import (
    FRIEND,
    GUESTS,
    KYIV,
    LISE_PARIS,
    LISE_VIENNA,
    LYON,
    MARYA_LIVES,
    MARYA_PLAYS,
    POND,
    Barrier,
    Call,
    ForgetMatching,
    HoldWriterLock,
    Open,
    Options,
    Pause,
    ServerProcesses,
    Signal,
    Step,
    Sync,
    Wait,
    db_path,
    guest_memory,
    open_world,
)
from tests.unit.mcp.memory_world import memory_sources


if TYPE_CHECKING:
    from multiprocessing.process import BaseProcess

    from chaoscypher_core import Engine
    from chaoscypher_core.models import Edge, Node


def nodes_labelled(engine: Engine, label: str) -> list[Node]:
    """Every node with ``label``."""
    nodes = engine.graph_repository.list_nodes(limit=1000, include_embedding=False)
    return [n for n in nodes if n.label == label]


def located_in_facts(engine: Engine, subject_label: str) -> dict[str, Edge]:
    """The one ``subject_label`` node's ``located_in`` facts, keyed by their object's label."""
    [subject] = nodes_labelled(engine, subject_label)  # one node: identity converged too
    facts: dict[str, Edge] = {}
    for edge in engine.graph_repository.edges_from(subject.id, "located_in"):
        target = engine.graph_repository.get_node(edge.target_node_id, include_embedding=False)
        assert target is not None
        facts[target.label] = edge
    return facts


def test_two_servers_start_on_a_new_database_at_once_and_both_answer(
    tmp_path: Path, servers: ServerProcesses
) -> None:
    plan = [Barrier(), Open(str(tmp_path)), Call("list_memories", {})]
    run = servers.run_writers([plan, plan])
    run.assert_clean()
    for i in (0, 1):
        [result] = run.results[i]
        assert (result.text, result.is_error) == (NOTHING_YET, False)
    assert db_path(tmp_path).exists()


def test_single_valued_facts_from_two_processes_settle_as_a_serial_order(
    tmp_path: Path, servers: ServerProcesses
) -> None:
    """Lyon (2026-10-20) and Kyiv (2026-10-21) at once, ten rounds: Kyiv open, Lyon closed by it.

    The jitter of round ``i`` comes from ``random.Random(i)``, so a failing round reproduces.
    """
    rounds = [tmp_path / f"round{i}" for i in range(10)]
    plans: list[list[Step]] = [[], []]
    for i, d in enumerate(rounds):
        rng = random.Random(i)
        for plan, memory in zip(plans, (LYON, KYIV), strict=True):
            plan.extend(
                [Open(str(d)), Barrier(), Pause(rng.randint(0, 50)), Call("remember", memory)]
            )
    run = servers.run_writers(plans)
    run.assert_clean()
    assert not any(r.is_error for rs in run.results.values() for r in rs)
    for d in rounds:
        engine = open_world(d)
        try:
            kyiv_memory = next(
                s["id"]
                for s in memory_sources(engine.storage_adapter)
                if str(s["valid_from"]).startswith("2026-10-21")
            )
            facts = located_in_facts(engine, "Pierre Bezukhov")
            assert facts["Kyiv"].valid_to is None, d.name
            assert (facts["Lyon"].valid_to, facts["Lyon"].closed_by_source_id) == (
                date(2026, 10, 21),
                kyiv_memory,
            ), d.name
        finally:
            engine.close()


def test_a_long_lived_server_sees_another_processes_memory(
    tmp_path: Path, servers: ServerProcesses
) -> None:
    """Server A recalls nothing, B remembers Paris, A finds it; B moves her to Vienna, A sees it.

    The Paris memory is both the first find and the fact Vienna supersedes (spec § 3.6).
    """
    d = str(tmp_path)
    where = Call("recall", {"query": "Where does Lise live?"})
    server_a = [Open(d), Call("recall", {"query": "Lise"}), Signal("a1"), Wait("b1"), where,
                Signal("a2"), Wait("b2"), where]  # fmt: skip
    process_b = [Open(d), Wait("a1"), Call("remember", LISE_PARIS), Signal("b1"),
                 Wait("a2"), Call("remember", LISE_VIENNA), Signal("b2")]  # fmt: skip
    run = servers.run_writers([server_a, process_b], events=("a1", "b1", "a2", "b2"))
    run.assert_clean()
    nothing, found, moved = (r.text for r in run.results[0])
    assert "Lise" not in nothing
    assert "located_in Paris" in found
    assert "located_in Vienna" in moved
    assert "located_in Paris" not in moved


def test_a_write_blocked_past_the_busy_timeout_is_an_error_not_a_hang(
    tmp_path: Path, servers: ServerProcesses
) -> None:
    """Another connection holds the writer lock: ``remember`` answers busy within the wait + 5 s."""

    def hold(sync: Sync, _procs: Sequence[BaseProcess]) -> None:
        assert sync.events["ready"].wait(120)
        holder = sqlite3.connect(db_path(tmp_path), timeout=0.1)
        holder.execute("BEGIN IMMEDIATE")
        sync.events["locked"].set()
        try:
            assert sync.events["called"].wait(120)
        finally:
            holder.execute("ROLLBACK")
            holder.close()

    plan = [Open(str(tmp_path)), Signal("ready"), Wait("locked"), Call("remember", LYON),
            Signal("called")]  # fmt: skip
    run = servers.run_writers(
        [plan],
        options=Options(busy_timeout_ms=1000),
        events=("ready", "locked", "called"),
        parent=hold,
    )
    run.assert_clean()
    [answer] = run.results[0]
    assert answer.is_error
    assert "the database is busy" in answer.text.lower()
    assert answer.seconds < 1 + 5


def test_a_remember_waiting_on_the_model_holds_no_writer_lock(
    tmp_path: Path, servers: ServerProcesses
) -> None:
    """While A's model is mid-extraction, another connection takes the writer lock at once."""

    def take_the_lock(sync: Sync, _procs: Sequence[BaseProcess]) -> None:
        assert sync.events["model_entered"].wait(120)
        try:
            other = sqlite3.connect(db_path(tmp_path), timeout=0.1)
            other.execute("PRAGMA busy_timeout=100")
            other.execute("BEGIN IMMEDIATE")  # raises "database is locked" if A holds it
            other.execute("ROLLBACK")
            other.close()
        finally:
            sync.events["model_release"].set()

    run = servers.run_writers(
        [[Open(str(tmp_path)), Call("remember", POND)]],
        options=Options(script="blocking"),
        parent=take_the_lock,
    )
    run.assert_clean()
    [answer] = run.results[0]
    assert not answer.is_error, answer.text


def test_a_writer_killed_holding_the_lock_does_not_block_the_others(
    tmp_path: Path, servers: ServerProcesses
) -> None:
    """SQLite gives the lock back with the dead process's connection: the next write goes through."""

    def kill_the_holder(sync: Sync, procs: Sequence[BaseProcess]) -> None:
        assert sync.events["held"].wait(120)
        procs[0].kill()
        procs[0].join(30)
        sync.events["killed"].set()

    d = str(tmp_path)
    holder = [Open(d), Signal("a_open"), Wait("b_open"), HoldWriterLock("held")]
    writer = [Open(d), Signal("b_open"), Wait("a_open"), Wait("killed"), Call("remember", LYON)]
    run = servers.run_writers(
        [holder, writer], events=("a_open", "b_open", "held", "killed"), parent=kill_the_holder
    )
    run.assert_clean(killed={0})
    assert run.exitcodes[0] == -signal.SIGKILL
    [answer] = run.results[1]
    assert not answer.is_error, answer.text
    assert answer.seconds < 10


@pytest.mark.parametrize("vectors", [True, False], ids=["vectors", "keyword-only"])
def test_two_servers_remember_at_once_and_every_memory_lands(
    tmp_path: Path, servers: ServerProcesses, vectors: bool
) -> None:
    """Twenty memories each from two processes started together: all forty land, findable."""
    halves = (GUESTS[:20], GUESTS[20:])
    plans: list[list[Step]] = [
        [
            Open(str(tmp_path)),
            Barrier(),
            *(Call("remember", {"text": guest_memory(n), "by": by}) for n in names),
        ]
        for names, by in zip(halves, ("alice", "bob"), strict=True)
    ]
    run = servers.run_writers(plans, options=Options(vectors=vectors))
    run.assert_clean()
    answers = [r for rs in run.results.values() for r in rs]
    assert len(answers) == 40
    assert all(not r.is_error and r.text.startswith("Remembered") for r in answers), answers
    assert [r.text for r in answers if "Not linked yet" in r.text] == []
    engine = open_world(tmp_path)
    try:
        sources = memory_sources(engine.storage_adapter)
        assert len(sources) == 40
        for s in sources:
            chunks, _ = engine.storage_adapter.get_chunks_by_source(s["id"])
            [chunk] = chunks
            name = chunk["content"].split()[0]
            assert engine.search_repository.keyword_search(
                name, item_type="chunk", source_ids=[s["id"]]
            ), name
        [estate] = nodes_labelled(engine, "Otradnoe")
        assert len(engine.graph_repository.node_members(estate.id)) == 40
    finally:
        engine.close()


def test_a_new_name_remembered_in_two_processes_ends_on_one_node(
    tmp_path: Path, servers: ServerProcesses
) -> None:
    """Two first mentions of Marya at once: the later identity pass joins them (G3)."""
    d = str(tmp_path)
    run = servers.run_writers(
        [
            [Open(d), Barrier(), Call("remember", MARYA_PLAYS)],
            [Open(d), Barrier(), Call("remember", MARYA_LIVES)],
        ]
    )
    run.assert_clean()
    engine = open_world(tmp_path)
    try:
        [marya] = nodes_labelled(engine, "Marya Bolkonskaya")
        assert len(engine.graph_repository.node_members(marya.id)) == 2
    finally:
        engine.close()


def test_forget_in_one_process_while_another_remembers_the_same_subject(
    tmp_path: Path, servers: ServerProcesses
) -> None:
    """No error; the forgotten fact is gone and the new one is there."""
    d = str(tmp_path)
    forgetter = [
        Open(d),
        Call("remember", LYON),
        Signal("saved"),
        Barrier(),
        ForgetMatching("Lyon"),
    ]
    rememberer = [Open(d), Wait("saved"), Barrier(), Call("remember", FRIEND)]
    run = servers.run_writers([forgetter, rememberer], events=("saved",))
    run.assert_clean()
    assert not any(r.is_error for rs in run.results.values() for r in rs)
    assert run.results[0][-1].text.startswith("Forgotten")
    engine = open_world(tmp_path)
    try:
        assert "Lyon" not in located_in_facts(engine, "Pierre Bezukhov")
        [pierre] = nodes_labelled(engine, "Pierre Bezukhov")
        [anna] = nodes_labelled(engine, "Anna Pavlovna")
        graph = engine.graph_repository
        # friend_of is symmetric: the commit orders its endpoints by node, either way round
        friends = graph.edges_from(pierre.id, "friend_of") + graph.edges_from(anna.id, "friend_of")
        assert [{e.source_node_id, e.target_node_id} for e in friends] == [{pierre.id, anna.id}]
    finally:
        engine.close()
