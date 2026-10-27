# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The commit's edge-template fallback: the default it falls back to exists, and is made sure first.

When a source's own edge template cannot be created, its edges fall back to the default
link template (``GraphSettings.default_edge_template``), a foreign-key target. Both fallback
paths (the batch and the single template) make sure it exists before they try, never in the
``except``: after a flush that failed inside the commit's transaction, the session refuses
every statement until the transaction ends, and a restore there raised
``PendingRollbackError`` over the real failure.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.models import GraphEdge
from chaoscypher_core.database.migrations.runner import upgrade_to_head
from chaoscypher_core.repo_factories import get_transaction_graph_repository
from chaoscypher_core.services.sources.engine.commit.relation import RelationshipCommitHandler
from chaoscypher_core.settings import GraphSettings


DEFAULT = GraphSettings().default_edge_template


@pytest.fixture
def adapter(tmp_path: Path) -> SqliteAdapter:
    """A database at head without the shipped templates (the migrations alone built it)."""
    db = tmp_path / "app.db"
    upgrade_to_head(db)
    adapter = SqliteAdapter(str(db), database_name="default")
    adapter.connect()
    yield adapter
    adapter.disconnect()


def _flush_that_fails(adapter: SqliteAdapter) -> None:
    session = adapter.session
    assert session is not None
    session.add(
        GraphEdge(
            id="edge_to_nowhere",
            database_name="default",
            graph_name="knowledge",
            template_id="no_such_template",
            source_node_id="no_such_node",
            target_node_id="no_such_node",
            label="about",
            properties={},
        )
    )
    session.flush()


async def test_the_batch_fallback_restores_the_default_template(
    adapter: SqliteAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    graph = get_transaction_graph_repository(adapter, "default")

    async def fails(_templates: Any) -> Any:
        raise RuntimeError("the batch insert failed")

    monkeypatch.setattr(graph, "upsert_templates_batch", fails)
    mapping, *_ = await RelationshipCommitHandler(graph).batch_create_edge_templates(["cites"])
    assert mapping == {"cites": DEFAULT}
    assert graph.get_template(DEFAULT) is not None


def test_the_single_fallback_after_a_failed_flush_answers_the_default(
    adapter: SqliteAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Inside the commit's transaction, a failed flush leaves the fallback to answer, not raise."""
    graph = get_transaction_graph_repository(adapter, "default")
    real = graph.create_template

    def create_template(template: Any, *args: Any, **kwargs: Any) -> Any:
        if template.name == "cites":  # the source's own template; the default's restore runs
            _flush_that_fails(adapter)
        return real(template, *args, **kwargs)

    monkeypatch.setattr(graph, "create_template", create_template)
    answered: list[str] = []
    with pytest.raises(Exception), adapter.transaction():  # noqa: B017 - the block's own end
        answered.append(RelationshipCommitHandler(graph)._create_edge_template("cites"))
    assert answered == [DEFAULT]


async def test_the_batch_fallback_after_a_failed_flush_answers_the_default(
    adapter: SqliteAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    graph = get_transaction_graph_repository(adapter, "default")

    async def fails(_templates: Any) -> Any:
        _flush_that_fails(adapter)

    monkeypatch.setattr(graph, "upsert_templates_batch", fails)
    answered: list[dict[str, str]] = []

    async def in_the_commits_transaction() -> None:
        with adapter.transaction():
            mapping, *_ = await RelationshipCommitHandler(graph).batch_create_edge_templates(
                ["cites"]
            )
            answered.append(mapping)

    with pytest.raises(Exception):  # noqa: B017 - the block's own end
        await in_the_commits_transaction()
    assert answered == [{"cites": DEFAULT}]
