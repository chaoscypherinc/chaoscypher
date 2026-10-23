# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Every shipped plugin is a floor or a pack that only adds (spec 2026-10-07 § 3)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from chaoscypher_core.services.sources.engine.extraction.domains.config_schema import (
    DomainConfigModel,
)
from chaoscypher_core.services.sources.engine.extraction.domains.convert_packs import (
    SUBTYPE_SPLITS,
    apply_mapping_splits,
    convert_config,
    core_edge_templates,
    main,
    pending_conversions,
    restore_rescue_keywords,
    split_keywords,
)
from chaoscypher_core.services.sources.engine.extraction.domains.core_types import (
    CORE_LABELS,
    CORE_TYPES,
    FLOOR_LIMITS,
    FLOOR_NAME,
    NOT_RESCUE_KEYWORDS,
    PACK_ALIASES,
    core_type_of,
    is_core_type,
    subtype_key,
)
from chaoscypher_core.services.sources.engine.extraction.domains.registry import DomainRegistry
from chaoscypher_core.services.sources.engine.extraction.domains.stack import DomainStack
from chaoscypher_core.services.sources.engine.extraction.utils.line_parser import (
    parse_entity_line,
    parse_relationship_line,
)
from chaoscypher_core.services.sources.engine.extraction.utils.relation_vocabulary import (
    canonical_edge_label,
)
from chaoscypher_core.services.sources.engine.extraction.utils.type_rescue import TypeVocabulary


PLUGINS = Path(DomainRegistry.builtin_plugin_dir())


def _plugins() -> dict[str, dict[str, Any]]:
    return {
        p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted(PLUGINS.glob("*.jsonld"))
    }


def test_exactly_one_floor_and_it_is_generic() -> None:
    roles = {name: cfg.get("role") for name, cfg in _plugins().items()}
    assert [n for n, r in roles.items() if r == "floor"] == ["generic"]
    assert all(r == "pack" for n, r in roles.items() if n != "generic")


def test_the_floor_declares_exactly_the_core_types_and_labels() -> None:
    floor = _plugins()["generic"]
    assert [t["name"] for t in floor["templates"]["node_templates"]] == list(CORE_TYPES)
    labels = {canonical_edge_label(e["name"]) for e in floor["templates"]["edge_templates"]}
    assert labels == CORE_LABELS and "related_to" in labels
    assert floor["strict_entity_types"] is True
    assert "extraction_limits" not in floor, "the floor's limits live in core_types.FLOOR_LIMITS"
    assert "detection" not in floor, "the floor is never ranked (spec § 3)"


def test_detection_never_ranks_the_floor() -> None:
    registry = DomainRegistry()
    text = "the person and the organization met at the event in the city " * 50
    assert all(d.name != "generic" for d, _c in registry.rank_domains(text, "notes.txt", {}))


def test_each_floor_keyword_belongs_to_one_core_type() -> None:
    """The rescue routes a keyword one way: no normalization keyword on two core types."""
    owner: dict[str, str] = {}
    for tmpl in _plugins()["generic"]["templates"]["node_templates"]:
        for word in tmpl.get("normalization_keywords", []):
            assert word.lower() not in owner, (word, owner.get(word.lower()), tmpl["name"])
            owner[word.lower()] = tmpl["name"]
    assert owner["group"] == "Group"


def test_no_floor_description_names_another_core_type() -> None:
    import re

    for tmpl in _plugins()["generic"]["templates"]["node_templates"]:
        others = [t for t in CORE_TYPES if t != tmpl["name"]]
        named = [t for t in others if re.search(rf"\b{t.lower()}s?\b", tmpl["description"].lower())]
        assert not named, (tmpl["name"], named)


def test_the_floor_edges_are_the_converters_core_edges() -> None:
    """One source for the core labels' shapes: the floor file is what the converter writes."""
    assert _plugins()["generic"]["templates"]["edge_templates"] == core_edge_templates()


