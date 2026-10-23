# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""LLM prompt templates for entity extraction.

Contains all pipe-delimited format prompt templates used by the
AIEntityExtractor for 2-pass extraction: entities+properties first,
then relationships.

Evidence-gated format specifications (sentence-referenced):
- E|name|type|aliases|confidence|sent_ref|description (entities)
- R|source_index|target_index|type|confidence|sent_ref|justification (relationships)
- P|entity_index|key|value (properties)
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from chaoscypher_core.services.sources.engine.extraction.domains.core_types import (
    CORE_TYPES,
    qualified_type,
)


# System prompt for extraction
SYSTEM_PROMPT = (
    "You are an expert at extracting structured knowledge from text. "
    "Output ONLY the requested format with no additional text."
)

# ==========================================================================
# PASS 1: ENTITY + PROPERTY HARVEST PROMPT
# ==========================================================================

ENTITY_HARVEST_TEMPLATE = """Extract entities and their properties from the numbered sentences below.

<numbered_sentences>
{numbered_sentences}
</numbered_sentences>

{known_entities}OUTPUT FORMAT:
- E|name|type|aliases|confidence|sent_ref|description
- P|entity_index|key|value

RULES:
1. Output each entity's E| line followed immediately by its P| lines.
2. Only extract facts explicitly stated in the sentences — do NOT infer.
3. Every E| MUST include sent_ref pointing to supporting sentence(s). Valid forms:
   - single: ``S3``
   - range:  ``S2-S5``
   - list:   ``S1, S5`` (comma-separated, for non-adjacent sentences)
   - mixed:  ``S1-S3, S7``
4. P| lines MUST appear immediately after their parent E| line. Use the entity INDEX (0-based from your output order). Do NOT output a second batch of P| lines.
5. A literal | inside a field must be written \\| and a literal backslash as \\\\ - the only unescaped | characters are the field separators.
6. Keep every field even when empty (write `||` for no aliases); never omit a field.

Entity types:
{node_templates}
{strict_type_instruction}

ENTITY GUIDELINES:
- NAME: the entity's name as the text writes it (a proper name, a specific identifier, or the
  title a person is called by), NOT a phrase you invented. NAME may be a KNOWN ENTITIES name when
  this passage itself names that entity (its name, or a distinctive alias of it, appears in this
  passage); a title or kinship word alone never identifies a KNOWN ENTITY.
  GOOD names: "Captain Orsolya Darnell", "Vellmarsh", "Siege of Corrowick"
  BAD names: "The emotional state of X", "The relationship between X and Y", "Feelings of sadness"
- DESCRIPTION: 2–3 short sentences, one fact per sentence, each fact stated in the text.
  Aim for 100+ characters. Include nationality, occupation, family ties, and distinguishing traits.
- ALIASES: every other name the text gives this same individual (semicolon-separated): its name
  variants (formal names, nicknames, diminutives, patronymics, the name with its title) and an
  epithet or description the text uses for this one individual alone
  GOOD aliases: "Orsolya; Captain Orsolya; Orsolya Darnell; the harbour captain; Bram's daughter"
  BAD aliases: "Nieces; The soldiers; Friend" (group nouns, a role many people share), and
  a name KNOWN ENTITIES gives to someone else
- PROPERTIES: every property the text states — titles, roles, family positions, occupations,
  nationalities, physical descriptions. Do NOT repeat the same property key for the same entity.
- Confidence: 1.0 named outright, 0.7-0.9 named indirectly (by title or epithet), 0.5-0.6 uncertain
- Do NOT extract pronouns, vague references, structural markers, or generic objects
- Do NOT write emotions, states, or actions as entities (a named idea, theory or doctrine is a Concept)
  BAD: "The act of being in a state of sorrow", "Feelings of anger", "A moment of reflection"
- A sentence fragment is NOT an entity name
{entity_exclusions}

{entity_format_example}"""  # noqa: RUF001 - the description rule (spec section 4) is quoted verbatim

# ==========================================================================
# PASS 2: RELATIONSHIP HARVEST PROMPT
# ==========================================================================

