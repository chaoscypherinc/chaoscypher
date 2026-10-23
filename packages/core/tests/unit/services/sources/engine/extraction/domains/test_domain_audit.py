# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The domain configs before the literary/generic A/B (domain audit, 2026-10-06).

Each arm must work as well as it can, so the A/B measures the literary
specialization and not a neglected arm: every predicate a domain's guidance
or prompt example names is a template of that domain, the prompt's format
example is in the domain's own types and passes its edge constraints, the
naming tables are consistent by construction, and the genre-level changes
(edges, vocabulary, constraints, limits) hold. Every check runs over every
registered domain where it is a property of all of them.
"""

from __future__ import annotations

import json
import re
from functools import cache
from pathlib import Path
from typing import Any

import pytest

from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
    NameRecord,
    fold,
    is_descriptor,
    plan_name_pairs,
)
from chaoscypher_core.services.sources.engine.extraction.domains import get_domain_registry
from chaoscypher_core.services.sources.engine.extraction.domains.core_types import (
    CORE_LABELS,
    CORE_TYPES,
    FLOOR_LIMITS,
    core_type_of,
    split_qualified_type,
)
from chaoscypher_core.services.sources.engine.extraction.domains.stack import DomainStack
from chaoscypher_core.services.sources.engine.extraction.extraction_plan import (
    build_extraction_plan,
    stored_source_row,
)
from chaoscypher_core.services.sources.engine.extraction.utils.ai_entities import (
    AIEntityExtractor,
    _build_entity_prompt,
)
from chaoscypher_core.services.sources.engine.extraction.utils.entity_cleaner import (
    validate_relationship_type_constraints,
)
from chaoscypher_core.services.sources.engine.extraction.utils.prompts import (
    declared_predicates,
    declared_subtypes,
    declared_type_names,
    example_relationship_endpoints,
)
from chaoscypher_core.settings import EngineSettings
from chaoscypher_core.testing.repo_files import repo_root
from tests.fakes.domain_tables import domain_conventions, domain_vocabulary


_PLUGINS = (
    Path(__file__).parents[7]
    / "src/chaoscypher_core/services/sources/engine/extraction/domains/plugins"
)
_PREDICATE = re.compile(r"(?<![\w-])[a-z]+(?:_[a-z]+)+(?![\w-])")
"""A snake_case token: how guidance names a relationship type."""


@cache
def _registry() -> Any:
    """The product's domain registry."""
    return get_domain_registry(EngineSettings())


def _domains() -> list[str]:
    """Every registered domain."""
    return sorted(str(d["name"]) for d in _registry().list_domain_info())


def _config(name: str) -> dict[str, Any]:
    """A shipped plugin's ``.jsonld``."""
    return json.loads((_PLUGINS / f"{name}.jsonld").read_text(encoding="utf-8"))


def _edges(name: str) -> dict[str, dict[str, Any]]:
    """A domain's edge templates by name, as its config declares them (constraints, inverses)."""
    domain = _registry().get_domain(name)
    return {e["name"]: e for e in domain.config["templates"].get("edge_templates") or []}


def _plan(name: str) -> Any:
    """The plan a stored source of domain ``name`` runs with (the floor plus that pack)."""
    return build_extraction_plan(
        DomainStack.from_domain_name(_registry(), name),
        EngineSettings(),
        source_row=stored_source_row(),
    )


# --- the prompt names only what the domain declares ------------------------------------


@pytest.mark.parametrize("name", _domains())
def test_every_predicate_a_domains_guidance_names_is_its_template(name: str) -> None:
    """The generic guidance named ``preceded_by``/``followed_by`` (its template was ``precedes``).

    A pack's guidance may also name a core label: the floor declares it for every pack.
    """
    domain = _registry().get_domain(name)
    text = f"{domain.get_entity_guidance() or ''}\n{domain.get_relationship_guidance() or ''}"
    examples = domain.get_examples() or {}
    named = set(_PREDICATE.findall(text)) | {
        str(e.get("type")) for e in examples.get("relationship_examples") or [] if e.get("type")
    }
    assert named - set(_edges(name)) - CORE_LABELS == set()


