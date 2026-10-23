# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""join_audit.py: which named endpoints of a pack's kinship and service edges the cited sentences name.

A tiny pack is built in ``tmp_path`` with the real tables (``graph_nodes``,
``graph_edges``, ``relationship_citations``, ``document_chunks``) and read
by the tool as it reads a reference pack's ``app.db`` (spec 2026-10-11 D8,
§ Measurement).
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from chaoscypher_core.services.sources.engine.extraction.utils.sentence_splitter import (
    split_into_sentences,
)
from chaoscypher_core.testing.repo_files import repo_file_or_skip


TOOL = "internal/benchmarks/tools/join_audit.py"

_SCHEMA = """
CREATE TABLE graph_nodes (
    id VARCHAR NOT NULL PRIMARY KEY, database_name VARCHAR NOT NULL, graph_name VARCHAR NOT NULL,
    template_id VARCHAR NOT NULL, label VARCHAR NOT NULL, entity_type VARCHAR, properties JSON,
    position_x FLOAT, position_y FLOAT, embedding JSON, created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL, source_id VARCHAR, ccx_iri VARCHAR
);
CREATE TABLE graph_edges (
    id VARCHAR NOT NULL PRIMARY KEY, database_name VARCHAR NOT NULL, graph_name VARCHAR NOT NULL,
    template_id VARCHAR NOT NULL, source_node_id VARCHAR, target_node_id VARCHAR,
    label VARCHAR NOT NULL, properties JSON, created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL, source_id VARCHAR, ccx_iri VARCHAR
);
CREATE TABLE relationship_citations (
    id VARCHAR NOT NULL PRIMARY KEY, database_name VARCHAR NOT NULL, edge_id VARCHAR NOT NULL,
    edge_label VARCHAR NOT NULL, edge_type VARCHAR, source_entity_label VARCHAR NOT NULL,
    target_entity_label VARCHAR NOT NULL, source_id VARCHAR, chunk_id VARCHAR,
    confidence FLOAT NOT NULL, extraction_method VARCHAR NOT NULL, justification VARCHAR,
    created_at DATETIME NOT NULL, citation_metadata JSON
);
CREATE TABLE document_chunks (
    id VARCHAR NOT NULL PRIMARY KEY, database_name VARCHAR NOT NULL, source_id VARCHAR,
    chunk_index INTEGER NOT NULL, content VARCHAR NOT NULL, raw_content TEXT, embedding BLOB,
    embedding_model VARCHAR, embedding_dimensions INTEGER, page_number INTEGER, section VARCHAR,
    group_index INTEGER, chunk_metadata JSON, status VARCHAR NOT NULL, created_at DATETIME NOT NULL,
    char_start INTEGER, char_end INTEGER, citation_offset_method VARCHAR(8) NOT NULL,
    embedded_at DATETIME, start_time FLOAT, end_time FLOAT
);
"""

_NOW = "2026-10-12 00:00:00"

CHUNK = (
    "The count kissed his wife's hand. "
    "Her brother laughed at the window. "
    "Mira Kell was the daughter of Count Oswin Kell. "
    "Tobin Ashgrove walked with Lady Brannoch Vey. "
    "Bram nodded to the girl. "
    "Tobin bowed to Lady Corra. "
    "Count Varro Pell entered the hall. "
    "I told my sister so."
)
"""One chunk: S1 a title only and a kinship word (no sentence before it), S2 nobody (nor S1),
S3 names both, S4 two labels, S5 a one-word alias (S4 before it names two nodes: the look-back),
S6 a two-word alias, S7 a title opening another name, S8 a possessive alone."""

NODES: dict[str, tuple[str, str, list[str]]] = {
    "count": ("Count Oswin Kell", "Person/character", ["Count Kell", "Bram", "the count"]),
    "mira": ("Mira Kell", "Person/character", ["My girl"]),
    "lady": ("Lady Brannoch Vey", "Person/character", ["Lady Corra", "Corra"]),
    "tobin": ("Tobin Ashgrove", "Person/character", []),
    "steward": ("the old steward", "Person/character", []),
    "wife": ("his wife", "Person/character", []),
    "house": ("Kell House", "Location", []),
}


