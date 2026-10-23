# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The retrieval recipe and the index digest under audit wave 5.

- Dropping ``vicomte``, ``vicomtesse``, ``duc`` and ``duchesse`` from the
  literary titles moved 4 of 120 benchmark contexts and left the recipe as it
  was: the naming tables were in no hash and no sample entity carried a
  domain-only title.
- The sample world had no citations of one entity at different confidences
  and no scoped run whose scope the hybrid cut could starve.
- ``index_digest`` ignored the graph tables: an edited edge changed 120 of 120
  contexts under the same digest.
"""

from __future__ import annotations

import dataclasses
import json
import sqlite3
from typing import TYPE_CHECKING, Any

import pytest

from chaoscypher_core.benchmark import pins
from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
    NameConventions,
    NameForm,
    NameTables,
    fold,
    is_descriptor,
    name_form,
)
from chaoscypher_core.services.sources.engine.extraction import domain_resolver


if TYPE_CHECKING:
    from pathlib import Path


def _mutated(conv: NameConventions, field: str) -> NameConventions:
    """``conv`` with one more entry in the table ``field`` (whatever its shape)."""
    value = getattr(conv, field)
    if field == "generation_words":
        return dataclasses.replace(conv, generation_words=(*value, ("zzgen", "elder")))
    if field == "name_forms":
        return dataclasses.replace(conv, name_forms=(*value, ("zz", (NameForm("zzq", "zz", "f"),))))
    if field == "title_equivalents":
        return dataclasses.replace(conv, title_equivalents=(*value, frozenset({"zza", "zzb"})))
    if value is None or isinstance(value, frozenset):  # a set table, or one left unset
        changes: dict[str, Any] = {field: (value or frozenset()) | {"zzmutation"}}
        return dataclasses.replace(conv, **changes)
    msg = f"NameConventions.{field}: a table the guard cannot mutate; teach _mutated its shape"
    raise AssertionError(msg)


_TABLES = [f.name for f in dataclasses.fields(NameConventions)]


def test_every_table_is_one_the_recipe_hashes() -> None:
    """``as_dict`` (what the recipe hashes) holds every field: a new table cannot be left out."""
    conv = domain_resolver.registered_name_conventions()[0]
    assert set(conv.as_dict()) == set(_TABLES)


@pytest.mark.parametrize("field", _TABLES)
def test_each_table_of_each_registered_domain_moves_the_naming_part(
    monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    """One more entry in one table of one domain's conventions: another naming part, every time."""
    sets = domain_resolver.registered_name_conventions()
    assert len(sets) > 2  # the universal conventions and the domains'
    before = pins.name_conventions_part()
    seen = {before}
    for i in range(len(sets)):
        changed = [*sets[:i], _mutated(sets[i], field), *sets[i + 1 :]]
        with monkeypatch.context() as m:
            m.setattr(domain_resolver, "registered_name_conventions", lambda *_a, c=changed: c)
            part = pins.name_conventions_part()
        assert part not in seen, (field, i)
        seen.add(part)
    assert pins.name_conventions_part() == before


def test_the_recipe_takes_the_naming_part(monkeypatch: pytest.MonkeyPatch) -> None:
    before = pins.retrieval_recipe()
    original = pins.name_conventions_part
    monkeypatch.setattr(pins, "name_conventions_part", lambda: original() + " ")
    assert pins.retrieval_recipe() != before


