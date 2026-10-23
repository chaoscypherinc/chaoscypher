# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Strict core types with rescue (spec 2026-10-07 § 6): every entity leaves the per-chunk filters core-typed."""

from __future__ import annotations

from typing import Any

import pytest

from chaoscypher_core.services.sources.engine.extraction.domains.core_types import CORE_TYPES
from chaoscypher_core.services.sources.engine.extraction.domains.factory import get_domain_registry
from chaoscypher_core.services.sources.engine.extraction.domains.stack import DomainStack
from chaoscypher_core.services.sources.engine.extraction.extraction_plan import (
    build_extraction_plan,
    chunk_kwargs_from_config,
    chunk_kwargs_to_config,
)
from chaoscypher_core.services.sources.engine.extraction.utils.ai_entities import _filter_entities
from chaoscypher_core.services.sources.engine.extraction.utils.constants import (
    UNKNOWN_ENTITY_TYPE,
)
from chaoscypher_core.services.sources.engine.extraction.utils.entity_cleaner import (
    validate_relationship_type_constraints,
)
from chaoscypher_core.services.sources.engine.extraction.utils.filtering_config import (
    FilteringConfig,
    resolve_filtering_config,
)
from chaoscypher_core.services.sources.engine.extraction.utils.filtering_log import FilteringLog
from chaoscypher_core.services.sources.engine.extraction.utils.line_parser import (
    parse_extraction_output,
)
from chaoscypher_core.services.sources.engine.extraction.utils.type_normalizer import (
    _DEFAULT_GENERIC_TYPES,
    apply_type_aliases,
    normalize_entity_types,
)
from chaoscypher_core.services.sources.engine.extraction.utils.type_rescue import (
    Rescue,
    TypeVocabulary,
    rescue_invalid_entity_types,
    rescue_type,
)
from chaoscypher_core.settings import EngineSettings


@pytest.fixture(scope="module")
def registry() -> Any:
    return get_domain_registry()


@pytest.fixture(scope="module")
def literary(registry: Any) -> DomainStack:
    return DomainStack.resolve(registry, ["literary"])


@pytest.fixture(scope="module")
def floor(registry: Any) -> DomainStack:
    return DomainStack.resolve(registry, [])


def _stage(log: FilteringLog, name: str) -> dict[str, Any]:
    stages = [s for s in log.to_dict()["stages"] if s["stage"] == name]
    assert len(stages) == 1, log.to_dict()
    return stages[0]


# --------------------------------------------------------------------------- rescue_type


def test_a_written_subtype_name_is_rescued_to_its_core_type(literary: DomainStack) -> None:
    assert rescue_type("Character", literary) == ("Person", "character")
    assert rescue_type("Military Unit", literary) == ("Organization", "military_unit")
    # The subtype key as the prompt lists it, and any case, name the same subtype.
    assert rescue_type("military_unit", literary) == ("Organization", "military_unit")
    assert rescue_type("CHARACTER", literary) == ("Person", "character")


def test_an_exact_core_type_is_itself(literary: DomainStack, floor: DomainStack) -> None:
    for core in CORE_TYPES:
        assert rescue_type(core, literary) == (core, None)
        assert rescue_type(core.lower(), floor) == (core, None)


def test_a_pack_alias_wins_over_the_cross_pack_table(registry: Any, literary: DomainStack) -> None:
    # literary aliases Historical Figure to its Character subtype; the table alone says historical_figure.
    assert rescue_type("Historical Figure", literary) == ("Person", "character")
    assert rescue_type("Historical Figure", DomainStack.resolve(registry, [])) == (
        "Person",
        "historical_figure",
    )
    # An alias to a core type keeps the table's subtype of the written name when it is a kind of it.
    assert rescue_type("Historical Event", literary) == ("Event", "historical_event")
    # reference: Setting is a Configuration Item there, a Location (literary's Setting) elsewhere.
    reference = DomainStack.resolve(registry, ["reference"])
    assert rescue_type("Setting", reference) == ("Object", "configuration_item")