RELATIONSHIP_HARVEST_TEMPLATE = """Extract relationships between the entities listed below, based on the numbered sentences.

<numbered_sentences>
{numbered_sentences}
</numbered_sentences>

ENTITIES (use these index numbers for source and target):
<entity_list>
{entity_list}
</entity_list>

OUTPUT FORMAT:
- R|source_index|target_index|type|confidence|sent_ref|justification

RULES:
1. R| indices MUST be valid (0 to {max_entity_index}). No self-relationships.
2. Only extract facts explicitly stated in the sentences — do NOT infer.
3. Every R| MUST include sent_ref pointing to sentence(s) containing BOTH entities. Valid forms:
   - single: ``S3``
   - range:  ``S2-S5``
   - list:   ``S1, S5`` (comma-separated, for non-adjacent sentences)
   - mixed:  ``S1-S3, S7``
4. A literal | inside a field must be written \\| and a literal backslash as \\\\ - the only unescaped | characters are the field separators.

CRITICAL: The entity list above is the COMPLETE and ONLY set of entities.
- Do NOT reference entities you remember from the text that are not in the list above.
- If an entity you expect is missing from the list, skip relationships involving it.
- Only use index numbers 0 through {max_entity_index}. Any index outside this range is INVALID.

Relationship types:
{edge_templates}

RELATIONSHIP GUIDELINES:
- Extract every relationship that a cited sentence states between two listed entities — do not skip connections the text states outright
- If no sentence states a relationship between two entities, output no R| line for them; an entity may have none
- DIRECTIONALITY: each type has the source→target direction its description gives; never reverse it, even when the relation could be read either way
- Only emit relationship types from the list above; never invent a new one — pick the closest listed type instead
- Prefer the most specific listed type over a generic one
- JUSTIFICATION: One sentence (50-300 characters) quoting or paraphrasing the cited sentence as evidence. Evidence only: no deliberation, no alternatives, no notes to yourself

{relationship_format_example}"""

EXCLUSIONS_HEADER = "- SKIP these — they are NOT entities:"
"""The line the floor's and the packs' entity exclusions render under (junk only: a guard test holds it)."""

STRICT_TYPE_INSTRUCTION = (
    "\nIMPORTANT: The type is one of the entity types listed above; never another word. "
    "If an entity does not fit any listed type, use the closest match."
)
"""Rendered under the type list when the floor's types are exhaustive (``strict_entity_types``)
and no pack offers a subtype (:func:`strict_type_instruction`)."""

STRICT_SUBTYPE_INSTRUCTION = (
    "\nIMPORTANT: The type is one of the entity types listed above, or Core/subtype with a "
    "subtype from its list; never another word. "
    "If an entity does not fit any listed type, use the closest match."
)
""":data:`STRICT_TYPE_INSTRUCTION` when the type list offers subtypes (:func:`strict_type_instruction`)."""

_SUBTYPE_CLAUSE = re.compile(
    r"^- (?P<name>[^:\n]+?):[^\n]*? \(subtypes: (?P<subtypes>[^)\n]*?) — write ",
    re.MULTILINE,
)
"""A node line's subtype clause as :func:`subtype_clause` writes it (its reader)."""


def subtype_clause(core: str, subtypes: Sequence[str]) -> str:
    """The clause a node line offers its subtypes in, as the slugs to write.

    ``(subtypes: author, character, narrator — write Person/author)``: the
    subtype keys, then the type column's form of the first
    (``core_types.qualified_type``). :func:`declared_subtypes` reads it back.
    """
    return f"(subtypes: {', '.join(subtypes)} — write {qualified_type(core, subtypes[0])})"


def declared_subtypes(node_templates_formatted: str) -> dict[str, list[str]]:
    """The subtypes a formatted node-template list offers, by core type, in order."""
    return {
        m.group("name").strip(): [s.strip() for s in m.group("subtypes").split(",") if s.strip()]
        for m in _SUBTYPE_CLAUSE.finditer(node_templates_formatted)
    }


def strict_type_instruction(node_templates_formatted: str) -> str:
    """The strict line under a type list: :data:`STRICT_SUBTYPE_INSTRUCTION` when it offers subtypes."""
    if declared_subtypes(node_templates_formatted):
        return STRICT_SUBTYPE_INSTRUCTION
    return STRICT_TYPE_INSTRUCTION


