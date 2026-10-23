# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""GraphProvenance: a fact's provenance from its member sources, its validity and its first citation."""

from __future__ import annotations

from collections.abc import Generator
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.adapters.sqlite.models import (
    DocumentChunk,
    GraphEdge,
    RelationshipCitation,
    SourceRow,
)
from chaoscypher_core.adapters.sqlite.repos.graph.sqlite_repository import GraphRepository
from chaoscypher_core.mcp.provenance import GraphProvenance
from chaoscypher_core.models import EdgeCreate, NodeCreate, TemplateCreate
from chaoscypher_core.services.workflows.tools.engine.memory_render import (
    Provenance,
    fact_line,
    tag,
)


DB = "test"


def _source(sid: str, **fields: object) -> SourceRow:
    return SourceRow(
        id=sid,
        database_name=DB,
        filename=f"{sid}.txt",
        filepath="",
        file_type="text",
        file_size=1,
        content_hash=f"hash-{sid}",
        status="committed",
        **fields,
    )


def _citation(
    cid: str, edge_id: str, source_id: str, chunk_id: str, text: str
) -> RelationshipCitation:
    return RelationshipCitation(
        id=cid,
        database_name=DB,
        edge_id=edge_id,
        edge_label="lives_in",
        source_entity_label="Pierre",
        target_entity_label="Lyon",
        source_id=source_id,
        chunk_id=chunk_id,
        extraction_method="ai_extraction",
        citation_metadata={"sentences": [{"text": text, "chunk_id": chunk_id, "local_index": 0}]},
    )


@pytest.fixture
def world(tmp_path: Path) -> Generator[SimpleNamespace]:
    """One graph: Pierre lives_in Lyon (a memory and a document), knows Anna (two memories), Paris (a document), Boris (a pack)."""
    db_path = tmp_path / "app.db"
    SQLModel.metadata.create_all(get_engine(db_path), checkfirst=True)
    adapter = SqliteAdapter(str(db_path), database_name=DB)
    adapter.connect()
    session = adapter.session
    assert session is not None
    session.add_all(
        [
            _source(
                "mem",
                source_type="memory",
                memory_by="alice",
                valid_from="2026-10-20",
                title="memory",
            ),
            _source("old", source_type="memory", memory_by="alice", valid_from="2026-03-02"),
            _source("new", source_type="memory", memory_by="bob", valid_from="2026-10-21"),
            _source("doc", source_type="file", title="War and Peace"),
            _source("pack", source_type="imported", title="literary"),
        ]
    )
    session.commit()
    session.add_all(
        [
            DocumentChunk(
                id="c_doc", database_name=DB, source_id="doc", chunk_index=0, content="x"
            ),
            DocumentChunk(
                id="c_mem",
                database_name=DB,
                source_id="mem",
                chunk_index=0,
                content="Pierre moved to Lyon in the spring.",
            ),
        ]
    )
    session.commit()
    graph = GraphRepository(session, database_name=DB)
    graph.create_template(TemplateCreate(name="thing", template_type="node"), custom_id="t_node")
    graph.create_template(TemplateCreate(name="rel", template_type="edge"), custom_id="t_edge")
    for nid in ("pierre", "lyon", "anna", "paris", "boris"):
        graph.create_node(
            NodeCreate(template_id="t_node", label=nid.title(), entity_type="Person"),
            custom_id=nid,
        )
    members = {
        "e_lyon": ("lyon", "lives_in", ["doc", "mem"]),
        "e_anna": ("anna", "knows", ["old", "new"]),
        "e_paris": ("paris", "visited", ["doc"]),
        "e_boris": ("boris", "knows", ["pack"]),
    }
    for eid, (target, label, sources) in members.items():
        graph.create_edge(
            EdgeCreate(
                template_id="t_edge",
                source_node_id="pierre",
                target_node_id=target,
                label=label,
            ),
            custom_id=eid,
        )
        graph.record_edge_sources(
            [(eid, sid, {"label": label, "properties": {}}) for sid in sources]
        )
    session.commit()
    # the document's citation sorts first (chunk c_doc < c_mem); the memory's is the one quoted
    session.add_all(
        [
            _citation("rc1", "e_lyon", "doc", "c_doc", "Pierre settled at Lyon."),
            _citation("rc2", "e_lyon", "mem", "c_mem", "Pierre moved to Lyon in the spring."),
            _citation("rc3", "e_paris", "doc", "c_doc", "Pierre saw Paris once."),
        ]
    )
    session.commit()
    engine = SimpleNamespace(graph_repository=graph, storage_adapter=adapter, database_name=DB)
    yield SimpleNamespace(engine=engine, session=session)
    adapter.disconnect()


def test_a_memory_member_wins_over_a_document_with_its_by_date_and_sentence(
    world: SimpleNamespace,
) -> None:
    p = GraphProvenance(world.engine).for_edge("e_lyon")
    assert (p.kind, p.by, p.valid_from, p.valid_to) == ("memory", "alice", date(2026, 10, 20), None)
    assert p.sentence == "Pierre moved to Lyon in the spring."
    assert p.chunk_id == "c_mem"
    assert (
        fact_line("Pierre", "lives_in", "Lyon", p)
        == 'Pierre lives_in Lyon [alice 2026-10-20] "Pierre moved to Lyon in the spring."'
    )


def test_the_newest_memory_names_the_fact(world: SimpleNamespace) -> None:
    p = GraphProvenance(world.engine).for_edge("e_anna")
    assert (p.kind, p.by, p.valid_from, p.sentence) == ("memory", "bob", date(2026, 10, 21), None)


def test_an_edge_only_a_document_attests_is_tagged_doc(world: SimpleNamespace) -> None:
    p = GraphProvenance(world.engine).for_edge("e_paris")
    assert (p.kind, p.name, p.by, p.valid_from) == ("doc", "War and Peace", None, None)
    assert tag(p) == "[doc War and Peace]"
    assert p.sentence == "Pierre saw Paris once."


def test_an_imported_package_is_a_pack(world: SimpleNamespace) -> None:
    p = GraphProvenance(world.engine).for_edge("e_boris")
    assert tag(p) == "[pack literary]"


def test_the_edges_validity_window_is_read(world: SimpleNamespace) -> None:
    row = world.session.get(GraphEdge, "e_lyon")
    row.valid_from = "2026-03-02"
    row.valid_to = "2026-10-21"
    row.closed_by_source_id = "new"
    world.session.add(row)
    world.session.commit()
    p = GraphProvenance(world.engine).for_edge("e_lyon")
    assert (p.valid_from, p.valid_to) == (date(2026, 3, 2), date(2026, 10, 21))
    assert tag(p) == "[alice 2026-03-02..2026-10-21, replaced]"


def test_for_edges_reads_a_batch_and_for_chunk_reads_the_chunks_source(
    world: SimpleNamespace,
) -> None:
    prov = GraphProvenance(world.engine)
    batch = prov.for_edges(["e_lyon", "e_paris", "e_lyon"])
    assert set(batch) == {"e_lyon", "e_paris"}
    assert batch["e_lyon"] == prov.for_edge("e_lyon")
    assert prov.for_chunk("c_mem") == Provenance(
        "memory", "alice", None, date(2026, 10, 20), None, None, chunk_id="c_mem"
    )
    assert tag(prov.for_chunk("c_doc")) == "[doc War and Peace]"
