# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Convert domain plugins to the additive shape: ``generic`` becomes the floor, every other a pack.

Spec 2026-10-07 "additive domains" § 3. Run it over the shipped plugins (the
default) or over a user's own ``.jsonld`` files::

    python -m chaoscypher_core.services.sources.engine.extraction.domains.convert_packs
    python -m chaoscypher_core.services.sources.engine.extraction.domains.convert_packs --write
    python -m chaoscypher_core.services.sources.engine.extraction.domains.convert_packs --write my.jsonld

Without ``--write`` it reports what it would change. It is idempotent: a pack
already converted is left as it is, and the floor's edge templates are
rewritten from :data:`CORE_EDGES` (unchanged when they already match), so a
second run changes nothing. A node template whose name is not in the mapping
table (``core_types``) is printed as ``missing: <plugin>: <name>`` and the run
exits 1 before writing anything.

What a pack conversion does: every node template becomes a ``subtypes`` entry
of its core type (a name that *is* a core type, or a synonym of one, folds into
it, its properties kept as the pack's ``core_properties``); every edge template whose label is a core label is dropped (the floor
declares it); edge endpoint types and type-alias targets are mapped onto the
core types; ``entity_examples`` become E-lines the real line parser reads; an
``extraction_limits`` value below the floor's is dropped (the floor's applies);
a template's ``normalization_keywords`` become its subtype's ``rescue_keywords``
(a folded template's, the pack's ``core_rescue_keywords`` of its core type: the
type rescue's pack keyword tier); ``compatibility_group``, ``absorbs_types``,
``strict_entity_types`` and the removed legacy keys are deleted.

A pack converted before 2026-10-07 lost its templates' keywords; ``--keywords-from
DIR`` gives each pack the keywords of the unconverted plugin of the same file
name in ``DIR`` (:func:`restore_rescue_keywords`; idempotent).
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from chaoscypher_core.exceptions import OperationError, ValidationError
from chaoscypher_core.services.sources.engine.extraction.domains.config_schema import REMOVED_KEYS
from chaoscypher_core.services.sources.engine.extraction.domains.core_types import (
    CORE_LABELS,
    CORE_TYPES,
    FLOOR_LIMITS,
    FLOOR_NAME,
    NOT_RESCUE_KEYWORDS,
    PACK_ALIASES,
    core_type_of,
)
from chaoscypher_core.services.sources.engine.extraction.domains.stack import (
    PACK_CONTRIBUTION_REPORT_CHARS,
)
from chaoscypher_core.services.sources.engine.extraction.utils.relation_vocabulary import (
    UNIVERSAL_VOCABULARY,
    canonical_edge_label,
    label_evidence_words,
)


_P, _O, _L, _E, _W, _C, _J, _G = CORE_TYPES
_ALL = list(CORE_TYPES)
_AGENTS = [_P, _O, _G]


@dataclass(frozen=True)
class CoreEdge:
    """One core label's floor template: what it states and between which core types."""

    description: str
    source_types: list[str] = field(default_factory=list)
    target_types: list[str] = field(default_factory=list)
    symmetric: bool = False
    inverse: str | None = None
    icon: str = "Link"
    color: str = "#cc7e32"
    quality_score: int = 20
    reason: str | None = None
    """``two_endpoints_only_reason`` when the label is not stated by the universal vocabulary."""


# The floor's edge templates (descriptions taken from the plugins that defined the label,
# generalised over the core types). One source: the converter writes generic.jsonld's
# edge templates from this table and the conformance test pins the file to it.
CORE_EDGES: dict[str, CoreEdge] = {
    # family and personal
    "parent_of": CoreEdge("Person is the parent of another person", [_P], [_P],
                          inverse="child_of", icon="FamilyRestroom", color="#3281cc", quality_score=25),
    "child_of": CoreEdge("Person is the child of another person", [_P], [_P],
                         inverse="parent_of", icon="FamilyRestroom", color="#3281cc", quality_score=25),
    "sibling_of": CoreEdge("Person is the brother or sister of another person", [_P], [_P],
                           symmetric=True, icon="People", color="#3281cc", quality_score=25),
    "spouse_of": CoreEdge("Person is married to another person", [_P], [_P],
                          symmetric=True, icon="Favorite", color="#3295cc", quality_score=25),
    # every core type on both ends, declared: strict edge constraints drop a label with none
    "related_to": CoreEdge("Generic relationship when no specific type applies", _ALL, _ALL,
                           symmetric=True, icon="Link", color="#cc7e32", quality_score=5),
    "friend_of": CoreEdge("Person is a friend of another person", [_P], [_P],
                          symmetric=True, icon="Handshake", color="#32cc8a"),
    "rival_of": CoreEdge("Person, organization or group is a rival, enemy or competitor of another",
                         _AGENTS, _AGENTS, symmetric=True, icon="CompareArrows", color="#32ccb2"),
    "mentor_of": CoreEdge("Person teaches, mentors or guides another person", [_P], [_P],
                          inverse="student_of", icon="School", color="#53cc32"),
    "student_of": CoreEdge("Person is a student, pupil, apprentice or disciple of another person",
                           [_P], [_P], inverse="mentor_of", icon="School", color="#3282cc",
                           quality_score=25),
    # membership and work
    "member_of": CoreEdge("Person, organization or group is a member of an organization or group "
                          "(a family, household, party, regiment, alliance)", _AGENTS, [_O, _G],
                          icon="Groups", color="#cc7d32"),
    "leader_of": CoreEdge("Person leads, heads or rules an organization, group or place",
                          [_P], [_O, _G, _L], icon="Star", color="#3632cc", quality_score=25),
    "employee_of": CoreEdge("Person works at or for an organization", [_P], [_O],
                            icon="Work", color="#32cc43", quality_score=25),
    "founder_of": CoreEdge("Person, organization or group founded or established an organization, "
                           "group, place or work", _AGENTS, [_O, _G, _L, _W],
                           icon="AccountBalance", color="#9b32cc", quality_score=25),
    "owner_of": CoreEdge("Person, organization or group owns or possesses an object, work, place "
                         "or organization", _AGENTS, [_J, _W, _L, _O],
                         icon="Inventory2", color="#ccb932", quality_score=25),
    "part_of": CoreEdge("Entity is a component or subdivision of another", _ALL, _ALL,
                        icon="AccountTree", color="#32cc89", quality_score=15),
    # place and time
    "located_in": CoreEdge("Entity is located, based, situated, or geographically anchored in a "
                           "place", _ALL, [_L], icon="Place", color="#32cc66", quality_score=15),
    "born_in": CoreEdge("Person was born in a place or during a period", [_P], [_L, _E],
                        icon="Place", color="#32cc55", quality_score=15),
    "died_in": CoreEdge("Person died in a place or during a period", [_P], [_L, _E],
                        icon="Place", color="#cc3255", quality_score=15),
    "travelled_to": CoreEdge("Person, organization or group travelled, journeyed or went to a place",
                             _AGENTS, [_L], icon="Explore", color="#32a4cc", quality_score=15),
    "occurred_at": CoreEdge("Event occurred, took place, or unfolded at a location", [_E], [_L],
                            icon="Place", color="#cc3732", quality_score=15),
    "occurred_during": CoreEdge("Entity occurred, existed, or unfolded during an event or period",
                                _ALL, [_E], icon="CalendarMonth", color="#cc5a32",
                                quality_score=15),
    "preceded_by": CoreEdge("Entity came after another (the target came first): an event, a work, "
                            "an office holder", [_P, _O, _G, _E, _W, _C], [_P, _O, _G, _E, _W, _C],
                            inverse="followed_by", icon="Timeline", color="#b832cc",
                            quality_score=15),
    "followed_by": CoreEdge("Entity came before another (the target came next): an event, a work, "
                            "an office holder", [_P, _O, _G, _E, _W, _C], [_P, _O, _G, _E, _W, _C],
                            inverse="preceded_by", icon="Timeline", color="#b832cc",
                            quality_score=15),
    # participation and action
    "participates_in": CoreEdge("Person, organization or group takes part in an event, battle or "
                                "ceremony", _AGENTS, [_E], icon="EmojiEvents", color="#cc9132",
                                quality_score=25),
    "hosts": CoreEdge("Person, organization, group or place hosts an event (a reception, ball, "
                      "dinner, meeting or conference)", [_P, _O, _G, _L], [_E],
                      icon="Celebration", color="#41d67d"),
    "attends": CoreEdge("Person or group attends an event or an institution", [_P, _G], [_E, _O],
                        icon="Event", color="#41a6d6"),
    "commands": CoreEdge("Person or organization gives military or authority orders to a person, "
                         "unit or group", [_P, _O], [_P, _O, _G], icon="MilitaryTech",
                         color="#32cc8a"),
    "serves": CoreEdge("Person, organization or group serves, attends, or works for a person, "
                       "household, organization or place", _AGENTS, [*_AGENTS, _L], icon="Work",
                       color="#cc7d32", quality_score=25,
                       reason=("Replayed on the v3 corpus (audit wave 6) the role words as evidence "
                               "(valet, steward, footman) add two false edges: a servant named "
                               "beside someone he does not serve.")),
    "allied_with": CoreEdge("Person, organization, group or state is allied with another",
                            [*_AGENTS, _L], [*_AGENTS, _L], symmetric=True, icon="Handshake",
                            color="#61cc32", quality_score=25),
    "opposes": CoreEdge("Person, organization or group opposes another, or a policy, idea, law or "
                        "event", [*_AGENTS, _L], [*_AGENTS, _L, _C, _W, _E], icon="ThumbDown",
                        color="#8932cc", quality_score=25),
    "interacts_with": CoreEdge("Entity meets, talks to, deals with or acts on another (use a "
                               "specific label when one fits)", _ALL, _ALL, symmetric=True,
                               icon="SyncAlt", color="#bf32cc", quality_score=15),
    "influences": CoreEdge("Entity influences, affects, or impacts another", _ALL, _ALL,
                           icon="AutoAwesome", color="#32c7cc", quality_score=25),
    # works and ideas
    "author_of": CoreEdge("Person, organization or group wrote, composed or made a work", _AGENTS,
                          [_W], inverse="created_by", icon="Create", color="#32ccc6",
                          quality_score=25),
    "created_by": CoreEdge("Work, object, idea, event or organization was created, authored, "
                           "founded, or produced by a person, organization or group",
                           [_W, _J, _C, _E, _O], _AGENTS, inverse="author_of", icon="Create",
                           color="#32ccad", quality_score=25),
    "about": CoreEdge("Work is about, or has as its subject, an entity", [_W], _ALL,
                      icon="Article", color="#3270cc", quality_score=15),
    "references": CoreEdge("Work or person cites, alludes to, or invokes another entity, work or "
                           "idea", [_W, _P, _O], _ALL, icon="Link", color="#cacc32",
                           quality_score=15),
    "mentions": CoreEdge("Work or document mentions or refers to an entity in passing", [_W], _ALL,
                         icon="Article", color="#325dcc", quality_score=15),
    "describes": CoreEdge("Work, person or organization describes or depicts an entity",
                          [_W, _P, _O], _ALL, icon="Description", color="#3248cc",
                          quality_score=15),
    "belongs_to": CoreEdge("Entity belongs to, falls under or is classed in a group, organization, "
                           "category or topic", _ALL, [_O, _G, _C], icon="Category",
                           color="#3280cc", quality_score=15),
    "instance_of": CoreEdge("Entity is an instance, example, exemplar, or member of a category, "
                            "type, concept, or topic", _ALL, [_C], icon="Category",
                            color="#afcc32", quality_score=15),
    "associated_with": CoreEdge("Entity is associated or connected with another (use a specific "
                                "label when one fits)", _ALL, _ALL, symmetric=True,
                                icon="People", color="#32cc5b", quality_score=15),
}  # fmt: skip

if set(CORE_EDGES) != CORE_LABELS:  # pragma: no cover - import guard
    msg = f"CORE_EDGES and CORE_LABELS differ: {sorted(set(CORE_EDGES) ^ CORE_LABELS)}"
    raise OperationError(msg, operation="import_convert_packs")

LABEL_RENAMES: dict[tuple[str, str], str] = {
    ("financial", "parent_of"): "parent_company_of",
    ("cybersecurity", "hosts"): "hosts_resource",
}
"""Pack labels spelled like a core label but stating something else (a parent company is
no parent; infrastructure hosting malware hosts no event): renamed, not dropped."""

FLOOR_DESCRIPTIONS: dict[str, str] = {
    "Organization": (
        "A company, institution, government body, agency or other formally constituted body"
    ),
}
"""Floor descriptions rewritten so no core type's description names another core type."""

KEYWORD_OWNERS: dict[str, str] = {
    "group": "Group",  # a collective of people (Organization's and Category's 'group' merged in)
    "class": "Concept",  # a class of things (from Category/Type), not a social class
    "area": "Location",  # a geographic area (Topic's 'subject area' is Concept's 'subject')
    "product": "Object",  # a product is a thing (Item); a creative output is Work's 'artwork'
}
"""Which core type a normalization keyword shared by two merged templates belongs to."""

_NEW_GROUP_TEMPLATE: dict[str, Any] = {
    "@type": "NodeTemplate",
    "id": "gen_template_group",
    "name": "Group",
    "description": (
        "A family, household, social class, faction, crowd or other collective of people "
        "without a formal charter or membership"
    ),
    "icon": "Groups",
    "color": "#d6418a",
    "requires_named_referent": True,
    "quality_score": 20,
    "normalization_keywords": [
        "family", "household", "clan", "tribe", "faction", "class", "crowd", "community",
        "dynasty", "gang", "circle",
    ],
}  # fmt: skip


@dataclass(frozen=True)
class SubtypeSplit:
    """A subtype split off another subtype of the same pack (the table review, Denis 2026-10-08)."""

    pack: str
    from_subtype: str
    """The subtype it splits off; its entry is placed right after this one."""
    subtype: dict[str, Any]
    """The new subtype's entry, as a converted pack writes it."""
    keywords: tuple[str, ...]
    """The rescue keywords it takes from ``from_subtype`` (they leave that subtype)."""


SUBTYPE_SPLITS: tuple[SubtypeSplit, ...] = (
    SubtypeSplit(
        pack="intelligence",
        from_subtype="Source",
        subtype={
            "name": "Human Source",
            "of": "Person",
            "description": "A person who supplies intelligence: an informant, defector or recruited asset",
            "properties": [
                {"name": "cryptonym", "display_name": "Cryptonym", "property_type": "text"},
                {"name": "reliability", "display_name": "Reliability Rating", "property_type": "text"},
            ],
            "names_a_person": True,
            "icon": "RecordVoiceOver",
            "color": "#4174d6",
            "quality_score": 25,
            "requires_named_referent": False,
            "rescue_keywords": ["human source"],
        },
        keywords=("asset", "informant", "defector", "human source"),
    ),
)  # fmt: skip
"""Subtypes the mapping review split off: intelligence's people who inform are Person/human_source
(their aliases are :data:`core_types.PACK_ALIASES`); its ``Source`` stays a collection source."""

SUBTYPE_DESCRIPTIONS: dict[tuple[str, str], str] = {
    ("intelligence", "Source"): (
        "A collection source as a provenance: a SIGINT intercept, imagery, an open source or a "
        "reporting channel (a person who informs is a Human Source)"
    ),
    ("political", "Political Entity"): (
        "A state or nation as a political unit (a parliament, bloc or party is an organization)"
    ),
}
"""Pack subtype descriptions the mapping review narrowed (Denis, 2026-10-08)."""


def _moved_keywords(plugin: str, subtype: str) -> set[str]:
    """The rescue keywords a split took from ``subtype`` of ``plugin`` (lowercase)."""
    return {
        w.lower()
        for split in SUBTYPE_SPLITS
        if split.pack == plugin and split.from_subtype == subtype
        for w in split.keywords
    }


def split_keywords(plugin: str, template: str) -> dict[str, set[str]]:
    """Where a split moved an original template's rescue keywords: ``{subtype name: words}``.

    The conformance test's reading: a word the old template carried is held by
    the subtype a split gave it to rather than by the template's own subtype.
    """
    return {
        str(split.subtype["name"]): {w.lower() for w in split.keywords}
        for split in SUBTYPE_SPLITS
        if split.pack == plugin and split.from_subtype == template
    }


def apply_mapping_splits(pack: dict[str, Any]) -> bool:
    """Apply the mapping review's splits to a converted pack (idempotent).

    Adds each :data:`SUBTYPE_SPLITS` subtype after the one it splits off and
    moves its rescue keywords there, writes the :data:`SUBTYPE_DESCRIPTIONS`,
    and sets the pack's :data:`core_types.PACK_ALIASES` in its
    ``type_aliases``.

    Returns:
        Whether ``pack`` changed (it is modified in place).
    """
    plugin = str(pack.get("name") or "")
    subtypes: list[dict[str, Any]] = pack.get("subtypes") or []
    changed = False
    for split in (s for s in SUBTYPE_SPLITS if s.pack == plugin):
        names = [str(s.get("name")) for s in subtypes]
        name = str(split.subtype["name"])
        if name not in names:
            at = names.index(split.from_subtype) + 1 if split.from_subtype in names else len(names)
            subtypes.insert(at, copy.deepcopy(split.subtype))
            changed = True
        target = next(s for s in subtypes if s.get("name") == name)
        merged = _merge_keywords(list(target.get("rescue_keywords") or []), list(split.keywords))
        if merged != target.get("rescue_keywords"):
            target["rescue_keywords"] = merged
            changed = True
        source = next((s for s in subtypes if s.get("name") == split.from_subtype), None)
        if source is not None:
            moved = _moved_keywords(plugin, split.from_subtype)
            kept = [w for w in source.get("rescue_keywords") or [] if w.lower() not in moved]
            if kept != list(source.get("rescue_keywords") or []):
                source["rescue_keywords"] = kept
                changed = True
    for (owner, name), description in SUBTYPE_DESCRIPTIONS.items():
        entry = next((s for s in subtypes if s.get("name") == name), None)
        if owner == plugin and entry is not None and entry.get("description") != description:
            entry["description"] = description
            changed = True
    if subtypes:
        pack["subtypes"] = subtypes
    aliases = PACK_ALIASES.get(plugin)
    if aliases:
        have = dict(pack.get("type_aliases") or {})
        want = {**have, **aliases}
        if want != have:
            pack["type_aliases"] = want
            changed = True
    return changed


@dataclass
class Conversion:
    """What converting one plugin did (or would do)."""

    plugin: str
    config: dict[str, Any]
    changed: bool = False
    missing: list[str] = field(default_factory=list)
    subtypes: int = 0
    folded: list[str] = field(default_factory=list)
    """Node templates that are a core type (or a synonym of one): folded into it."""
    folded_properties: dict[str, list[str]] = field(default_factory=dict)
    """Properties of the folded templates, kept as the pack's ``core_properties``."""
    dropped_labels: list[str] = field(default_factory=list)
    renamed_labels: dict[str, str] = field(default_factory=dict)
    dropped_aliases: list[str] = field(default_factory=list)
    dropped_limits: dict[str, float] = field(default_factory=dict)
    dropped_person_flags: list[str] = field(default_factory=list)
    pass_chars: tuple[int, int] = (0, 0)
    """Raw prompt-size estimate: (subtype descriptions, edge descriptions), in characters."""


def core_edge_templates() -> list[dict[str, Any]]:
    """The floor's edge templates, one per core label, as ``generic.jsonld`` declares them.

    A label the universal relation vocabulary states may rest on one named
    endpoint; every other is ``two_endpoints_only``, with a reason when its
    label carries an evidence word (the guard tests in ``test_relation_vocabulary``).
    """
    out: list[dict[str, Any]] = []
    for label, spec in CORE_EDGES.items():
        tmpl: dict[str, Any] = {
            "@type": "EdgeTemplate",
            "id": f"gen_edge_{label}",
            "name": label,
            "description": spec.description,
            "icon": spec.icon,
            "color": spec.color,
            "quality_score": spec.quality_score,
        }
        if spec.source_types:
            tmpl["source_types"] = list(spec.source_types)
        if spec.target_types:
            tmpl["target_types"] = list(spec.target_types)
        if spec.symmetric:
            tmpl["symmetric"] = True
        if spec.inverse:
            tmpl["inverse"] = spec.inverse
        if not UNIVERSAL_VOCABULARY.relation_words(label) and label != "related_to":
            tmpl["two_endpoints_only"] = True
            if spec.reason or label_evidence_words(label):
                tmpl["two_endpoints_only_reason"] = spec.reason or "unmeasured"
        out.append(tmpl)
    return out


def _core_of(name: str, result: Conversion) -> str | None:
    """The core type ``name`` maps to in this pack; None (recorded as missing) when the table lacks it."""
    try:
        return core_type_of(name, pack=result.plugin)[0]
    except KeyError:
        if name not in result.missing:
            result.missing.append(name)
        return None


def _core_types(names: list[str], result: Conversion) -> list[str]:
    """``names`` (old template names) as their core types, each once, in order."""
    return list(dict.fromkeys(c for n in names if (c := _core_of(n, result)) is not None))


def _clean_properties(props: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Property templates without ``absorbs_types`` (the rescue reads subtypes instead)."""
    return [{k: v for k, v in p.items() if k != "absorbs_types"} for p in props]


def _bump_major(version: Any) -> str:
    """``1.9.0`` -> ``2.0.0``: the shape changed."""
    try:
        major = int(str(version).split(".")[0])
    except ValueError:
        major = 0
    return f"{major + 1}.0.0"


def _ordered(config: dict[str, Any], first: dict[str, Any]) -> dict[str, Any]:
    """``config`` with ``first``'s keys placed after ``description`` (a readable file)."""
    out: dict[str, Any] = {}
    for key, value in config.items():
        if key in first:
            continue
        out[key] = value
        if key == "description":
            out.update(first)
    for key, value in first.items():
        out.setdefault(key, value)
    return out


def _convert_floor(config: dict[str, Any], result: Conversion) -> dict[str, Any]:
    """The generic plugin as the floor: the eight core types, the core labels, strict types."""
    edges = core_edge_templates()
    if config.get("role") == "floor":
        if config.get("templates", {}).get("edge_templates") == edges:
            return config
        return {**config, "templates": {**config["templates"], "edge_templates": edges}}

    old = {t["name"]: t for t in config.get("templates", {}).get("node_templates", [])}
    keywords: dict[str, list[str]] = {t: [] for t in CORE_TYPES}
    for name, tmpl in old.items():
        keywords[core_type_of(name)[0]].extend(_keywords(tmpl))
    source = {t: old.get(t) for t in CORE_TYPES}
    source["Object"] = old.get("Object") or old.get("Item")
    nodes: list[dict[str, Any]] = []
    for core in CORE_TYPES:
        base = source.get(core)
        tmpl = dict(base) if base else dict(_NEW_GROUP_TEMPLATE) if core == "Group" else None
        if tmpl is None:
            msg = f"the floor has no template to carry over for {core}"
            raise ValidationError(msg, field="templates.node_templates")
        tmpl["name"] = core
        tmpl["id"] = f"gen_template_{core.lower()}"
        tmpl.pop("compatibility_group", None)
        if "properties" in tmpl:
            tmpl["properties"] = _clean_properties(tmpl["properties"])
        if core in FLOOR_DESCRIPTIONS:
            tmpl["description"] = FLOOR_DESCRIPTIONS[core]
        keywords[core] = list(dict.fromkeys([*_keywords(tmpl), *keywords[core]]))
        nodes.append(tmpl)
    owned = _owned_keywords(keywords)
    for tmpl in nodes:
        if owned[tmpl["name"]]:
            tmpl["normalization_keywords"] = owned[tmpl["name"]]
        else:
            tmpl.pop("normalization_keywords", None)
    result.folded = [n for n in old if n not in CORE_TYPES]

    limits = config.get("extraction_limits") or {}
    if limits and limits != dict(FLOOR_LIMITS):
        msg = f"generic's extraction_limits {limits} differ from core_types.FLOOR_LIMITS"
        raise ValidationError(msg, field="extraction_limits")
    dropped = ("extraction_limits", "detection", *REMOVED_KEYS)  # the floor is never ranked
    new = {k: v for k, v in config.items() if k not in dropped}
    new["version"] = _bump_major(config.get("version"))
    new["strict_entity_types"] = True
    new["templates"] = {"node_templates": nodes, "edge_templates": edges}
    return _ordered(new, {"role": "floor"})


def _owned_keywords(keywords: dict[str, list[str]]) -> dict[str, list[str]]:
    """Each normalization keyword on exactly one core type: :data:`KEYWORD_OWNERS`, else its one holder.

    A keyword naming a core type belongs to it. A keyword two types hold with no
    owner declared is an error (the rescue would route it two ways).
    """
    holders: dict[str, list[str]] = {}
    for core, words in keywords.items():
        for word in words:
            holders.setdefault(word.lower(), []).append(core)
    owners = {**KEYWORD_OWNERS, **{t.lower(): t for t in CORE_TYPES}}
    unresolved = sorted(w for w, h in holders.items() if len(set(h)) > 1 and w not in owners)
    if unresolved:
        msg = f"floor keywords on two core types with no owner: {unresolved}"
        raise ValidationError(msg, field="normalization_keywords")
    out: dict[str, list[str]] = {core: [] for core in keywords}
    for core, words in keywords.items():
        out[core] = [w for w in words if owners.get(w.lower(), core) == core]
    for word, core in owners.items():
        if word in holders and word not in [w.lower() for w in out[core]]:
            out[core].append(word)
    return out


def _entity_example_lines(
    examples: list[dict[str, Any]], result: Conversion
) -> list[dict[str, Any]]:
    """Today's ``entity_examples`` as E-lines in the core types, the subtype in the note."""
    out: list[dict[str, Any]] = []
    for ex in examples:
        if "line" in ex:
            out.append(ex)
            continue
        written = str(ex.get("type", ""))
        core = _core_of(written, result)
        if core is None:
            continue
        names = [ex["name"]] if ex.get("name") else list(ex.get("examples") or [])
        if ex.get("entity"):
            names = [ex["entity"]]
        aliases = "; ".join(ex.get("aliases") or [])
        note = ex.get("note") or ""
        for name in names:
            text = ex.get("text") or (f"{written}: {note}" if note else f"A {written}.")
            out.append(
                {
                    "line": f"E|{name}|{core}|{aliases}|1.0|S1|{text}",
                    "note": f"subtype {written}" + (f"; {note}" if note else ""),
                }
            )
    return out


def _rename_in_text(text: str, renames: dict[str, str]) -> str:
    """``text`` with each old label in ``renames`` replaced by its new one, as a whole word."""
    for old, new in renames.items():
        text = re.sub(rf"(?<![\w-]){re.escape(old)}(?![\w-])", new, text)
    return text


def _keywords(tmpl: dict[str, Any]) -> list[str]:
    """A template's ``normalization_keywords``, each once, in order, :data:`NOT_RESCUE_KEYWORDS` left out."""
    words = (str(k) for k in tmpl.get("normalization_keywords") or [])
    return list(dict.fromkeys(w for w in words if w.strip().lower() not in NOT_RESCUE_KEYWORDS))


def _merge_keywords(have: list[str], add: list[str]) -> list[str]:
    """``have`` followed by the words of ``add`` it lacks."""
    return list(dict.fromkeys([*have, *add]))


def restore_rescue_keywords(pack: dict[str, Any], original: dict[str, Any]) -> bool:
    """Give a converted pack its original templates' ``normalization_keywords`` as rescue keywords.

    A template that became a subtype gives its subtype's ``rescue_keywords``;
    one folded into a core type gives the pack's ``core_rescue_keywords`` of
    that core type. Words already there are kept (idempotent). ``original``
    is the plugin as it was before conversion (its ``templates.node_templates``).

    Returns:
        Whether ``pack`` changed (it is modified in place).
    """
    plugin = str(pack.get("name") or original.get("name") or "")
    by_name = {str(s.get("name")): s for s in pack.get("subtypes") or []}
    core_keywords: dict[str, list[str]] = dict(pack.get("core_rescue_keywords") or {})
    changed = False
    for tmpl in (original.get("templates") or {}).get("node_templates") or []:
        words = _keywords(tmpl)
        name = str(tmpl.get("name", ""))
        if not words or not name:
            continue
        core, sub = core_type_of(name, pack=plugin)
        if sub is None:
            merged = _merge_keywords(core_keywords.get(core, []), words)
            changed = changed or merged != core_keywords.get(core)
            core_keywords[core] = merged
            continue
        entry = by_name.get(name)
        if entry is None:
            continue
        merged = _merge_keywords(list(entry.get("rescue_keywords") or []), words)
        if merged != entry.get("rescue_keywords"):
            entry["rescue_keywords"] = merged
            changed = True
    if core_keywords and core_keywords != pack.get("core_rescue_keywords"):
        ordered: dict[str, Any] = {}
        for key, value in pack.items():
            if key == "core_rescue_keywords":
                continue
            ordered[key] = value
            if key == ("core_properties" if "core_properties" in pack else "subtypes"):
                ordered["core_rescue_keywords"] = core_keywords
        pack.clear()
        pack.update(ordered)
        changed = True
    return changed


def _convert_pack(config: dict[str, Any], result: Conversion) -> dict[str, Any]:  # noqa: C901, PLR0912, PLR0915 - one pass per field of the old shape
    """A plugin as a pack: subtypes of the core types, the labels the core lacks."""
    if config.get("role") == "pack":
        return config
    plugin = result.plugin
    templates = config.get("templates") or {}

    subtypes: list[dict[str, Any]] = []
    subtype_names: set[str] = set()
    core_properties: dict[str, list[dict[str, Any]]] = {}
    core_keywords: dict[str, list[str]] = {}
    for tmpl in templates.get("node_templates") or []:
        name = str(tmpl.get("name", ""))
        try:
            core, sub = core_type_of(name, pack=plugin)
        except KeyError:
            result.missing.append(name)
            continue
        if sub is None:
            result.folded.append(name)
            if _keywords(tmpl):
                core_keywords[core] = _merge_keywords(core_keywords.get(core, []), _keywords(tmpl))
            if tmpl.get("properties"):
                result.folded_properties[name] = [p["name"] for p in tmpl["properties"]]
                props = core_properties.setdefault(core, [])
                known = {p["name"] for p in props}
                props.extend(
                    p for p in _clean_properties(tmpl["properties"]) if p["name"] not in known
                )
            continue
        entry: dict[str, Any] = {
            "name": name,
            "of": core,
            "description": tmpl.get("description", ""),
        }
        if tmpl.get("properties"):
            entry["properties"] = _clean_properties(tmpl["properties"])
        if tmpl.get("names_a_person") is True:
            if core == "Person":
                entry["names_a_person"] = True
            else:
                result.dropped_person_flags.append(name)
        for key in ("icon", "color", "quality_score", "requires_named_referent"):
            if key in tmpl:
                entry[key] = tmpl[key]
        if _keywords(tmpl):
            entry["rescue_keywords"] = _keywords(tmpl)
        subtypes.append(entry)
        subtype_names.add(name)
    if result.missing:
        return config
    result.subtypes = len(subtypes)

    renames = {old: new for (p, old), new in LABEL_RENAMES.items() if p == plugin}
    result.renamed_labels = dict(renames)
    edges: list[dict[str, Any]] = []
    for tmpl in templates.get("edge_templates") or []:
        edge = dict(tmpl)
        edge["name"] = renames.get(edge["name"], edge["name"])
        if edge.get("inverse"):
            edge["inverse"] = renames.get(edge["inverse"], edge["inverse"])
        if canonical_edge_label(edge["name"]) in CORE_LABELS:
            result.dropped_labels.append(edge["name"])
            continue
        if edge["name"] != tmpl["name"]:
            edge["id"] = str(edge.get("id", "")).replace(tmpl["name"], edge["name"])
        for key in ("source_types", "target_types"):
            if key in edge:
                edge[key] = _core_types(edge[key], result)
        edges.append(edge)

    aliases: dict[str, str] = {}
    for alias, target in (config.get("type_aliases") or {}).items():
        if alias.strip().lower() in {t.lower() for t in CORE_TYPES}:
            result.dropped_aliases.append(alias)
            continue
        mapped = target if target in subtype_names else _core_of(target, result)
        if mapped is not None:
            aliases[alias] = mapped

    limits = dict(config.get("extraction_limits") or {})
    for key, floor in FLOOR_LIMITS.items():
        if key in limits and limits[key] < floor:
            result.dropped_limits[key] = limits.pop(key)

    examples = dict(config.get("examples") or {})
    if examples.get("entity_examples"):
        examples["entity_examples"] = _entity_example_lines(examples["entity_examples"], result)
    if renames and examples.get("relationship_examples"):
        examples["relationship_examples"] = [
            {**ex, "type": renames.get(ex.get("type", ""), ex.get("type", ""))}
            for ex in examples["relationship_examples"]
        ]

    if result.missing:
        return config

    drop = {"templates", "type_aliases", "extraction_limits", "examples", "strict_entity_types",
            *REMOVED_KEYS}  # fmt: skip
    new: dict[str, Any] = {}
    for key, value in config.items():
        if key in drop:
            if key == "templates":
                new["subtypes"] = subtypes
                if core_properties:
                    new["core_properties"] = core_properties
                if core_keywords:
                    new["core_rescue_keywords"] = core_keywords
                new["templates"] = {"edge_templates": edges}
            elif key == "type_aliases" and aliases:
                new["type_aliases"] = aliases
            elif key == "extraction_limits" and limits:
                new["extraction_limits"] = limits
            elif key == "examples":
                new["examples"] = examples
            continue
        new[key] = value
    if renames:
        for key in ("entity_guidance", "relationship_guidance"):
            if isinstance(new.get(key), str):
                new[key] = _rename_in_text(new[key], renames)
        vocabulary = new.get("relation_vocabulary") or {}
        new["relation_vocabulary"] = {
            rel: {**entry, "labels": [renames.get(x, x) for x in entry.get("labels", [])]}
            for rel, entry in vocabulary.items()
        } if vocabulary else vocabulary  # fmt: skip
        if not new["relation_vocabulary"]:
            new.pop("relation_vocabulary")
    new["version"] = _bump_major(config.get("version"))
    return _ordered(new, {"role": "pack", "extends": "core"})


def rendered_pass_chars(pack: dict[str, Any], floor: dict[str, Any]) -> tuple[int, int]:
    """What a converted pack adds to the floor's pass-1 and pass-2 prompts, in rendered characters.

    The stack's own measure (:meth:`DomainStack.prompt_contribution`), so the
    converter reports what the stack renders.
    """
    from chaoscypher_core.services.sources.engine.extraction.domains.configurable import (
        ConfigurableDomain,
    )
    from chaoscypher_core.services.sources.engine.extraction.domains.stack import DomainStack

    domain = ConfigurableDomain(pack)
    stack = DomainStack(ConfigurableDomain(floor), (domain,))
    sizes = stack.prompt_contribution()[domain.name]
    return sizes["pass1"], sizes["pass2"]


def convert_config(config: dict[str, Any], plugin: str) -> Conversion:
    """Convert one parsed plugin (``plugin`` is its name); ``generic`` becomes the floor."""
    result = Conversion(plugin=plugin, config=config)
    if plugin == FLOOR_NAME:
        new = _convert_floor(config, result)
    else:
        new = copy.deepcopy(_convert_pack(config, result))
        if not result.missing:
            apply_mapping_splits(new)
    result.config = new
    result.changed = new != config
    return result


def _render(config: dict[str, Any]) -> str:
    """A plugin file's text: the shipped files' format (2-space JSON, UTF-8, newline at the end)."""
    return json.dumps(config, indent=2, ensure_ascii=False) + "\n"


def convert_file(path: Path) -> Conversion:
    """Convert the plugin at ``path`` (not written)."""
    config = json.loads(path.read_text(encoding="utf-8"))
    result = convert_config(config, str(config.get("name") or path.stem))
    if not result.changed and _render(config) != path.read_text(encoding="utf-8"):
        result.changed = True  # same content, other formatting: writing normalises it
    return result


def pending_conversions(plugin_dir: Path) -> list[str]:
    """The plugins under ``plugin_dir`` a converter run would still change (empty when done)."""
    return [
        path.stem
        for path in sorted(Path(plugin_dir).glob("*.jsonld"))
        if (r := convert_file(path)).changed or r.missing
    ]


def _report(result: Conversion) -> list[str]:
    """The lines ``main`` prints for one plugin's conversion: what changed and what was dropped."""
    lines = [f"{result.plugin}: {'converted' if result.changed else 'unchanged'}"]
    if result.plugin != FLOOR_NAME:
        p1, p2 = result.pass_chars
        lines.append(
            f"  subtypes {result.subtypes}; core labels dropped {len(result.dropped_labels)} "
            f"{result.dropped_labels}; rendered chars pass1 {p1} pass2 {p2}"
        )
    for label, value in (
        ("folded into a core type", result.folded),
        ("properties of folded templates", result.folded_properties),
        ("renamed labels", result.renamed_labels),
        ("dropped aliases of core types", result.dropped_aliases),
        ("limits below the floor dropped", result.dropped_limits),
        ("names_a_person dropped (not a Person)", result.dropped_person_flags),
    ):
        if value:
            lines.append(f"  {label}: {value}")
    return lines


def _out(text: str) -> None:
    """One line of the tool's report, on stdout."""
    sys.stdout.write(text + "\n")


def main(argv: list[str] | None = None) -> int:
    """Convert the plugins named (default: the shipped ones); ``--write`` writes them."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument(
        "paths", nargs="*", type=Path, help="plugin files (default: the shipped ones)"
    )
    parser.add_argument("--write", action="store_true", help="write the converted files")
    parser.add_argument(
        "--keywords-from",
        type=Path,
        default=None,
        help="a directory of the unconverted plugins: restore their templates' keywords",
    )
    args = parser.parse_args(argv)
    from chaoscypher_core.services.sources.engine.extraction.domains.registry import DomainRegistry

    paths = args.paths or sorted(DomainRegistry.builtin_plugin_dir().glob("*.jsonld"))
    results = [(path, convert_file(path)) for path in paths]
    if args.keywords_from is not None:
        for path, result in results:
            source = args.keywords_from / path.name
            if result.plugin != FLOOR_NAME and source.is_file():
                original = json.loads(source.read_text(encoding="utf-8"))
                if restore_rescue_keywords(result.config, original):
                    apply_mapping_splits(result.config)  # a restored word a split moved moves again
                    result.changed = True
    missing = [f"missing: {r.plugin}: {name}" for _p, r in results for name in r.missing]
    for line in missing:
        _out(line)
    if missing:
        return 1
    floor = (
        next(
            (r.config for _p, r in results if r.plugin == FLOOR_NAME),
            None,
        )
        or convert_file(DomainRegistry.builtin_plugin_dir() / f"{FLOOR_NAME}.jsonld").config
    )
    over: list[str] = []
    for path, result in results:
        if result.plugin != FLOOR_NAME:
            result.pass_chars = rendered_pass_chars(result.config, floor)
        _out("\n".join(_report(result)))
        if result.plugin != FLOOR_NAME and max(result.pass_chars) > PACK_CONTRIBUTION_REPORT_CHARS:
            over.append(f"{result.plugin} {result.pass_chars}")
        if args.write and result.changed:
            path.write_text(_render(result.config), encoding="utf-8")
    if over:
        _out(f"above the {PACK_CONTRIBUTION_REPORT_CHARS}-char report mark (not a limit): {over}")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    sys.exit(main())
