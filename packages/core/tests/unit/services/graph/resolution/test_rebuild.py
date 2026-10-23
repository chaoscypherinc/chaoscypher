# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""A node that loses a source is rebuilt from the sources it still has (#795, spec 2026-10-17 D3).

Real SQLite graph: two or three sources merge into one node through the real
``apply_resolution``, then one source is released through
``release_source_graph``. What survives must read as the remaining sources
wrote it: no name, text or ``resolved_from`` entry of the released source, its
home a surviving member, its label the survivor ranking's pick.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import text
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.adapters.sqlite.models import SourceRow
from chaoscypher_core.adapters.sqlite.repos.graph.sqlite_repository import GraphRepository
from chaoscypher_core.models import EdgeCreate, NodeCreate, ReleasedRows, TemplateCreate
from chaoscypher_core.services.graph.management.release import (
    refresh_released_graph,
    release_source_graph,
)
from chaoscypher_core.services.graph.resolution.apply import (
    RESOLVED_FROM,
    apply_resolution,
    fold_members,
    node_record,
)
from chaoscypher_core.services.graph.resolution.plan import (
    AliasDrop,
    Merge,
    ResolutionPlan,
    fold,
)
from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
    MERGED_NAMES_KEY,
)
from tests.fakes.domain_tables import domain_conventions


LITERARY = domain_conventions("literary")


@pytest.fixture
def adapter(tmp_path: Path) -> Generator[SqliteAdapter]:
    db_path = tmp_path / "app.db"
    SQLModel.metadata.create_all(get_engine(str(db_path)), checkfirst=True)
    a = SqliteAdapter(str(db_path), database_name="test")
    a.connect()
    yield a
    a.disconnect()


@pytest.fixture
def repo(adapter: SqliteAdapter) -> GraphRepository:
    r = GraphRepository(adapter.session, database_name="test")
    for tpl, kind in (("character", "node"), ("rel", "edge")):
        r.create_template(TemplateCreate(name=tpl, template_type=kind), custom_id=tpl)
    return r


def _sources(repo: GraphRepository, *sids: str) -> None:
    for sid in sids:
        repo.session.add(
            SourceRow(
                id=sid,
                database_name="test",
                filename=f"{sid}.txt",
                filepath=f"/tmp/{sid}.txt",
                file_type="text",
                file_size=1,
                content_hash=f"hash-{sid}",
                status="committed",
            )
        )
    repo.session.commit()
    repo.session.exec(text("UPDATE sources SET forced_domain = 'literary'"))  # type: ignore[call-overload]
    repo.session.commit()


def _node(
    repo: GraphRepository, nid: str, label: str, sid: str | None = None, **props: Any
) -> None:
    repo.create_node(
        NodeCreate(
            template_id="character",
            label=label,
            entity_type="character",
            properties={"aliases": [], **props},
            source_id=sid,
        ),
        custom_id=nid,
    )


def _edge(repo: GraphRepository, eid: str, src: str, tgt: str, label: str, sid: str) -> None:
    repo.create_edge(
        EdgeCreate(
            template_id="rel",
            source_node_id=src,
            target_node_id=tgt,
            label=label,
            properties={"justification": f"{label} per {sid}"},
            source_id=sid,
        ),
        custom_id=eid,
    )


def _merge(repo: GraphRepository, keep: str, drop: str) -> None:
    apply_resolution(repo, ResolutionPlan(merges=[Merge(keep=keep, drop=drop, why="t")]))
    repo.session.commit()


DESC = {
    "a": "Heir to a vast fortune who returns from abroad.",
    "b": "Stout young man with spectacles at the soirée.",
    "c": "Freemason who argues about Napoleon with everyone.",
}
"""What each source writes about Pierre: texts sharing no words, so a merge keeps each."""


