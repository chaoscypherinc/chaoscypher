# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""``_import_citations`` writes one batch, not one row + one SELECT per citation.

Performance regression guard (hunt-queue drain 2026-08-12): the citation
loop used to call the single-row ``create_citation`` — which ends in
``_maybe_commit()``, and the importer never opens ``adapter.transaction()``,
so that was one COMMIT per citation — and to issue a full-row
``get_node_by_ccx_iri`` SELECT per citation purely to read one scalar
(``entity_type``). ``create_citations_batch``'s own docstring puts the
batch win at ~20-50x for 100+ citations.

These tests pin the call counts (N citations -> exactly one batch call, zero
per-row writes, zero per-citation node SELECTs) AND the per-citation
validation/skip semantics they replaced: an unresolved entity is still
skipped with a warning and never reaches the batch.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from chaoscypher_core.services.package.importer import CcxImporter
from chaoscypher_core.services.package.importer.models import ImportStats


def _citation(iri: str, confidence: float = 0.9) -> dict[str, Any]:
    """One ``ccx:citation`` record as the package mapping emits it."""
    return {
        "ccx:citation": {"@id": iri},
        "ccx:confidence": confidence,
        "ccx:extractionMethod": "ai_extraction",
    }


def _importer() -> tuple[CcxImporter, MagicMock, MagicMock]:
    """A CcxImporter over counting graph + sources stubs."""
    graph = MagicMock()
    sources = MagicMock()
    sources.create_citations_batch.return_value = []
    return CcxImporter(graph_repository=graph, sources_repository=sources), graph, sources


def test_import_citations_writes_one_batch_and_no_per_row_selects() -> None:
    """Five citations -> one create_citations_batch, zero create_citation."""
    importer, graph, sources = _importer()
    stats = ImportStats()
    iris = [f"urn:ccx:chaoscypher:node/n{i}" for i in range(5)]

    importer._import_citations(
        [_citation(iri) for iri in iris],
        "chunk-1",
        "src-1",
        "default",
        stats,
        {iri: f"local-{i}" for i, iri in enumerate(iris)},
        {iri: f"Label {i}" for i, iri in enumerate(iris)},
        dict.fromkeys(iris, "Person"),
        {},
    )

    assert sources.create_citations_batch.call_count == 1
    assert sources.create_citation.call_count == 0
    # The entity_type map replaces the per-citation full-row node SELECT.
    assert graph.get_node_by_ccx_iri.call_count == 0

    rows = sources.create_citations_batch.call_args.args[0]
    assert len(rows) == 5
    assert stats.citations_imported == 5


def test_import_citations_preserves_row_payload() -> None:
    """Batched rows carry the same fields the single-row writer got."""
    importer, _graph, sources = _importer()
    stats = ImportStats()
    iri = "urn:ccx:chaoscypher:node/alice"

    importer._import_citations(
        [_citation(iri, confidence=0.87)],
        "chunk-1",
        "src-1",
        "default",
        stats,
        {iri: "node_local"},
        {iri: "Alice"},
        {iri: "Person"},
        {},
    )

    row = sources.create_citations_batch.call_args.args[0][0]
    assert row["database_name"] == "default"
    assert row["entity_uri"] == "node_local"
    assert row["entity_label"] == "Alice"
    assert row["entity_type"] == "Person"
    assert row["source_id"] == "src-1"
    assert row["chunk_id"] == "chunk-1"
    assert row["confidence"] == 0.87
    assert row["extraction_method"] == "ai_extraction"
    assert row["id"].startswith("citation")


def test_import_citations_still_skips_unresolved_entities() -> None:
    """An unresolvable entity is warned + skipped, never batched."""
    importer, _graph, sources = _importer()
    stats = ImportStats()
    known = "urn:ccx:chaoscypher:node/known"

    importer._import_citations(
        [
            _citation(known),
            _citation("urn:ccx:chaoscypher:node/dangling"),
            {"ccx:citation": "not-a-dict"},
            {},
        ],
        "chunk-1",
        "src-1",
        "default",
        stats,
        {known: "local-known"},
        {known: "Known"},
        {known: "Person"},
        {},
    )

    rows = sources.create_citations_batch.call_args.args[0]
    assert [r["entity_uri"] for r in rows] == ["local-known"]
    assert stats.citations_imported == 1
    assert len(stats.warnings) == 3


def test_import_citations_no_write_when_every_citation_is_skipped() -> None:
    """All-unresolved input issues no write at all (not an empty batch call)."""
    importer, _graph, sources = _importer()
    stats = ImportStats()

    importer._import_citations(
        [_citation("urn:ccx:chaoscypher:node/dangling")],
        "chunk-1",
        "src-1",
        "default",
        stats,
        {},
        {},
        {},
        {},
    )

    sources.create_citations_batch.assert_not_called()
    sources.create_citation.assert_not_called()
    assert stats.citations_imported == 0