def test_a_pack_qualified_row_wins_over_the_plain_one(registry: Any) -> None:
    news = DomainStack.resolve(registry, ["news"])
    assert rescue_type("Publication", news) == ("Organization", "publication")
    assert rescue_type("Publication", DomainStack.resolve(registry, [])) == ("Work", "publication")


def test_a_name_no_table_knows_is_rescued_by_keywords_or_none(floor: DomainStack) -> None:
    assert rescue_type("Noble Family", floor) == ("Group", None)
    assert rescue_type("Xyzzy", floor) is None
    assert rescue_type("Xyzzy", floor, description="A small city on the river.") == (
        "Location",
        None,
    )
    # A keyword is a whole word: "he" is no Person keyword inside "the" or "scheme".
    assert rescue_type("Scheme", floor, description="the plan of the war") is None
    # An empty type column states no type: only the description can rescue it.
    assert rescue_type(UNKNOWN_ENTITY_TYPE, floor) is None
    assert rescue_type("", floor) is None


_TEN = [
    "Battle",
    "War",
    "Army",
    "Regiment",
    "Prince",
    "Countess",
    "Emperor",
    "Serf",
    "Nation",
    "Ship",
]


def test_the_packs_rescue_keywords_rescue_what_the_old_packs_rescued(registry: Any) -> None:
    """Review 2026-10-07 (a): the ten written types that dropped once the packs lost their keywords.

    Under literary, the five its original templates' keywords named are
    rescued again (literary's Event ``battle``, Social Group ``army`` and
    ``regiment``, Character ``prince`` and ``countess``). The other five no
    literary template ever named (the old literary rescue dropped ``War``,
    ``Emperor``, ``Serf`` and ``Ship`` too, and reached ``Nation`` only by the
    substring "examination of" in Theme's keywords): ``War`` and ``Emperor``
    are historical's keywords and ``Nation`` political's, so each is rescued
    under the pack that named it; ``Serf`` and ``Ship`` were never any shipped
    template's keyword and drop under every stack.
    """
    literary = TypeVocabulary.from_stack(DomainStack.resolve(registry, ["literary"]))
    assert {name: literary.rescue(name) for name in _TEN} == {
        "Battle": ("Event", None),
        "Army": ("Group", "social_group"),
        "Regiment": ("Group", "social_group"),
        "Prince": ("Person", "character"),
        "Countess": ("Person", "character"),
        "War": None,
        "Emperor": None,
        "Serf": None,
        "Nation": None,
        "Ship": None,
    }
    historical = DomainStack.resolve(registry, ["historical"])
    assert rescue_type("War", historical) == ("Event", "historical_event")
    assert rescue_type("Emperor", historical) == ("Person", "historical_figure")
    assert rescue_type("Nation", DomainStack.resolve(registry, ["political"])) == (
        "Location",
        "political_entity",
    )
    # The floor alone has none of these words: the packs' keywords are the packs'.
    floor_only = TypeVocabulary.from_stack(DomainStack.resolve(registry, []))
    assert all(floor_only.rescue(name) is None for name in _TEN)


def test_the_pack_keywords_come_after_the_table_and_before_the_floor(literary: DomainStack) -> None:
    vocabulary = TypeVocabulary.from_stack(literary)
    # The table first: Setting is a mapped name, whatever a keyword says.
    assert vocabulary.decide("Setting").tier == "mapped"  # type: ignore[union-attr]
    # A pack keyword over the floor's: "city" is both literary's Location word and the floor's.
    decision = vocabulary.decide("Quux", description="A city on the river.")
    assert decision is not None
    assert (decision.tier, decision.core) == ("pack_keyword", "Location")
    # The floor's keywords when no pack word is written.
    decision = vocabulary.decide("Quux", description="Something that happened.")
    assert decision is not None
    assert (decision.tier, decision.core) == ("keyword", "Event")


