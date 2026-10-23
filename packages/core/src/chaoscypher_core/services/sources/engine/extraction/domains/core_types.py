# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The core vocabulary every extraction gets, and the map from every old template name onto it.

One source (spec 2026-10-07 § 2-3): the floor plugin's node templates, the packs'
subtypes, the type rescue, the dedup type gate, the commit templates and the
ccx import of a bundle from before the core types (a database is not
migrated in place, 2026-10-08) all read these.

* :data:`CORE_TYPES` — the eight entity types of the floor (``generic.jsonld``).
  Every node's ``entity_type`` is one of them; anything more specific is a
  subtype, carried as ``properties.entity_subtype``.
* :data:`CORE_LABELS` — the floor's relationship vocabulary. A pack adds labels
  the core lacks and never re-declares one of these; ``related_to`` is the
  fallback and is never removed.
* :data:`FLOOR_LIMITS` — the floor's extraction limits. A pack may raise one,
  never lower it.
* :func:`core_type_of` — every node template name the 19 shipped plugins ever
  declared, mapped to ``(core type, subtype)``. The converter
  (``convert_packs``) refuses a name that is not here, so the table is complete
  by construction. A name that means different things in different packs has a
  pack-qualified row (``"news:publication"``) that wins over the plain one for
  that pack. Rows ending ``# review`` are judgement calls for Denis.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Final

from chaoscypher_core.exceptions import OperationError


CORE_TYPES: Final[tuple[str, ...]] = (
    "Person", "Organization", "Location", "Event", "Work", "Concept", "Object", "Group",
)  # fmt: skip
"""The floor's entity types, in the order the floor plugin declares them."""

CORE_LABELS: Final[frozenset[str]] = frozenset({
    # family and personal
    "parent_of", "child_of", "sibling_of", "spouse_of", "related_to", "friend_of", "rival_of",
    "mentor_of", "student_of",
    # membership and work
    "member_of", "leader_of", "employee_of", "founder_of", "owner_of", "part_of",
    # place and time
    "located_in", "born_in", "died_in", "travelled_to", "occurred_at", "occurred_during",
    "preceded_by", "followed_by",
    # participation and action
    "participates_in", "hosts", "attends", "commands", "serves", "allied_with",
    "opposes", "interacts_with", "influences",
    # works and ideas
    "author_of", "created_by", "about", "references", "mentions", "describes", "belongs_to",
    "instance_of", "associated_with",
})  # fmt: skip
"""The floor's relationship labels (canonical form, ``canonical_edge_label``)."""

FLOOR_NAME: Final[str] = "generic"
"""The floor plugin's name: stored rows, manifests and ``--domain generic`` mean "floor only"."""

NOT_RESCUE_KEYWORDS: Final[frozenset[str]] = frozenset({
    "he", "she", "him", "her", "his", "hers", "they", "them", "it", "a man", "a woman",
})  # fmt: skip
"""Words no rescue keyword may be (lowercase): a pronoun or a bare "a man" says nothing of the type.

Until 2026-10-07 the floor's Person keywords held ``he``, ``she``, ``a man`` and
``a woman`` (and biographical's Person ``he``, ``she``), so an untyped entity
whose description said "she sang" or "a city he loved" was rescued to
Person. The converter drops them, the type rescue ignores them and the
conformance test refuses a shipped plugin carrying one."""

FALLBACK_LABEL: Final[str] = "related_to"
"""The label used when no specific one applies; always a core label."""

FLOOR_LIMITS: Final[Mapping[str, int]] = {"max_entity_degree": 80, "max_same_source_type": 20}
"""The floor's ``extraction_limits`` (``FilteringConfig`` fields): a pack may only raise them."""

if FALLBACK_LABEL not in CORE_LABELS or len(CORE_LABELS) < 38:  # pragma: no cover - import guard
    msg = "CORE_LABELS must keep the fallback label and about 40 labels (spec decision 2)"
    raise OperationError(msg, operation="import_core_types")

