# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The resolver treats a flagged orphan as a normal node, and a merge connects it (2026-10-07).

Keep, flag, never delete: the commit keeps an edgeless entity that carries
evidence, flagged ``properties.orphan`` (``commit.service.flag_orphan_entities``),
and the post-commit resolver may connect it later. It plans merges over a
flagged orphan as over any node; the merge moves every edge onto the kept
node, so the merged node keeps the flag only when both were orphans
(``deduplication.service._merge_orphan_flag``, through ``merge_node_properties``,
the one property policy), and the orphan's description is merged like any
other.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.adapters.sqlite.repos.graph.sqlite_repository import GraphRepository
from chaoscypher_core.models import EdgeCreate, NodeCreate, TemplateCreate
from chaoscypher_core.services.graph.resolution import resolve_graph
from chaoscypher_core.services.sources.engine.deduplication.name_identity import ORPHAN_KEY
from chaoscypher_core.services.sources.engine.deduplication.service import merge_node_properties
from tests.fakes.domain_tables import domain_conventions


LITERARY = domain_conventions("literary")


def test_an_orphan_merged_into_a_connected_node_loses_the_flag_and_keeps_its_description() -> None:
    merged = merge_node_properties(
        {"definition": "A visitor of the Rostóvs."},
        {"definition": "Julie's mother.", ORPHAN_KEY: True},
    )
    assert ORPHAN_KEY not in merged
    assert "Julie's mother." in merged["definition"]
    assert "A visitor of the Rostóvs." in merged["definition"]


def test_a_connected_node_merged_into_an_orphan_connects_it() -> None:
    merged = merge_node_properties({"definition": "Julie's mother.", ORPHAN_KEY: True}, {})
    assert ORPHAN_KEY not in merged
    assert merged["definition"] == "Julie's mother."


def test_two_orphans_merged_stay_one_orphan() -> None:
    merged = merge_node_properties({ORPHAN_KEY: True}, {ORPHAN_KEY: True, "title": "Countess"})
    assert merged[ORPHAN_KEY] is True
    assert merged["title"] == "Countess"


def test_a_value_of_the_texts_under_the_key_is_no_flag_and_is_kept() -> None:
    assert merge_node_properties({"orphan": "yes"}, {ORPHAN_KEY: True})["orphan"] == "yes"
    assert merge_node_properties({ORPHAN_KEY: True}, {"orphan": "yes"})["orphan"] == "yes"


@pytest.fixture
def repo(tmp_path: Path) -> Generator[GraphRepository]:
    db_path = tmp_path / "app.db"
    SQLModel.metadata.create_all(get_engine(db_path), checkfirst=True)
    adapter = SqliteAdapter(str(db_path), database_name="test")
    adapter.connect()
    assert adapter.session is not None
    repo = GraphRepository(adapter.session, database_name="test")
    for tpl, kind in (("character", "node"), ("rel", "edge")):
        repo.create_template(TemplateCreate(name=tpl, template_type=kind), custom_id=tpl)
    yield repo
    adapter.disconnect()


def _node(repo: GraphRepository, nid: str, label: str, **props: object) -> None:
    repo.create_node(
        NodeCreate(template_id="character", label=label, entity_type="character", properties=props),
        custom_id=nid,
    )


@pytest.mark.parametrize("orphan_is_fuller", [True, False])
def test_the_resolver_merges_a_flagged_orphan_like_any_node(
    repo: GraphRepository, orphan_is_fuller: bool
) -> None:
    """The Book One pair, one of them a flagged orphan: merged, connected, both descriptions kept."""
    full, twin = (
        ("Princess Anna Mikháylovna Drubetskáya", "Princess Drubetskáya")
        if orphan_is_fuller
        else ("Princess Drubetskáya", "Princess Anna Mikháylovna Drubetskáya")
    )
    _node(
        repo,
        "orphan",
        full,
        aliases=[],
        definition="An elderly lady from one of the best families in Russia who has become poor.",
        **{ORPHAN_KEY: True},
    )
    _node(
        repo,
        "connected",
        twin,
        aliases=[],
        definition="A member of the Rostóv household who assists the countess.",
    )
    _node(repo, "boris", "Borís", aliases=[], definition="Her son.")
    repo.create_edge(
        EdgeCreate(
            template_id="rel", source_node_id="connected", target_node_id="boris", label="parent_of"
        ),
        custom_id="e1",
    )

    plan, _report = resolve_graph(repo, conventions=LITERARY)

    assert len(plan.merges) == 1
    (merge,) = plan.merges
    kept = repo.get_node(merge.keep)
    assert kept is not None
    assert repo.get_node(merge.drop) is None
    assert ORPHAN_KEY not in kept.properties
    assert "elderly lady" in kept.properties["definition"]
    assert "Rostóv household" in kept.properties["definition"]
    edges = list(repo.list_edges(source_node_id=kept.id))
    assert [(e.target_node_id, e.label) for e in edges] == [("boris", "parent_of")]