def test_dropping_domain_titles_moves_the_recipe_through_the_real_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The audit's case, through the one reading of a domain's tables both the handler and the hash use."""
    drop = {"vicomte", "vicomtesse", "duc", "duchesse"}
    original = domain_resolver.name_tables_of_domain

    def without(domain: Any) -> NameTables:
        tables = original(domain)
        if getattr(domain, "name", None) != "literary":
            return tables
        return dataclasses.replace(
            tables,
            titles=tuple(t for t in tables.titles if fold(t) not in drop),
            feminine_titles=tuple(t for t in tables.feminine_titles if fold(t) not in drop),
            masculine_titles=tuple(t for t in tables.masculine_titles if fold(t) not in drop),
        )

    before = pins.retrieval_recipe()
    monkeypatch.setattr(domain_resolver, "name_tables_of_domain", without)
    assert pins.retrieval_recipe() != before
    monkeypatch.undo()
    assert pins.retrieval_recipe() == before


def test_the_sample_cast_exercises_every_naming_table() -> None:
    """A domain-only title, a title equivalent, a feminine surname form, a generation word, a relation word."""
    from chaoscypher_core.benchmark import retrieval_sample as rs
    from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
        DEFAULT_NAME_CONVENTIONS,
    )
    from chaoscypher_core.services.sources.engine.extraction.domain_resolver import (
        DomainResolver,
        domain_name_conventions,
    )
    from chaoscypher_core.settings import EngineSettings

    conv = domain_name_conventions(rs.SAMPLE_DOMAIN, DomainResolver(EngineSettings()))
    names = [n for _, label, _, aliases, _, _ in rs.SAMPLE_NODES for n in (label, *aliases)]
    words = {w for n in names for w in fold(n).split()}
    question_words = {w for q in rs.SAMPLE_QUESTIONS for w in fold(q).split()}
    domain_only = (words & question_words & conv.titles) - DEFAULT_NAME_CONVENTIONS.titles
    assert domain_only, "no title only the domain has, in a name and a question"
    assert any(len(group & words) > 1 for group in conv.title_equivalents)
    assert any((f := name_form(w, conv)) is not None and f.gender == "f" for w in words)
    assert words & set(conv.generation)
    assert any(is_descriptor(n, conv) for n in names)
    assert words & conv.gendered_titles


def test_the_sample_orders_one_entitys_citations_by_confidence() -> None:
    """Confidence and chunk id disagree on the order of one entity's citations."""
    from chaoscypher_core.benchmark import retrieval_sample as rs
    from chaoscypher_core.services.workflows.tools.engine.handlers.graphrag_handlers import (
        _citation_tiebreak,
    )

    for entity in {e for e, _ in rs.SAMPLE_CITATION_CONFIDENCE}:
        cites = [
            {"chunk_id": c, "confidence": rs.SAMPLE_CITATION_CONFIDENCE.get((entity, c), 1.0)}
            for c in rs.SAMPLE_CITATIONS[entity]
        ]
        by_rule = [c["chunk_id"] for c in sorted(cites, key=_citation_tiebreak)]
        assert by_rule != sorted(rs.SAMPLE_CITATIONS[entity])


_HANDLERS = "chaoscypher_core.services.workflows.tools.engine.handlers.graphrag_handlers"
_SEARCH = "chaoscypher_core.adapters.sqlite.repos.search"


@pytest.mark.parametrize(
    ("module", "name", "replace"),
    [
        # the confidence half of the citation order ignored
        (_HANDLERS, "_citation_tiebreak", lambda f: lambda c: (0.0, str(c.get("chunk_id")))),
        # the scope left out of the search (applied by the handler after the cut, as before wave 5)
        (_SEARCH, "_scope_clause", lambda f: lambda item_type, column, ids, **kw: ("", "", {})),
    ],
    ids=["confidence-ignored", "scope-after-the-cut"],
)
def test_the_recipe_moves_with_the_wave5_rules(
    monkeypatch: pytest.MonkeyPatch, module: str, name: str, replace: Any
) -> None:
    import importlib

    before = pins.retrieval_recipe()
    owner: Any = importlib.import_module(module)
    attr = name
    if "." in name:
        cls, attr = name.split(".")
        owner = getattr(owner, cls)
    with monkeypatch.context() as m:
        m.setattr(owner, attr, replace(getattr(owner, attr)))
        assert pins.retrieval_recipe() != before, name
    assert pins.retrieval_recipe() == before


