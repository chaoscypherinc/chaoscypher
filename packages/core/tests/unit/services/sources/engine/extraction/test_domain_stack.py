# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The extraction prompt is assembled from the floor and the chosen packs (spec 2026-10-07 § 4)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from chaoscypher_core.exceptions import OperationError
from chaoscypher_core.services.sources.engine.extraction.domains.core_types import (
    CORE_TYPES,
    FLOOR_NAME,
)
from chaoscypher_core.services.sources.engine.extraction.domains.packs import InvalidPacksError
from chaoscypher_core.services.sources.engine.extraction.domains.registry import DomainRegistry
from chaoscypher_core.services.sources.engine.extraction.domains.stack import (
    PASSES,
    DomainStack,
    format_contribution_table,
    pack_contribution_table,
)
from chaoscypher_core.services.sources.engine.extraction.extraction_plan import (
    PROMPT_KWARG_NAMES,
    build_extraction_plan,
    prompt_inputs,
)
from chaoscypher_core.services.sources.engine.extraction.utils import ai_entities
from chaoscypher_core.services.sources.engine.extraction.utils.ai_entities import (
    render_plan_prompts,
)
from chaoscypher_core.services.sources.engine.extraction.utils.line_parser import (
    parse_entity_line,
)
from chaoscypher_core.services.sources.engine.extraction.utils.prompts import (
    STRICT_SUBTYPE_INSTRUCTION,
    STRICT_TYPE_INSTRUCTION,
    declared_predicates,
    declared_subtypes,
    declared_type_names,
    render_entity_format_example,
)
from chaoscypher_core.services.sources.engine.extraction.utils.type_rescue import TypeVocabulary
from chaoscypher_core.settings import EngineSettings


LITERARY_BUDGET = (
    7_900,
    12_500,
)  # pass 1 was 7,740 before the TITLES rule (6083ae535) added 220 characters to literary
"""Pass-1 and pass-2 characters floor + literary may render on an empty chunk.

Pass 1 is the literary prompt before the stack (the 2026-10-07 survey, at 34b34ac1c);
pass 2 is the controller's ruling (2026-10-07): the floor's larger label vocabulary is
the point of the restructure, so pass 2 may grow to 12,500 from today's 9,819."""

PACK_BUDGET = (9000, 12_500)
"""Pass-1 and pass-2 characters the floor plus any one pack may render on an empty chunk
(controller's ruling, 2026-10-07)."""

_SHIPPED_PACKS = sorted(
    path.stem for path in DomainRegistry.builtin_plugin_dir().glob("*.jsonld")
    if path.stem != FLOOR_NAME
)  # fmt: skip
"""The shipped packs, by file name (checked against the registry below)."""


@pytest.fixture(scope="module")
def registry() -> DomainRegistry:
    """The shipped floor and packs only (no user plugin directory)."""
    return DomainRegistry()


def _packs(registry: DomainRegistry) -> list[str]:
    return [n for n in registry.names() if getattr(registry.get_domain(n), "role", None) == "pack"]


def test_floor_only_offers_the_core_types_and_labels(registry: DomainRegistry) -> None:
    stack = DomainStack.resolve(registry, [])
    assert stack.names == (FLOOR_NAME,)
    assert [line.split(":")[0].lstrip("- ") for line in stack.node_lines()] == list(CORE_TYPES)
    assert any(line.startswith("- related_to") for line in stack.edge_lines())
    assert "subtypes:" not in "\n".join(stack.node_lines())


def test_a_pack_adds_subtypes_labels_and_guidance_without_removing(
    registry: DomainRegistry,
) -> None:
    floor = DomainStack.resolve(registry, [])
    lit = DomainStack.resolve(registry, ["literary"])
    assert lit.names == ("literary",)
    person = next(line for line in lit.node_lines() if line.startswith("- Person"))
    assert person.startswith(
        "- Person: An individual human being "
        "(subtypes: author, character, narrator — write Person/author) (properties: "
    )
    assert set(floor.edge_lines()) <= set(lit.edge_lines())
    assert any(line.startswith("- betrothed_to") for line in lit.edge_lines())
    assert floor.guidance()[0] in lit.guidance()[0]  # the floor's rules are kept verbatim
    assert floor.guidance()[1] in lit.guidance()[1]
    assert "Pack literary:" in lit.guidance()[0]
    floor_rules = {rule.description for rule in floor.exclusions()}
    assert floor_rules < {rule.description for rule in lit.exclusions()}