def test_import_citations_records_node_source_links() -> None:
    """The node -> source back-fill map records every citing source, first citer first (#795)."""
    importer, _graph, _sources = _importer()
    stats = ImportStats()
    iri = "urn:ccx:chaoscypher:node/alice"
    node_source_links: dict[str, list[str]] = {"local-alice": ["src-first"]}

    importer._import_citations(
        [_citation(iri)],
        "chunk-1",
        "src-second",
        "default",
        stats,
        {iri: "local-alice"},
        {iri: "Alice"},
        {iri: "Person"},
        node_source_links,
    )
    importer._import_citations(  # the same source citing again adds nothing
        [_citation(iri)],
        "chunk-2",
        "src-second",
        "default",
        stats,
        {iri: "local-alice"},
        {iri: "Alice"},
        {iri: "Person"},
        node_source_links,
    )

    assert node_source_links == {"local-alice": ["src-first", "src-second"]}


def test_finalize_homes_a_node_with_its_first_citer_and_links_every_other() -> None:
    """A node two sources cite is homed at the first and attested by both (#795)."""
    importer, graph, _sources = _importer()
    graph.assign_source_to_nodes.return_value = 0
    readings = {"shared": {"label": "Shared"}, "only-a": {"label": "Only A"}}
    importer._finalize_imported_sources(
        "default",
        {"iri:a": ("src-a", None), "iri:b": ("src-b", None)},
        [],
        {"shared": ["src-b", "src-a"], "only-a": ["src-a"]},
        ["shared", "only-a"],
        readings,
    )
    calls = [c.args for c in graph.assign_source_to_nodes.call_args_list]
    assert calls == [
        (["only-a"], "src-a", "default"),
        (["shared"], "src-b", "default"),  # home: stamped while still unhomed
        (["shared"], "src-a", "default"),  # then a member: the home stays src-b
    ]
    # every link records what the package wrote for the node, never the row (#795 M5)
    assert all(
        c.kwargs == {"readings": readings} for c in graph.assign_source_to_nodes.call_args_list
    )


def test_import_citations_falls_back_to_iri_label_and_null_type() -> None:
    """A node with no label/type entry falls back exactly as before."""
    importer, _graph, sources = _importer()
    stats = ImportStats()
    iri = "urn:ccx:chaoscypher:node/bare"

    importer._import_citations(
        [{"ccx:citation": {"@id": iri}}],
        "chunk-1",
        "src-1",
        "default",
        stats,
        {iri: "local-bare"},
        {},
        {},
        {},
    )

    row = sources.create_citations_batch.call_args.args[0][0]
    assert row["entity_label"] == iri
    assert row["entity_type"] is None
    # Defaults preserved from the single-row path.
    assert row["confidence"] == 1.0
    assert row["extraction_method"] == "imported"


def test_only_a_node_an_outside_source_attests_is_rebuilt_under_the_engine_settings() -> None:
    """A node only this import's sources attest holds the package's one reading: left as written.

    One a source outside the import attests too is rebuilt from its members, ranked
    under the importer's engine settings (as a release ranks), and its id returned so
    the import clears its vector.
    """
    from unittest.mock import patch

    from chaoscypher_core.settings import EngineSettings

    settings = EngineSettings()
    graph = MagicMock()
    importer = CcxImporter(
        graph_repository=graph, sources_repository=MagicMock(), settings=settings
    )
    release = "chaoscypher_core.services.graph.management.release"
    with (
        patch(f"{release}.graph_conventions", return_value="conv") as conventions,
        patch(f"{release}.rebuild_from_members") as rebuild,
    ):
        rebuild.return_value.changed_node_ids = frozenset({"shared"})
        changed = importer._rebuild_shared_nodes(
            "default",
            {"ours": {"src-a", "src-b"}, "shared": {"src-a", "extracted"}},
            {"src-a", "src-b"},
        )
    assert changed == frozenset({"shared"})
    graph.node_members.assert_not_called()
    assert conventions.call_args.args[3] is settings
    rebuild.assert_called_once_with(graph, ["shared"], [], "conv")


def test_finalize_reads_members_once_and_refreshes_only_its_own_sole_readings() -> None:
    """An unlinked upserted node whose sole member is this import's source gets its reading again.

    One attested by another source alone is never touched: the upsert's content may
    be another package's reading of the same IRI.
    """
    importer, graph, _sources = _importer()
    graph.assign_source_to_nodes.return_value = 0
    graph.node_member_sources.return_value = {
        "linked": {"src-a"},
        "uncited-ours": {"src-a"},
        "uncited-theirs": {"elsewhere"},
    }
    readings = {n: {"label": n} for n in ("linked", "uncited-ours", "uncited-theirs")}
    importer._finalize_imported_sources(
        "default",
        {"iri:a": ("src-a", None), "iri:b": ("src-b", None)},
        [],
        {"linked": ["src-a"]},
        ["linked", "uncited-ours", "uncited-theirs"],
        readings,
    )
    graph.node_member_sources.assert_called_once()
    calls = [c.args for c in graph.assign_source_to_nodes.call_args_list]
    assert calls == [(["linked"], "src-a", "default"), (["uncited-ours"], "src-a", "default")]