def _graph_db(path: Path) -> None:
    con = sqlite3.connect(path)
    for table in ("vec_search_chunks", "vec_search_nodes"):
        con.execute(f'CREATE TABLE "{table}_rowids" (rowid INTEGER PRIMARY KEY, id TEXT)')
        con.execute(
            f'CREATE TABLE "{table}_vector_chunks00" (rowid INTEGER PRIMARY KEY, vectors BLOB)'
        )
    con.execute(
        "CREATE TABLE graph_edges (id TEXT PRIMARY KEY, source_node_id TEXT, "
        "target_node_id TEXT, label TEXT, updated_at TEXT)"
    )
    con.execute(
        "CREATE TABLE source_citations (id TEXT PRIMARY KEY, entity_uri TEXT, chunk_id TEXT, "
        "confidence REAL, created_at TEXT)"
    )
    con.execute(
        "CREATE TABLE relationship_citations (id TEXT PRIMARY KEY, edge_id TEXT, chunk_id TEXT, "
        "citation_metadata TEXT, created_at TEXT)"
    )
    con.execute("INSERT INTO graph_edges VALUES ('e1', 'a', 'b', 'parent_of', '2026-10-06')")
    con.execute("INSERT INTO source_citations VALUES ('s1', 'a', 'c1', 1.0, '2026-10-06')")
    sentences = [{"text": "A is the parent of B.", "chunk_id": "c1", "local_index": 0}]
    con.execute(
        "INSERT INTO relationship_citations VALUES ('r1', 'e1', 'c1', ?, '2026-10-09')",
        (json.dumps({"sentences": sentences}),),
    )
    con.commit()
    con.close()


@pytest.mark.parametrize(
    ("statement", "moves"),
    [
        ("UPDATE graph_edges SET label = 'spouse_of'", True),
        ("UPDATE source_citations SET chunk_id = 'c2'", True),
        ("UPDATE source_citations SET confidence = 0.5", True),
        ("UPDATE graph_edges SET updated_at = '2030-01-01'", False),
        ("UPDATE source_citations SET created_at = '2030-01-01'", False),
        ("UPDATE relationship_citations SET citation_metadata = '{\"sentences\": []}'", True),
        ("UPDATE relationship_citations SET edge_id = 'e2'", True),
        ("UPDATE relationship_citations SET created_at = '2030-01-01'", False),
    ],
)
def test_the_index_digest_covers_the_graph_tables(
    tmp_path: Path, statement: str, *, moves: bool
) -> None:
    """What the rung reads moves the digest; when a row was written does not."""
    a, b = tmp_path / "a.db", tmp_path / "b.db"
    _graph_db(a)
    _graph_db(b)
    with sqlite3.connect(b) as con:
        con.execute(statement)
    assert (pins.index_digest(a) != pins.index_digest(b)) is moves


def test_the_recipe_sees_the_shortfall_check_when_the_scope_is_lost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The check fires only on a lost hit, which correct code never has: the recipe sees it under one.

    With the scope left out of the search (the hits come from the whole
    graph and the handler drops those out of scope), the sample's scoped
    runs come up short of what the search saw and record
    ``hybrid_search:scope_short``; with the check off as well they do not.
    Until wave 6 the check also fired on a scope holding few chunks above
    the similarity floor, so the sample could see it unaided.
    """
    import importlib

    search: Any = importlib.import_module(_SEARCH)
    handlers: Any = importlib.import_module(_HANDLERS).GraphRAGToolHandlers
    with monkeypatch.context() as m:
        m.setattr(search, "_scope_clause", lambda item_type, column, ids, **kw: ("", "", {}))
        scope_lost = pins.retrieval_recipe()
        m.setattr(handlers, "_check_scope_filled", lambda *a, **k: None)
        assert pins.retrieval_recipe() != scope_lost