def _load() -> ModuleType:
    path = repo_file_or_skip(TOOL)
    spec = importlib.util.spec_from_file_location("join_audit", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # the dataclasses resolve their annotations through it
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def tool() -> ModuleType:
    return _load()


def _sentence(index: int, chunk: str = CHUNK) -> dict[str, Any]:
    return {"text": split_into_sentences(chunk)[index], "chunk_id": "c1", "local_index": index}


def _pack(
    root: Path,
    edges: Sequence[tuple[str, str, str, str, Sequence[int] | None, str]],
    *,
    flagged: Sequence[str] = (),
    nodes: dict[str, tuple[str, str, list[str]]] = NODES,
    chunk: str = CHUNK,
    domain: str | None = None,
) -> Path:
    """A pack dir with ``app.db``: ``edges`` are ``(id, label, source, target, sentence indexes, justification)``.

    A ``None`` index list is a citation with no sentence index (its justification is read).
    ``domain`` writes a ``manifest.yaml`` naming it as the ``extraction_domain``.
    """
    root.mkdir(parents=True, exist_ok=True)
    if domain is not None:
        (root / "manifest.yaml").write_text(f"extraction_domain: {domain}\n", encoding="utf-8")
    con = sqlite3.connect(root / "app.db")
    con.executescript(_SCHEMA)
    for key, (label, etype, aliases) in nodes.items():
        con.execute(
            "INSERT INTO graph_nodes (id, database_name, graph_name, template_id, label, entity_type,"
            " properties, created_at, updated_at) VALUES (?, 'db', 'g', 't', ?, ?, ?, ?, ?)",
            (key, label, etype, json.dumps({"aliases": aliases}), _NOW, _NOW),
        )
    con.execute(
        "INSERT INTO document_chunks (id, database_name, chunk_index, content, group_index,"
        " chunk_metadata, status, created_at, citation_offset_method)"
        " VALUES ('c1', 'db', 0, ?, 0, '{}', 'ready', ?, 'exact')",
        (chunk, _NOW),
    )
    for edge_id, label, source, target, indexes, justification in edges:
        con.execute(
            "INSERT INTO graph_edges (id, database_name, graph_name, template_id, source_node_id,"
            " target_node_id, label, properties, created_at, updated_at)"
            " VALUES (?, 'db', 'g', 't', ?, ?, ?, ?, ?, ?)",
            (
                edge_id,
                source,
                target,
                label,
                json.dumps(
                    {"justification": justification}
                    | ({"unevidenced": 1, "evidence_reason": "test"} if edge_id in flagged else {})
                ),
                _NOW,
                _NOW,
            ),
        )
        metadata = (
            {"sent_ref": "", "sentences": []}
            if indexes is None
            else {
                "sent_ref": ",".join(f"S{i + 1}" for i in indexes),
                "sentences": [_sentence(i, chunk) for i in indexes],
            }
        )
        con.execute(
            "INSERT INTO relationship_citations (id, database_name, edge_id, edge_label,"
            " source_entity_label, target_entity_label, chunk_id, confidence, extraction_method,"
            " justification, created_at, citation_metadata)"
            " VALUES (?, 'db', ?, ?, ?, ?, 'c1', 1.0, 'ai_extraction', ?, ?, ?)",
            (
                f"rc-{edge_id}",
                edge_id,
                label,
                nodes[source][0],
                nodes[target][0],
                justification,
                _NOW,
                json.dumps(metadata),
            ),
        )
    con.commit()
    con.close()
    return root


THREE = [
    ("e1", "child_of", "mira", "count", [2], "Mira is the count's daughter."),
    ("e2", "spouse_of", "count", "lady", [0], "The count kisses his wife's hand."),
    ("e3", "sibling_of", "tobin", "mira", [1], "Her brother laughed."),
]
"""The three-edge pack: one named, one title-only, one unnamed."""


def _by_id(rows: list[Any]) -> dict[str, Any]:
    return {r.edge_id: r for r in rows}


def test_three_edges_named_title_only_unnamed(tool: ModuleType, tmp_path: Path) -> None:
    rows = tool.audit(tool.load_pack(_pack(tmp_path / "p", THREE, flagged=("e2",))))
    by = _by_id(rows)
    assert by["e2"].flagged and not by["e1"].flagged  # the filter's flag rides along
    assert (by["e1"].source_verdict, by["e1"].target_verdict) == ("named", "named")
    assert (by["e2"].source_verdict, by["e2"].target_verdict) == ("title-only", "unnamed")
    assert (by["e3"].source_verdict, by["e3"].target_verdict) == ("unnamed", "unnamed")
    assert by["e2"].sentence == "The count kissed his wife's hand."
    assert not by["e2"].from_justification
    assert [r.edge_id for r in rows] == ["e2", "e3", "e1"]  # title-only, then unnamed, then named
    assert tool.totals(rows) == {
        "edges": 3,
        "edges with a title-only or unnamed endpoint": 2,
        "of them flagged unevidenced by the filter": 1,
        "of them unflagged (the joins the filter did not catch)": 1,
        "named": 2,
        "title-only": 1,
        "unnamed": 3,
        "reference": 0,
    }


def test_a_reference_endpoint_is_no_named_node(tool: ModuleType, tmp_path: Path) -> None:
    pack = _pack(tmp_path / "p", [("e1", "serves", "steward", "count", [0], "")])
    (row,) = tool.audit(tool.load_pack(pack))
    assert (row.source_verdict, row.target_verdict) == ("reference", "title-only")


def test_a_one_word_alias_is_not_a_name(tool: ModuleType, tmp_path: Path) -> None:
    """D8 rule 1: only a multi-word alias that is a name names; "Bram" alone does not."""
    pack = _pack(tmp_path / "p", [("e1", "parent_of", "count", "mira", [4], "")])
    (row,) = tool.audit(tool.load_pack(pack))
    assert row.source_verdict == "unnamed"


def test_a_multi_word_alias_names(tool: ModuleType, tmp_path: Path) -> None:
    """The lady's alias "Lady Corra" names her: her label's words are not written in S6."""
    pack = _pack(tmp_path / "p", [("e1", "courts", "tobin", "lady", [5], "")])
    (row,) = tool.audit(tool.load_pack(pack))
    assert (row.source_verdict, row.target_verdict) == ("named", "named")


def test_a_title_opening_another_name_is_no_title_only(tool: ModuleType, tmp_path: Path) -> None:
    """Another count's name ("Count Varro Pell") is written: the count's title does not stand alone there."""
    pack = _pack(tmp_path / "p", [("e1", "spouse_of", "count", "lady", [6], "")])
    (row,) = tool.audit(tool.load_pack(pack))
    assert row.source_verdict == "unnamed"


def test_a_cited_sentence_continued_past_the_chunk_is_read_whole(
    tool: ModuleType, tmp_path: Path
) -> None:
    """The citation's text is the chunk's sentence continued (a group sentence over a break): read whole."""
    pack = _pack(tmp_path / "p", [("e1", "child_of", "mira", "count", [4], "")])
    con = sqlite3.connect(pack / "app.db")
    whole = {**_sentence(4), "text": "Bram nodded to the girl.\n\nMira answered him."}
    con.execute(
        "UPDATE relationship_citations SET citation_metadata = ?",
        (json.dumps({"sent_ref": "S5", "sentences": [whole]}),),
    )
    con.commit()
    con.close()
    (row,) = tool.audit(tool.load_pack(pack))
    assert row.sentence == "Bram nodded to the girl. Mira answered him."
    assert row.source_verdict == "named"


def test_an_alias_of_a_possessive_and_lowercase_words_names_no_one(
    tool: ModuleType, tmp_path: Path
) -> None:
    """The alias "My girl" reads as "My" (``alias_as_name``): no name, so S8's "my" does not name Mira."""
    pack = _pack(tmp_path / "p", [("e1", "sibling_of", "mira", "tobin", [7], "")])
    (row,) = tool.audit(tool.load_pack(pack))
    assert row.source_verdict == "unnamed"


def test_a_label_with_no_naming_word_is_a_reference(tool: ModuleType, tmp_path: Path) -> None:
    pack = _pack(tmp_path / "p", [("e1", "spouse_of", "wife", "count", [7], "")])
    (row,) = tool.audit(tool.load_pack(pack))
    assert row.source_verdict == "reference"


def test_no_sentence_index_reads_the_justification(tool: ModuleType, tmp_path: Path) -> None:
    pack = _pack(tmp_path / "p", [("e1", "child_of", "mira", "count", None, "Mira is his child.")])
    (row,) = tool.audit(tool.load_pack(pack))
    assert row.from_justification
    assert row.sentence == "Mira is his child."
    assert (row.source_verdict, row.target_verdict) == ("named", "unnamed")
    assert "[justification]" in "\n".join(tool.render_rows([row]))


def test_families_filter_and_all(tool: ModuleType, tmp_path: Path) -> None:
    edges = [*THREE, ("e4", "located_in", "mira", "house", [2], "")]
    pack = tool.load_pack(_pack(tmp_path / "p", edges))
    assert {r.edge_id for r in tool.audit(pack)} == {"e1", "e2", "e3"}
    assert {r.edge_id for r in tool.audit(pack, families=None)} == {"e1", "e2", "e3", "e4"}
    assert {r.edge_id for r in tool.audit(pack, families=frozenset({"sibling_of"}))} == {"e3"}


def test_two_packs_list_the_edges_each_has_alone(tool: ModuleType, tmp_path: Path) -> None:
    a = tool.load_pack(_pack(tmp_path / "a", THREE))
    b = tool.load_pack(
        _pack(tmp_path / "b", [*THREE[:2], ("e9", "serves", "tobin", "count", [3], "")])
    )
    only_a, only_b = tool.edge_diff(tool.audit(a), tool.audit(b))
    assert only_a == [("sibling_of", "Tobin Ashgrove", "Mira Kell")]
    assert only_b == [("serves", "Tobin Ashgrove", "Count Oswin Kell")]


def test_main_output_is_stable(
    tool: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    a = _pack(tmp_path / "a", THREE)
    b = _pack(tmp_path / "b", THREE[:2])
    assert tool.main([str(a), str(b / "app.db")]) == 0
    first = capsys.readouterr().out
    assert tool.main([str(a), str(b / "app.db")]) == 0
    assert capsys.readouterr().out == first
    assert "title-only: 1" in first
    assert "sibling_of | Tobin Ashgrove | Mira Kell" in first
    lines = first.splitlines()
    table = lines.index(next(line for line in lines if line.startswith("label | source")))
    assert lines[table + 1].startswith(
        "spouse_of | Count Oswin Kell | Lady Brannoch Vey | title-only"
    )


# -- final review 2 of 2026-10-12 (I2, M10): the filter's one reading of a known endpoint -----


VARNS: dict[str, tuple[str, str, list[str]]] = {
    "ilse": ("Ilse Marren", "Person/character", []),
    "mirela": ("Lady Mirela Varn", "Person/character", []),
    "edric": ("Lord Edric Varn", "Person/character", []),
}
VARN_CHUNK = "Lord Varn rode out. Ilse Marren waved to him."


def test_a_family_word_two_named_nodes_claim_names_neither(
    tool: ModuleType, tmp_path: Path
) -> None:
    """D8 rule 1: "Lord Varn" names neither Varn when both are nodes; one alone it names."""
    edge = [("e1", "child_of", "ilse", "mirela", [0], "")]
    (row,) = tool.audit(
        tool.load_pack(_pack(tmp_path / "two", edge, nodes=VARNS, chunk=VARN_CHUNK))
    )
    assert row.target_verdict == "unnamed"
    alone = {k: v for k, v in VARNS.items() if k != "edric"}
    (row,) = tool.audit(
        tool.load_pack(_pack(tmp_path / "one", edge, nodes=alone, chunk=VARN_CHUNK))
    )
    assert row.target_verdict == "named"


def test_the_sentence_before_the_cited_one_names_and_is_printed(
    tool: ModuleType, tmp_path: Path
) -> None:
    """The filter's look-back: S5 names nobody, S4 before it names both; the row shows it."""
    pack = _pack(tmp_path / "p", [("e1", "courts", "tobin", "lady", [4], "")])
    (row,) = tool.audit(tool.load_pack(pack))
    assert (row.source_verdict, row.target_verdict) == ("named", "named")
    assert row.preceding == "Tobin Ashgrove walked with Lady Brannoch Vey."
    assert tool.render_rows([row])[1].endswith(
        "| [before: Tobin Ashgrove walked with Lady Brannoch Vey.] Bram nodded to the girl."
    )


def test_the_known_list_is_the_filters_own(tool: ModuleType, tmp_path: Path) -> None:
    """One ``KnownEntities`` over the named nodes: references left out, every label its own entry."""
    pack = tool.load_pack(_pack(tmp_path / "p", THREE))
    known = tool.known_entities(pack.nodes, pack.conventions)
    assert known.resolves("Count Oswin Kell") == "count oswin kell"
    assert known.resolves("the old steward") is None
    assert known.resolves("Count Kell") == "count oswin kell"  # its alias, one entry gave it


def test_the_manifest_domain_names_the_conventions(
    tool: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The pack's own naming conventions (the extraction's), universal when the manifest names none."""
    from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
        DEFAULT_NAME_CONVENTIONS,
    )
    from chaoscypher_core.services.sources.engine.extraction.domain_resolver import (
        conventions_of_packs,
    )

    literary = tool.load_pack(_pack(tmp_path / "lit", THREE, domain="literary"))
    assert literary.domain == "literary"
    assert literary.conventions == conventions_of_packs(("literary",))
    assert literary.conventions != DEFAULT_NAME_CONVENTIONS
    plain = tool.load_pack(_pack(tmp_path / "plain", THREE))
    assert plain.domain is None and plain.conventions == DEFAULT_NAME_CONVENTIONS
    assert tool.main([str(tmp_path / "lit")]) == 0
    assert "conventions: literary" in capsys.readouterr().out