def test_a_pronoun_never_rescues_a_type(floor: DomainStack, literary: DomainStack) -> None:
    """Review 2026-10-07 (b): ``he``, ``she``, ``a man``, ``a woman`` are no Person keywords."""
    for stack in (floor, literary):
        assert rescue_type(UNKNOWN_ENTITY_TYPE, stack, description="She sang.") is None
        assert rescue_type("Xyzzy", stack, description="a city he loved") == ("Location", None)
        assert rescue_type("Xyzzy", stack, description="A man of the old school.") is None
    # The literary pack's own "ball" makes the singer's ball an Event, never a Person.
    assert rescue_type(UNKNOWN_ENTITY_TYPE, floor, description="She sang at the ball.") is None
    assert rescue_type(UNKNOWN_ENTITY_TYPE, literary, description="She sang at the ball.") == (
        "Event",
        None,
    )


@pytest.mark.asyncio
async def test_rescue_type_and_the_pipeline_are_one_decision(floor: DomainStack) -> None:
    """Review 2026-10-07 I1: what ``rescue_type`` says of a record is what the per-chunk filter does."""
    records = [
        {"name": "the count", "type": UNKNOWN_ENTITY_TYPE, "description": ""},
        {"name": "the count", "type": UNKNOWN_ENTITY_TYPE, "description": "Went to the Archives."},
        {"name": "Natasha's mother", "type": "", "description": "Wept at the window."},
        {"name": "the army", "type": UNKNOWN_ENTITY_TYPE, "description": "Marched at dawn."},
        {"name": "Lise", "type": "Character", "description": ""},
        {"name": "Glorp", "type": "Quux", "description": "a city"},
        {"name": "Glorp", "type": "Quux", "description": ""},
    ]
    vocabulary = TypeVocabulary.from_stack(floor)
    for record in records:
        said = rescue_type(record["type"], vocabulary, record=record)
        kept, _rels, _mapping, _stats = rescue_invalid_entity_types(
            [dict(record)], [], vocabulary, salvage=False
        )
        # no core type answers (None): the pipeline keeps it as a Concept flagged unplaced_type
        unplaced = bool(kept) and (kept[0].get("properties") or {}).get("unplaced_type") is True
        done = (
            (kept[0]["type"], (kept[0].get("properties") or {}).get("entity_subtype"))
            if kept and not unplaced
            else None
        )
        assert said == done, record
        assert unplaced == (said is None), record


def test_the_subtype_key_form_is_a_table_name() -> None:
    """Review 2026-10-07 M1: one key reading for the table (``military_unit`` is ``Military Unit``)."""
    from chaoscypher_core.services.sources.engine.extraction.domains.core_types import core_type_of

    assert core_type_of("military_unit") == ("Organization", "military_unit")
    assert core_type_of("Publication", ["literary", "news"]) == ("Organization", "publication")
    assert core_type_of("Publication", ["literary"]) == ("Work", "publication")


def test_the_vocabulary_round_trips_through_the_plan(literary: DomainStack) -> None:
    plan = build_extraction_plan(literary, EngineSettings())
    assert plan.chunk_kwargs["valid_entity_type_names"] == set(CORE_TYPES)
    restored = chunk_kwargs_from_config(chunk_kwargs_to_config(plan.chunk_kwargs))
    assert restored["type_vocabulary"] == plan.chunk_kwargs["type_vocabulary"]
    vocabulary = TypeVocabulary.from_config(
        restored["type_vocabulary"],
        types=restored["valid_entity_type_names"],
        keywords=restored["normalization_rules"],
    )
    assert vocabulary.rescue("Character") == ("Person", "character")
    assert vocabulary.rescue("Historical Figure") == ("Person", "character")
    assert vocabulary == TypeVocabulary.from_stack(literary)


# --------------------------------------------------------------------------- the per-chunk filter


def _off_but_rescue(**overrides: Any) -> FilteringConfig:
    return FilteringConfig(
        evidence_validation_mode="off",
        enable_entity_exclusions=False,
        enable_plausibility_filter=False,
        **overrides,
    )


