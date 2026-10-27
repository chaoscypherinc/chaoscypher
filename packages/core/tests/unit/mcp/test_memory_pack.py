# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Memory packs: a scope exported as ``.ccx`` comes back as memories in an empty engine.

The pack carries each memory's ``by``, ``scope`` and ``valid_from``, the relations' citations and
the closures its memories made (spec 2026-10-20, slice 7 and § 8b): the imported memories answer
``recall``, ``list_memories`` and ``digest`` with the same text, can be forgotten, and supersede.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from chaoscypher_core.mcp.memory import memory_handle, remove_source
from chaoscypher_core.mcp.memory_link import link_repository
from chaoscypher_core.mcp.memory_pack import export_memory_pack
from chaoscypher_core.models import MemoryLinkState
from chaoscypher_core.operations.importing.imported_source_handler import index_imported_package
from chaoscypher_core.services.export import CcxExporter
from chaoscypher_core.services.package.importer import CcxImporter, ImportOptions
from tests.unit.mcp.memory_world import build_world, call, memory_sources
from tests.unit.mcp.test_memory_supersession import LYON, PARIS, SCRIPT


WORK_PARIS = {**PARIS, "scope": "work"}
WORK_LYON = {**LYON, "scope": "work"}
HOME_BORIS = {
    "text": "Boris Drubetskoy walked through the garden",
    "by": "bob",
    "valid_from": "2026-10-01",
    "scope": "home",
}
QUESTION = {"query": "Where does Pierre live?"}
DIGEST = {"since": "2026-01-01"}


async def import_pack(world: SimpleNamespace, data: bytes) -> None:
    """``package load``'s two steps: the import, then the search indexing of what it brought."""
    database = world.engine.settings.current_database
    importer = CcxImporter(
        graph_repository=world.graph,
        sources_repository=world.storage,
        settings=world.engine.settings,
    )
    stats = await importer.import_from_bytes(data, ImportOptions(database_name=database))
    assert not stats.errors, stats.errors
    await index_imported_package(
        imported_source_ids=stats.imported_source_ids,
        imported_node_ids=stats.imported_node_ids,
        storage_adapter=world.storage,
        graph_repository=world.graph,
        indexing_service=world.engine.indexing_service,
        search_repository=world.search,
        database_name=database,
    )


@pytest.fixture
async def origin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncGenerator[SimpleNamespace]:
    w = build_world(tmp_path / "origin", monkeypatch, script=SCRIPT)
    await call(w.server, "remember", WORK_PARIS)
    await call(w.server, "remember", WORK_LYON)
    yield w
    w.engine.close()


@pytest.fixture
def target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncGenerator[SimpleNamespace]:
    w = build_world(tmp_path / "target", monkeypatch, script=SCRIPT)
    yield w
    w.engine.close()


def handle_of(world: SimpleNamespace, text: str) -> str:
    rows = memory_sources(world.storage)
    ids = [r["id"] for r in rows]
    for r in rows:
        chunks, _ = world.storage.get_chunks_by_source(r["id"], page_size=1)
        if chunks and chunks[0]["content"] == text:
            return memory_handle(r["id"], ids)
    raise AssertionError(text)


async def test_an_imported_scope_answers_with_the_same_text(
    origin: SimpleNamespace, target: SimpleNamespace
) -> None:
    before = [
        await call(origin.server, "recall", QUESTION),
        await call(origin.server, "list_memories", {}),
        await call(origin.server, "digest", DIGEST),
    ]
    await import_pack(target, export_memory_pack(origin.engine, scope="work"))
    after = [
        await call(target.server, "recall", QUESTION),
        await call(target.server, "list_memories", {}),
        await call(target.server, "digest", DIGEST),
    ]
    assert "located_in Lyon [alice 2026-10-20]" in before[0]
    assert "replaced" in before[2]
    assert after == before


async def test_an_imported_memory_keeps_its_provenance(
    origin: SimpleNamespace, target: SimpleNamespace
) -> None:
    await import_pack(target, export_memory_pack(origin.engine, scope="work"))
    rows = sorted(memory_sources(target.storage), key=lambda r: r["valid_from"])
    assert [
        (r["source_type"], r["memory_by"], r["memory_scope"], r["valid_from"]) for r in rows
    ] == [("memory", "alice", "work", "2026-03-02"), ("memory", "alice", "work", "2026-10-20")]