@pytest.mark.parametrize("name", _domains())
def test_the_format_example_is_in_the_domains_own_types_and_passes_its_constraints(
    name: str,
) -> None:
    """The shared prompt's example renders from the domain's declared types and predicates.

    Until 2026-10-06 it showed every domain a Character, an Author and
    ``participates_in``/``commands``/``enemy_of``, which the generic domain
    forbids; each rendered relationship must also survive the domain's strict
    edge constraints (the literary ``commands`` example targets a unit).
    """
    plan = _plan(name)
    kwargs = plan.chunk_kwargs
    nodes, edges = kwargs["node_templates_formatted"], kwargs["edge_templates_formatted"]
    entity_prompt = _build_entity_prompt(
        template=AIEntityExtractor.ENTITY_HARVEST_TEMPLATE,
        numbered_text="S1 text",
        node_templates_formatted=nodes,
        entity_exclusions=None,
        strict_entity_types=False,
        entity_guidance=None,
        entity_examples=None,
    )
    relationship_prompt = AIEntityExtractor(
        settings=EngineSettings()
    ).render_relationship_prompt_template(edges)
    types, predicates = set(declared_type_names(nodes)), set(declared_predicates(edges))
    shown_types = {ln.split("|")[2] for ln in entity_prompt.splitlines() if ln.startswith("E|")}
    shown_predicates = {
        ln.split("|")[3] for ln in relationship_prompt.splitlines() if ln.startswith("R|")
    }
    offered = declared_subtypes(nodes)
    for shown in shown_types:
        qualified = split_qualified_type(shown)
        if qualified is not None:  # the one Core/subtype line: a subtype the list offers
            assert qualified[1] in offered.get(qualified[0], []), (name, shown)
            continue
        assert shown in types | {"<entity type>"}, (name, shown)
    assert shown_predicates <= predicates | {"<relationship type>"}
    constraints = plan.edge_type_constraints or {}
    for source_type, predicate, target_type in example_relationship_endpoints(nodes, edges):
        rule = constraints.get(predicate)
        if rule is None or source_type is None or target_type is None:
            continue
        kept, _stats = validate_relationship_type_constraints(
            [{"source": 0, "target": 1, "type": predicate}],
            [{"name": "a", "type": source_type}, {"name": "b", "type": target_type}],
            {predicate: rule},
            strict_edge_type_constraints=True,
            enable_direction_correction=False,
        )
        assert kept, (name, source_type, predicate, target_type)


def test_the_literary_commands_example_survives_strict_mode() -> None:
    """A margrave commands his lancers: the floor's ``commands`` takes a person and a unit.

    ``commands`` is a core label (spec 2026-10-07 § 2): the floor declares it over the
    core types, a Character being a Person and a Military Unit an Organization.
    """
    constraints = _registry().get_domain("generic").get_edge_type_constraints()["commands"]
    unit = core_type_of("Military Unit")[0]
    assert core_type_of("Character")[0] in constraints["source_types"]
    assert unit in constraints["target_types"]


# --- naming tables: gendered titles and generation words are titles --------------------


@pytest.mark.parametrize("name", _domains())
def test_gendered_titles_and_generation_words_are_title_words(name: str) -> None:
    """Structurally (``NameConventions.build``) and in the ``.jsonld`` a reader of ``title_words`` reads."""
    conv = domain_conventions(name)
    folded_titles = {fold(t) for t in conv.titles}
    assert conv.gendered_titles | set(conv.generation) <= folded_titles
    config = _config(name)
    declared = [*config.get("feminine_titles", []), *config.get("masculine_titles", []),
                *config.get("generation_words", {})]  # fmt: skip
    if declared:
        assert set(declared) <= set(config["title_words"]), sorted(
            set(declared) - set(config["title_words"])
        )


def test_the_literary_title_tables_hold_the_missing_titles_and_their_spellings() -> None:
    """vicomte, marquis, earl, duc and the rest; madam, vicomte/viscount, duc/duke one title each."""
    literary = domain_conventions("literary")
    assert {"vicomte", "vicomtesse", "marquis", "marquise", "earl", "duc", "frau", "herr"} <= (
        literary.titles
    )
    same = [
        ("madam", "mme"),
        ("vicomte", "viscount"),
        ("duc", "duke"),
        ("duchesse", "duchess"),
    ]
    for a, b in same:
        assert literary.title_class[a] == literary.title_class[b], (a, b)
    records = [NameRecord("Madam Duval", "C"), NameRecord("Mme Duval", "C")]
    assert plan_name_pairs(records, conventions=literary) != []


# --- literary: genre-level edges, vocabulary, constraints, limits ----------------------


