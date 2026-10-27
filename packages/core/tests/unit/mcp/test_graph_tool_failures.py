# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The graph tools through the MCP server when a call fails.

One failed call used to take the server's session down: ``tool_handler`` swallowed the
error without a rollback, and every later call in the process failed with
``PendingRollbackError``. A failed call now leaves the next one to run.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
import structlog
from sqlmodel import select

from chaoscypher_core.adapters.sqlite.models import GraphEdge
from chaoscypher_core.settings import GraphSettings
from tests.unit.mcp.memory_world import build_world, call, call_raw


if TYPE_CHECKING:
    from chaoscypher_core.models import EdgeCreate, NodeCreate


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    w = build_world(tmp_path, monkeypatch)
    yield w
    w.engine.close()


async def _two_nodes(world: SimpleNamespace) -> tuple[str, str]:
    created = json.loads(
        await call(world.server, "create_template", {"name": "Note", "template_type": "node"})
    )
    template_id = created["template_id"]
    ids = []
    for label in ("a", "b"):
        node = json.loads(
            await call(
                world.server,
                "create_node",
                {"template_id": template_id, "label": label, "properties": {}},
            )
        )
        ids.append(node["node_id"])
    return ids[0], ids[1]


def _fail_inside_the_flush(world: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    """The repository's edge insert names a template that does not exist: its flush fails."""
    real = world.graph.create_edge

    def create_edge(edge: EdgeCreate, **kwargs: Any) -> Any:
        return real(edge.model_copy(update={"template_id": "no_such_template"}), **kwargs)

    monkeypatch.setattr(world.graph, "create_edge", create_edge)


async def test_a_call_failing_inside_a_flush_leaves_the_next_call_to_run(
    world: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    a, b = await _two_nodes(world)
    _fail_inside_the_flush(world, monkeypatch)
    _text, is_error = await call_raw(
        world.server, "create_edge", {"source_node_id": a, "target_node_id": b}
    )
    assert is_error
    listed = json.loads(await call(world.server, "list_templates", {}))
    assert listed["success"] is True


# ----- create_edge answers what it can do, never a foreign-key failure --------


def _delete_template(world: SimpleNamespace, template_id: str) -> None:
    db = world.engine.data_dir / "app.db"
    with sqlite3.connect(db) as conn:
        conn.execute("DELETE FROM graph_templates WHERE id = ?", (template_id,))
    world.graph.session.expire_all()


async def test_an_edge_with_no_template_saves_where_the_default_one_was_deleted(
    world: SimpleNamespace,
) -> None:
    a, b = await _two_nodes(world)
    default = GraphSettings().default_edge_template
    _delete_template(world, default)
    answer = json.loads(
        await call(world.server, "create_edge", {"source_node_id": a, "target_node_id": b})
    )
    assert answer["success"] is True
    edge = world.graph.get_edge(answer["edge_id"])
    assert edge is not None and edge.template_id == default
    assert world.graph.get_template(default) is not None


async def test_an_edge_names_its_template_by_id_or_name(world: SimpleNamespace) -> None:
    a, b = await _two_nodes(world)
    await call(world.server, "create_template", {"name": "cites", "template_type": "edge"})
    answer = json.loads(
        await call(
            world.server,
            "create_edge",
            {"source_node_id": a, "target_node_id": b, "template_id": "cites"},
        )
    )
    edge = world.graph.get_edge(answer["edge_id"])
    assert edge is not None and world.graph.get_template(edge.template_id).name == "cites"


async def test_an_edge_with_an_unknown_template_answers_unknown_template(
    world: SimpleNamespace,
) -> None:
    a, b = await _two_nodes(world)
    text, is_error = await call_raw(
        world.server,
        "create_edge",
        {"source_node_id": a, "target_node_id": b, "template_id": "no_such_template"},
    )
    assert is_error
    assert json.loads(text)["code"] == "UNKNOWN_TEMPLATE"


async def test_an_edge_to_a_missing_node_answers_unknown_node(world: SimpleNamespace) -> None:
    a, _b = await _two_nodes(world)
    text, is_error = await call_raw(
        world.server, "create_edge", {"source_node_id": a, "target_node_id": "node_missing"}
    )
    assert is_error
    answer = json.loads(text)
    assert answer["code"] == "UNKNOWN_NODE"
    assert answer["error"] == "Unknown node: node_missing"


async def test_a_failure_inside_the_flush_answers_a_fixed_reason_and_no_sql(
    world: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    a, b = await _two_nodes(world)
    _fail_inside_the_flush(world, monkeypatch)
    text, is_error = await call_raw(
        world.server, "create_edge", {"source_node_id": a, "target_node_id": b}
    )
    assert is_error
    answer = json.loads(text)
    assert answer["code"] == "MISSING_REFERENCE"
    assert "INSERT" not in text and "FOREIGN" not in text and a not in text


async def test_a_node_naming_a_deleted_shipped_template_restores_it(
    world: SimpleNamespace,
) -> None:
    default = GraphSettings().default_node_template
    _delete_template(world, default)
    answer = json.loads(
        await call(
            world.server,
            "create_node",
            {"template_id": default, "label": "a", "properties": {}},
        )
    )
    assert answer["node"]["template_id"] == default


async def test_a_failed_remove_document_leaves_the_storage_session_usable(
    world: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chaoscypher_core.mcp import memory

    async def remove_source(engine: Any, _source_id: str) -> Any:
        session = engine.storage_adapter.session
        session.add(
            GraphEdge(
                id="edge_to_nowhere",
                database_name="test",
                graph_name="knowledge",
                template_id="no_such_template",
                source_node_id="no_such_node",
                target_node_id="no_such_node",
                label="about",
                properties={},
            )
        )
        session.flush()

    monkeypatch.setattr(memory, "remove_source", remove_source)
    text, _is_error = await call_raw(world.server, "remove_document", {"source_id": "src_x"})
    assert json.loads(text)["code"] == "MISSING_REFERENCE"
    assert world.storage.session.exec(select(GraphEdge.id)).all() == []


# ----- a failed tool's log carries no words it was given ---------------------

MARKER = "ZQX-marker-of-the-memorys-words"


async def test_a_failed_create_node_logs_no_word_it_was_given(
    world: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A client saves a memory as a node's properties; its failed insert logs none of them."""
    real = world.graph.create_node

    def create_node(node: NodeCreate, **kwargs: Any) -> Any:
        return real(node.model_copy(update={"template_id": "no_such_template"}), **kwargs)

    monkeypatch.setattr(world.graph, "create_node", create_node)
    with structlog.testing.capture_logs() as logs:
        text, is_error = await call_raw(
            world.server,
            "create_node",
            {"template_id": "Person", "label": MARKER, "properties": {"text": MARKER}},
        )
    assert is_error and json.loads(text)["code"] == "MISSING_REFERENCE"
    [failed] = [r for r in logs if r["event"] == "create_node_failed"]
    assert failed["error_type"] == "IntegrityError"
    assert failed["outcome"] == "MISSING_REFERENCE"
    assert failed["tool"] == "create_node"
    assert [r["event"] for r in logs if MARKER in str(r)] == []


async def test_a_remove_document_whose_cleanup_raises_still_answers_its_own_failure(
    world: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chaoscypher_core.mcp import memory

    async def remove_source(_engine: Any, _source_id: str) -> Any:
        raise ValueError(MARKER)

    def dead() -> None:
        raise RuntimeError(MARKER)

    monkeypatch.setattr(memory, "remove_source", remove_source)
    monkeypatch.setattr(world.graph, "discard_failed_work", dead)
    text, is_error = await call_raw(world.server, "remove_document", {"source_id": "src_x"})
    assert json.loads(text)["code"] == "OPERATION_FAILED"
    assert MARKER not in text
