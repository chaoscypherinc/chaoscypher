# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The canvas payload carries the pipeline's node flags, which its minimal projection does not load (2026-10-07).

Keep, flag, never delete: the commit keeps an edgeless entity with evidence
flagged ``properties.orphan`` and the deduplication keeps a reference flagged
``properties.descriptor_reference``. The canvas groups every flagged node in
its "Unresolved" region (``packages/interface``: ``nodeFlags``), so each
canvas node carries ``properties`` holding exactly the flags it has
(``name_identity.PIPELINE_FLAG_KEYS``), read by one SQL scan
(``nodes_with_true_properties``), never the other properties. The same for
edges since 2026-10-07: a relationship past a cap is kept flagged
``properties.capped``, and one no cited sentence states is kept flagged
``properties.unevidenced`` with ``properties.evidence_reason``. Each canvas
edge carries its flags (``name_identity.PIPELINE_EDGE_FLAG_KEYS``, the one
edge-flag table, read by ``edges_with_true_properties``) and the reason; the
canvas draws a capped edge thin and an unevidenced one dashed, and the edge
detail shows the reason (``edgeFlags``); the minimal edge listing PageRank
walks projects the flags too (``list_edges_minimal``). Since 2026-10-08 an
entity its sentence does not name is kept flagged ``unevidenced`` (the edges'
key) and an excluded name ``excluded_name``; an edge the strict type check
refuses ``unmatched_label`` (the written label beside it) or ``type_mismatch``
(the reason beside it). One guard pins both tables to the interface's
(``NODE_FLAGS``, ``EDGE_FLAGS``), and each edge flag's detail
(``EDGE_FLAG_DETAIL_KEYS``) to its ``reasonKey``.
"""

from __future__ import annotations

import re
from collections.abc import Generator
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.adapters.sqlite.repos.graph.sqlite_repository import GraphRepository
from chaoscypher_core.models import EdgeCreate, NodeCreate, TemplateCreate
from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
    CAPPED_KEY,
    DESCRIPTOR_REFERENCE_KEY,
    EDGE_FLAG_DETAIL_KEYS,
    EVIDENCE_REASON_KEY,
    EXCLUDED_BY_KEY,
    EXCLUDED_NAME_KEY,
    LOW_PLAUSIBILITY_KEY,
    ORPHAN_KEY,
    PIPELINE_EDGE_FLAG_KEYS,
    PIPELINE_FLAG_KEYS,
    TYPE_MISMATCH_KEY,
    TYPE_MISMATCH_REASON_KEY,
    UNEVIDENCED_KEY,
    UNMATCHED_LABEL_KEY,
    UNPLACED_TYPE_KEY,
    WRITTEN_LABEL_KEY,
)
from chaoscypher_cortex.features.graph.models import CanvasEdge, CanvasNode
from chaoscypher_cortex.features.graph.service import GraphService


REASON = '"St. Petersburg" is named in S6 but "Anna Pávlovna Schérer" is not'


@pytest.fixture
def repo(tmp_path: Path) -> Generator[GraphRepository]:
    db_path = tmp_path / "app.db"
    SQLModel.metadata.create_all(get_engine(db_path), checkfirst=True)
    adapter = SqliteAdapter(str(db_path), database_name="test")
    adapter.connect()
    assert adapter.session is not None
    repo = GraphRepository(adapter.session, database_name="test")
    repo.create_template(TemplateCreate(name="person", template_type="node"), custom_id="person")
    for nid, props in (
        ("julie", {"definition": "Nicholas's friend."}),
        ("marya", {ORPHAN_KEY: True, "definition": "Julie's mother."}),
        ("count", {DESCRIPTOR_REFERENCE_KEY: True}),
        ("visitor", {ORPHAN_KEY: True, DESCRIPTOR_REFERENCE_KEY: True}),
        ("sonya", {ORPHAN_KEY: "yes", DESCRIPTOR_REFERENCE_KEY: "true"}),  # the text's values
        ("one", {ORPHAN_KEY: 1}),
        ("room", {LOW_PLAUSIBILITY_KEY: True, "plausibility": 0.1}),
        ("glorp", {UNPLACED_TYPE_KEY: True, "written_type": "Quux"}),
        ("pierre", {UNEVIDENCED_KEY: True, EVIDENCE_REASON_KEY: '"Pierre" is not named in S3'}),
        ("chapter", {EXCLUDED_NAME_KEY: True, EXCLUDED_BY_KEY: "Chapter headings"}),
        ("text", {UNEVIDENCED_KEY: "yes", EXCLUDED_NAME_KEY: "no"}),  # the text's values
    ):
        repo.create_node(
            NodeCreate(template_id="person", label=nid, entity_type="person", properties=props),
            custom_id=nid,
        )
    repo.create_template(TemplateCreate(name="knows", template_type="edge"), custom_id="knows")
    for eid, (src, tgt), props in (
        ("e-plain", ("julie", "marya"), {"confidence": 0.9}),
        ("e-capped", ("julie", "count"), {CAPPED_KEY: True, "cap_pass": "degree"}),
        ("e-text", ("julie", "sonya"), {CAPPED_KEY: "true"}),  # the text's value
        (
            "e-unevidenced",
            ("julie", "visitor"),
            {UNEVIDENCED_KEY: True, EVIDENCE_REASON_KEY: REASON, "justification": "She waits."},
        ),
        ("e-both", ("marya", "room"), {CAPPED_KEY: True, UNEVIDENCED_KEY: True}),
        ("e-text-unevidenced", ("one", "glorp"), {UNEVIDENCED_KEY: "true"}),  # the text's
        (
            "e-unmatched",
            ("pierre", "julie"),
            {UNMATCHED_LABEL_KEY: True, WRITTEN_LABEL_KEY: "owes_money_to"},
        ),
        (
            "e-mismatch",
            ("chapter", "julie"),
            {TYPE_MISMATCH_KEY: True, TYPE_MISMATCH_REASON_KEY: "parent_of takes a Person"},
        ),
        ("e-text-mismatch", ("text", "julie"), {TYPE_MISMATCH_KEY: "true"}),  # the text's
    ):
        repo.create_edge(
            EdgeCreate(
                template_id="knows",
                source_node_id=src,
                target_node_id=tgt,
                label="knows",
                properties=props,
            ),
            custom_id=eid,
        )
    yield repo
    adapter.disconnect()


def test_the_scan_finds_json_true_exactly(repo: GraphRepository) -> None:
    assert repo.nodes_with_true_properties(PIPELINE_FLAG_KEYS) == {
        "count": [DESCRIPTOR_REFERENCE_KEY],
        "marya": [ORPHAN_KEY],
        "visitor": [ORPHAN_KEY, DESCRIPTOR_REFERENCE_KEY],
        "room": [LOW_PLAUSIBILITY_KEY],
        "glorp": [UNPLACED_TYPE_KEY],
        "pierre": [UNEVIDENCED_KEY],
        "chapter": [EXCLUDED_NAME_KEY],
    }
    assert repo.nodes_with_true_properties([]) == {}
    assert repo.edges_with_true_properties(PIPELINE_EDGE_FLAG_KEYS) == {
        "e-capped": {CAPPED_KEY: True},
        "e-unevidenced": {UNEVIDENCED_KEY: True},
        "e-both": {CAPPED_KEY: True, UNEVIDENCED_KEY: True},
        "e-unmatched": {UNMATCHED_LABEL_KEY: True},
        "e-mismatch": {TYPE_MISMATCH_KEY: True},
    }
    assert repo.edges_with_true_properties([UNEVIDENCED_KEY], carry=(EVIDENCE_REASON_KEY,)) == {
        "e-unevidenced": {UNEVIDENCED_KEY: True, EVIDENCE_REASON_KEY: REASON},
        "e-both": {UNEVIDENCED_KEY: True},
    }
    assert repo.edges_with_true_properties([]) == {}


def test_the_scan_reads_only_the_given_ids_across_batches(
    repo: GraphRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``ids`` scopes both scans; batches of one prove the per-batch results are joined."""
    from chaoscypher_core.adapters.sqlite import utils

    monkeypatch.setattr(utils, "SQL_IN_BATCH", 1)
    assert repo.nodes_with_true_properties(
        PIPELINE_FLAG_KEYS, ids=["visitor", "julie", "pierre", "visitor"]
    ) == {"visitor": [ORPHAN_KEY, DESCRIPTOR_REFERENCE_KEY], "pierre": [UNEVIDENCED_KEY]}
    assert repo.nodes_with_true_properties(PIPELINE_FLAG_KEYS, ids=[]) == {}
    assert repo.edges_with_true_properties(
        PIPELINE_EDGE_FLAG_KEYS,
        carry=(EVIDENCE_REASON_KEY,),
        ids=["e-plain", "e-unevidenced", "e-both"],
    ) == {
        "e-unevidenced": {UNEVIDENCED_KEY: True, EVIDENCE_REASON_KEY: REASON},
        "e-both": {CAPPED_KEY: True, UNEVIDENCED_KEY: True},
    }
    assert repo.edges_with_true_properties(PIPELINE_EDGE_FLAG_KEYS, ids=[]) == {}