GENRE_EDGES = {
    "godparent_of": "godchild_of",
    "godchild_of": "godparent_of",
    "betrothed_to": None,
    "patron_of": None,
    "heir_of": None,
    "hosts": None,
}


@pytest.mark.parametrize("edge", list(GENRE_EDGES))
def test_the_literary_genre_edges_exist_with_their_vocabulary(edge: str) -> None:
    """Each new edge is a template, its inverse declared, and the vocabulary has words for it.

    ``hosts`` is a core label since 2026-10-07: the floor declares it two-endpoints-only,
    and since 2026-10-08 literary gives it no words (a pack cannot loosen a floor rule).
    """
    edges = {**_edges("generic"), **_edges("literary")}
    assert edge in edges
    assert (edge in _edges("generic")) == (edge in CORE_LABELS)
    if GENRE_EDGES[edge]:
        assert edges[edge].get("inverse") == GENRE_EDGES[edge]
    stated = bool(domain_vocabulary("literary").relation_words(edge))
    assert stated == (not edges[edge].get("two_endpoints_only")), edge
    assert edges["hosts"]["target_types"] == ["Event"]
    assert edges["betrothed_to"].get("symmetric") is True


def test_hyphenated_role_words_make_descriptors() -> None:
    """``aide-de-camp`` and the ``-in-law`` words are one word each (B1)."""
    literary = domain_conventions("literary")
    assert is_descriptor("the aide-de-camp of General Kutúzov", literary)
    assert is_descriptor("the sister-in-law of the little princess", literary)
    assert not is_descriptor("the sister of", literary)


def test_the_widened_constraints_keep_the_v3_edge_shapes() -> None:
    """related_to takes families; commands units and groups; serves units; possesses estates.

    related_to, commands and serves are core labels (the floor's, over the core types);
    possesses is literary's own.
    """
    floor = _registry().get_domain("generic").get_edge_type_constraints()
    literary = _registry().get_domain("literary").get_edge_type_constraints()
    unit, group = core_type_of("Military Unit")[0], core_type_of("Social Group")[0]
    # the fallback takes any two entities, declared so strict constraints keep it
    assert floor["related_to"] == {
        "source_types": list(CORE_TYPES),
        "target_types": list(CORE_TYPES),
    }
    assert {unit, group} <= set(floor["commands"]["target_types"])
    assert unit in floor["serves"]["target_types"]
    assert "Location" in literary["possesses"]["target_types"]


def test_the_literary_limits_preset_and_exclusions() -> None:
    """Degree caps above v3's hubs (32); the dead ``lenient`` key gone; no corpus phrase."""
    config = _config("literary")
    assert config["extraction_limits"]["max_entity_degree"] == 80
    assert config["extraction_limits"]["max_same_source_type"] == 20
    assert "extraction_filtering_mode" not in config
    assert _plan("literary").filtering_mode == "balanced"
    text = json.dumps(config, ensure_ascii=False)
    for phrase in ("scarlet-liveried", "freemasonry", "orthodox"):
        assert phrase not in text
    # a pack declares no node templates and no normalization keywords (spec 2026-10-07 § 3)
    assert "node_templates" not in config["templates"]
    keywords = {
        n["name"]: n.get("normalization_keywords") or []
        for n in _config("generic")["templates"]["node_templates"]
    }
    assert not {"icon", "inheritance"} & set(keywords["Object"])


def test_the_literary_guidance_covers_events() -> None:
    """Receptions, balls, dinners, name days, duels are Events with a host, a date and a place."""
    domain = _registry().get_domain("literary")
    guidance = domain.get_entity_guidance() or ""
    for word in ("Receptions", "balls", "dinners", "name days", "duels", "host, date, place"):
        assert word in guidance
    # Event is a core type: literary adds its properties to it (core_properties)
    assert [p["name"] for p in domain.core_properties("Event")] == ["host", "date", "place"]


# --- generic: honest at its best, without literary's genre tables ---------------------


def test_the_generic_domain_has_person_kin_and_social_edges() -> None:
    """parent_of/child_of, sibling_of, spouse_of, friend_of, member_of, each with kin words."""
    edges = _edges("generic")
    assert edges["parent_of"]["inverse"] == "child_of"
    assert edges["child_of"]["inverse"] == "parent_of"
    for label in ("sibling_of", "spouse_of", "friend_of"):
        assert edges[label].get("symmetric") is True
        assert domain_vocabulary("generic").relation_words(label)
    assert "Person" in edges["member_of"]["source_types"]
    # the floor's limits are core_types.FLOOR_LIMITS, read through the domain
    assert _registry().get_domain("generic").get_extraction_limits() == dict(FLOOR_LIMITS)
    assert FLOOR_LIMITS["max_entity_degree"] == 80