def test_a_packs_core_properties_render_beside_the_floors(registry: DomainRegistry) -> None:
    """Literary's Event host/date/place sit on the core Event line; the floor's date is not repeated."""
    floor_event = next(
        line
        for line in DomainStack.resolve(registry, []).node_lines()
        if line.startswith("- Event")
    )
    event = next(
        line
        for line in DomainStack.resolve(registry, ["literary"]).node_lines()
        if line.startswith("- Event")
    )
    assert "date=Date" in floor_event and "host=" not in floor_event
    assert "host=Host" in event and "place=Place" in event
    assert event.count("date=") == 1


def test_two_packs_compose(registry: DomainRegistry) -> None:
    both = DomainStack.resolve(registry, ["literary", "historical"])
    lit = DomainStack.resolve(registry, ["literary"])
    hist = DomainStack.resolve(registry, ["historical"])
    assert both.names == ("historical", "literary")
    labels = declared_predicates("\n".join(both.edge_lines()))
    assert len(labels) == len(set(labels))  # a label both packs declare is offered once
    assert set(labels) == set(declared_predicates("\n".join(lit.edge_lines()))) | set(
        declared_predicates("\n".join(hist.edge_lines()))
    )
    assert "Pack historical:" in both.guidance()[0] and "Pack literary:" in both.guidance()[0]


def test_resolve_refuses_what_is_not_a_pack(registry: DomainRegistry) -> None:
    for name in (FLOOR_NAME, "no_such_pack"):
        with pytest.raises(InvalidPacksError, match=name):
            DomainStack.resolve(registry, [name])
    assert DomainStack.resolve(registry, ["literary", "literary"]).names == ("literary",)


def test_the_one_domain_shim(registry: DomainRegistry) -> None:
    """No name or the floor's is the floor; a pack's is that pack; a label is its packs."""
    for name in (None, "", FLOOR_NAME, "no_such_pack"):
        assert DomainStack.from_domain_name(registry, name).names == (FLOOR_NAME,)
    assert DomainStack.from_domain_name(registry, "literary").names == ("literary",)
    # The packs' label (``packs_label``) that ``extraction_domain`` and a
    # one-name parameter carry: both packs, in either spelling.
    for label in ("historical+literary", "literary+historical"):
        assert DomainStack.from_domain_name(registry, label).names == ("historical", "literary")
    assert DomainStack.from_domain_name(registry, "literary+no_such_pack").names == ("literary",)


def test_the_resolvers_read_a_label_as_its_stack() -> None:
    """``domain_stack`` (the dedup and relation resolvers' lookup) reads the packs' label."""
    from chaoscypher_core.services.sources.engine.extraction.domain_resolver import (
        DomainResolver,
        domain_stack,
    )

    stack = domain_stack("historical+literary", None)
    assert stack is not None
    assert stack.names == ("historical", "literary")
    resolver = DomainResolver(None)
    union = resolver.get_domain_inverse_relationships("historical+literary")
    for name in ("historical", "literary"):
        assert set(resolver.get_domain_inverse_relationships(name).items()) <= set(union.items())


def test_the_plan_reads_the_stack(registry: DomainRegistry) -> None:
    settings = EngineSettings()
    lit = DomainStack.resolve(registry, ["literary"])
    kwargs = build_extraction_plan(lit, settings).chunk_kwargs
    assert kwargs["node_templates_formatted"] == "\n".join(lit.node_lines())
    assert kwargs["edge_templates_formatted"] == "\n".join(lit.edge_lines())
    assert (kwargs["entity_guidance"], kwargs["relationship_guidance"]) == lit.guidance()
    assert kwargs["valid_entity_type_names"] == set(CORE_TYPES)
    assert declared_type_names(kwargs["node_templates_formatted"]) == list(CORE_TYPES)
    assert "betrothed_to" in declared_predicates(kwargs["edge_templates_formatted"])
    with pytest.raises(OperationError, match="DomainStack"):
        build_extraction_plan(registry.get_domain("literary"), settings)