def test_the_canvas_reads_flags_for_the_rows_it_draws_only(repo: GraphRepository) -> None:
    """The flag reads follow the capped listing, not the whole graph (2026-10-12 hunt).

    Without ``ids`` both reads scanned every row in the database on every
    canvas load (~4 s at 50k nodes / 200k edges), whatever the cap drew.
    """
    settings = MagicMock()
    settings.pagination.canvas_max_nodes = 3
    settings.pagination.canvas_max_edges = 2
    service = GraphService(
        graph_repository=repo, adapter=MagicMock(), database_name="test", settings=settings
    )
    seen: dict[str, object] = {}
    real_nodes, real_edges = repo.nodes_with_true_properties, repo.edges_with_true_properties

    def nodes_spy(keys: object, ids: object = None) -> object:
        seen["nodes"] = ids
        return real_nodes(keys, ids=ids)  # type: ignore[arg-type]

    def edges_spy(keys: object, carry: object = (), ids: object = None) -> object:
        seen["edges"] = ids
        return real_edges(keys, carry=carry, ids=ids)  # type: ignore[arg-type]

    repo.nodes_with_true_properties = nodes_spy  # type: ignore[method-assign,assignment]
    repo.edges_with_true_properties = edges_spy  # type: ignore[method-assign,assignment]
    data = service.get_canvas_data()
    assert seen["nodes"] == [n["id"] for n in data["nodes"]]
    assert seen["edges"] == [e["id"] for e in data["edges"]]
    assert len(data["nodes"]) == 3
    assert len(data["edges"]) == 2