async def _filter(
    entities: list[dict[str, Any]],
    stack: DomainStack,
    config: FilteringConfig,
    log: FilteringLog,
) -> list[dict[str, Any]]:
    plan = build_extraction_plan(stack, EngineSettings())
    kwargs = plan.chunk_kwargs
    filtered, _stats = await _filter_entities(
        entities=entities,
        sentences=[],
        chunk_content="",
        filtering_config=config,
        entity_exclusions=None,
        valid_entity_type_names=kwargs["valid_entity_type_names"],
        named_referent_types=kwargs["named_referent_types"],
        normalization_rules=kwargs["normalization_rules"],
        property_type_mapping=kwargs["property_type_mapping"],
        type_vocabulary=TypeVocabulary.from_config(kwargs["type_vocabulary"]),
        filtering_log=log,
    )
    return filtered


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["unfiltered", "minimal", "balanced", "strict"])
async def test_an_unknown_written_type_is_rescued_by_keywords_or_kept_flagged_never_unknown(
    literary: DomainStack, mode: str
) -> None:
    entities = [
        {"name": "Pierre Bezukhov", "type": "Character", "description": "A count."},
        {"name": "Moscow", "type": UNKNOWN_ENTITY_TYPE, "description": "The old city."},
        {"name": "Rostov Family", "type": "Noble Family", "description": ""},
        {"name": "Glorp", "type": UNKNOWN_ENTITY_TYPE, "description": ""},
        {"name": "Zyx", "type": "Quux", "description": "Nothing said."},
    ]
    # The preset's own rescue switch, its other entity filters off (no sentences to cite).
    config = resolve_filtering_config(mode)
    config.evidence_validation_mode = "off"
    config.enable_entity_exclusions = False
    config.enable_plausibility_filter = False
    log = FilteringLog()
    filtered = await _filter(entities, literary, config, log)

    assert all(e["type"] in CORE_TYPES for e in filtered), filtered
    by_name = {e["name"]: e for e in filtered}
    assert by_name["Pierre Bezukhov"]["type"] == "Person"
    assert by_name["Pierre Bezukhov"]["properties"]["entity_subtype"] == "character"
    assert by_name["Moscow"]["type"] == "Location"
    assert "entity_subtype" not in (by_name["Moscow"].get("properties") or {})
    assert by_name["Rostov Family"]["type"] == "Group"
    # keep, flag, never delete (2026-10-07): no core type answers, so a Concept, flagged
    assert by_name["Glorp"]["type"] == "Concept"
    assert by_name["Glorp"]["properties"] == {"unplaced_type": True, "written_type": "UNKNOWN"}
    assert by_name["Zyx"]["properties"] == {"unplaced_type": True, "written_type": "Quux"}
    assert by_name["Zyx"]["type_rescued_from"] == "Quux"
    stage = _stage(log, "type_rescue")
    assert stage["counts"]["unknown_type"] == 2
    assert stage["removed_count"] == 0
    flagged = {i["name"]: (i["details"]["tier"], i["details"]["kept"]) for i in stage["items"]}
    assert flagged == {"Glorp": ("unknown_type", True), "Zyx": ("unknown_type", True)}


@pytest.mark.asyncio
async def test_a_subtype_the_record_carries_is_kept(literary: DomainStack) -> None:
    entities = [
        {
            "name": "Prince Andrew",
            "type": "Character",
            "description": "A prince.",
            "properties": {"entity_subtype": "narrator"},
        }
    ]
    filtered = await _filter(entities, literary, _off_but_rescue(), FilteringLog())
    assert filtered[0]["type"] == "Person"
    assert filtered[0]["properties"]["entity_subtype"] == "narrator"
    assert filtered[0]["type_rescued_from"] == "Character"


@pytest.mark.asyncio
async def test_an_untyped_reference_is_a_person_when_its_words_say_one(
    floor: DomainStack,
) -> None:
    entities = [
        # A bare title with a fact of its own and an empty type column: a person.
        {"name": "the count", "type": UNKNOWN_ENTITY_TYPE, "description": "Went to the Archives."},
        # A relation of a named person.
        {"name": "Natasha's mother", "type": "", "description": "Wept at the window."},
        # A reference whose words name no person, and a person reference that says nothing.
        {"name": "the army", "type": UNKNOWN_ENTITY_TYPE, "description": "Marched at dawn."},
        {"name": "the count", "type": UNKNOWN_ENTITY_TYPE, "description": ""},
    ]
    log = FilteringLog()
    filtered = await _filter(entities, floor, _off_but_rescue(), log)
    assert [(e["name"], e["type"]) for e in filtered] == [
        ("the count", "Person"),
        ("Natasha's mother", "Person"),
        # no core type answers: kept as a Concept flagged unplaced_type (2026-10-07)
        ("the army", "Concept"),
        ("the count", "Concept"),
    ]
    assert [e["properties"]["unplaced_type"] for e in filtered[2:]] == [True, True]
    assert _stage(log, "type_rescue")["counts"]["unknown_type"] == 2