# Extraction rules for UI display
EXTRACTION_RULES_TEMPLATE = """Output format: Pipe-delimited lines (evidence-gated)
- E|name|type|aliases|confidence|sent_ref|description
- R|source_index|target_index|type|confidence|sent_ref|justification
- P|entity_index|key|value
- A literal | inside a field is written \\| (a literal backslash as \\\\)"""


# ==========================================================================
# FORMAT EXAMPLES, IN THE CORE TYPES
# ==========================================================================
#
# The format examples were once a fixed literary world (Character, Author,
# participates_in, commands, enemy_of) shown to every domain, so the generic
# domain's prompt taught types and predicates it forbids (audit 2026-10-06,
# domain audit). Every prompt now lists the floor's core types and labels
# (spec 2026-10-07 § 4), so the example is one entity each of Person,
# Location and Event and two core-label relationships between them. A line
# renders only when the prompt's type list declares its type (or one of its
# predicates); an older stored type list without them gets the schematic.


@dataclass(frozen=True)
class _ExampleEntity:
    """One entity of the format example: its line fields and the core type it renders as.

    ``subtypes`` are the pack subtypes the entity fits, in preference order:
    the example writes the first entity's first one the type list offers as
    ``Core/subtype`` (one subtyped line, :func:`render_entity_format_example`).
    """

    name: str
    type_name: str
    aliases: str
    confidence: str
    sent_ref: str
    description: str
    properties: tuple[tuple[str, str], ...] = ()
    subtypes: tuple[str, ...] = ()


@dataclass(frozen=True)
class _ExampleRelationship:
    """One relationship of the format example, by entity position, and the predicates it may take."""

    source: int
    target: int
    types: tuple[str, ...]
    confidence: str
    sent_ref: str
    justification: str


_PERSON, _ORGANIZATION, _LOCATION, _EVENT, *_ = CORE_TYPES
_EXAMPLE_ENTITIES = (
    _ExampleEntity(
        "Captain Orsolya Darnell", _PERSON, "Orsolya; Captain Orsolya; Orsolya Darnell", "0.9",
        "S1-S2",
        "Harbour captain of Vellmarsh. Eldest daughter of the lamplighter Bram Darnell. "
        "Commands the town watch.",
        (("title", "Captain"), ("occupation", "Harbour Captain"),
         ("family_role", "Eldest daughter"), ("nationality", "Vellish")),
        ("character", "historical_figure"),
    ),
    _ExampleEntity(
        "Vellmarsh", _LOCATION, "", "1.0", "S1",
        "Walled harbour town on the Vellish coast. Its old sea walls are in poor repair.",
        subtypes=("setting",),
    ),
    _ExampleEntity(
        "Siege of Corrowick", _EVENT, "Corrowick", "1.0", "S4",
        "Forty-day siege of the walled port of Corrowick. It ended when the harbour chain broke.",
        (("event_type", "Siege"),),
        ("conflict", "historical_event", "plot_element", "operation"),
    ),
)  # fmt: skip
_EXAMPLE_RELATIONSHIPS = (
    _ExampleRelationship(
        0, 2, ("participates_in",), "0.9", "S4",
        "Captain Darnell held the north gate of Corrowick through the forty days of the siege",
    ),
    _ExampleRelationship(
        0, 1, ("located_in",), "0.9", "S1",
        "Captain Darnell keeps the harbour watch of Vellmarsh",
    ),
)  # fmt: skip

_NODE_LINE = re.compile(r"^- (?P<name>[^:\n]+?):", re.MULTILINE)
_EDGE_LINE = re.compile(r"^- (?P<slug>[^\s:(]+)(?::| \()", re.MULTILINE)
"""An edge line: ``- slug: description`` or ``- slug (Name): description``."""

_SCHEMATIC_ENTITY = (
    "EXAMPLE (the shape of a line; use a type from the list above):\n"
    "E|<entity name>|<entity type>|<alias; alias>|<confidence>|<sent_ref>|<description>\n"
    "P|0|<property key>|<value>"
)
_SCHEMATIC_RELATIONSHIP = (
    "EXAMPLE (the shape of a line; use a type from the list above):\n"
    "R|<source index>|<target index>|<relationship type>|<confidence>|<sent_ref>|<justification>"
)