CoreTypeMap = Mapping[str, tuple[str, str | None]]
"""Old template name (lowercase) -> ``(core type, subtype or None)``."""

_SYNONYMS: Final[frozenset[str]] = frozenset({"item"})
"""Old names that are the core type itself rather than a kind of it (no subtype)."""

# Old template name (as shipped in any plugin, compared case-insensitively) -> core type.
# The subtype is the name in snake_case (``military unit`` -> ``military_unit``), except a
# core type's own name and the _SYNONYMS, which have none. One row per distinct name of the
# 19 plugins as of 2026-10-07, plus a "<pack>:<name>" row where one pack means
# something else by a shared name (core_type_of(name, pack=...) reads it first).
_CORE_TYPE_OF: Final[Mapping[str, str]] = {
    "accessibility guideline": "Work",
    "achievement": "Event",  # review
    "agency": "Organization",
    "analyst": "Person",
    "anatomy": "Object",  # review
    "angelic being": "Person",  # review
    "anomalous object": "Object",
    "api reference": "Work",
    "argument": "Concept",
    "artifact": "Object",
    "assessment": "Work",
    "attack technique": "Concept",
    "author": "Person",
    "award": "Concept",  # review
    "benchmark": "Concept",  # review
    "bill": "Work",
    "biomarker": "Concept",  # review
    "cable": "Work",
    "campaign": "Event",
    "career": "Event",  # review
    "case": "Event",  # review
    "category": "Concept",
    "character": "Person",
    "charge": "Concept",  # review
    "chemical": "Object",  # review
    "class": "Object",  # review
    "classification marking": "Concept",  # review
    "clause": "Work",
    "clinical trial": "Event",
    "color": "Concept",  # review
    "color palette": "Work",  # review
    "commandment": "Concept",
    "communication": "Event",  # review
    "company": "Organization",
    "component": "Object",  # review
    "compound": "Object",  # review
    "concept": "Concept",
    "condition": "Concept",
    "configuration": "Object",  # review
    "configuration item": "Object",  # review
    "conflict": "Event",
    "constant": "Object",  # review
    "contract": "Work",
    "court": "Organization",
    "covenant": "Concept",  # review
    "creation": "Work",
    "crime scene": "Location",
    "cultural achievement": "Work",
    "cultural reference": "Concept",
    "currency": "Concept",  # review
    "dataset": "Work",
    "date": "Event",  # review
    "debate": "Event",
    "decorator": "Object",  # review
    "decree": "Work",
    "definition": "Concept",
    "deity": "Person",  # review
    "denomination": "Organization",  # review
    "design pattern": "Concept",
    "design principle": "Concept",
    "design system": "Work",
    "designer": "Person",
    "detection rule": "Object",  # review
    "diagnostic test": "Concept",  # review
    "diagram": "Work",
    "dialogue": "Work",
    "diplomatic relation": "Event",  # review
    "discipline": "Concept",
    "doctrine": "Concept",
    "dosage": "Concept",
    "drug": "Object",
    "dynasty": "Group",
    "earnings report": "Work",
    "economic event": "Event",
    "education": "Event",  # review
    "educator": "Person",
    "electoral district": "Location",
    "endpoint": "Concept",  # review
    "epistemological position": "Concept",
    "equation": "Concept",
    "equipment": "Object",
    "era": "Event",  # review
    "error code": "Concept",  # review
    "ethical theory": "Concept",
    "event": "Event",
    "evidence": "Object",  # review
    "example": "Concept",  # review
    "exception": "Object",  # review
    "exercise": "Work",
    "experiment": "Event",
    "exploit": "Object",
    "field": "Concept",
    "figure": "Work",
    "financial instrument": "Object",  # review
    "financial metric": "Concept",
    "financial record": "Work",
    "finding": "Concept",
    "foia case": "Work",  # review
    "forensic report": "Work",
    "formula": "Concept",
    "framework": "Concept",
    "function": "Object",  # review
    "fund": "Organization",  # review
    "gene": "Object",  # review
    "genre": "Concept",
    "glossary term": "Concept",
    "government agency": "Organization",
    "grid system": "Concept",
    "guideline": "Work",
    "heuristic": "Concept",
    "historical event": "Event",
    "historical figure": "Person",
    "human source": "Person",  # Denis 2026-10-08: informants, defectors, recruited assets (intelligence)
    "hypothesis": "Concept",
    "ideology": "Concept",
    "index": "Concept",  # review
    "indicator of compromise": "Object",  # review
    "industry": "Concept",
    "infrastructure": "Object",
    "innovation": "Concept",  # review
    "institution": "Organization",
    "instrument": "Object",
    "intelligence report": "Work",
    "interface": "Object",  # review
    "international organization": "Organization",
    "intrusion set": "Group",  # review
    "investigation": "Event",
    "investor": "Person",  # review
    "item": "Object",
    "judge": "Person",
    "jurisdiction": "Location",
    "law": "Work",
    "learning objective": "Concept",
    "learning path": "Work",  # review
    "legacy": "Concept",
    "legacy impact": "Concept",
    "legal action": "Event",
    "legal argument": "Concept",
    "legal doctrine": "Concept",
    "legal filing": "Work",
    "legal term": "Concept",
    "legislation": "Work",
    "lesson": "Work",
    "life event": "Event",
    "literary device": "Concept",
    "literary work": "Work",
    "location": "Location",
    "logical fallacy": "Concept",
    "malware": "Object",
    "manual": "Work",
    "market": "Organization",  # review
    "measure": "Concept",
    "measurement": "Concept",
    "mechanism of action": "Concept",
    "metaphysical claim": "Concept",
    "method": "Concept",
    "metric": "Concept",
    "military unit": "Organization",
    "miracle": "Event",
    "misconception": "Concept",
    "mitigation": "Concept",
    "mockup": "Work",
    "model": "Concept",
    "module": "Work",  # review (educational module; the technical sense is a code module)
    "movement": "Group",  # review
    "narrator": "Person",
    "news event": "Event",
    "note": "Concept",  # review
    "object": "Object",
    "obligation": "Concept",
    "operation": "Event",
    "organism": "Object",  # review
    "organization": "Organization",
    "outcome": "Concept",
    "package": "Object",  # review
    "parameter": "Object",  # review
    "party": "Person",  # review (a person or entity in legal proceedings)
    "pathogen": "Object",  # review
    "patient population": "Group",
    "person": "Person",
    "persona": "Person",  # review
    "personal relationship": "Concept",  # review
    "phenomenon": "Concept",
    "philosopher": "Person",
    "philosophical concept": "Concept",
    "philosophical era": "Event",  # review
    "philosophical problem": "Concept",
    "philosophical tradition": "Concept",
    "philosophical work": "Work",
    "philosophy": "Concept",
    "plot element": "Event",
    "policy": "Concept",  # review
    "political entity": "Organization",  # review (a state as an actor; countries as places are Location)
    "political event": "Event",
    "political figure": "Person",
    "political movement": "Group",  # review
    "political party": "Organization",
    "portfolio": "Object",  # review
    "practice": "Concept",
    "precedent": "Work",
    "prerequisite": "Concept",
    "primary source": "Work",
    "principle": "Concept",
    "procedure": "Concept",  # review (medical procedure and documented procedure)
    "process": "Concept",
    "product": "Object",
    "program": "Event",  # review
    "prophet": "Person",
    "protocol": "Concept",
    "provision": "Work",  # review
    "publication": "Work",  # review (news outlet in news; publication in scientific)
    "quote": "Work",
    "region": "Location",
    "regulation": "Work",
    "regulator": "Organization",
    "religion": "Concept",  # review
    "religious event": "Event",
    "religious figure": "Person",
    "religious law": "Work",  # review
    "religious tradition": "Concept",  # review
    "remedy": "Concept",
    "requirement": "Concept",
    "researcher": "Person",
    "residence": "Location",
    "resource": "Work",
    "right": "Concept",
    "risk": "Concept",
    "risk factor": "Concept",
    "route": "Object",  # review
    "rule": "Concept",
    "sacred place": "Location",
    "sacred text": "Work",
    "sanction": "Concept",  # review
    "school of thought": "Concept",
    "sect": "Group",  # review
    "section": "Work",
    "sector": "Concept",
    "security framework": "Work",  # review
    "security incident": "Event",
    "security researcher": "Person",
    "sensor": "Object",
    "service": "Object",
    "setting": "Location",
    "side effect": "Concept",
    "sighting": "Event",
    "skill": "Concept",
    "social group": "Group",
    "software": "Object",
    "source": "Person",  # review (an intelligence or news source; may be an asset)
    "species": "Concept",  # review
    "specification": "Work",
    "standard": "Work",
    "standards body": "Organization",
    "statement": "Work",  # review
    "statute": "Work",
    "step": "Concept",  # review
    "structural unit": "Work",
    "subject": "Concept",
    "symbol": "Concept",
    "symptom": "Concept",
    "target": "Organization",  # review
    "taxonomy": "Concept",
    "technology": "Object",  # review
    "territory": "Location",
    "testimony": "Work",
    "theme": "Concept",
    "theological concept": "Concept",
    "theological debate": "Event",
    "theory": "Concept",
    "thesis": "Concept",
    "thought experiment": "Concept",
    "threat actor": "Group",  # review (marked names_a_person; APT groups are collectives)
    "time period": "Event",  # review
    "tool": "Object",
    "topic": "Concept",
    "trade route": "Location",
    "transaction": "Event",
    "treatment": "Concept",
    "treaty": "Work",
    "type": "Concept",
    "typealias": "Object",  # review
    "typeface": "Work",  # review
    "user research method": "Concept",
    "variable": "Concept",
    "vehicle": "Object",
    "version": "Concept",  # review
    "vulnerability": "Concept",
    "weapon": "Object",
    "witness": "Person",
    "work": "Work",
    # pack-qualified rows: the same name, another meaning in that pack
    "news:publication": "Organization",  # review (a news outlet, not a published work)
    "technical:module": "Object",  # review (a code module, beside package and class)
    "technical:type": "Object",  # review (a struct, enum or generic type: a code construct)
    "intelligence:source": "Concept",  # Denis 2026-10-08: a collection source (a provenance); a person is human_source
    "political:political entity": "Location",  # Denis 2026-10-08: a state or nation; bodies are Organization subtypes
}

