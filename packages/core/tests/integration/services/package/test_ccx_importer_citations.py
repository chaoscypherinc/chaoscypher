# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Integration test: CcxImporter round-trips entity citations + chunk_index.

Seeds a source + chunk + a SourceCitation linking the chunk to a knowledge
node, exports the package via ``CcxExporter``, imports it into a SEPARATE
database via ``CcxImporter``, and asserts the citation survives WITH its
entity link, confidence, and extraction_method (FIX 1). The same test also
pins that the round-tripped ``chunk_index`` matches the source's original
index rather than a running import counter (FIX 4).
"""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING

import numpy as np
import pytest

from chaoscypher_core.adapters.sqlite.models import (
    DocumentChunk,
    GraphNode,
    GraphTemplate,
    SourceCitation,
    SourceRow,
    SourceTag,
    SourceTagAssignment,
)
from chaoscypher_core.adapters.sqlite.repos import GraphRepository
from chaoscypher_core.app_config import get_settings
from chaoscypher_core.app_config.engine_factory import build_engine_settings
from chaoscypher_core.services.export import CcxExporter
from chaoscypher_core.services.package.importer import CcxImporter, ImportOptions


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter


def _seed_source_with_citation(adapter: SqliteAdapter, database_name: str = "default") -> None:
    """Seed a node + source + chunk + a citation linking the chunk to the node.

    The chunk uses a non-zero ``chunk_index`` (2) so the FIX 4 assertion is
    meaningful: a running import counter would land it at 0, not 2.
    """
    assert adapter.session is not None
    session = adapter.session

    session.add(
        SourceRow(
            id="src_cite",
            database_name=database_name,
            filename="cite.txt",
            filepath="/data/cite.txt",
            title="Cited Doc",
            source_type="text",
            status="committed",
            extraction_domain="literature",
            full_text="Alice is a person mentioned here.",
        )
    )
    session.flush()

    session.add(
        GraphTemplate(
            id="tpl_person",
            database_name=database_name,
            name="Person",
            template_type="node",
            source_id="src_cite",
        )
    )
    session.flush()

    session.add(
        GraphNode(
            id="node_alice",
            database_name=database_name,
            graph_name="knowledge",
            template_id="tpl_person",
            label="Alice",
            entity_type="Person",
            source_id="src_cite",
            embedding=[0.1, 0.2, 0.3],
        )
    )
    session.add(
        DocumentChunk(
            id="chunk_cite",
            database_name=database_name,
            source_id="src_cite",
            chunk_index=2,
            content="Alice is a person mentioned here.",
            char_start=0,
            char_end=33,
            status="committed",
            embedding=base64.b64encode(np.array([0.4, 0.5, 0.6], dtype=np.float32).tobytes()),
        )
    )
    session.flush()

    session.add(
        SourceCitation(
            id="cit_1",
            database_name=database_name,
            entity_uri="node_alice",
            entity_label="Alice",
            entity_type="Person",
            source_id="src_cite",
            chunk_id="chunk_cite",
            confidence=0.87,
            extraction_method="ai_extraction",
        )
    )
    # A tag on the source — exported as ccx:Source keywords, restored on import.
    session.add(SourceTag(id="tag_lit", database_name=database_name, name="classic-literature"))
    session.flush()
    session.add(
        SourceTagAssignment(
            id="asg_1", source_id="src_cite", tag_id="tag_lit", database_name=database_name
        )
    )
    session.commit()


@pytest.mark.asyncio
async def test_ccx_importer_round_trips_citation_with_entity_link(
    integration_adapter: SqliteAdapter,
) -> None:
    """A citation linking a chunk to a node survives export -> import."""
    _seed_source_with_citation(integration_adapter)
    assert integration_adapter.session is not None

    graph_repo = GraphRepository(integration_adapter.session, "default")
    settings = build_engine_settings(get_settings())
    exporter = CcxExporter(
        graph_repository=graph_repo,
        sources_repository=integration_adapter,
        settings=settings,
        workflow_db=None,
    )
    data = exporter.export(include_embeddings=False, source_ids=["src_cite"])

    target_db = "imported_cite"
    target_repo = GraphRepository(integration_adapter.session, target_db)
    importer = CcxImporter(
        graph_repository=target_repo,
        sources_repository=integration_adapter,
        workflow_db=None,
    )
    stats = await importer.import_from_bytes(data, ImportOptions(database_name=target_db))

    assert not stats.errors, stats.errors
    # The citation must NOT have been skipped as a dangling/unresolved entity.
    assert stats.citations_imported == 1, stats.warnings

    # Resolve the imported source + chunk.
    src = integration_adapter.get_source_by_ccx_iri(
        "urn:ccx:chaoscypher:source/src_cite", target_db
    )
    assert src is not None

    # The imported node (the cited entity), so we can assert the citation
    # points at it. The minted IRI is deterministic from the original node id.
    alice_iri = "urn:ccx:chaoscypher:node/node_alice"
    alice = target_repo.get_node_by_ccx_iri(alice_iri, target_db)
    assert alice is not None
    assert alice["label"] == "Alice"

    # The chunk_index round-trips to the ORIGINAL index (2), not a counter (0).
    listed = integration_adapter.list_chunks(
        database_name=target_db, source_id=src["id"], include_content=True
    )
    assert len(listed) == 1
    chunk = integration_adapter.get_chunk(listed[0]["id"], target_db)
    assert chunk is not None
    assert chunk["chunk_index"] == 2

    # The citation survived WITH its entity link + confidence + extraction_method.
    citations = integration_adapter.list_citations(database_name=target_db)
    assert len(citations) == 1
    citation = citations[0]
    assert citation["confidence"] == pytest.approx(0.87)
    assert citation["extraction_method"] == "ai_extraction"
    assert citation["chunk_id"] == chunk["id"]
    # The entity link resolves to the imported Alice node by its LOCAL node id
    # (exactly like a normal extraction stores it) — NOT the package CCX IRI.
    # The source-group node + Entity Distribution panel match citation entity ids
    # against the graph's local node ids, so storing the IRI here would orphan
    # the source group (no icon) and empty the entity panels.
    assert citation["entity_uri"] == alice["id"]
    assert citation["entity_uri"] != alice_iri
    # The label round-tripped from the imported node (not the bare node id).
    assert citation["entity_label"] == "Alice"
    # The citation carries the cited node's entity_type — this is what powers
    # the source's Entity Distribution panel (citations grouped by entity_type).
    # Without it every imported entity falls into the "Unknown" bucket.
    assert citation["entity_type"] == "Person"

    # Import-completeness: the imported source must end up in the same finalized
    # shape a normal extraction->commit produces, so it does not read as an
    # empty/"pending" source in the UI.
    from sqlmodel import select

    session = integration_adapter.session
    # The cited node is linked back to its source (source-scoped views +
    # ON DELETE CASCADE so a later wipe-and-reimport actually removes nodes).
    alice_row = session.exec(
        select(GraphNode).where(
            GraphNode.ccx_iri == alice_iri, GraphNode.database_name == target_db
        )
    ).one()
    assert alice_row.source_id == src["id"]
    # The denormalized source_document_id property is synced to the LOCAL source
    # (the bundle carried the original export-machine id, which is stale).
    assert (alice_row.properties or {}).get("source_document_id") == src["id"]
    # The importer surfaces the imported knowledge node ids so knowledge-only
    # imports (lexicon, CLI), which have no source, can index off the list.
    assert alice_row.id in stats.imported_node_ids
    # The source's tags round-trip (exported as keywords, restored + assigned).
    imported_tags = {t["name"] for t in integration_adapter.get_source_tags(src["id"])}
    assert "classic-literature" in imported_tags
    # Denormalized counters the source-detail UI reads directly.
    src_row = session.exec(
        select(SourceRow).where(SourceRow.id == src["id"], SourceRow.database_name == target_db)
    ).one()
    assert src_row.chunk_count == 1
    assert src_row.commit_nodes_created == 1
    # Imports arrive committed — never auto-kick extraction on them.
    assert src_row.auto_analyze is False
    # The extraction domain round-trips so the graph view renders this imported
    # source's group-node icon (domain -> icon) exactly like an extracted source.
    assert src_row.extraction_domain == "literature"
    # An imported source has NO on-disk staged file, so filepath is empty.
    # A bare display name here would make delete_source_files rmtree the CWD.
    assert src_row.filepath == ""

    # The importer surfaces the imported source id so the worker import handler
    # can enqueue OP_INDEX_IMPORTED_SOURCE (re-embed chunks + index node/chunk
    # vectors). The storage-only importer itself never enqueues.
    assert stats.imported_source_ids == [src["id"]]

    # Imported templates are owned by the source (source_id), so they
    # cascade-delete with it via the graph_templates.source_id FK — matching
    # extraction's source-scoped templates. Without this they survive a source
    # delete as orphans (system templates stay shared, source_id=NULL).
    imported_templates = [
        t
        for t in session.exec(
            select(GraphTemplate).where(GraphTemplate.database_name == target_db)
        ).all()
        if not t.id.startswith("system_template_")
    ]
    assert imported_templates, "expected at least one imported template"
    assert all(t.source_id == src["id"] for t in imported_templates)


@pytest.mark.asyncio
async def test_ccx_importer_restores_embeddings_when_model_matches(
    integration_adapter: SqliteAdapter,
) -> None:
    """Export with vectors -> import restores node + chunk vectors (model matches).

    The package's embedding model/dimensions match this machine, so the importer
    restores the node vector onto ``graph_nodes.embedding`` and the chunk vector
    onto ``document_chunks.embedding`` WITH ``embedded_at`` stamped — letting the
    later index op skip re-embedding (and a model-less import stay searchable).
    """
    from sqlmodel import select

    _seed_source_with_citation(integration_adapter)
    assert integration_adapter.session is not None
    session = integration_adapter.session

    graph_repo = GraphRepository(session, "default")
    settings = build_engine_settings(get_settings())
    exporter = CcxExporter(
        graph_repository=graph_repo,
        sources_repository=integration_adapter,
        settings=settings,
        workflow_db=None,
    )
    data = exporter.export(include_embeddings=True, source_ids=["src_cite"])

    target_db = "imported_emb"
    target_repo = GraphRepository(session, target_db)
    importer = CcxImporter(
        graph_repository=target_repo,
        sources_repository=integration_adapter,
        workflow_db=None,
    )
    stats = await importer.import_from_bytes(data, ImportOptions(database_name=target_db))

    assert not stats.errors, stats.errors
    # One node vector + one chunk vector restored; no re-embed needed.
    assert stats.embeddings_restored == 2
    assert stats.embeddings_need_regeneration is False

    # Node vector restored onto graph_nodes.embedding (JSON list[float]).
    alice_row = session.exec(
        select(GraphNode).where(
            GraphNode.ccx_iri == "urn:ccx:chaoscypher:node/node_alice",
            GraphNode.database_name == target_db,
        )
    ).one()
    assert alice_row.embedding == pytest.approx([0.1, 0.2, 0.3])

    # Chunk vector restored + embedded_at stamped (gates it out of re-embedding).
    chunk_row = session.exec(
        select(DocumentChunk).where(DocumentChunk.database_name == target_db)
    ).one()
    assert chunk_row.embedding is not None
    assert chunk_row.embedded_at is not None
    restored = np.frombuffer(base64.b64decode(chunk_row.embedding), dtype=np.float32).tolist()
    assert restored == pytest.approx([0.4, 0.5, 0.6], abs=1e-5)


@pytest.mark.asyncio
async def test_ccx_importer_skips_embeddings_on_model_mismatch(
    integration_adapter: SqliteAdapter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A package whose embedding model differs from this machine is NOT restored.

    Cross-model vectors live in a different space, so restoring them would
    silently corrupt search — the importer must skip the restore, flag
    regeneration, and leave the node vectorless for the index op to re-embed.
    """
    from sqlmodel import select

    _seed_source_with_citation(integration_adapter)
    assert integration_adapter.session is not None
    session = integration_adapter.session

    graph_repo = GraphRepository(session, "default")
    settings = build_engine_settings(get_settings())
    exporter = CcxExporter(
        graph_repository=graph_repo,
        sources_repository=integration_adapter,
        settings=settings,
        workflow_db=None,
    )
    # Export bakes the descriptor with the CURRENT model.
    data = exporter.export(include_embeddings=True, source_ids=["src_cite"])

    # Now the import machine reports a DIFFERENT embedding model.
    monkeypatch.setattr(get_settings().embedding, "model", "some-other-embed-model:1b")

    target_db = "imported_mismatch"
    importer = CcxImporter(
        graph_repository=GraphRepository(session, target_db),
        sources_repository=integration_adapter,
        workflow_db=None,
    )
    stats = await importer.import_from_bytes(data, ImportOptions(database_name=target_db))

    assert not stats.errors, stats.errors
    assert stats.embeddings_restored == 0
    assert stats.embeddings_need_regeneration is True
    assert stats.embedding_mismatch_reason is not None
    # The node landed WITHOUT a restored vector (left for the index op to re-embed).
    alice_row = session.exec(
        select(GraphNode).where(
            GraphNode.ccx_iri == "urn:ccx:chaoscypher:node/node_alice",
            GraphNode.database_name == target_db,
        )
    ).one()
    assert not alice_row.embedding