def declared_type_names(node_templates_formatted: str) -> list[str]:
    """The entity type names a formatted node-template list declares, in order."""
    return [m.group("name").strip() for m in _NODE_LINE.finditer(node_templates_formatted)]


def declared_predicates(edge_templates_formatted: str) -> list[str]:
    """The relationship types a formatted edge-template list declares, in order."""
    return [m.group("slug") for m in _EDGE_LINE.finditer(edge_templates_formatted)]


def _rendered_entities(types: Sequence[str]) -> list[_ExampleEntity]:
    """The example entities whose core type the type list declares, in example order."""
    declared = set(types)
    return [entity for entity in _EXAMPLE_ENTITIES if entity.type_name in declared]


def _example_types(shown: Sequence[_ExampleEntity], offered: dict[str, list[str]]) -> list[str]:
    """The type column of each shown example entity: one ``Core/subtype``, the rest core types.

    The first entity one of whose fitting subtypes the type list offers under
    its core type writes it (``Person/character`` under literary); the others,
    and every entity when none fits, write the core type.
    """
    types = [entity.type_name for entity in shown]
    for index, entity in enumerate(shown):
        fit = next((s for s in entity.subtypes if s in offered.get(entity.type_name, ())), None)
        if fit is not None:
            types[index] = qualified_type(entity.type_name, fit)
            break
    return types


def render_entity_format_example(node_templates_formatted: str) -> str:
    """The pass-1 format example: one entity each of Person, Location and Event.

    Every example entity whose core type the type list declares, as an
    ``E|`` line (and its ``P|`` lines), one of them written ``Core/subtype``
    when the list offers a subtype it fits (:func:`_example_types`); the
    schematic line when fewer than two are declared.
    """
    shown = _rendered_entities(declared_type_names(node_templates_formatted))
    if len(shown) < 2:
        return _SCHEMATIC_ENTITY
    types = _example_types(shown, declared_subtypes(node_templates_formatted))
    lines = [f"EXAMPLE ({len(shown)} entities, indices 0-{len(shown) - 1}):"]
    for index, (entity, type_name) in enumerate(zip(shown, types, strict=True)):
        lines.append(
            f"E|{entity.name}|{type_name}|{entity.aliases}|{entity.confidence}|"
            f"{entity.sent_ref}|{entity.description}"
        )
        lines.extend(f"P|{index}|{key}|{value}" for key, value in entity.properties)
    return "\n".join(lines)


def render_relationship_format_example(edge_templates_formatted: str) -> str:
    """The pass-2 format example in the domain's own predicates.

    Each example relationship whose predicates include one the domain lists,
    with that predicate (indices are the example world's, as an illustration
    of the line); the schematic line when none does.
    """
    declared = set(declared_predicates(edge_templates_formatted))
    lines = []
    for rel in _EXAMPLE_RELATIONSHIPS:
        predicate = next((t for t in rel.types if t in declared), None)
        if predicate is not None:
            lines.append(
                f"R|{rel.source}|{rel.target}|{predicate}|{rel.confidence}|{rel.sent_ref}|"
                f"{rel.justification}"
            )
    if not lines:
        return _SCHEMATIC_RELATIONSHIP
    return "\n".join(["EXAMPLE (using indices from entity list):", *lines])


def example_relationship_endpoints(
    node_templates_formatted: str, edge_templates_formatted: str
) -> list[tuple[str | None, str, str | None]]:
    """``(source type, predicate, target type)`` of each rendered example relationship.

    The endpoint types are the ones the pass-1 example renders its entities
    as under the domain's types (None when that entity does not render): what
    a test checks against the domain's edge constraints.
    """
    declared = set(declared_type_names(node_templates_formatted))
    as_type = [e.type_name if e.type_name in declared else None for e in _EXAMPLE_ENTITIES]
    predicates = set(declared_predicates(edge_templates_formatted))
    out = []
    for rel in _EXAMPLE_RELATIONSHIPS:
        predicate = next((t for t in rel.types if t in predicates), None)
        if predicate is not None:
            out.append((as_type[rel.source], predicate, as_type[rel.target]))
    return out