def _two_source_pierre(repo: GraphRepository) -> None:
    """A's Pierre and Natasha keep B's; B's ``loves`` edge folds into A's."""
    _sources(repo, "a", "b")
    _node(repo, "a_pierre", "Pierre", "a", description=DESC["a"])
    _node(repo, "a_natasha", "Natasha", "a")
    _node(repo, "a_only", "Prince Andrew", "a")
    _node(
        repo,
        "b_pierre",
        "Pierre Bezúkhov",
        "b",
        aliases=["Count Bezúkhov"],
        description=DESC["b"],
    )
    _node(repo, "b_natasha", "Natasha", "b")
    _edge(repo, "a_loves", "a_pierre", "a_natasha", "loves", "a")
    _edge(repo, "b_loves", "b_pierre", "b_natasha", "loves", "b")  # folds into a_loves
    repo.session.commit()
    apply_resolution(
        repo,
        ResolutionPlan(
            merges=[
                Merge(keep="a_pierre", drop="b_pierre", why="t"),
                Merge(keep="a_natasha", drop="b_natasha", why="t"),
            ]
        ),
    )
    repo.session.commit()
    assert set(repo.edge_members("a_loves")) == {"a", "b"}


def _text(node: Any) -> str:
    return str((node.label, node.properties))


# -- the fold a merge and a rebuild share -------------------------------------------------------


def test_fold_members_matches_a_merge(repo: GraphRepository) -> None:
    """``fold_members`` is what ``_apply_merges`` writes for the same pair, alias drops included."""
    _node(
        repo,
        "anna",
        "Anna Pávlovna Schérer",
        aliases=["Annette", "Anna Mikháylovna"],
        aliases_given=["Annette", "Anna Mikháylovna"],
        description="The hostess.",
    )
    _node(
        repo,
        "mlle",
        "Mlle Schérer",
        aliases=["the hostess"],
        rank="maid of honour",
        description="Hostess of the soirée in Petersburg.",
    )
    _node(repo, "drub", "Anna Mikháylovna Drubetskáya")
    keep, drop = repo.get_node("anna"), repo.get_node("mlle")
    assert keep is not None and drop is not None
    expected = {
        "aliases": ["Annette", "Mlle Schérer", "the hostess"],  # the dropped alias is gone
        "rank": "maid of honour",
        "description": "The hostess. Hostess of the soirée in Petersburg.",
        "aliases_given": ["Annette"],
        MERGED_NAMES_KEY: ["Mlle Schérer"],
        RESOLVED_FROM: ["Mlle Schérer"],
    }
    assert fold_members(node_record(keep), [node_record(drop)], {fold("Anna Mikháylovna")}) == (
        expected
    )

    apply_resolution(
        repo,
        ResolutionPlan(
            merges=[Merge(keep="anna", drop="mlle", why="t")],
            alias_drops=[AliasDrop(node="anna", alias="Anna Mikháylovna", owner="drub")],
        ),
    )
    merged = repo.get_node("anna")
    assert merged is not None
    assert merged.properties == expected


# -- the rebuild ------------------------------------------------------------------------------


def test_rebuild_drops_the_released_sources_names(
    adapter: SqliteAdapter, repo: GraphRepository
) -> None:
    _two_source_pierre(repo)
    merged = repo.get_node("a_pierre")
    assert merged is not None and "Pierre Bezúkhov" in _text(merged)  # the merge took b's name

    released = release_source_graph(repo, "b", sources=adapter, database_name="test")
    repo.session.commit()

    pierre = repo.get_node("a_pierre")
    assert pierre is not None
    assert pierre.label == "Pierre"
    assert pierre.source_id == "a"
    assert "Bezúkhov" not in _text(pierre)
    assert DESC["b"] not in _text(pierre)
    assert RESOLVED_FROM not in pierre.properties
    assert MERGED_NAMES_KEY not in pierre.properties
    assert pierre.properties["description"] == DESC["a"]
    assert "a_pierre" in released.stale_node_ids

    loves = repo.get_edge("a_loves")
    assert loves is not None
    assert loves.properties == {"justification": "loves per a"}  # b's folded copy is gone
    assert loves.source_id == "a"


def _three_source_pierre(repo: GraphRepository) -> None:
    """C's Pierre Kirílovich folds into B's Pierre Bezúkhov, which folds into A's Pierre."""
    _sources(repo, "a", "b", "c")
    _node(repo, "a_pierre", "Pierre", "a", description=DESC["a"])
    _node(repo, "b_pierre", "Pierre Bezúkhov", "b", description=DESC["b"])
    _node(repo, "c_pierre", "Pierre Kirílovich Bezúkhov", "c", description=DESC["c"])
    repo.session.commit()
    _merge(repo, "b_pierre", "c_pierre")
    _merge(repo, "a_pierre", "b_pierre")
    assert set(repo.node_members("a_pierre")) == {"a", "b", "c"}