def test_the_format_example_shows_one_person_location_and_event(registry: DomainRegistry) -> None:
    nodes = "\n".join(DomainStack.resolve(registry, []).node_lines())
    entity_lines = [ln for ln in render_entity_format_example(nodes).splitlines() if ln[:2] == "E|"]
    assert [line.split("|")[2] for line in entity_lines] == ["Person", "Location", "Event"]


def test_the_format_example_writes_one_subtype_when_the_packs_offer_one(
    registry: DomainRegistry,
) -> None:
    """Floor + literary: the captain is ``Person/character``, the rest core types (one subtyped line)."""
    nodes = "\n".join(DomainStack.resolve(registry, ["literary"]).node_lines())
    entity_lines = [ln for ln in render_entity_format_example(nodes).splitlines() if ln[:2] == "E|"]
    assert [line.split("|")[2] for line in entity_lines] == [
        "Person/character",
        "Location",
        "Event",
    ]


def _taught_types(stack: DomainStack) -> list[tuple[str, str]]:
    """``(core type, written type)`` of every ``Core/subtype`` the stack's pass-1 prompt teaches.

    Each node line's ``write Core/subtype`` and the format example's one subtyped line.
    """
    nodes = "\n".join(stack.node_lines())
    taught = [
        (core, f"{core}/{subtypes[0]}") for core, subtypes in declared_subtypes(nodes).items()
    ]
    for line in render_entity_format_example(nodes).splitlines():
        if line.startswith("E|") and "/" in line.split("|")[2]:
            written = line.split("|")[2]
            taught.append((written.split("/")[0], written))
    return taught


@pytest.mark.parametrize("pack", _SHIPPED_PACKS)
def test_every_subtype_the_prompt_teaches_is_placed_by_the_decision(
    registry: DomainRegistry, pack: str
) -> None:
    """What the prompt tells the model to write is what the pipeline stores: parsed, then decided.

    Every ``Core/subtype`` a pack's prompt shows goes through the real line
    parser and the type rescue's one decision to that core type with the
    subtype (tier ``subtyped``), never the unplaced flag (2026-10-08 final
    review: the model was never told how to write a subtype, so 207 of 208
    nodes of the literary graph had none).
    """
    stack = DomainStack.resolve(registry, [pack])
    vocabulary = TypeVocabulary.from_stack(stack)
    for core, written in _taught_types(stack):
        parsed = parse_entity_line(f"E|Orsolya Darnell|{written}||0.9|S1|A harbour captain.")
        assert parsed is not None and parsed["type"] == written, (pack, written)
        decision = vocabulary.decide(parsed["type"], record=parsed, salvage=True)
        assert decision is not None and decision.tier == "subtyped", (pack, written, decision)
        assert (decision.core, decision.subtype) == (core, written.split("/", 1)[1])


@pytest.mark.parametrize(
    ("packs", "instruction"),
    [([], STRICT_TYPE_INSTRUCTION), (["literary"], STRICT_SUBTYPE_INSTRUCTION)],
    ids=["floor", "floor+literary"],
)
def test_the_strict_line_names_the_subtype_form_only_when_one_is_offered(
    registry: DomainRegistry, packs: list[str], instruction: str
) -> None:
    pass1, _ = render_plan_prompts(
        prompt_inputs(DomainStack.resolve(registry, packs), EngineSettings()),
        numbered_text="",
        entity_list="",
        max_entity_index=0,
    )
    assert instruction in pass1
    assert pass1.count("IMPORTANT: The type is one of the entity types listed above") == 1


def test_the_examples_cap_applies_to_each_member(registry: DomainRegistry) -> None:
    lit = DomainStack.resolve(registry, ["literary"])
    floor_entity, floor_rel = DomainStack.resolve(registry, []).examples(max_chars=200)
    entity, relationship = lit.examples(max_chars=200)
    assert entity.startswith(floor_entity) and relationship.startswith(floor_rel)
    assert len(entity) <= 2 * 200 + 2 and len(relationship) <= 2 * 200 + 2


def test_limits_take_the_floor_and_raise_with_the_packs(registry: DomainRegistry) -> None:
    floor = DomainStack.resolve(registry, []).limits()
    both = DomainStack.resolve(registry, ["historical", "literary"]).limits()
    for key, value in floor.items():
        assert both[key] >= value
    assert both["loop_max_entity_count"] == 50  # literary's 50 over historical's 40