def test_every_floor_edge_constrains_over_core_types() -> None:
    for edge in _plugins()["generic"]["templates"]["edge_templates"]:
        assert set(edge.get("source_types", [])) <= set(CORE_TYPES), edge["name"]
        assert set(edge.get("target_types", [])) <= set(CORE_TYPES), edge["name"]
        if edge.get("inverse"):
            assert edge["inverse"] in CORE_LABELS, edge["name"]


@pytest.mark.parametrize(
    "name", [p.stem for p in sorted(PLUGINS.glob("*.jsonld")) if p.stem != "generic"]
)
def test_a_pack_only_adds(name: str) -> None:
    cfg = _plugins()[name]
    assert cfg["extends"] == "core"
    assert "node_templates" not in cfg.get("templates", {}), (
        "packs declare subtypes, not node templates"
    )
    core_names = {t.lower() for t in CORE_TYPES}
    for sub in cfg.get("subtypes", []):
        assert sub["of"] in CORE_TYPES and sub["name"].strip().lower() not in core_names
    for edge in cfg.get("templates", {}).get("edge_templates", []):
        assert canonical_edge_label(edge["name"]) not in CORE_LABELS, (
            f"{name} re-declares {edge['name']}"
        )
        assert set(edge["source_types"]) <= set(CORE_TYPES)
        assert set(edge["target_types"]) <= set(CORE_TYPES)
    for key, value in (cfg.get("extraction_limits") or {}).items():
        if key in FLOOR_LIMITS:
            assert value >= FLOOR_LIMITS[key], (name, key, value)
    subtypes = {s["name"] for s in cfg.get("subtypes", [])}
    for alias, target in (cfg.get("type_aliases") or {}).items():
        assert not is_core_type(alias) and alias not in subtypes, (name, alias)
        assert is_core_type(target) or target in subtypes, (name, alias, target)


def _relationship_line(example: dict[str, Any]) -> str:
    """A relationship example as the ``R|`` line it teaches: source 0, target 1, its label, its note."""
    justification = str(example.get("note") or example.get("type") or "")
    return f"R|0|1|{example.get('type', '')}|1.0|S1|{justification.replace('|', chr(92) + '|')}"


@pytest.mark.parametrize("name", [p.stem for p in sorted(PLUGINS.glob("*.jsonld"))])
def test_every_example_parses_through_the_real_line_parser(name: str) -> None:
    """Spec § 3: every example parses through the real line parser, in the stack's own vocabulary.

    An entity example is an ``E|`` line: it parses, and its type is a core type
    or a ``Core/subtype`` the type rescue's decision places (a subtype the pack
    offers). A relationship example is a record (source, target, label, note):
    it is written as the ``R|`` line it teaches and read by the real ``R|``
    parser, and its label is one of the floor's or the pack's own. Until
    2026-10-08 only the entity examples were parsed, and no type or label
    was checked.
    """
    registry = DomainRegistry()
    stack = DomainStack.resolve(registry, [] if name == FLOOR_NAME else [name])
    vocabulary = TypeVocabulary.from_stack(stack)
    labels = {canonical_edge_label(t["name"]) for t in stack.get_templates()["edge_templates"]}
    examples = _plugins()[name].get("examples", {})
    for ex in examples.get("entity_examples", []):
        parsed = parse_entity_line(ex["line"])
        assert parsed is not None, f"{name}: example does not parse: {ex['line']}"
        decision = vocabulary.decide(parsed["type"])
        assert parsed["type"] in CORE_TYPES or (
            decision is not None and decision.tier == "subtyped"
        ), (name, ex["line"], decision)
    for ex in examples.get("relationship_examples", []):
        assert ex.get("source") and ex.get("target"), (name, ex)
        parsed = parse_relationship_line(_relationship_line(ex))
        assert parsed is not None, (name, _relationship_line(ex))
        assert (parsed["source"], parsed["target"]) == (0, 1)
        assert canonical_edge_label(parsed["type"]) in labels, (name, parsed["type"])
    assert examples.get("relationship_examples"), f"{name} teaches no relationship"