def test_three_way_merge_release_middle(adapter: SqliteAdapter, repo: GraphRepository) -> None:
    """Review Focus 1: deleting B leaves A's and C's content, nothing of B's."""
    _three_source_pierre(repo)

    release_source_graph(repo, "b", sources=adapter, database_name="test")
    repo.session.commit()

    pierre = repo.get_node("a_pierre")
    assert pierre is not None
    body = _text(pierre)
    assert DESC["a"].rstrip(".") in body
    assert DESC["c"].rstrip(".") in body
    assert DESC["b"].rstrip(".") not in body
    names = [pierre.label, *pierre.properties.get("aliases", [])]
    assert "Pierre Bezúkhov" not in names
    assert "Pierre Bezúkhov" not in pierre.properties.get(RESOLVED_FROM, [])
    assert "Pierre Bezúkhov" not in pierre.properties.get(MERGED_NAMES_KEY, [])
    assert {pierre.label, *pierre.properties["aliases"]} >= {"Pierre", "Pierre Kirílovich Bezúkhov"}
    assert set(repo.node_members("a_pierre")) == {"a", "c"}


def test_releasing_the_home_source_rehomes_the_node(
    adapter: SqliteAdapter, repo: GraphRepository
) -> None:
    """Review Focus 2: same id, home a surviving member, label from the survivor ranking."""
    _three_source_pierre(repo)

    release_source_graph(repo, "a", sources=adapter, database_name="test")
    repo.session.commit()

    pierre = repo.get_node("a_pierre")
    assert pierre is not None
    # b wrote Pierre Bezúkhov, c Pierre Kirílovich Bezúkhov: the fuller name keeps the label
    assert (pierre.label, pierre.source_id) == ("Pierre Kirílovich Bezúkhov", "c")
    assert pierre.properties["aliases"] == ["Pierre Bezúkhov"]
    assert DESC["a"].rstrip(".") not in _text(pierre)
    assert (pierre.label, *pierre.properties["aliases"]).count("Pierre") == 0


def test_the_name_more_remaining_sources_write_keeps_the_label(
    adapter: SqliteAdapter, repo: GraphRepository
) -> None:
    """Each reading counts once: two sources writing Pétya out-rank one writing the longer Pierre."""
    _sources(repo, "a", "b", "c", "d")
    _node(repo, "d_count", "Count Rostóv", "d")
    _node(repo, "a_petya", "Pétya", "a")
    _node(repo, "b_petya", "Pétya", "b")
    _node(repo, "c_pierre", "Pierre", "c")
    repo.session.commit()
    for drop in ("a_petya", "b_petya", "c_pierre"):
        _merge(repo, "d_count", drop)

    release_source_graph(repo, "d", sources=adapter, database_name="test")
    repo.session.commit()

    node = repo.get_node("d_count")
    assert node is not None
    assert node.label == "Pétya"
    assert node.source_id in {"a", "b"}


def test_a_rehomed_node_takes_its_new_homes_type_and_template(
    adapter: SqliteAdapter, repo: GraphRepository
) -> None:
    """The home wrote Person/noble under its own Person template; the member left wrote Person."""
    _sources(repo, "a", "b")
    for sid in ("a", "b"):
        repo.create_template(
            TemplateCreate(name="Person", template_type="node", source_id=sid),
            custom_id=f"person_{sid}",
        )
    for nid, label, sid, etype in (
        ("a_pierre", "Pierre", "a", "Person/noble"),
        ("b_pierre", "Pierre Bezúkhov", "b", "Person"),
    ):
        repo.create_node(
            NodeCreate(
                template_id=f"person_{sid}",
                label=label,
                entity_type=etype,
                properties={"aliases": []},
                source_id=sid,
            ),
            custom_id=nid,
        )
    repo.session.commit()
    _merge(repo, "a_pierre", "b_pierre")

    release_source_graph(repo, "a", sources=adapter, database_name="test")
    repo.session.commit()

    pierre = repo.get_node("a_pierre")
    assert pierre is not None
    assert (pierre.source_id, pierre.entity_type, pierre.template_id) == ("b", "Person", "person_b")


