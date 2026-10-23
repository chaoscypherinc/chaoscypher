# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""One universal title table, the floor every domain starts from (audit wave 7).

Two universal title lists stood in ``name_identity``: a ``TITLES`` default for
``name_words`` and ``UNIVERSAL_TITLE_WORDS``, the thirteen words a domain
without its own fell back to. Under the generic domain ``prince``,
``countess`` and ``monsieur`` were naming words, so "Monsieur" named
Buonaparte and "princess" named Mary in sentences about someone else, and the
generic arm kept 13 edges the literary arm drops, all four false ones among
them. Now one table of the language-level address titles of English and
French (with their gender and their spellings) is unioned into every domain's
conventions; the literary domain adds only its genre's (``dowager``,
``vicomtesse``, Russian surname forms, ``old`` and ``young``). Since
2026-10-11 the floor holds the kinship vocatives too (``Mamma``, ``Papa``,
``Uncle``, ``Aunt``): a family's word for one of its own names no one.
"""

from __future__ import annotations

import ast
import inspect
from typing import Any

import pytest

from chaoscypher_core.services.sources.engine.deduplication import name_identity as ni
from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
    UNIVERSAL_FEMININE_TITLES,
    UNIVERSAL_MASCULINE_TITLES,
    UNIVERSAL_TITLE_WORDS,
    NameConventions,
    fold,
    name_words,
    reference_key,
)
from tests.fakes.domain_tables import domain_conventions


GENERIC = domain_conventions("generic")
LITERARY = domain_conventions("literary")


def test_monsieur_is_a_title_under_the_generic_domain() -> None:
    """The auditor's case: ``Monsieur Pierre`` is named by ``Pierre`` alone, under every table."""
    assert name_words("Monsieur Pierre", GENERIC.titles) == {"pierre"}
    assert name_words("Monsieur Pierre", LITERARY.titles) == {"pierre"}
    assert name_words("Monsieur Pierre") == {"pierre"}
    assert name_words("Prince Andrew", GENERIC.titles) == {"andrew"}


def test_every_domain_starts_from_the_floor() -> None:
    """Each registered domain's titles, gendered titles and spellings hold the universal ones."""
    from chaoscypher_core.services.sources.engine.extraction.domains import get_domain_registry
    from chaoscypher_core.settings import EngineSettings

    floor = {fold(t) for t in UNIVERSAL_TITLE_WORDS}
    for info in get_domain_registry(EngineSettings()).list_domain_info():
        conv = domain_conventions(str(info["name"]))
        assert floor <= {fold(t) for t in conv.titles}, info["name"]
        assert conv.feminine_titles >= UNIVERSAL_FEMININE_TITLES, info["name"]
        assert conv.masculine_titles >= UNIVERSAL_MASCULINE_TITLES, info["name"]
        assert conv.title_class["monsieur"] == conv.title_class["mr"], info["name"]
    # a domain's own table adds to the floor, never replaces it
    own = NameConventions.build(titles=["professor"], feminine_titles=["abbess"])
    assert {"professor", "prince", "mlle"} <= own.titles
    assert {"abbess", "princess"} <= own.feminine_titles


def test_the_floors_gendered_titles_say_their_gender() -> None:
    """A title the floor drops from a name says the gender it says in the language."""
    assert {fold(t) for t in UNIVERSAL_FEMININE_TITLES | UNIVERSAL_MASCULINE_TITLES} <= {
        fold(t) for t in UNIVERSAL_TITLE_WORDS
    }
    assert not UNIVERSAL_FEMININE_TITLES & UNIVERSAL_MASCULINE_TITLES
    assert "countess" in GENERIC.feminine_titles and "count" in GENERIC.masculine_titles


@pytest.mark.parametrize(
    ("a", "b"),
    [("Lady Russell", "Russell Crowe"), ("Countess Thomas", "Thomas Rostóv"),
     ("King of Prussia", "Queen of Prussia")],
)  # fmt: skip
def test_a_floor_title_keeps_the_genders_apart_under_generic(a: str, b: str) -> None:
    """Dropped from the name, the title still says a woman (or a man): no join under generic."""
    records = [ni.NameRecord(a, "Character"), ni.NameRecord(b, "Character")]
    assert ni.plan_name_pairs(records, conventions=GENERIC) == []


def test_mme_and_madame_are_one_title_under_generic() -> None:
    """One title's French and English spellings are one title (``title_equivalents`` floor)."""
    pairs = ni.plan_name_pairs(
        [ni.NameRecord("Mme Duval", "Character"), ni.NameRecord("Madame Duval", "Character")],
        conventions=GENERIC,
    )
    assert [(i, j) for i, j, _ in pairs] == [(0, 1)]