def test_the_example_check_fails_an_undeclared_label_and_an_old_type_name() -> None:
    """What the check above refuses: a label no member declares, a type written by its old name."""
    stack = DomainStack.resolve(DomainRegistry(), ["literary"])
    labels = {canonical_edge_label(t["name"]) for t in stack.get_templates()["edge_templates"]}
    parsed = parse_relationship_line(_relationship_line({"type": "befriends_wildly", "note": "x"}))
    assert parsed is not None and canonical_edge_label(parsed["type"]) not in labels
    decision = TypeVocabulary.from_stack(stack).decide("Character")
    assert decision is not None and decision.tier == "mapped"  # neither a core type nor subtyped


def test_every_old_template_name_maps_to_a_core_type() -> None:
    # the mapping table is the migration's source; every name the 19 plugins ever shipped is in it
    for name, cfg in _plugins().items():
        for sub in cfg.get("subtypes", []):
            core, subtype = core_type_of(sub["name"], pack=name)
            assert core == sub["of"] and subtype == sub["name"].lower().replace(" ", "_")


def test_nothing_is_left_to_convert() -> None:
    """The converter run over the shipped plugins changes nothing (it is idempotent)."""
    assert pending_conversions(PLUGINS) == []


def test_core_type_of_refuses_an_unknown_name_and_reads_any_case() -> None:
    assert core_type_of("  Military Unit ") == ("Organization", "military_unit")
    assert core_type_of("CHARACTER") == ("Person", "character")
    assert core_type_of("Person") == ("Person", None)
    assert core_type_of("Item") == ("Object", None)
    with pytest.raises(KeyError):
        core_type_of("Spaceship Captain")


@pytest.mark.parametrize(
    ("name", "pack", "plain", "qualified"),
    [
        ("Publication", "news", "Work", "Organization"),
        ("Module", "technical", "Work", "Object"),
        ("Type", "technical", "Concept", "Object"),
        ("Source", "intelligence", "Person", "Concept"),
        ("Political Entity", "political", "Organization", "Location"),
    ],
)
def test_a_pack_qualified_row_wins_for_its_pack(
    name: str, pack: str, plain: str, qualified: str
) -> None:
    key = "_".join(name.lower().split())
    assert core_type_of(name) == (plain, key)
    assert core_type_of(name, pack=pack) == (qualified, key)
    assert core_type_of(name, pack="literary") == (plain, key)
    sub = next(s for s in _plugins()[pack]["subtypes"] if s["name"] == name)
    assert sub["of"] == qualified


def test_a_pack_that_redeclares_a_core_name_is_refused(tmp_path: Path) -> None:
    bad = {
        **_plugins()["literary"],
        "name": "bad",
        "subtypes": [{"name": "Person", "of": "Person", "description": "x"}],
    }
    (tmp_path / "bad.jsonld").write_text(json.dumps(bad), encoding="utf-8")
    registry = DomainRegistry(user_plugin_dirs=[tmp_path])
    assert "bad" not in registry.names()
    assert registry.load_errors()["bad"].startswith("subtype 'Person' is a core type")


def test_a_pack_that_redeclares_a_core_label_is_refused(tmp_path: Path) -> None:
    literary = _plugins()["literary"]
    edges = [*literary["templates"]["edge_templates"], {"name": "Parent Of", "description": "x",
             "source_types": ["Person"], "target_types": ["Person"]}]  # fmt: skip
    bad = {**literary, "name": "bad", "templates": {"edge_templates": edges}}
    (tmp_path / "bad.jsonld").write_text(json.dumps(bad), encoding="utf-8")
    registry = DomainRegistry(user_plugin_dirs=[tmp_path])
    assert "bad" not in registry.names()
    assert registry.load_errors()["bad"].startswith("edge label 'Parent Of' is a core label")


def test_a_pack_cannot_lower_a_floor_limit(tmp_path: Path) -> None:
    bad = {**_plugins()["literary"], "name": "bad", "extraction_limits": {"max_entity_degree": 1}}
    (tmp_path / "bad.jsonld").write_text(json.dumps(bad), encoding="utf-8")
    registry = DomainRegistry(user_plugin_dirs=[tmp_path])
    assert registry.load_errors()["bad"].startswith(
        "extraction_limits.max_entity_degree 1 is below the floor"
    )


