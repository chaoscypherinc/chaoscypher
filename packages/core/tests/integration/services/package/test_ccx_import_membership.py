# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""A CCX import that links an already-merged node records what its package says (#795 follow-up M5).

A node's membership snapshot is what one source wrote for it; a release
rebuilds the node from the snapshots of the sources it still has. A CCX
import links the nodes its package cites to the importing source. Until the
#795 follow-up it recorded that source's snapshot from the node's row (which,
on a merged node, carries every member's names), and the import's upsert
re-stamped the node's *home* source's snapshot with the package's content,
so a later release brought a deleted source's names back through another
source's snapshot.

Real path: a package exported from one database (``CcxExporter``) is imported
into another (``CcxImporter``); the merge is ``apply_resolution``; the deletes
are ``SourceService.delete_source`` plus the post-release refresh.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
from sqlmodel import select
from structlog.testing import capture_logs

from chaoscypher_core.adapters.sqlite.models import (
    DocumentChunk,
    GraphNode,
    GraphTemplate,
    SourceCitation,
    SourceRow,
)
from chaoscypher_core.app_config import get_settings
from chaoscypher_core.app_config.engine_factory import build_engine_settings
from chaoscypher_core.models import NodeCreate
from chaoscypher_core.repo_factories import get_transaction_graph_repository
from chaoscypher_core.services.export import CcxExporter
from chaoscypher_core.services.graph.management.release import refresh_after_release_sync
from chaoscypher_core.services.graph.management.source import SourceService
from chaoscypher_core.services.graph.resolution.apply import apply_resolution
from chaoscypher_core.services.graph.resolution.plan import Merge, ResolutionPlan
from chaoscypher_core.services.package.importer import CcxImporter, ImportOptions


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter


ORIGIN, TARGET = "default", "target"  # the exporter reads the configured database
X_IRI = "urn:ccx:chaoscypher:node/node_x"

C_READING = {"aliases": ["the heir"], "description": "C: the heir of the old count."}
D_READING = {"aliases": ["the bastard son"], "description": "D: the old count's bastard son."}
A_READING = {"aliases": ["Count Bezúkhov"], "description": "A: stout, with spectacles."}
B_READING = {"aliases": ["Petrusha"], "description": "B: argues about Napoleon."}


def _source(session: Any, sid: str, db: str) -> None:
    session.add(
        SourceRow(
            id=sid,
            database_name=db,
            filename=f"{sid}.txt",
            filepath=f"/data/{sid}.txt",
            title=sid,
            source_type="text",
            status="committed",
            full_text=f"Pierre, as {sid} tells it.",
        )
    )
    session.flush()


def _cite(session: Any, sid: str) -> None:
    """A chunk of ``sid`` in the origin database citing ``node_x``."""
    session.add(
        DocumentChunk(
            id=f"chunk_{sid}",
            database_name=ORIGIN,
            source_id=sid,
            chunk_index=0,
            content=f"Pierre, as {sid} tells it.",
            char_start=0,
            char_end=20,
            status="committed",
        )
    )
    session.flush()
    session.add(
        SourceCitation(
            id=f"cit_{sid}",
            database_name=ORIGIN,
            entity_uri="node_x",
            entity_label="Pierre",
            entity_type="Person",
            source_id=sid,
            chunk_id=f"chunk_{sid}",
            confidence=1.0,
            extraction_method="ai_extraction",
        )
    )
    session.commit()


def _export(adapter: SqliteAdapter, sid: str, reading: dict[str, Any]) -> bytes:
    """A package of ``sid`` alone, whose ``node_x`` reads as ``reading``."""
    assert adapter.session is not None
    node = adapter.session.get(GraphNode, "node_x")
    assert node is not None
    node.properties = dict(reading)
    adapter.session.add(node)
    adapter.session.commit()
    exporter = CcxExporter(
        graph_repository=get_transaction_graph_repository(adapter, ORIGIN),
        sources_repository=adapter,
        settings=build_engine_settings(get_settings()),
        workflow_db=None,
    )
    return exporter.export(include_embeddings=False, source_ids=[sid])


async def _import(adapter: SqliteAdapter, data: bytes) -> None:
    importer = CcxImporter(
        graph_repository=get_transaction_graph_repository(adapter, TARGET),
        sources_repository=adapter,
        workflow_db=None,
    )
    stats = await importer.import_from_bytes(data, ImportOptions(database_name=TARGET))
    assert not stats.errors, stats.errors
    assert stats.sources_imported, stats


def _local(adapter: SqliteAdapter, sid: str) -> str:
    row = adapter.get_source_by_ccx_iri(f"urn:ccx:chaoscypher:source/{sid}", TARGET)
    assert row is not None
    return str(row["id"])


def _delete(adapter: SqliteAdapter, sid: str) -> None:
    deletion = SourceService(repository=adapter, database_name=TARGET).delete_source(sid)
    refresh_after_release_sync(adapter, TARGET, deletion.released, embedder=None, search=None)