@pytest.mark.asyncio
async def test_salvage_off_still_rescues_types(literary: DomainStack) -> None:
    """``enable_type_rescue=False`` turns off the junk and absorption tiers, never the core-type rescue."""
    entities = [
        {"name": "Character", "type": "Character", "description": ""},
        {"name": "he", "type": "Character", "description": ""},
    ]
    filtered = await _filter(
        entities, literary, _off_but_rescue(enable_type_rescue=False), FilteringLog()
    )
    assert [(e["name"], e["type"]) for e in filtered] == [("Character", "Person"), ("he", "Person")]
    filtered = await _filter(entities, literary, _off_but_rescue(), FilteringLog())
    assert filtered == []


def test_a_caller_type_list_is_what_entities_leave_with() -> None:
    """A probe's own type list (``valid_entity_type_names``) replaces the core types.

    With no ``Concept`` in it there is nowhere to keep an entity no listed type answers to:
    it is dropped and counted (the core types always list ``Concept``: kept flagged there).
    """
    vocabulary = TypeVocabulary(types=("Character", "Place"))
    entities = [
        {"name": "Pierre", "type": "Character"},
        {"name": "Moscow", "type": "City", "description": "a city"},
        {"name": "Borodino", "type": "Battle"},
    ]
    rescued, _rels, _mapping, stats = rescue_invalid_entity_types(entities, [], vocabulary)
    assert [(e["name"], e["type"]) for e in rescued] == [("Pierre", "Character")]
    assert stats["unknown_type"] == 2


# --------------------------------------------------------------------------- aliases at finalize


def test_apply_type_aliases_writes_the_subtype_from_the_mapping_table() -> None:
    entities: list[dict[str, Any]] = [
        {"name": "Funke", "type": "Historical Figure"},
        {"name": "Austerlitz", "type": "Historical Event"},
        {"name": "Pierre", "type": "Historical Figure", "properties": {"entity_subtype": "x"}},
        {"name": "Lise", "type": "Character"},
        {"name": "Moscow", "type": "Location"},
    ]
    aliases = {"Historical Figure": "Character", "Historical Event": "Event"}
    assert apply_type_aliases(entities, aliases, packs=("literary",)) == 4
    assert [(e["type"], (e.get("properties") or {}).get("entity_subtype")) for e in entities] == [
        ("Person", "character"),
        ("Event", "historical_event"),
        ("Person", "x"),
        ("Person", "character"),
        ("Location", None),
    ]
    # Idempotent: a core-typed record is not rewritten again.
    assert apply_type_aliases(entities, aliases, packs=("literary",)) == 0


def test_the_generic_types_are_no_core_types() -> None:
    assert {"UNKNOWN", "Unknown", "Thing", "Entity"} == _DEFAULT_GENERIC_TYPES
    rules = {"Person": ["person", "he"], "Object": ["thing"]}
    entities = [
        {"name": "Hope", "type": "Concept", "description": "the person he loved"},
        {"name": "Sword", "type": "Object", "description": "the person he loved"},
        {"name": "X", "type": "Thing", "description": "the person"},
    ]
    # A floor template flagged generic is still a core type: the normalizer never retypes one.
    normalize_entity_types(entities, rules, generic_types={"Concept", "Object", "Thing"})
    assert [e["type"] for e in entities] == ["Concept", "Object", "Person"]


# --------------------------------------------------------------------------- edge constraints