async def test_only_the_scope_travels(origin: SimpleNamespace, target: SimpleNamespace) -> None:
    await call(origin.server, "remember", HOME_BORIS)
    await import_pack(target, export_memory_pack(origin.engine, scope="work"))
    assert {r["memory_scope"] for r in memory_sources(target.storage)} == {"work"}
    listed = await call(target.server, "list_memories", {})
    assert "Boris" not in listed


async def test_no_scope_exports_every_memory(
    origin: SimpleNamespace, target: SimpleNamespace
) -> None:
    await call(origin.server, "remember", HOME_BORIS)
    await import_pack(target, export_memory_pack(origin.engine, scope=None))
    assert {r["memory_scope"] for r in memory_sources(target.storage)} == {"work", "home"}


async def test_forgetting_the_imported_lyon_memory_reopens_paris(
    origin: SimpleNamespace, target: SimpleNamespace
) -> None:
    await import_pack(target, export_memory_pack(origin.engine, scope="work"))
    handle = handle_of(target, LYON["text"])
    text = await call(target.server, "forget", {"handle": handle, "confirm": True})
    assert text == f"Forgotten {handle}: 1 facts removed, 0 kept."
    answer = await call(target.server, "recall", QUESTION)
    assert "located_in Paris [alice 2026-03-02]" in answer and "replaced" not in answer
    assert "Lyon" not in answer


async def test_an_imported_memory_is_superseded_by_a_later_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: SimpleNamespace
) -> None:
    paris_only = build_world(tmp_path / "paris", monkeypatch, script=SCRIPT)
    try:
        await call(paris_only.server, "remember", WORK_PARIS)
        data = export_memory_pack(paris_only.engine, scope="work")
    finally:
        paris_only.engine.close()
    await import_pack(target, data)
    text = await call(target.server, "remember", WORK_LYON)
    assert (
        "Changed: Pierre Bezukhov located_in Paris [alice 2026-03-02] -> Lyon [alice 2026-10-20]. "
        "Both kept." in text.splitlines()
    )


async def test_the_pack_is_titled_by_its_scope(origin: SimpleNamespace) -> None:
    import ccx

    assert ccx.open_package(export_memory_pack(origin.engine, scope="work")).manifest.title == (
        "memory:work"
    )
    assert ccx.open_package(export_memory_pack(origin.engine, scope=None)).manifest.title == (
        "memory:all"
    )
    titled = export_memory_pack(origin.engine, scope="work", title="Pierre's moves")
    assert ccx.open_package(titled).manifest.title == "Pierre's moves"


async def test_an_imported_document_stays_an_imported_source(
    origin: SimpleNamespace, target: SimpleNamespace
) -> None:
    """A document is no memory: it comes back as an ``imported`` source, its relations attested."""
    await origin.engine.process_document("Pierre Bezukhov was born in Moscow.", filename="book.txt")
    (book,) = [
        r for r in origin.storage.list_sources(page_size=10)[0] if r["source_type"] != "memory"
    ]
    exporter = CcxExporter(
        graph_repository=origin.graph,
        sources_repository=origin.storage,
        settings=origin.engine.settings,
    )
    await import_pack(target, exporter.export(source_ids=[book["id"]], include_embeddings=False))
    (row,) = target.storage.list_sources(page_size=10)[0]
    assert (row["source_type"], row["memory_by"], row["valid_from"]) == ("imported", None, None)
    (born_in,) = [e for e in target.graph.list_edges() if e.label == "born_in"]
    assert list(target.graph.edge_members(born_in.id)) == [row["id"]]
    assert born_in.valid_from is None
    answer = await call(target.server, "recall", {"query": "Where was Pierre born?"})
    assert "born_in Moscow [pack " in answer
    assert answer.endswith('] "Pierre Bezukhov was born in Moscow."')


async def test_an_imported_memory_reads_as_linked(
    origin: SimpleNamespace, target: SimpleNamespace
) -> None:
    grey = "The weather was grey."
    await call(origin.server, "remember", {"text": grey, "scope": "work"})
    await import_pack(target, export_memory_pack(origin.engine, scope="work"))
    repo = link_repository(target.engine)
    states = {}
    for row in memory_sources(target.storage):
        link = repo.read_link_row(row["id"])
        assert link is not None
        states[link.full_text] = link.state
    assert states == {
        PARIS["text"]: MemoryLinkState.LINKED,
        LYON["text"]: MemoryLinkState.LINKED,
        grey: MemoryLinkState.LINKED_EMPTY,  # attests no node, as at its origin
    }
    listed = await call(target.server, "list_memories", {})
    assert [line for line in listed.splitlines() if not line.endswith('"')] == [
        next(line for line in listed.splitlines() if grey in line)
    ]


