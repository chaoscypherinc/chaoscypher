# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Every tool call reads fresh rows (spec 2026-10-24 § 3.3 (b), G4).

The engine keeps long-lived sessions, and SQLAlchemy hands back a row already in a
session's identity map without reloading it. Two engines on one database file stand for two
processes here: what one writes, the other's next call must see. One process is the point
of these tests (identity maps are per session); the real processes are in
``tests/integration/mcp/test_memory_writers.py``.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import inspect
from sqlmodel import select

from chaoscypher_core.adapters.sqlite.models import GraphEdge, GraphNode
from chaoscypher_core.mcp.memory import NOTHING_YET
from chaoscypher_core.mcp.memory_link import MemoryLinker
from chaoscypher_core.mcp.server import create_mcp_server
from chaoscypher_core.models import NodeCreate, TemplateCreate
from tests.unit.mcp.memory_processes import LISE_PARIS, LISE_VIENNA, LYON, WRITERS_SCRIPT
from tests.unit.mcp.memory_world import build_world, call, engine_at, settings


if TYPE_CHECKING:
    from chaoscypher_core import Engine


@pytest.fixture
async def two(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[SimpleNamespace]:
    """Server A (its world) and server B, a second engine on the same database file."""
    a = build_world(tmp_path, monkeypatch, script=WRITERS_SCRIPT)
    b_engine = engine_at(tmp_path, settings())  # the scripted model is patched on the class
    b_linker = MemoryLinker(b_engine)
    try:
        yield SimpleNamespace(
            a=a, b_engine=b_engine, b_server=create_mcp_server(b_engine, linker=b_linker)
        )
    finally:
        await b_linker.close()
        b_engine.close()
        await a.linker.close()
        a.engine.close()


async def test_a_long_lived_server_recalls_what_another_engine_replaced(
    two: SimpleNamespace,
) -> None:
    await call(two.a.server, "remember", LISE_PARIS)
    first = await call(two.a.server, "recall", {"query": "Where does Lise live?"})
    assert "located_in Paris" in first
    # A's sessions hold the Paris edge, open: the identity map is weak, so the test keeps
    # the rows, as a call still running in this server (a link past its wait) would
    held = _edge_rows(two.a.engine)
    assert held and all(row.valid_to is None for row in held)
    await call(two.b_server, "remember", LISE_VIENNA)  # B closes it
    now = await call(two.a.server, "recall", {"query": "Where does Lise live?"})
    assert "located_in Vienna" in now
    assert "located_in Paris" not in now


def _edge_rows(engine: Engine) -> list[GraphEdge]:
    """Every edge row, loaded into both of the engine's long-lived sessions."""
    rows: list[GraphEdge] = []
    for session in (engine.graph_repository.session, engine.storage_adapter.session):
        rows += session.exec(select(GraphEdge)).all()
    return rows


async def test_a_memory_another_engine_forgot_is_gone_from_this_servers_answers(
    two: SimpleNamespace,
) -> None:
    await call(two.a.server, "remember", LYON)
    assert "moved to Lyon" in await call(two.a.server, "list_memories", {})
    preview = await call(two.b_server, "forget", {"query": "Lyon"})
    found = re.search(r'forget\(handle="([^"]+)"', preview)
    assert found, preview
    await call(two.b_server, "forget", {"handle": found.group(1), "confirm": True})
    assert await call(two.a.server, "list_memories", {}) == NOTHING_YET
    assert "located_in Lyon" not in await call(
        two.a.server, "recall", {"query": "Where does Pierre live?"}
    )


def test_expire_cached_rows_reloads_a_clean_session_and_leaves_pending_changes_alone(
    two: SimpleNamespace,
) -> None:
    engine = two.a.engine
    graph = engine.graph_repository
    graph.create_template(TemplateCreate(name="Person", template_type="node"), custom_id="person")
    graph.create_node(NodeCreate(template_id="person", label="Boris"), custom_id="boris")
    node = graph.session.get(GraphNode, "boris")
    assert node is not None and node.label == "Boris"
    engine.expire_cached_rows()
    assert "label" in inspect(node).unloaded  # clean session: expired
    node.label = "Boris Drubetskoy"  # now an unflushed change
    engine.expire_cached_rows()
    assert node in graph.session.dirty
    assert node.label == "Boris Drubetskoy"