@pytest.mark.parametrize("role", ["pack", "floor"])
def test_a_user_plugin_named_generic_never_replaces_the_floor(tmp_path: Path, role: str) -> None:
    """A user pack named 'generic', or a second floor, is refused with a load error naming it."""
    if role == "pack":
        user = {**_plugins()["literary"], "name": "generic"}
    else:
        user = dict(_plugins()["generic"])
    (tmp_path / "generic.jsonld").write_text(json.dumps(user), encoding="utf-8")
    registry = DomainRegistry(user_plugin_dirs=[tmp_path])
    floor = registry.get("generic")
    assert floor is not None and floor.role == "floor"
    assert floor.get_templates()["node_templates"]
    assert "generic" in registry.load_errors()["generic"]


def test_a_user_plugin_cannot_be_the_floor(tmp_path: Path) -> None:
    other = {**_plugins()["generic"], "name": "myfloor"}
    (tmp_path / "myfloor.jsonld").write_text(json.dumps(other), encoding="utf-8")
    registry = DomainRegistry(user_plugin_dirs=[tmp_path])
    assert "myfloor" not in registry.names()
    assert "floor" in registry.load_errors()["myfloor"]


def test_the_shipped_plugins_all_load(tmp_path: Path) -> None:
    registry = DomainRegistry()
    assert registry.load_errors() == {}
    assert sorted(registry.names()) == sorted(_plugins())


def _old_shape_plugin() -> dict[str, Any]:
    """A user plugin as every plugin was before 2026-10-07."""
    return {
        "name": "saga",
        "version": "1.4.0",
        "description": "Sagas",
        "strict_entity_types": True,
        "type_aliases": {"Object": "Character", "Hero": "Character", "Hall": "Location"},
        "templates": {
            "node_templates": [
                {"name": "Character", "description": "A person of the saga", "names_a_person": True,
                 "compatibility_group": "people", "normalization_keywords": ["jarl"],
                 "properties": [{"name": "kin", "display_name": "Kin", "property_type": "text",
                                 "absorbs_types": ["Kinship"]}]},
                {"name": "Location", "description": "A place",
                 "properties": [{"name": "region", "display_name": "Region", "property_type": "text"}]},
                {"name": "Military Unit", "description": "A war band"},
            ],
            "edge_templates": [
                {"name": "parent_of", "description": "x", "source_types": ["Character"],
                 "target_types": ["Character"]},
                {"name": "sworn_to", "description": "Bound by oath to", "source_types": ["Character"],
                 "target_types": ["Character", "Military Unit"], "two_endpoints_only": True},
            ],
        },
        "examples": {"entity_examples": [{"name": "Egil", "type": "Character", "note": "a skald"}]},
        "extraction_limits": {"max_entity_degree": 20, "max_relationship_ratio": 6.0},
    }  # fmt: skip


def test_the_converter_turns_an_old_plugin_into_a_valid_pack() -> None:
    result = convert_config(_old_shape_plugin(), "saga")
    pack = result.config
    assert result.changed and not result.missing
    DomainConfigModel.model_validate(pack)
    assert (pack["role"], pack["extends"], pack["version"]) == ("pack", "core", "2.0.0")
    assert [(s["name"], s["of"]) for s in pack["subtypes"]] == [
        ("Character", "Person"),
        ("Military Unit", "Organization"),
    ]
    assert pack["subtypes"][0]["names_a_person"] is True
    assert pack["subtypes"][0]["properties"] == [
        {"name": "kin", "display_name": "Kin", "property_type": "text"}
    ]
    assert "compatibility_group" not in pack["subtypes"][0]
    assert "normalization_keywords" not in pack["subtypes"][0]
    # A template's keywords become its subtype's rescue keywords (the rescue's pack keyword tier).
    assert pack["subtypes"][0]["rescue_keywords"] == ["jarl"]
    assert pack["core_properties"] == {
        "Location": [{"name": "region", "display_name": "Region", "property_type": "text"}]
    }
    assert result.dropped_labels == ["parent_of"]
    (sworn,) = pack["templates"]["edge_templates"]
    assert (sworn["source_types"], sworn["target_types"]) == (
        ["Person"],
        ["Person", "Organization"],
    )
    assert pack["type_aliases"] == {"Hero": "Character", "Hall": "Location"}
    assert result.dropped_aliases == ["Object"]
    assert pack["extraction_limits"] == {"max_relationship_ratio": 6.0}
    assert result.dropped_limits == {"max_entity_degree": 20}
    assert "strict_entity_types" not in pack
    (example,) = pack["examples"]["entity_examples"]
    assert parse_entity_line(example["line"])["type"] == "Person"