def _text(adapter: SqliteAdapter, node_id: str) -> str:
    assert adapter.session is not None
    adapter.session.expire_all()
    node = adapter.session.get(GraphNode, node_id)
    assert node is not None, "the merged node survived"
    return f"{node.label} {node.properties}"


def _packages(adapter: SqliteAdapter) -> tuple[bytes, bytes]:
    """The origin: one node cited by C and D, exported once per source (C's reading, D's)."""
    session = adapter.session
    assert session is not None
    _source(session, "src_c", ORIGIN)
    _source(session, "src_d", ORIGIN)
    session.add(
        GraphTemplate(id="tpl_person", database_name=ORIGIN, name="Person", template_type="node")
    )
    session.flush()
    session.add(
        GraphNode(
            id="node_x",
            database_name=ORIGIN,
            graph_name="knowledge",
            template_id="tpl_person",
            label="Pierre",
            entity_type="Person",
            source_id="src_c",
            properties={},
        )
    )
    session.commit()
    origin = get_transaction_graph_repository(adapter, ORIGIN)
    origin.record_node_sources([("node_x", sid, {}) for sid in ("src_c", "src_d")])
    _cite(session, "src_c")
    _cite(session, "src_d")
    return _export(adapter, "src_c", C_READING), _export(adapter, "src_d", D_READING)


@pytest.mark.asyncio
async def test_a_second_package_never_overwrites_the_first_packages_reading(
    integration_adapter: SqliteAdapter,
) -> None:
    """Import C, then D (the same entity, no merge), delete D: C's text is back.

    The upsert of D's package names no writer; the node's only member is C.
    Fix round 1 refreshed the sole member's reading with whatever package was
    being imported, so C's reading became D's and deleting D lost C's text for good.
    """
    adapter = integration_adapter
    package_c, package_d = _packages(adapter)
    await _import(adapter, package_c)
    await _import(adapter, package_d)
    repo = get_transaction_graph_repository(adapter, TARGET)
    row = repo.get_node_by_ccx_iri(X_IRI, TARGET)
    assert row is not None
    n = str(row["id"])
    c_local, d_local = _local(adapter, "src_c"), _local(adapter, "src_d")
    members = repo.node_members(n)
    assert members[c_local]["aliases"] == ["the heir"], "C's reading was overwritten"
    assert members[d_local]["aliases"] == ["the bastard son"]
    both = _text(adapter, n)
    assert "the heir" in both and "the bastard son" in both

    _delete(adapter, d_local)
    after = _text(adapter, n)
    assert "the heir" in after, "C's text was lost with D"
    assert "bastard" not in after


@pytest.mark.asyncio
async def test_a_rebuilt_shared_node_is_re_embedded_by_the_imports_index_step(
    integration_adapter: SqliteAdapter,
) -> None:
    """A node an outside source shares is rebuilt by the import: its old vector is cleared.

    The import's indexing step re-embeds only nodes with no vector of the right
    dimension, so a rebuilt node whose text changed kept its previous vector
    (it no longer matched the names the fold added) until the vector is cleared.
    """
    from chaoscypher_core.operations.importing.imported_source_handler import (
        _fetch_nodes_by_ids,
        _reembed_nodes,
    )

    adapter = integration_adapter
    package_c, package_d = _packages(adapter)
    await _import(adapter, package_c)
    repo = get_transaction_graph_repository(adapter, TARGET)
    row = repo.get_node_by_ccx_iri(X_IRI, TARGET)
    assert row is not None
    n = str(row["id"])
    repo.update_node_embeddings_batch({n: [1.0, 0.0, 0.0, 0.0]})  # C's import was embedded
    await _import(adapter, package_d)  # C attests it from outside D's import: rebuilt

    texts: list[str] = []

    async def embed(batch: list[str]) -> Any:
        texts.extend(batch)
        return SimpleNamespace(embeddings=[[0.0, 1.0, 0.0, 0.0]] * len(batch))

    provider = SimpleNamespace(batch_embed=embed)
    embedded = await _reembed_nodes(_fetch_nodes_by_ids(repo, [n]), provider, repo, 4)
    assert embedded == 1, "the rebuilt node kept its old vector"
    assert "the heir" in texts[0] and "the bastard son" in texts[0]
    node = repo.get_node(n, include_embedding=True)
    assert node is not None and node.embedding == [0.0, 1.0, 0.0, 0.0]