def test_floor_plus_literary_renders_within_7900_and_12500_characters(
    registry: DomainRegistry,
) -> None:
    p1, p2 = DomainStack.resolve(registry, ["literary"]).rendered_sizes()
    assert p1 <= LITERARY_BUDGET[0] and p2 <= LITERARY_BUDGET[1], (p1, p2)


def test_the_shipped_packs_are_the_registered_packs(registry: DomainRegistry) -> None:
    assert _packs(registry) == _SHIPPED_PACKS
    assert len(_SHIPPED_PACKS) == 18


@pytest.mark.parametrize("pack", _SHIPPED_PACKS)
def test_the_floor_plus_any_one_pack_is_within_the_budget(
    registry: DomainRegistry, pack: str
) -> None:
    p1, p2 = DomainStack.resolve(registry, [pack]).rendered_sizes()
    assert p1 <= PACK_BUDGET[0] and p2 <= PACK_BUDGET[1], (pack, p1, p2)


def test_a_plan_input_the_prompts_do_not_render_fails_loudly(
    registry: DomainRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one renderer consumes every prompt input; one added to the plan but not rendered raises."""
    kwargs = prompt_inputs(DomainStack.resolve(registry, ["literary"]), EngineSettings())
    entity, relationship = render_plan_prompts(
        kwargs, numbered_text="", entity_list="", max_entity_index=0
    )
    assert kwargs["entity_guidance"] in entity and kwargs["relationship_guidance"] in relationship
    with pytest.raises(OperationError, match="entity_guidance"):
        render_plan_prompts(
            {k: v for k, v in kwargs.items() if k != "entity_guidance"},
            numbered_text="",
            entity_list="",
            max_entity_index=0,
        )
    monkeypatch.setattr(
        ai_entities, "PROMPT_KWARG_NAMES", PROMPT_KWARG_NAMES | {"subtype_guidance"}
    )
    with pytest.raises(OperationError, match="subtype_guidance"):
        render_plan_prompts(
            {**kwargs, "subtype_guidance": "x"},
            numbered_text="",
            entity_list="",
            max_entity_index=0,
        )


def test_prompt_contributions_are_reported(registry: DomainRegistry, tmp_path: Path) -> None:
    """Every pack's contribution per pass is measured and written out; the report mark is not a limit."""
    rows = pack_contribution_table(registry)
    assert [name for name, *_ in rows] == _packs(registry)
    assert len(rows) == 18
    for name, p1, p2 in rows:
        assert p1 > 0 and p2 > 0, name
    table = tmp_path / "pack_contribution.md"
    table.write_text(format_contribution_table(rows), encoding="utf-8")
    body = table.read_text(encoding="utf-8").splitlines()[2:]
    assert len(body) == 18


def test_prompt_contribution_has_one_entry_per_pack(registry: DomainRegistry) -> None:
    both = DomainStack.resolve(registry, ["historical", "literary"])
    contribution: dict[str, Any] = both.prompt_contribution()
    assert set(contribution) == {"historical", "literary"}
    for sizes in contribution.values():
        assert set(sizes) == set(PASSES) and min(sizes.values()) > 0


def _rule_lines(text: str | None) -> set[str]:
    """A guidance text's rule lines, normalised (section headers, which end in ':', left out)."""
    lines = (line.strip().lstrip("-").strip().lower() for line in (text or "").splitlines())
    return {line for line in lines if line and not line.endswith(":")}


def test_no_pack_repeats_a_floor_rule(registry: DomainRegistry) -> None:
    """The floor's rules are in every prompt once (spec § 3: a pack rule that duplicates the floor is removed)."""
    floor = registry.get_domain(FLOOR_NAME)
    floor_rules = _rule_lines(floor.get_entity_guidance()) | _rule_lines(
        floor.get_relationship_guidance()
    )
    for name in _packs(registry):
        pack = registry.get_domain(name)
        repeated = (
            _rule_lines(pack.get_entity_guidance()) | _rule_lines(pack.get_relationship_guidance())
        ) & floor_rules
        assert not repeated, (name, repeated)
