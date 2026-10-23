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

import pytest

from chaoscypher_core.mcp.memory import memory_handle
from chaoscypher_core.mcp.memory_pack import export_memory_pack
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