@pytest.mark.asyncio
async def test_a_ccx_link_of_a_merged_node_never_brings_a_deleted_sources_names_back(
    integration_adapter: SqliteAdapter,
) -> None:
    """Merge A and B into an imported node, import a second package that links it, delete A then D."""
    adapter = integration_adapter
    session = adapter.session
    assert session is not None
    package_c, package_d = _packages(adapter)

    # target: C's package lands, then A's and B's readings merge into its node
    await _import(adapter, package_c)
    c_local = _local(adapter, "src_c")
    repo = get_transaction_graph_repository(adapter, TARGET)
    imported = repo.get_node_by_ccx_iri(X_IRI, TARGET)
    assert imported is not None
    n = str(imported["id"])
    for sid in ("src_a", "src_b"):
        _source(session, sid, TARGET)
    session.commit()
    for sid, reading in (("src_a", A_READING), ("src_b", B_READING)):
        repo.create_node(
            NodeCreate(
                template_id=str(imported["template_id"]),
                label="Pierre Bezúkhov" if sid == "src_a" else "Pierre",
                entity_type="Person",
                properties={**reading, "source_document_id": sid},  # as a commit stamps it
                source_id=sid,
            ),
            custom_id=f"{sid}_pierre",
        )
    apply_resolution(
        repo,
        ResolutionPlan(
            merges=[
                Merge(keep=n, drop="src_a_pierre", why="t"),
                Merge(keep=n, drop="src_b_pierre", why="t"),
            ]
        ),
    )
    assert set(repo.node_members(n)) == {c_local, "src_a", "src_b"}
    assert "Count Bezúkhov" in _text(adapter, n)

    # D's package cites the same entity: the import links the merged node to D
    await _import(adapter, package_d)
    d_local = _local(adapter, "src_d")
    assert set(repo.node_members(n)) == {c_local, d_local, "src_a", "src_b"}
    linked = _text(adapter, n)  # the row is every member's fold, not D's package alone
    for name in ("Count Bezúkhov", "Petrusha", "the heir", "the bastard son"):
        assert name in linked, f"{name!r} missing after the import"

    _delete(adapter, "src_a")
    after_a = _text(adapter, n)
    assert "Bezúkhov" not in after_a, "A's names came back"
    assert "stout, with spectacles" not in after_a
    for kept in ("Petrusha", "the heir", "the bastard son"):
        assert kept in after_a, f"{kept!r} lost with A"
    rebuilt = session.get(GraphNode, n)
    assert rebuilt is not None
    # the rebuild keeps the provenance its chosen reading carries: its own source
    assert (rebuilt.properties or {}).get("source_document_id") == rebuilt.source_id

    _delete(adapter, d_local)
    after_d = _text(adapter, n)
    assert "the bastard son" not in after_d, "D's names came back"
    assert "bastard son" not in after_d
    for kept in ("Petrusha", "the heir"):
        assert kept in after_d, f"{kept!r} lost with D"
    assert session.exec(select(GraphNode.id).where(GraphNode.database_name == TARGET)).all() == [n]


@pytest.mark.asyncio
async def test_a_fresh_multi_source_import_leaves_its_shared_node_as_the_package_wrote_it(
    integration_adapter: SqliteAdapter,
) -> None:
    """One node two sources of one bundle cite: homed at its first citer, no merge provenance.

    Every member holds the same package reading, so there is nothing to fold:
    until the fix round the import rebuilt it anyway, folding the reading into
    itself (``resolved_from`` / ``merged_names`` of its own label), moving its
    home to whichever source id sorted first and dropping ``source_document_id``.
    """
    adapter = integration_adapter
    session = adapter.session
    assert session is not None
    _source(session, "src_c", ORIGIN)
    _source(session, "src_d", ORIGIN)
    session.add(
        GraphTemplate(id="tpl_person", database_name=ORIGIN, name="Person", template_type="node")
    )
    session.flush()
    session.add(
        GraphNode(
            id="node_x",
            database_name=ORIGIN,
            graph_name="knowledge",
            template_id="tpl_person",
            label="Pierre",
            entity_type="Person",
            source_id="src_c",
            properties={"description": "The heir.", "aliases": ["Petrusha"]},
        )
    )
    session.commit()
    origin = get_transaction_graph_repository(adapter, ORIGIN)
    origin.record_node_sources([("node_x", sid, {}) for sid in ("src_c", "src_d")])
    _cite(session, "src_c")
    _cite(session, "src_d")
    exporter = CcxExporter(
        graph_repository=origin,
        sources_repository=adapter,
        settings=build_engine_settings(get_settings()),
        workflow_db=None,
    )
    with capture_logs() as logs:
        await _import(
            adapter, exporter.export(include_embeddings=False, source_ids=["src_c", "src_d"])
        )
    assert not [e for e in logs if e["event"] == "ccx_import_shared_nodes_rebuilt"]

    repo = get_transaction_graph_repository(adapter, TARGET)
    row = repo.get_node_by_ccx_iri(X_IRI, TARGET)
    assert row is not None
    c_local, d_local = _local(adapter, "src_c"), _local(adapter, "src_d")
    assert set(repo.node_members(row["id"])) == {c_local, d_local}
    assert row["source_id"] == c_local, "the home is the first citer"
    assert row["label"] == "Pierre"
    assert row["properties"] == {
        "description": "The heir.",
        "aliases": ["Petrusha"],
        "source_document_id": c_local,
    }
