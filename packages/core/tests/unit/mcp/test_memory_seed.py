# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""``build_seed``: the ``about`` names, the graph nodes a memory text names, then the speaker."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.adapters.sqlite.repos.graph.sqlite_repository import GraphRepository
from chaoscypher_core.adapters.sqlite.repos.search import SearchRepository
from chaoscypher_core.mcp.memory_seed import build_seed
from chaoscypher_core.models import NodeCreate, TemplateCreate
from chaoscypher_core.settings import EngineSettings


SPEAKER = {
    "name": "alice",
    "type": "Person",
    "aliases": [],
    "properties": {"role": "the speaker (I, me)"},
}


@pytest.fixture
def engine(tmp_path: Path) -> Generator[SimpleNamespace]:
    """An engine-shaped namespace: the graph, the search index and the settings."""
    db_path = tmp_path / "app.db"
    sa_engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(sa_engine, checkfirst=True)
    adapter = SqliteAdapter(str(db_path), database_name="test")
    adapter.connect()
    graph = GraphRepository(adapter.session, database_name="test")
    search = SearchRepository(sa_engine, vector_dim=8)
    graph.create_template(TemplateCreate(name="Person", template_type="node"), custom_id="person")
    for nid, label, aliases in (
        ("pierre", "Pierre Bezukhov", ["Pierre"]),
        ("anna", "Anna Pavlovna", ["Anna"]),
    ):
        node = graph.create_node(
            NodeCreate(
                template_id="person",
                label=label,
                entity_type="Person",
                properties={"aliases": aliases},
            ),
            custom_id=nid,
        )
        search.index_node(node)
    yield SimpleNamespace(
        graph_repository=graph, search_repository=search, settings=EngineSettings()
    )
    adapter.disconnect()


def test_a_text_naming_pierre_seeds_pierre_and_not_anna(engine: SimpleNamespace) -> None:
    records = build_seed(engine, "Pierre moved to Lyon.", about=[], by="alice").records("alice")
    assert [r["name"] for r in records] == ["Pierre Bezukhov", "alice"]
    assert records[0] == {
        "name": "Pierre Bezukhov",
        "type": "Person",
        "aliases": ["Pierre"],
        "properties": {},
    }


def test_about_adds_a_named_node(engine: SimpleNamespace) -> None:
    records = build_seed(engine, "She moved to Lyon.", about=["Anna"], by="alice").records("alice")
    assert [r["name"] for r in records] == ["Anna Pavlovna", "alice"]


def test_the_about_names_come_before_the_texts_own(engine: SimpleNamespace) -> None:
    records = build_seed(engine, "Pierre moved to Lyon.", about=["Anna"], by="alice").records(
        "alice"
    )
    assert [r["name"] for r in records] == ["Anna Pavlovna", "Pierre Bezukhov", "alice"]


def test_the_speaker_is_last_and_present_with_an_empty_text(engine: SimpleNamespace) -> None:
    assert build_seed(engine, "", about=[], by="alice").records("alice") == [SPEAKER]
    records = build_seed(engine, "Pierre and Anna met.", about=["Anna"], by="alice").records(
        "alice"
    )
    assert records[-1] == SPEAKER
    assert [r["name"] for r in records].count("Anna Pavlovna") == 1


def test_the_limit_bounds_the_records_and_keeps_the_speaker(engine: SimpleNamespace) -> None:
    engine.settings.extraction.known_entities_limit = 2
    records = build_seed(engine, "Pierre and Anna met.", about=[], by="alice").records("alice")
    assert len(records) == 2 and records[-1] == SPEAKER