def test_the_generic_domain_has_none_of_literarys_genre_tables() -> None:
    """The A/B measures the specialization: no old/young, genre titles or Russian forms.

    The language-level titles (``princess``, ``monsieur``) are the universal
    floor every domain starts from (audit wave 7), never literary's own.
    """
    config = _config("generic")
    for table in ("feminine_titles", "masculine_titles", "generation_words", "name_forms",
                  "title_equivalents", "role_words", "relation_vocabulary"):  # fmt: skip
        assert table not in config, table
    generic = domain_conventions("generic")
    assert not generic.name_forms
    assert "old" not in generic.generation and "dowager" not in generic.titles
    assert "vicomtesse" not in generic.feminine_titles
    assert "princess" in generic.titles and "princess" in generic.feminine_titles


def test_the_shared_prompt_keeps_a_bare_title() -> None:
    """A person called only by a title is written as the passage calls them (keep, flag, never delete).

    The rule that once made the extractor skip one ("the abbé", the q131 trap)
    is gone: the pipeline keeps the reference and folds or flags it, and the
    floor's TITLES rule, rendered for every domain, says so.
    """
    assert "A bare title or role with no name" not in AIEntityExtractor.ENTITY_HARVEST_TEMPLATE
    assert "- TITLES, ROLES AND REFERENCES:" in (
        _registry().get_domain("generic").get_entity_guidance() or ""
    )
    assert "never invent a new one" in AIEntityExtractor.RELATIONSHIP_HARVEST_TEMPLATE
    for name in _domains():
        assert "UNIVERSAL RULES" not in (
            _registry().get_domain(name).get_relationship_guidance() or ""
        )


_V3_PACK = repo_root() / "internal/benchmarks/reference/war_and_peace_book1-v3/app.db"


@pytest.mark.skipif(not _V3_PACK.exists(), reason="the v3 reference pack is not in this tree")
def test_strict_constraints_over_the_v3_graph_drop_only_type_mistakes() -> None:
    """Read-only replay: strict mode over v3's 252 edges, its types read as core types.

    Until 2026-10-07 literary's own constraints dropped 15 (21 before the
    widening). Now the node types are the core types the migration gives them
    (``core_type_of``) and the constraints are the floor's plus literary's:
    9 refused, every one the extraction's own type mistake (a cause or a country
    joined as a group, a family typed as a person); a person related to a
    family, a family to a family and an estate possessed pass. Since
    2026-10-08 (keep, flag, never delete) the 9 are kept, flagged
    ``type_mismatch``, and none is dropped.
    """
    import sqlite3

    from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
        TYPE_MISMATCH_KEY,
        pipeline_flag,
    )

    with sqlite3.connect(f"file:{_V3_PACK}?mode=ro", uri=True) as conn:
        nodes = conn.execute("SELECT id, label, entity_type FROM graph_nodes").fetchall()
        edges = conn.execute(
            "SELECT source_node_id, target_node_id, label FROM graph_edges"
        ).fetchall()
    position = {nid: i for i, (nid, _l, _t) in enumerate(nodes)}
    entities = [{"name": label, "type": core_type_of(etype)[0]} for _id, label, etype in nodes]
    relationships = [
        {"source": position[s], "target": position[t], "type": label} for s, t, label in edges
    ]
    constraints = {
        **_registry().get_domain("generic").get_edge_type_constraints(),
        **_registry().get_domain("literary").get_edge_type_constraints(),
    }
    kept, stats = validate_relationship_type_constraints(
        relationships, entities, constraints, strict_edge_type_constraints=True
    )
    assert len(relationships) == 252
    assert len(kept) == 252
    refused = [r for r in kept if pipeline_flag(r.get("properties"), TYPE_MISMATCH_KEY)]
    assert len(refused) == stats["type_mismatch"] == 9, stats
    assert stats["unmatched_label"] == 0
    kept_shapes = {
        (entities[r["source"]]["type"], r["type"], entities[r["target"]]["type"])
        for r in kept
        if r not in refused
    }
    assert ("Person", "related_to", "Group") in kept_shapes
    assert ("Group", "related_to", "Group") in kept_shapes
    assert ("Person", "possesses", "Location") in kept_shapes