def test_the_minimal_edges_carry_their_flags_for_the_walk(repo: GraphRepository) -> None:
    by_id = {e.id: e for e in repo.list_edges_minimal(include_disabled_sources=True)}
    assert by_id["e-capped"].properties == {CAPPED_KEY: True}
    assert by_id["e-plain"].properties == {}
    assert by_id["e-text"].properties == {}
    assert by_id["e-unevidenced"].properties == {UNEVIDENCED_KEY: True}
    assert by_id["e-both"].properties == {CAPPED_KEY: True, UNEVIDENCED_KEY: True}
    assert by_id["e-text-unevidenced"].properties == {}
    assert by_id["e-unmatched"].properties == {UNMATCHED_LABEL_KEY: True}
    assert by_id["e-mismatch"].properties == {TYPE_MISMATCH_KEY: True}
    assert by_id["e-text-mismatch"].properties == {}


def test_the_canvas_nodes_carry_their_flags_and_nothing_else(repo: GraphRepository) -> None:
    settings = MagicMock()
    settings.pagination.canvas_max_nodes = 100
    settings.pagination.canvas_max_edges = 100
    service = GraphService(
        graph_repository=repo, adapter=MagicMock(), database_name="test", settings=settings
    )
    data = service.get_canvas_data()
    by_id = {n["id"]: n for n in data["nodes"]}
    assert by_id["marya"]["properties"] == {ORPHAN_KEY: True}
    assert by_id["visitor"]["properties"] == {DESCRIPTOR_REFERENCE_KEY: True, ORPHAN_KEY: True}
    assert by_id["count"]["properties"] == {DESCRIPTOR_REFERENCE_KEY: True}
    assert by_id["pierre"]["properties"] == {UNEVIDENCED_KEY: True}
    assert by_id["chapter"]["properties"] == {EXCLUDED_NAME_KEY: True}
    for nid in ("julie", "sonya", "one", "text"):
        assert "properties" not in by_id[nid]
    # the response model takes them as they are
    assert CanvasNode.model_validate(by_id["marya"]).properties == {ORPHAN_KEY: True}
    assert CanvasNode.model_validate(by_id["julie"]).properties is None
    edges = {e["id"]: e for e in data["edges"]}
    assert edges["e-capped"]["properties"] == {CAPPED_KEY: True}
    assert edges["e-unevidenced"]["properties"] == {
        UNEVIDENCED_KEY: True,
        EVIDENCE_REASON_KEY: REASON,
    }
    assert edges["e-both"]["properties"] == {CAPPED_KEY: True, UNEVIDENCED_KEY: True}
    # each flag's detail rides with it (EDGE_FLAG_DETAIL_KEYS): the written label, the reason
    assert edges["e-unmatched"]["properties"] == {
        UNMATCHED_LABEL_KEY: True,
        WRITTEN_LABEL_KEY: "owes_money_to",
    }
    assert edges["e-mismatch"]["properties"] == {
        TYPE_MISMATCH_KEY: True,
        TYPE_MISMATCH_REASON_KEY: "parent_of takes a Person",
    }
    for eid in ("e-plain", "e-text", "e-text-unevidenced", "e-text-mismatch"):
        assert "properties" not in edges[eid]
    assert CanvasEdge.model_validate(edges["e-capped"]).properties == {CAPPED_KEY: True}
    assert CanvasEdge.model_validate(edges["e-unevidenced"]).properties == {
        UNEVIDENCED_KEY: True,
        EVIDENCE_REASON_KEY: REASON,
    }
    assert CanvasEdge.model_validate(edges["e-plain"]).properties is None