def test_the_converter_is_idempotent() -> None:
    once = convert_config(_old_shape_plugin(), "saga").config
    again = convert_config(once, "saga")
    assert not again.changed and again.config == once


def test_the_converter_refuses_a_name_the_table_lacks(tmp_path: Path) -> None:
    plugin = _old_shape_plugin()
    plugin["templates"]["node_templates"].append({"name": "Longship Crew", "description": "x"})
    result = convert_config(plugin, "saga")
    assert result.missing == ["Longship Crew"] and result.config == plugin
    path = tmp_path / "saga.jsonld"
    path.write_text(json.dumps(plugin), encoding="utf-8")
    assert main([str(path), "--write"]) == 1
    assert json.loads(path.read_text(encoding="utf-8")) == plugin  # nothing written


def test_the_schema_refuses_a_floor_with_detection_and_a_pack_named_generic() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="never ranked"):
        DomainConfigModel.model_validate({**_plugins()["generic"], "detection": {"keywords": {}}})
    with pytest.raises(ValidationError, match="floor's name"):
        DomainConfigModel.model_validate({**_plugins()["literary"], "name": "generic"})


ORIGINAL_KEYWORDS = Path(__file__).with_name("original_rescue_keywords.json")
"""Every shipped pack's templates' ``normalization_keywords`` as they were before the conversion
(``34b34ac1c``), frozen: what the packs' rescue keywords must still carry."""


def _pack_rescue_words(pack: dict[str, Any], template: str) -> list[str]:
    """The rescue keywords a converted pack holds for one of its original templates."""
    core, sub = core_type_of(template, pack=pack["name"])
    if sub is None:
        return list((pack.get("core_rescue_keywords") or {}).get(core) or [])
    (entry,) = [s for s in pack.get("subtypes") or [] if s["name"] == template]
    return list(entry.get("rescue_keywords") or [])


@pytest.mark.parametrize(
    "plugin", sorted(json.loads(ORIGINAL_KEYWORDS.read_text(encoding="utf-8")))
)
def test_every_pack_subtype_carries_its_original_templates_keywords(plugin: str) -> None:
    """A written type the old pack rescued by keyword is rescued by the same words (review 2026-10-07 (a)).

    Task 1's conversion dropped the packs' ``normalization_keywords``, so
    ``Battle``, ``Regiment`` or ``Countess`` dropped as ``unknown_type`` under
    literary; ``convert_packs --keywords-from`` restored them as the subtypes'
    ``rescue_keywords`` (a folded template's as ``core_rescue_keywords``).
    Pronouns (``NOT_RESCUE_KEYWORDS``) are left out on purpose.
    """
    original = json.loads(ORIGINAL_KEYWORDS.read_text(encoding="utf-8"))[plugin]
    pack = _plugins()[plugin]
    for template, words in original.items():
        held = {w.lower() for w in _pack_rescue_words(pack, template)}
        wanted = {w.lower() for w in words} - NOT_RESCUE_KEYWORDS
        # a word a mapping split moved is held by the subtype it went to (Denis 2026-10-08)
        for subtype, moved in split_keywords(plugin, template).items():
            assert moved <= {w.lower() for w in _pack_rescue_words(pack, subtype)}, subtype
            wanted -= moved
        assert wanted <= held, (plugin, template, sorted(wanted - held))