def _registered_conventions() -> list[tuple[str, NameConventions]]:
    """Every registered domain's conventions, and the default ones (no domain)."""
    from chaoscypher_core.services.sources.engine.extraction.domains import get_domain_registry
    from chaoscypher_core.settings import EngineSettings

    names = [str(info["name"]) for info in get_domain_registry(EngineSettings()).list_domain_info()]
    return [(name, domain_conventions(name)) for name in names] + [
        ("default", ni.DEFAULT_NAME_CONVENTIONS)
    ]


def test_kinship_vocatives_are_titles_under_every_domain() -> None:
    """``Mamma``, ``Papa``, ``Uncle``, ``Aunt`` address someone; they never name a person (v5d).

    On the v5d literary pack ``the countess`` given ``Mamma`` folded onto
    Borís's unflagged ``Mamma`` node: ``Mamma`` had no title reading, so it
    was a given name, the alias evidence. Every registered domain.
    """
    for name, conv in _registered_conventions():
        assert name_words("Mamma", conv.titles) == set(), name
        assert name_words("Uncle Pierre", conv.titles) == {"pierre"}, name
        assert name_words("Aunt Marya", conv.titles) == {"marya"}, name
        # one title through ``title_class``: its one spelling is the group's least (``mama``)
        assert reference_key("Mamma", conv) == reference_key("Maman", conv) == "mama", name
        assert reference_key("Mama", conv) == "mama", name
        assert reference_key("Papa", conv) == reference_key("papá", conv) == "papa", name
        assert reference_key("Grandpapa", conv) == "grandpapa", name
        assert reference_key("Auntie", conv) == reference_key("Aunt", conv) == "aunt", name
        # both spellings of one grandmother's title (``grandmama`` read as a given name until M6)
        assert name_words("Grandmama", conv.titles) == set(), name
        assert reference_key("Grandmamma", conv) == reference_key("Grandmama", conv), name
        assert reference_key("Grandmama", conv) == "grandmama", name
        feminine = {"mamma", "mama", "maman", "aunt", "auntie", "grandmamma", "grandmama"}
        assert feminine <= conv.feminine_titles, name
        assert {"papa", "uncle", "grandpapa"} <= conv.masculine_titles, name


def test_a_kinship_title_keeps_the_genders_apart() -> None:
    """``Aunt Sonya`` is no ``Uncle Sonya``; ``Uncle Pierre`` is ``Pierre`` (a man's title, an unmarked name)."""
    for conv in (GENERIC, LITERARY):
        apart = [ni.NameRecord("Aunt Sonya", "Person"), ni.NameRecord("Uncle Sonya", "Person")]
        assert ni.plan_name_pairs(apart, conventions=conv) == []
        same = [ni.NameRecord("Uncle Pierre", "Person"), ni.NameRecord("Pierre", "Person")]
        assert [(i, j) for i, j, _ in ni.plan_name_pairs(same, conventions=conv)] == [(0, 1)]


@pytest.mark.parametrize("conv", [GENERIC, LITERARY], ids=["generic", "literary"])
def test_aunt_and_auntie_are_one_title(conv: NameConventions) -> None:
    """``Aunt Marya`` and ``Auntie Marya`` are one woman's title (``title_equivalents`` floor)."""
    records = [ni.NameRecord("Aunt Marya", "Person"), ni.NameRecord("Auntie Marya", "Person")]
    assert [(i, j) for i, j, _ in ni.plan_name_pairs(records, conventions=conv)] == [(0, 1)]


@pytest.mark.parametrize("conv", [GENERIC, LITERARY], ids=["generic", "literary"])
def test_two_mens_titles_on_one_given_name_stay_apart(conv: NameConventions) -> None:
    """``Uncle Pierre`` and ``Monsieur Pierre`` stay two: deliberate, not a missed merge.

    Two different titles of one gender are two people unless the titles are
    spellings of one (``title_equivalents``): the rule withholds a merge
    when the names say it could be someone else, and a nephew's uncle and a
    Monsieur both named Pierre may be two men.
    """
    records = [ni.NameRecord("Uncle Pierre", "Person"), ni.NameRecord("Monsieur Pierre", "Person")]
    assert ni.plan_name_pairs(records, conventions=conv) == []