async def test_the_scoped_resolution_covers_both_ends_of_a_surviving_edge(
    adapter: SqliteAdapter, repo: GraphRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Natasha is a's alone and unchanged, but the folded edge to her is rebuilt: flags re-apply.

    The resolution runs in the post-release refresh, after the commit, never inside the
    release's transaction: it loads and plans the whole graph.
    """
    from chaoscypher_core.services.graph.management import release

    _sources(repo, "a", "b")
    _node(repo, "a_pierre", "Pierre", "a")
    _node(repo, "a_natasha", "Natasha", "a")
    _node(repo, "b_pierre", "Pierre Bezúkhov", "b")
    _edge(repo, "a_loves", "a_pierre", "a_natasha", "loves", "a")
    _edge(repo, "b_loves", "b_pierre", "a_natasha", "loves", "b")  # folds into a_loves
    repo.session.commit()
    _merge(repo, "a_pierre", "b_pierre")
    scopes: list[set[str]] = []
    real = release.resolve_graph

    def spy(*args: Any, **kwargs: Any) -> Any:
        scopes.append(set(kwargs["scope"]))
        return real(*args, **kwargs)

    monkeypatch.setattr(release, "resolve_graph", spy)

    released = release_source_graph(repo, "b", sources=adapter, database_name="test")
    assert scopes == [], "the release resolved inside its transaction"
    assert released.resolve_node_ids == {"a_pierre", "a_natasha"}
    assert released.conventions is not None
    repo.session.commit()

    await refresh_released_graph(repo, released, embedder=None, search=None)

    assert scopes == [{"a_pierre", "a_natasha"}]


def test_a_single_reading_left_is_that_reading(
    adapter: SqliteAdapter, repo: GraphRepository
) -> None:
    """One source left: the node becomes what it wrote, no fold (a's own text and name)."""
    _two_source_pierre(repo)

    release_source_graph(repo, "a", sources=adapter, database_name="test")
    repo.session.commit()

    pierre = repo.get_node("a_pierre")
    assert pierre is not None
    assert pierre.source_id == "b"
    assert pierre.label == "Pierre Bezúkhov"
    assert pierre.properties["aliases"] == ["Count Bezúkhov"]
    assert pierre.properties["description"] == DESC["b"]
    loves = repo.get_edge("a_loves")
    assert loves is not None
    assert loves.properties == {"justification": "loves per b"}
    assert loves.source_id == "b"  # the edge is re-homed to its lowest remaining member


def test_a_survivor_nothing_changed_on_is_not_stale(
    adapter: SqliteAdapter, repo: GraphRepository
) -> None:
    """Prince Andrew was a's alone and is gone; a node b alone wrote is left alone."""
    _two_source_pierre(repo)
    _node(repo, "b_dolokhov", "Dolokhov", "b")
    repo.session.commit()

    released = release_source_graph(repo, "a", sources=adapter, database_name="test")
    repo.session.commit()

    assert "a_only" in released.deleted_node_ids
    assert "b_dolokhov" not in released.stale_node_ids


def test_release_source_graph_commits_nothing_itself(
    adapter: SqliteAdapter, repo: GraphRepository
) -> None:
    """It joins the caller's transaction: a rollback undoes the release and the rebuild."""
    _two_source_pierre(repo)
    before = repo.get_node("a_pierre")
    assert before is not None

    class BoomError(Exception):
        pass

    def release_then_fail() -> None:
        with adapter.transaction():
            release_source_graph(repo, "b", sources=adapter, database_name="test")
            raise BoomError

    with pytest.raises(BoomError):
        release_then_fail()

    adapter.session.expire_all()
    after = repo.get_node("a_pierre")
    assert after is not None
    assert (after.label, after.properties) == (before.label, before.properties)
    assert set(repo.node_members("a_pierre")) == {"a", "b"}


def test_identical_readings_rebuild_without_merge_provenance(
    adapter: SqliteAdapter, repo: GraphRepository
) -> None:
    """Two sources that wrote the same thing are one reading: nothing folds into itself.

    b and c hold identical readings of Pierre (two sources of one CCX bundle;
    their ``source_document_id`` differs, which is provenance, not content). When
    a is released, the rebuild is that one reading: no ``resolved_from`` or
    ``merged_names`` of its own label, and the home stays b. Until the fix round
    the two readings folded into each other.
    """
    _sources(repo, "a", "b", "c")
    _node(repo, "b_pierre", "Pierre", "b", description=DESC["b"], source_document_id="b")
    _node(repo, "a_pierre", "Pierre Bezúkhov", "a", description=DESC["a"])
    snapshot = repo.node_members("b_pierre")["b"]
    reading = {**snapshot, "properties": {**snapshot["properties"], "source_document_id": "c"}}
    repo.record_node_sources([("b_pierre", "c", reading)])
    repo.session.commit()
    _merge(repo, "b_pierre", "a_pierre")

    release_source_graph(repo, "a", sources=adapter, database_name="test")
    repo.session.commit()
    repo.session.expire_all()

    pierre = repo.get_node("b_pierre")
    assert pierre is not None
    assert pierre.label == "Pierre"
    assert pierre.source_id == "b"
    assert pierre.properties == {
        "aliases": [],
        "description": DESC["b"],
        "source_document_id": "b",
    }


def test_a_fold_keeps_only_the_kept_readings_provenance() -> None:
    """``source_document_id`` and the like name the kept reading's source, never a dropped one's."""
    keep = {
        "id": "k",
        "label": "Pierre",
        "aliases": [],
        "properties": {"aliases": [], "source_document_id": "b"},
    }
    dropped = {
        "id": "d",
        "label": "Pierre Bezúkhov",
        "aliases": [],
        "properties": {
            "aliases": [],
            "source_document_id": "a",
            "ingested_at": "then",
            "type": "noble",
        },
    }
    props = fold_members(keep, [dropped], set())
    assert props["source_document_id"] == "b"
    assert "ingested_at" not in props
    assert props["type"] == "noble"  # a content property, folded like any other


# -- after the commit: the index follows ------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_reembeds_the_rebuilt_nodes_with_their_new_text(
    adapter: SqliteAdapter, repo: GraphRepository
) -> None:
    _two_source_pierre(repo)
    _node(repo, "b_dolokhov", "Dolokhov", "b")  # b's alone: its search rows go
    repo.session.commit()
    released = release_source_graph(repo, "b", sources=adapter, database_name="test")
    repo.session.commit()

    texts: list[list[str]] = []

    async def embed(batch: list[str]) -> SimpleNamespace:
        texts.append(batch)
        return SimpleNamespace(embeddings=[[0.5, 0.5]] * len(batch))

    provider = SimpleNamespace(batch_embed=AsyncMock(side_effect=embed))
    search = MagicMock()

    await refresh_released_graph(repo, released, embedder=provider, search=search)

    assert len(texts) == 1 and len(texts[0]) == len(released.stale_node_ids)
    assert not any("Bezúkhov" in t for t in texts[0])
    pierre = repo.get_node("a_pierre", include_embedding=True)
    assert pierre is not None and pierre.embedding == [0.5, 0.5]
    # only the rows the release names: no whole-index rebuild
    search.rebuild_node_fulltext.assert_not_called()
    assert "b_dolokhov" in released.deleted_node_ids
    search.delete_nodes_batch.assert_called_once_with(sorted(released.deleted_node_ids))
    ((indexed,), _) = search.index_nodes_batch.call_args
    assert {n.id for n in indexed} == set(released.stale_node_ids)
    assert all(n.embedding == [0.5, 0.5] for n in indexed)  # the vector rows follow too


@pytest.mark.asyncio
async def test_a_node_the_refreshs_resolution_merges_away_leaves_the_index(
    adapter: SqliteAdapter, repo: GraphRepository
) -> None:
    """The scoped resolution runs in the refresh: the release only scopes it.

    A node it merges away loses its search rows and is not re-embedded; the node it merged
    into is re-embedded.
    """
    _sources(repo, "a")
    _node(repo, "a_pierre", "Pierre Bezúkhov", "a")
    _node(repo, "a_count", "Count Pierre Bezúkhov", "a")
    repo.session.commit()
    released = ReleasedRows(
        resolve_node_ids=frozenset({"a_pierre", "a_count"}), conventions=LITERARY
    )
    texts: list[str] = []

    async def embed(batch: list[str]) -> SimpleNamespace:
        texts.extend(batch)
        return SimpleNamespace(embeddings=[[0.5, 0.5]] * len(batch))

    search = MagicMock()
    await refresh_released_graph(
        repo,
        released,
        embedder=SimpleNamespace(batch_embed=AsyncMock(side_effect=embed)),
        search=search,
    )

    survivors = [n for n in ("a_pierre", "a_count") if repo.get_node(n) is not None]
    assert len(survivors) == 1, "the two Pierres merged"
    (gone,) = {"a_pierre", "a_count"} - set(survivors)
    search.delete_nodes_batch.assert_called_once_with([gone])
    ((indexed,), _) = search.index_nodes_batch.call_args
    assert {n.id for n in indexed} == set(survivors)


@pytest.mark.asyncio
async def test_a_resolution_that_fails_partway_still_cleans_up_the_groups_that_committed(
    adapter: SqliteAdapter, repo: GraphRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each merge group commits on its own: a failure on the second leaves the first applied.

    The first group's merged-away node must lose its search rows and its kept
    node must be re-embedded; until the #795 follow-up a failure anywhere made
    the refresh report nothing, so both stayed stale until a reindex.
    """
    from chaoscypher_core.services.graph.resolution import apply as apply_mod

    _sources(repo, "a")
    _node(repo, "a_pierre", "Pierre Bezúkhov", "a")
    _node(repo, "a_count", "Count Pierre Bezúkhov", "a")
    _node(repo, "a_andrew", "Andrew Bolkónski", "a")
    _node(repo, "a_prince", "Prince Andrew Bolkónski", "a")
    repo.session.commit()
    released = ReleasedRows(
        resolve_node_ids=frozenset({"a_pierre", "a_count", "a_andrew", "a_prince"}),
        conventions=LITERARY,
    )
    real_group = apply_mod._merge_group
    groups: list[str] = []

    def second_group_fails(*args: Any, **kwargs: Any) -> None:
        groups.append(args[2])
        if len(groups) == 2:
            raise RuntimeError("boom")
        real_group(*args, **kwargs)

    monkeypatch.setattr(apply_mod, "_merge_group", second_group_fails)
    texts: list[str] = []

    async def embed(batch: list[str]) -> SimpleNamespace:
        texts.extend(batch)
        return SimpleNamespace(embeddings=[[0.5, 0.5]] * len(batch))

    search = MagicMock()
    await refresh_released_graph(
        repo,
        released,
        embedder=SimpleNamespace(batch_embed=AsyncMock(side_effect=embed)),
        search=search,
    )

    assert len(groups) == 2, "two merge groups were planned"
    first = (
        {"a_pierre", "a_count"}
        if groups[0] in {"a_pierre", "a_count"}
        else {
            "a_andrew",
            "a_prince",
        }
    )
    survivors = {n for n in first if repo.get_node(n) is not None}
    assert len(survivors) == 1, "the first group committed"
    (kept,) = survivors
    (gone,) = first - survivors
    deleted = {i for c in search.delete_nodes_batch.call_args_list for i in c.args[0]}
    assert gone in deleted, "the merged-away node keeps its search rows"
    indexed = {n.id for c in search.index_nodes_batch.call_args_list for n in c.args[0]}
    assert kept in indexed, "the kept node was not re-embedded"
    assert repo.get_node(kept, include_embedding=True).embedding == [0.5, 0.5]  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_with_no_embedder_the_text_rows_follow_and_the_stale_ids_are_logged(
    adapter: SqliteAdapter, repo: GraphRepository
) -> None:
    _two_source_pierre(repo)
    released = release_source_graph(repo, "b", sources=adapter, database_name="test")
    repo.session.commit()
    search = MagicMock()

    await refresh_released_graph(repo, released, embedder=None, search=search)

    ((indexed,), _) = search.index_nodes_batch.call_args
    assert {n.id for n in indexed} == set(released.stale_node_ids)
    assert "Bezúkhov" not in str([n.properties for n in indexed])


@pytest.mark.asyncio
async def test_a_recommit_refreshes_what_its_release_rebuilt(
    adapter: SqliteAdapter, repo: GraphRepository
) -> None:
    """The commit's post-commit step re-embeds the release's stale nodes with its own provider."""
    from chaoscypher_core.services.sources.engine.commit.service import SourceCommitService
    from chaoscypher_core.settings import EngineSettings

    _two_source_pierre(repo)
    released = release_source_graph(repo, "b", sources=adapter, database_name="test")
    repo.session.commit()
    provider = SimpleNamespace(
        batch_embed=AsyncMock(
            side_effect=lambda texts: SimpleNamespace(embeddings=[[0.1, 0.2]] * len(texts))
        )
    )
    search = MagicMock()
    svc = SourceCommitService(
        graph_repository=repo,
        source_repository=MagicMock(),
        sources_repository=adapter,
        indexing_repository=MagicMock(),
        search_repository=search,
        settings=EngineSettings(),
        embedding_provider=provider,  # type: ignore[arg-type]
    )

    await svc._refresh_released(released)

    provider.batch_embed.assert_awaited_once()
    search.rebuild_node_fulltext.assert_not_called()
    search.index_nodes_batch.assert_called_once()
    pierre = repo.get_node("a_pierre", include_embedding=True)
    assert pierre is not None and pierre.embedding == [0.1, 0.2]


def _fake_embedder() -> SimpleNamespace:
    return SimpleNamespace(
        batch_embed=AsyncMock(
            side_effect=lambda texts: SimpleNamespace(embeddings=[[0.3, 0.4]] * len(texts))
        )
    )


def test_the_sync_refresh_runs_with_no_event_loop(
    adapter: SqliteAdapter, repo: GraphRepository
) -> None:
    """The CLI's form: no loop in this thread, so it runs one of its own."""
    from chaoscypher_core.services.graph.management.release import refresh_after_release_sync

    _two_source_pierre(repo)
    released = release_source_graph(repo, "b", sources=adapter, database_name="test")
    adapter.session.commit()

    refresh_after_release_sync(
        adapter, "test", released, embedder=_fake_embedder(), search=MagicMock()
    )

    pierre = repo.get_node("a_pierre", include_embedding=True)
    assert pierre is not None and pierre.embedding == [0.3, 0.4]


@pytest.mark.asyncio
async def test_the_sync_refresh_finishes_inside_a_running_loop(
    adapter: SqliteAdapter, repo: GraphRepository
) -> None:
    """A sync caller inside async code (an MCP tool) still gets the refresh done."""
    from chaoscypher_core.services.graph.management.release import refresh_after_release_sync

    _two_source_pierre(repo)
    released = release_source_graph(repo, "b", sources=adapter, database_name="test")
    adapter.session.commit()

    refresh_after_release_sync(
        adapter, "test", released, embedder=_fake_embedder(), search=MagicMock()
    )

    adapter.session.expire_all()
    pierre = repo.get_node("a_pierre", include_embedding=True)
    assert pierre is not None and pierre.embedding == [0.3, 0.4]


# -- the lazy embedder (round 2) ----------------------------------------------------------------


def _closable_embedder() -> SimpleNamespace:
    provider = _fake_embedder()
    provider.aclose = AsyncMock()
    return provider


@pytest.mark.asyncio
async def test_a_lazy_embedder_is_built_only_when_something_went_stale(
    adapter: SqliteAdapter, repo: GraphRepository
) -> None:
    from chaoscypher_core.models import ReleasedRows
    from chaoscypher_core.services.graph.management.release import LazyEmbedder

    build = MagicMock()
    await refresh_released_graph(
        repo,
        ReleasedRows(deleted_node_ids=frozenset({"gone"})),
        embedder=LazyEmbedder(build=build, owned=True),
        search=MagicMock(),
    )
    build.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("owned", [True, False])
async def test_an_owned_embedder_is_closed_after_and_a_lent_one_is_not(
    adapter: SqliteAdapter, repo: GraphRepository, owned: bool
) -> None:
    from chaoscypher_core.services.graph.management.release import LazyEmbedder

    _two_source_pierre(repo)
    released = release_source_graph(repo, "b", sources=adapter, database_name="test")
    repo.session.commit()
    provider = _closable_embedder()

    await refresh_released_graph(
        repo, released, embedder=LazyEmbedder(build=lambda: provider, owned=owned), search=None
    )

    provider.batch_embed.assert_awaited_once()
    assert provider.aclose.await_count == (1 if owned else 0)


@pytest.mark.asyncio
async def test_an_embedder_that_cannot_be_built_is_a_logged_non_fatal_failure(
    adapter: SqliteAdapter, repo: GraphRepository
) -> None:
    """A cloud provider with no API key: the committed release stands, the text rows follow."""
    from chaoscypher_core.services.graph.management.release import LazyEmbedder

    _two_source_pierre(repo)
    released = release_source_graph(repo, "b", sources=adapter, database_name="test")
    repo.session.commit()
    search = MagicMock()

    def no_key() -> Any:
        msg = "OPENAI_API_KEY is not set"
        raise ValueError(msg)

    await refresh_released_graph(
        repo, released, embedder=LazyEmbedder(build=no_key, owned=True), search=search
    )

    ((indexed,), _) = search.index_nodes_batch.call_args
    assert {n.id for n in indexed} == set(released.stale_node_ids)


@pytest.mark.asyncio
async def test_the_sync_refresh_keeps_the_callers_session_scope(
    adapter: SqliteAdapter, repo: GraphRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On the helper thread the adapter still resolves the caller's task-bound session."""
    from chaoscypher_core.models import ReleasedRows
    from chaoscypher_core.services.graph.management import release

    seen: list[Any] = []

    async def record(*args: Any, **kwargs: Any) -> None:
        seen.append(adapter.session)

    monkeypatch.setattr(release, "refresh_after_release", record)
    async with adapter.session_scope() as scoped:
        release.refresh_after_release_sync(
            adapter, "test", ReleasedRows(), embedder=None, search=None
        )
    assert seen == [scoped]


# -- a merge racing a release ------------------------------------------------------------------


def test_a_merge_racing_a_release_never_writes_the_released_names_back(
    adapter: SqliteAdapter, repo: GraphRepository
) -> None:
    """The post-commit pass (Neuron) merges c's Pierre into the a+b Pierre while a source delete
    (Cortex, another connection) releases b. The release's rebuild lands between the merge's
    read of the kept node and its write.

    Until #795 the merge read the kept node outside any lock, the release
    committed its rebuild (b's names gone), and the merge then wrote its fold of the earlier
    read over it: b's names came back on a node b no longer attests. Each merge group now holds
    the writer lock from its read (``write_locked``), so the release waits and rebuilds from the
    merged node's members, c's reading included.
    """
    import threading

    from chaoscypher_core.repo_factories import get_transaction_graph_repository

    _two_source_pierre(repo)
    _sources(repo, "c")
    _node(repo, "c_pierre", "Pierre", "c", description=DESC["c"])
    repo.session.commit()
    assert "Bezúkhov" in _text(repo.get_node("a_pierre"))

    def release_b() -> None:
        other = SqliteAdapter(str(adapter.db_path), database_name="test")
        other.connect()
        try:
            with other.transaction():
                release_source_graph(
                    get_transaction_graph_repository(other, "test"),
                    "b",
                    sources=other,
                    database_name="test",
                )
        finally:
            other.disconnect()

    cortex = threading.Thread(target=release_b)
    real_list_edges = repo.list_edges

    def list_edges_then_race(*args: Any, **kwargs: Any) -> Any:
        # the merge has read the kept and dropped nodes; it is about to move edges and write
        if not cortex.is_alive() and cortex.ident is None:
            cortex.start()
            cortex.join(timeout=1.0)  # finishes unless the merge holds the writer lock
        return real_list_edges(*args, **kwargs)

    repo.list_edges = list_edges_then_race  # type: ignore[method-assign]
    apply_resolution(
        repo, ResolutionPlan(merges=[Merge(keep="a_pierre", drop="c_pierre", why="t")])
    )
    repo.session.commit()
    cortex.join(timeout=30)
    assert not cortex.is_alive()

    repo.session.expire_all()
    pierre = repo.get_node("a_pierre")
    assert pierre is not None
    assert repo.get_node("c_pierre") is None
    assert set(repo.node_members("a_pierre")) == {"a", "c"}
    assert "Bezúkhov" not in _text(pierre), "the merge wrote the released source's names back"
    assert DESC["b"] not in _text(pierre)
    assert DESC["c"] in _text(pierre), "c's reading survives in the node"