def test_no_shipped_plugin_rescues_by_a_pronoun() -> None:
    """``he``, ``she``, ``a man`` say nothing of a type (review 2026-10-07 (b))."""
    for name, cfg in _plugins().items():
        words = [
            w
            for tmpl in (cfg.get("templates") or {}).get("node_templates") or []
            for w in tmpl.get("normalization_keywords") or []
        ]
        words += [w for s in cfg.get("subtypes") or [] for w in s.get("rescue_keywords") or []]
        words += [w for ws in (cfg.get("core_rescue_keywords") or {}).values() for w in ws]
        assert not {w.lower() for w in words} & NOT_RESCUE_KEYWORDS, name


def test_the_shipped_pack_subtypes_are_what_the_mapping_table_says() -> None:
    """The rescue reads the table before a pack's own subtypes; for the shipped packs both agree."""
    for name, cfg in _plugins().items():
        for sub in cfg.get("subtypes") or []:
            assert core_type_of(sub["name"], pack=name) == (sub["of"], subtype_key(sub["name"])), (
                name,
                sub["name"],
            )


def test_restore_rescue_keywords_is_idempotent_and_reads_folded_templates() -> None:
    old = _old_shape_plugin()
    old["templates"]["node_templates"][1]["normalization_keywords"] = ["hall", "he"]
    pack = convert_config(_old_shape_plugin(), "saga").config
    assert "core_rescue_keywords" not in pack
    assert restore_rescue_keywords(pack, old) is True
    assert pack["core_rescue_keywords"] == {"Location": ["hall"]}  # the pronoun is left out
    assert list(pack).index("core_rescue_keywords") == list(pack).index("core_properties") + 1
    assert restore_rescue_keywords(pack, old) is False
    DomainConfigModel.model_validate(pack)


def _rescued(pack: str, written: str) -> tuple[str, str | None] | None:
    from chaoscypher_core.services.sources.engine.extraction.domains import get_domain_registry
    from chaoscypher_core.services.sources.engine.extraction.domains.stack import DomainStack
    from chaoscypher_core.services.sources.engine.extraction.utils.type_rescue import rescue_type

    stack = DomainStack.resolve(get_domain_registry(), [pack])
    return rescue_type(written, stack)


@pytest.mark.parametrize(
    ("pack", "written", "expected"),
    [
        ("intelligence", "Informant", ("Person", "human_source")),
        ("intelligence", "Defector", ("Person", "human_source")),
        ("intelligence", "Asset", ("Person", "human_source")),
        ("intelligence", "Human Source", ("Person", "human_source")),
        ("intelligence", "Source", ("Concept", "source")),
        ("political", "Political Entity", ("Location", "political_entity")),
        ("political", "Parliament", ("Organization", "institution")),
        ("political", "Bloc", ("Organization", "international_organization")),
        ("political", "Party", ("Organization", "political_party")),
    ],
)
def test_the_mapping_splits_type_people_and_bodies(
    pack: str, written: str, expected: tuple[str, str]
) -> None:
    """Denis 2026-10-08: informants are people, collection sources a provenance; states are
    places, parliaments, blocs and parties organizations.
    """
    assert _rescued(pack, written) == expected


def test_the_mapping_splits_are_in_the_shipped_packs_and_idempotent() -> None:
    plugins = _plugins()
    for split in SUBTYPE_SPLITS:
        pack = plugins[split.pack]
        names = [s["name"] for s in pack["subtypes"]]
        assert names.index(split.subtype["name"]) == names.index(split.from_subtype) + 1
        source = next(s for s in pack["subtypes"] if s["name"] == split.from_subtype)
        assert not {w.lower() for w in source.get("rescue_keywords") or []} & set(split.keywords)
    for name, aliases in PACK_ALIASES.items():
        assert aliases.items() <= plugins[name]["type_aliases"].items()
        assert apply_mapping_splits(plugins[name]) is False