def test_edge_constraints_read_core_types(literary: DomainStack) -> None:
    constraints = literary.edge_type_constraints()
    assert constraints["child_of"] == {"source_types": ["Person"], "target_types": ["Person"]}
    entities = [
        {"name": "Natasha", "type": "Person", "properties": {"entity_subtype": "character"}},
        {"name": "Count Rostov", "type": "Person"},
        {"name": "Moscow", "type": "Location"},
    ]
    relationships = [
        {"source": 0, "target": 1, "type": "child_of"},
        {"source": 2, "target": 1, "type": "child_of"},
    ]
    log = FilteringLog()
    kept, stats = validate_relationship_type_constraints(
        relationships,
        entities,
        constraints,
        log,
        strict_edge_type_constraints=True,
    )
    # keep, flag, never delete (2026-10-08): the refused one is kept, flagged with the reason
    assert [(r["source"], r["target"]) for r in kept] == [(0, 1), (2, 1)]
    assert "properties" not in kept[0]
    assert kept[1]["properties"]["type_mismatch"] is True
    assert stats["type_mismatch"] == 1
    stage = _stage(log, "relationship_type_constraint")
    assert stage["items"][0]["reason"] == (
        "child_of takes a source of type Person, not Location: kept flagged"
    )


def test_edge_constraints_compare_a_mapped_name_by_its_core_type() -> None:
    """A record still carrying an old template name is compared by the core type the table gives it."""
    entities = [{"name": "Natasha", "type": "Character"}, {"name": "Lise", "type": "Person"}]
    constraints = {"child_of": {"source_types": ["Person"], "target_types": ["Person"]}}
    kept, _stats = validate_relationship_type_constraints(
        [{"source": 0, "target": 1, "type": "child_of"}],
        entities,
        constraints,
        strict_edge_type_constraints=True,
    )
    assert len(kept) == 1
    # No substring guess: "Personality" is no Person.
    entities[0]["type"] = "Personality"
    kept, _stats = validate_relationship_type_constraints(
        [{"source": 0, "target": 1, "type": "child_of"}],
        entities,
        constraints,
        strict_edge_type_constraints=True,
    )
    assert kept[0]["properties"]["type_mismatch"] is True


def test_core_typed_places_a_stored_node_on_its_core_type() -> None:
    """The one placing of a stored node's type (the commit, the ccx import): table, packs, unplaced."""
    from chaoscypher_core.services.sources.engine.extraction.utils.type_rescue import core_typed

    old = {"type": "Military Unit", "properties": {"rank": "regiment"}}
    placed = core_typed(old)
    assert placed["type"] == "Organization"
    assert placed["properties"] == {"rank": "regiment", "entity_subtype": "military_unit"}
    assert old == {"type": "Military Unit", "properties": {"rank": "regiment"}}  # not modified

    # a carried subtype wins over the table's
    carried = core_typed({"type": "Character", "properties": {"entity_subtype": "narrator"}})
    assert (carried["type"], carried["properties"]["entity_subtype"]) == ("Person", "narrator")

    # the packs' pack-qualified rows are read first, then the plain row
    assert core_typed({"type": "Publication"})["type"] == "Work"
    assert core_typed({"type": "Publication"}, ["news"])["type"] == "Organization"

    # a name the table does not hold: Concept, flagged, the written type kept
    unplaced = core_typed({"type": "Quux", "properties": {}})
    assert unplaced["type"] == "Concept"
    assert unplaced["properties"] == {"unplaced_type": True, "written_type": "Quux"}

    # already on the core types, or untyped: as it was
    core = {"type": "Person", "properties": {"entity_subtype": "character"}}
    assert core_typed(core) == core
    assert core_typed({"type": None, "name": "x"}) == {"type": None, "name": "x"}


# --------------------------------------------------------------------------- Core/subtype


@pytest.mark.parametrize(
    ("written", "expected"),
    [
        # a subtype the packs offer under the written core type
        ("Person/character", Rescue("subtyped", "Person", "character")),
        ("Organization/military_unit", Rescue("subtyped", "Organization", "military_unit")),
        ("person/Military Unit", Rescue("subtyped", "Organization", "military_unit")),
        # a subtype the packs offer under another core type: that subtype's own core type
        ("Location/character", Rescue("subtyped", "Person", "character")),
        # a subtype no pack offers: the written core type, kept flagged
        ("Person/wizard", Rescue("unplaced_subtype", "Person", None, unplaced=True)),
        # a bare core type, as before
        ("Person", Rescue("listed", "Person")),
    ],
)
def test_a_written_core_slash_subtype_is_one_decision(
    literary: DomainStack, written: str, expected: Rescue
) -> None:
    """``Core/subtype`` (the form the node lines teach) is decided once, the same by every reader."""
    vocabulary = TypeVocabulary.from_stack(literary)
    assert vocabulary.decide(written) == expected
    assert vocabulary.decide(written, record={"name": "Pierre"}, salvage=True) == expected
    assert vocabulary.place(written) == expected