PACK_ALIASES: Final[Mapping[str, Mapping[str, str]]] = {
    "intelligence": {
        "Asset": "Human Source",
        "Informant": "Human Source",
        "Defector": "Human Source",
    },
    "political": {
        "Parliament": "Institution",
        "Legislature": "Institution",
        "Bloc": "International Organization",
        "Political Bloc": "International Organization",
        "Party": "Political Party",
    },
}
"""Written names a pack reads as one of its subtypes (Denis, 2026-10-08), beside its own aliases.

The mapping splits of the table review: intelligence's informants, defectors
and recruited assets are people (``Person``/``human_source``) while its
``Source`` stays a collection source (``Concept``); political bodies
(parliaments, blocs, parties) are ``Organization`` subtypes while a
``Political Entity`` is a state (``Location``). The converter
(``convert_packs``) writes these into each pack's ``type_aliases``, so the
rescue's alias step reads them; a pack's own alias of the same name is
replaced."""

_CORE_BY_LOWER: Final[Mapping[str, str]] = {t.lower(): t for t in CORE_TYPES}


def subtype_key(name: str) -> str:
    """A subtype name as stored in ``entity_subtype``: lowercase, spaces as ``_``."""
    return "_".join(name.strip().lower().split())


SUBTYPE_SEPARATOR: Final[str] = "/"
"""What joins a core type and a subtype in the type column the model writes (``Person/character``)."""