def _module_title_tables() -> list[str]:
    """Module-level frozensets of ``name_identity`` holding three or more address titles."""
    tree = ast.parse(inspect.getsource(ni))
    probes = {"mr", "mrs", "miss", "prince", "count", "monsieur", "madame", "sir"}
    found = []
    for node in tree.body:
        target = node.targets[0] if isinstance(node, ast.Assign) else getattr(node, "target", None)
        value = getattr(node, "value", None)
        if not isinstance(target, ast.Name) or value is None:
            continue
        words = {
            c.value
            for c in ast.walk(value)
            if isinstance(c, ast.Constant) and isinstance(c.value, str)
        }
        if len(words & probes) >= 3:
            found.append(target.id)
    return found


def test_there_is_one_universal_title_table() -> None:
    """No second universal title list: a third copy is how the two arms drifted apart.

    The gendered tables and the spellings are the floor's subsets (checked
    above); any other module-level list of address titles fails here.
    """
    assert sorted(_module_title_tables()) == sorted(
        ["UNIVERSAL_TITLE_WORDS", "UNIVERSAL_FEMININE_TITLES", "UNIVERSAL_MASCULINE_TITLES",
         "UNIVERSAL_TITLE_EQUIVALENTS"]
    )  # fmt: skip
    assert not hasattr(ni, "TITLES")


# -- the v3 pack -------------------------------------------------------------------------------


_V3 = "internal/benchmarks/reference/war_and_peace_book1-v3/app.db"


def _v3_kept(path: Any, domain: str) -> set[tuple[str, str, str, str]]:
    """v3's relationships the evidence filter keeps under ``domain``'s tables (standard mode).

    Each group's text is rebuilt by the packer and split by today's splitter,
    so a ``sent_ref`` of the old extraction does not always point at its
    sentence; both arms read the same sentences, which is what is compared.
    """
    import json
    import sqlite3

    from chaoscypher_core.services.sources.engine.extraction.orchestration import (
        build_extraction_groups,
    )
    from chaoscypher_core.services.sources.engine.extraction.utils.evidence_validator import (
        validate_relationship_evidence,
    )
    from chaoscypher_core.services.sources.engine.extraction.utils.sentence_splitter import (
        split_into_sentences,
    )
    from tests.fakes.domain_tables import domain_vocabulary

    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
        chunks = [
            {"id": str(i), "content": content or ""}
            for i, (content,) in enumerate(
                conn.execute("SELECT content FROM document_chunks ORDER BY chunk_index")
            )
        ]
        entity_rows = conn.execute(
            "SELECT id, name, type, attributes FROM source_entities ORDER BY ordinal"
        ).fetchall()
        rel_rows = conn.execute(
            "SELECT source_entity_id, target_entity_id, predicate, attributes "
            "FROM source_relationships ORDER BY ordinal"
        ).fetchall()
    groups = [
        g["combined_content"] for g in build_extraction_groups([c for c in chunks if c["content"]])
    ]
    entities: dict[str, dict[str, Any]] = {}
    at: dict[str, set[Any]] = {}
    for eid, name, kind, attributes in entity_rows:
        attrs = json.loads(attributes or "{}")
        entities[eid] = {"name": name, "type": kind, "aliases": list(attrs.get("aliases") or [])}
        at[eid] = set(attrs.get("source_chunk_indices") or []) | {attrs.get("chunk_index")}
    conventions = domain_conventions(domain)
    vocabulary = domain_vocabulary(domain)
    kept: set[tuple[str, str, str, str]] = set()
    for source, target, label, attributes in rel_rows:
        attrs = json.loads(attributes)
        group = attrs["chunk_index"]
        ids = [e for e in entities if group in at[e]]
        ids += [x for x in (source, target) if x not in ids]
        rel = {"source": ids.index(source), "target": ids.index(target), "type": label,
               "sent_ref": attrs["sent_ref"]}  # fmt: skip
        if validate_relationship_evidence(
            rel,
            [entities[e] for e in ids],
            split_into_sentences(groups[group]),
            "standard",
            vocabulary=vocabulary,
            conventions=conventions,
        ):
            kept.add((entities[source]["name"], label, entities[target]["name"], attrs["sent_ref"]))
    return kept


def test_the_generic_arm_keeps_no_v3_edge_the_literary_arm_drops() -> None:
    """At HEAD of wave 6 generic kept 9 edges here that literary drops ("prince" a naming word).

    Generic has none of literary's genre tables (its surname forms, its kin
    words), so it may keep fewer; never one more.
    """
    from chaoscypher_core.testing.repo_files import repo_file_or_skip

    path = repo_file_or_skip(_V3)
    literary, generic = _v3_kept(path, "literary"), _v3_kept(path, "generic")
    assert generic - literary == set()
    assert len(literary - generic) <= 2