INTERFACE_UTILS = Path(__file__).resolve().parents[5] / "interface" / "src" / "utils"


def _table_keys(file_name: str, table_name: str) -> tuple[str, ...]:
    source = (INTERFACE_UTILS / file_name).read_text(encoding="utf-8")
    table = source[source.index(f"export const {table_name}") :]
    table = table[: table.index("] as const")]
    return tuple(re.findall(r"key: '([^']+)'", table))


def test_the_interface_reads_the_same_flags_in_the_same_order() -> None:
    """One list in two languages: ``NODE_FLAGS`` (interface) is ``PIPELINE_FLAG_KEYS`` (core)."""
    assert _table_keys("nodeFlags.ts", "NODE_FLAGS") == PIPELINE_FLAG_KEYS


def _table_entries(file_name: str, table_name: str) -> dict[str, str | None]:
    """Each entry of an interface flag table: its ``key`` and its ``reasonKey`` (None without one)."""
    source = (INTERFACE_UTILS / file_name).read_text(encoding="utf-8")
    table = source[source.index(f"export const {table_name}") :]
    table = table[: table.index("] as const")]
    entries: dict[str, str | None] = {}
    for block in table.split("{")[1:]:
        key = re.search(r"key: '([^']+)'", block)
        reason = re.search(r"reasonKey: '([^']+)'", block)
        if key:
            entries[key.group(1)] = reason.group(1) if reason else None
    return entries


def test_the_interface_reads_the_same_edge_flags_in_the_same_order() -> None:
    """``EDGE_FLAGS`` (interface) is ``PIPELINE_EDGE_FLAG_KEYS`` (core), each with its detail key."""
    assert _table_keys("edgeFlags.ts", "EDGE_FLAGS") == PIPELINE_EDGE_FLAG_KEYS
    assert _table_entries("edgeFlags.ts", "EDGE_FLAGS") == {
        key: EDGE_FLAG_DETAIL_KEYS.get(key) for key in PIPELINE_EDGE_FLAG_KEYS
    }