def qualified_type(core: str, subtype: str) -> str:
    """A subtype as the model writes it in the type column: ``Person/character``.

    The one writer of the form (the prompt's node lines and format example);
    :func:`split_qualified_type` is its reader.
    """
    return f"{core}{SUBTYPE_SEPARATOR}{subtype_key(subtype)}"


def split_qualified_type(written: str | None) -> tuple[str, str] | None:
    """``(core type, subtype key)`` of a written ``Core/subtype``; None for any other written type.

    The left side is read as a core type in any case (``person/Character`` is
    ``("Person", "character")``); a left side that is no core type, or an
    empty subtype, is not this form. Whether the subtype is one the packs
    offer, and under which core type, is the type rescue's one decision
    (``type_rescue.TypeVocabulary.decide``).
    """
    left, separator, right = (written or "").partition(SUBTYPE_SEPARATOR)
    if not separator:
        return None
    core = _CORE_BY_LOWER.get(_key(left))
    subtype = subtype_key(right)
    if core is None or not subtype:
        return None
    return core, subtype


def _row(key: str, core: str) -> tuple[str, str | None]:
    """``(core type, subtype)`` for a table key: no subtype when the name is the core type or a synonym."""
    name = key.split(":", 1)[-1]
    if name in _CORE_BY_LOWER or name in _SYNONYMS:
        return core, None
    return core, subtype_key(name)