@pytest.mark.asyncio
async def test_a_parsed_subtype_line_is_stored_as_its_core_type_and_subtype(
    literary: DomainStack,
) -> None:
    """Through the real line parser and the per-chunk filter: Person/character, flagged when unoffered."""
    entities, _rels, _props = parse_extraction_output(
        entities_str="\n".join(
            [
                "E|Pierre|Person/character||0.9|S1|A count's son.",
                "E|The Lancers|Location/military_unit||0.9|S1|A regiment.",
                "E|Mavra|Person/wizard||0.9|S1|A housekeeper.",
                "E|Moscow|Location||1.0|S1|The old capital.",
            ]
        ),
        relationships_str="",
    )
    assert [e["type"] for e in entities] == [
        "Person/character",
        "Location/military_unit",
        "Person/wizard",
        "Location",
    ]
    log = FilteringLog()
    filtered = await _filter(entities, literary, _off_but_rescue(), log)
    by_name = {e["name"]: e for e in filtered}
    assert (by_name["Pierre"]["type"], by_name["Pierre"]["properties"]) == (
        "Person",
        {"entity_subtype": "character"},
    )
    assert by_name["The Lancers"]["type"] == "Organization"
    assert by_name["The Lancers"]["properties"] == {"entity_subtype": "military_unit"}
    assert by_name["Mavra"]["type"] == "Person"
    assert by_name["Mavra"]["properties"] == {
        "unplaced_type": True,
        "written_type": "Person/wizard",
    }
    assert "properties" not in by_name["Moscow"] or not by_name["Moscow"]["properties"]
    stage = _stage(log, "type_rescue")
    assert (stage["counts"]["subtyped"], stage["counts"]["unplaced_subtype"]) == (2, 1)
    assert stage["counts"]["unknown_type"] == 0
    assert stage["removed_count"] == 0


def test_the_finalize_and_commit_readers_place_core_slash_subtype(literary: DomainStack) -> None:
    """The finalize rewrite (the MCP path's only one) and the commit read it by the same decision."""
    from chaoscypher_core.services.sources.engine.extraction.utils.type_normalizer import (
        canonicalize_entity_types,
    )
    from chaoscypher_core.services.sources.engine.extraction.utils.type_rescue import core_typed

    entities = [{"type": "Person/character"}, {"type": "Person/wizard"}, {"type": "Person"}]
    assert canonicalize_entity_types(entities, TypeVocabulary.from_stack(literary)) == 2
    assert entities[0] == {"type": "Person", "properties": {"entity_subtype": "character"}}
    assert entities[1]["type"] == "Person"
    assert entities[1]["properties"] == {"unplaced_type": True, "written_type": "Person/wizard"}
    assert entities[2] == {"type": "Person"}
    # the commit offers no pack subtype: one the extraction did not place is kept flagged
    placed = core_typed({"type": "Person/character", "properties": {}})
    assert placed["type"] == "Person"
    assert placed["properties"] == {"unplaced_type": True, "written_type": "Person/character"}


def test_core_slash_subtype_compares_as_its_core_type() -> None:
    """Ahead of the rescue (the evidence filter's person test), ``Person/character`` is a person."""
    from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
        DEFAULT_NAME_CONVENTIONS,
    )
    from chaoscypher_core.services.sources.engine.deduplication.similarity_matcher import (
        core_type_key,
    )

    assert core_type_key("Person/character") == "Person"
    assert core_type_key("Location/character") == "Location"
    assert DEFAULT_NAME_CONVENTIONS.is_person("Person/character")
    assert core_type_key("Wizard/thing") == "wizard/thing"  # no core type before the slash