async def test_imported_edges_have_a_home_and_their_memorys_date_without_a_rebuild(
    origin: SimpleNamespace, target: SimpleNamespace
) -> None:
    """The importer homes each edge it writes and dates it itself: no release keys on a home
    the rebuild may or may not write (an edge with no home is no person's).
    """
    from chaoscypher_core.services.graph.management import release

    def no_rebuild(*_args: object, **_kwargs: object) -> release.Rebuilt:
        return release.Rebuilt(changed_node_ids=frozenset(), endpoint_ids=frozenset())

    with patch.object(release, "rebuild_from_members", no_rebuild):
        await import_pack(target, export_memory_pack(origin.engine, scope="work"))

    edges = target.graph.list_edges()
    members = {e.id: set(target.graph.edge_members(e.id)) for e in edges}
    assert edges and all(e.source_id in members[e.id] for e in edges)
    assert {str(e.valid_from) for e in edges if e.label == "located_in"} == {
        PARIS["valid_from"],
        LYON["valid_from"],
    }
    assert not any(e.person_made for e in edges)


async def test_forgetting_an_imported_memory_removes_its_edges(
    origin: SimpleNamespace, target: SimpleNamespace
) -> None:
    await import_pack(target, export_memory_pack(origin.engine, scope="work"))
    lyon = [e for e in target.graph.list_edges() if e.label == "located_in"]
    assert len(lyon) == 2

    await call(
        target.server, "forget", {"handle": handle_of(target, LYON["text"]), "confirm": True}
    )

    labels = {
        target.graph.get_node(e.target_node_id, include_embedding=False).label
        for e in target.graph.list_edges()
    }
    assert "Lyon" not in labels and "Paris" in labels


async def test_deleting_an_imported_document_removes_its_edges_when_their_ends_survive(
    origin: SimpleNamespace, target: SimpleNamespace
) -> None:
    born = "Pierre Bezukhov was born in Moscow."
    await origin.engine.process_document(born, filename="book.txt")
    (book,) = [
        r for r in origin.storage.list_sources(page_size=10)[0] if r["source_type"] != "memory"
    ]
    exporter = CcxExporter(
        graph_repository=origin.graph,
        sources_repository=origin.storage,
        settings=origin.engine.settings,
    )
    await import_pack(target, exporter.export(source_ids=[book["id"]], include_embeddings=False))
    (imported,) = target.storage.list_sources(page_size=10)[0]
    visit = "Pierre Bezukhov visited Moscow."
    target.llm.script[visit] = (
        "E|Pierre Bezukhov|Person|Pierre|0.9|S1|A count\nE|Moscow|Location||0.9|S1|A city\n",
        "R|0|1|travelled_to|0.9|S1|Pierre Bezukhov visited Moscow\n",
    )
    await call(target.server, "remember", {"text": visit, "by": "alice"})

    await remove_source(target.engine, imported["id"])

    labels = {e.label for e in target.graph.list_edges()}
    assert "born_in" not in labels and "travelled_to" in labels
    nodes = {n.label for n in target.graph.list_nodes(limit=100, include_embedding=False)}
    assert {"Pierre Bezukhov", "Moscow"} <= nodes


async def test_a_second_package_citing_an_edge_keeps_its_first_home(
    origin: SimpleNamespace, target: SimpleNamespace
) -> None:
    """The importer homes an edge only when it has none: a second package whose source
    cites the same edge attests it, and its first home stays (as nodes are homed).
    """
    born = "Pierre Bezukhov was born in Moscow."
    for name in ("first.txt", "second.txt"):  # two documents, one merged fact
        await origin.engine.process_document(born, filename=name)
    books = [
        r for r in origin.storage.list_sources(page_size=10)[0] if r["source_type"] != "memory"
    ]
    exporter = CcxExporter(
        graph_repository=origin.graph,
        sources_repository=origin.storage,
        settings=origin.engine.settings,
    )
    first, second = (exporter.export(source_ids=[b["id"]], include_embeddings=False) for b in books)
    await import_pack(target, first)
    (born_in,) = [e for e in target.graph.list_edges() if e.label == "born_in"]
    home = born_in.source_id
    assert home is not None

    await import_pack(target, second)

    (after,) = [e for e in target.graph.list_edges() if e.label == "born_in"]
    assert after.id == born_in.id and after.source_id == home
    assert len(target.graph.edge_members(after.id)) == 2