def _key(name: str) -> str:
    """A name as the table keys it: lowercase, ``_`` read as a space (``military_unit``), spaces collapsed."""
    return " ".join(name.replace("_", " ").strip().lower().split())


_SUBTYPE_OF: Final[CoreTypeMap] = {name: _row(name, core) for name, core in _CORE_TYPE_OF.items()}
"""The mapping table: old template name (lowercase) -> ``(core type, subtype or None)``."""


def core_type_of(name: str, pack: str | Sequence[str] | None = None) -> tuple[str, str | None]:
    """The core type and subtype an old template name maps to; KeyError for a name not in the table.

    The one precedence of the table: a core type's own name is itself; with
    ``pack`` (one pack name, or a stack's pack names in stack order), the
    first pack-qualified row (``"<pack>:<name>"``) wins over the plain one.
    The name is read case-insensitively, ``_`` as a space, so the subtype
    key form (``military_unit``) is the name too. Unknown names are refused
    rather than guessed: the type rescue decides what to do with a type the
    table does not know.
    """
    key = _key(name)
    if key in _CORE_BY_LOWER:
        return _CORE_BY_LOWER[key], None
    packs = (pack,) if isinstance(pack, str) else tuple(pack or ())
    for each in packs:
        qualified = _SUBTYPE_OF.get(f"{_key(each)}:{key}")
        if qualified is not None:
            return qualified
    return _SUBTYPE_OF[key]


def is_core_type(name: str) -> bool:
    """Whether ``name`` is exactly one of the :data:`CORE_TYPES`."""
    return name in CORE_TYPES


def mapping_table() -> CoreTypeMap:
    """The whole mapping table (old name, lowercase -> ``(core type, subtype)``), read-only.

    Pack-qualified keys are ``"<pack>:<name>"``.
    """
    return _SUBTYPE_OF


__all__ = [
    "CORE_LABELS",
    "CORE_TYPES",
    "FALLBACK_LABEL",
    "FLOOR_LIMITS",
    "FLOOR_NAME",
    "NOT_RESCUE_KEYWORDS",
    "PACK_ALIASES",
    "SUBTYPE_SEPARATOR",
    "CoreTypeMap",
    "core_type_of",
    "is_core_type",
    "mapping_table",
    "qualified_type",
    "split_qualified_type",
    "subtype_key",
]
