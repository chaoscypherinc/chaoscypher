# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Type rescue: every entity leaves the per-chunk filters typed as a core type.

The floor's eight core types are the only entity types a source stores (spec
2026-10-07 § 6); a pack adds subtypes of them, carried as
``properties.entity_subtype``. What the model wrote in the type column is
decided once, by :meth:`TypeVocabulary.decide`, in this order (the tiers in
brackets run only for the per-chunk pipeline's salvage; :func:`rescue_type`
is the same decision without them):

1. **listed** — an exact type of the list (the core types) is itself.
   **subtyped** — ``Core/subtype`` (``core_types.split_qualified_type``, the
   form the prompt's node lines teach: ``Person/character``) with a subtype
   the packs offer is ``(that subtype's core type, subtype)``, whichever
   core type was written before the slash (``Location/character`` is
   Person/``character``); **unplaced_subtype** — one the packs do not
   offer is kept on the written core type, flagged ``unplaced_type`` with
   the written type under ``written_type`` (:func:`flag_unplaced_type`).
2. [**junk**] — an empty name, a name equal to its type, a single common
   word: dropped — unless the named record carries evidence (a description
   or an evidence sentence), which the keep rule never drops: it goes on to
   the later tiers and is kept flagged.
3. **mapped** — a pack's alias (exact key, the packs' ``type_aliases``) is
   read as its target; a target that is a core type keeps the subtype the
   mapping table gives the written name, when that is a kind of the same
   core type. Then the name itself: ``core_types.core_type_of`` (the one
   precedence of the mapping table: the stack's pack-qualified rows, then
   the plain row), else one of the packs' own subtype names: ``(core type,
   subtype)``.
4. [**absorbed**] — a property-like type becomes its target's property.
5. **pack_keyword** — the selected packs' rescue keywords (each subtype's
   ``rescue_keywords``, then the pack's ``core_rescue_keywords``): the
   description, then the written name, whole words only: ``(core, subtype)``.
6. **keyword** — the floor's ``normalization_keywords``, the same way:
   ``(core, None)``.
7. **reference_person** — a record whose label is a reference that says
   something of its own (``kept_as_reference``, read untyped) and whose
   own words name a person (a title, a relation or a role word): Person.
8. Otherwise ``None``: no core type answers to the written type. The
   per-chunk pipeline keeps the entity (keep, flag, never delete,
   2026-10-07): typed ``Concept`` (``name_identity.UNPLACED_CORE_TYPE``),
   flagged ``unplaced_type`` with the written type beside it under
   ``written_type`` (:func:`flag_unplaced_type`), and counted under
   ``unknown_type`` in the chunk's filtering log. Until then it was dropped
   there, description and all. No entity leaves typed ``UNKNOWN`` or as a
   name no core type answers to.

An empty type column (``UNKNOWN_ENTITY_TYPE``) states no type: steps 1, 3
and the name half of 5-6 are skipped for it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
    UNPLACED_CORE_TYPE,
    whole_token_pattern,
)
from chaoscypher_core.services.sources.engine.extraction.domains.core_types import (
    CORE_TYPES,
    NOT_RESCUE_KEYWORDS,
    core_type_of,
    split_qualified_type,
    subtype_key,
)
from chaoscypher_core.services.sources.engine.extraction.utils.constants import (
    UNKNOWN_ENTITY_TYPE,
)


if TYPE_CHECKING:
    from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
        NameConventions,
    )
    from chaoscypher_core.services.sources.engine.extraction.utils.filtering_log import (
        FilteredItem,
        FilteringLog,
    )


logger = structlog.get_logger(__name__)


SUBTYPE_PROPERTY = "entity_subtype"
"""The entity property a subtype is stored under (``core_types.subtype_key`` form)."""

SUBTYPES_PROPERTY = "entity_subtypes"
"""Where a merge of two subtypes of one core type keeps both (sorted; ``deduplication.service.merge_subtypes``).

``entity_subtype`` stays the survivor's; this list is written only when the
merged mentions carried more than one."""

UNKNOWN_TYPE_KEY = "unknown_type"
"""The ``type_rescue`` stage's count (and an item's tier) of the entities no core type answers to.

Since 2026-10-07 they are kept, typed ``Concept`` and flagged ``unplaced_type``
(:func:`flag_unplaced_type`): the count is of the flagged ones (until then, of
the dropped ones). A run's conformance requires it to be 0 on the benchmark
corpus (``graph_scan.py``): each one is a type the packs do not place."""

TIERS: tuple[str, ...] = (
    "listed", "subtyped", "unplaced_subtype", "junk", "mapped", "absorbed", "pack_keyword",
    "keyword", "reference_person",
)  # fmt: skip
"""The decision's tiers, in the order :meth:`TypeVocabulary.decide` tries them."""

DROPPING_TIERS = frozenset({"junk", "absorbed"})
"""Tiers that decide the record is no entity (it is dropped, or absorbed into another's property)."""

_UNTYPED = frozenset({"", UNKNOWN_ENTITY_TYPE.lower()})
"""Written types (lowercase) that state no type: the parser's placeholder for an empty column."""

KeywordEntry = tuple[str, str | None, tuple[str, ...]]
"""One rescue keyword list: ``(core type, subtype key or None, keywords)``."""


def _key(name: str) -> str:
    """A written type name as the tables key it: lowercase, ``_`` as a space, spaces collapsed."""
    return " ".join(name.replace("_", " ").strip().lower().split())


def _rescue_words(words: Iterable[Any]) -> tuple[str, ...]:
    """Keywords each once, in order, without :data:`NOT_RESCUE_KEYWORDS` (pronouns say no type)."""
    kept = (str(w) for w in words)
    return tuple(
        dict.fromkeys(w for w in kept if w.strip() and w.strip().lower() not in NOT_RESCUE_KEYWORDS)
    )


def has_phrase(text: str, phrase: str) -> bool:
    """Whether ``text`` writes ``phrase`` as whole tokens, case aside: the rescue's and the normalizer's keyword match.

    ``name_identity.whole_token_pattern`` over both sides lowercased. Until
    2026-10-07 both matched a keyword as a substring, so the floor's Person
    keyword ``he`` matched every description holding ``the`` and the type
    name ``Scheme``, and ``she`` matched ``shelter``.
    """
    needle = phrase.strip().lower()
    return bool(needle) and whole_token_pattern(needle).search(text.lower()) is not None


@dataclass(frozen=True)
class Rescue:
    """One decision of :meth:`TypeVocabulary.decide`: the tier that decided, and the type it gave.

    ``core`` is None for a dropping tier (:data:`DROPPING_TIERS`);
    ``unplaced`` is True when the record is kept on ``core`` but flagged
    ``unplaced_type`` (a ``Core/subtype`` whose subtype no pack offers).
    """

    tier: str
    core: str | None = None
    subtype: str | None = None
    unplaced: bool = False

    @property
    def kept(self) -> bool:
        """Whether the record stays an entity (typed ``core``)."""
        return self.core is not None


@dataclass(frozen=True)
class TypeVocabulary:
    """What the type rescue reads for one source: its type list, packs, subtypes, aliases and keywords.

    :meth:`from_stack` builds it from the source's ``DomainStack``; the
    extraction plan carries the JSON-safe part (:meth:`as_config`: packs,
    subtypes, aliases, pack keywords) as the ``type_vocabulary`` chunk kwarg,
    and the type list and the floor's keywords as ``valid_entity_type_names``
    and ``normalization_rules`` (one carrier per fact).

    Attributes:
        types: The types an entity may leave the filters with (the core
            types; a probe's own list when it supplies one).
        packs: The stack's pack names, in stack order (the pack-qualified
            rows of the mapping table are read for each).
        subtypes: Subtype name (:func:`_key` form) -> ``(core type, subtype
            key)``, the packs' declared subtypes, first pack wins.
        aliases: The packs' ``type_aliases`` (written name -> target), first
            pack wins.
        pack_keywords: The packs' rescue keywords, in stack order: each
            subtype's ``rescue_keywords``, then the pack's
            ``core_rescue_keywords``.
        keywords: Core type -> the floor's ``normalization_keywords``.
    """

    types: tuple[str, ...] = CORE_TYPES
    packs: tuple[str, ...] = ()
    subtypes: Mapping[str, tuple[str, str]] = field(default_factory=dict)
    aliases: Mapping[str, str] = field(default_factory=dict)
    pack_keywords: tuple[KeywordEntry, ...] = ()
    keywords: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    # ------------------------------------------------------------ builders

    @classmethod
    def from_stack(cls, stack: Any) -> TypeVocabulary:
        """The vocabulary of a ``DomainStack``: the core types, its packs' subtypes, aliases and keywords, the floor's keywords."""
        subtypes: dict[str, tuple[str, str]] = {}
        for spec in stack.subtypes():
            subtypes.setdefault(_key(spec.name), (spec.of, spec.key))
        aliases: dict[str, str] = {}
        pack_keywords: list[KeywordEntry] = []
        for pack in stack.packs:
            for alias, target in domain_type_aliases(pack).items():
                aliases.setdefault(alias, target)
            pack_keywords.extend(_pack_keywords(pack))
        return cls(
            packs=tuple(p.name for p in stack.packs),
            subtypes=subtypes,
            aliases=aliases,
            pack_keywords=tuple(pack_keywords),
            keywords=_core_keywords(stack.normalization_rules(), CORE_TYPES),
        )

    @classmethod
    def from_domain(cls, domain: Any | None) -> TypeVocabulary:
        """The vocabulary of one resolved domain (a pack, the floor, or None): what a finalize path holds.

        Defensive like ``post_extraction.apply_domain_type_aliases``: a
        domain without the accessors (or a mock) contributes nothing. A
        ``DomainStack`` (what the finalize paths resolve a source's packs to)
        is read as :meth:`from_stack`.
        """
        if domain is None:
            return cls()
        from chaoscypher_core.services.sources.engine.extraction.domains.stack import DomainStack

        if isinstance(domain, DomainStack):
            return cls.from_stack(domain)
        is_pack = getattr(domain, "role", None) == "pack"
        name = getattr(domain, "name", None)
        subtypes: dict[str, tuple[str, str]] = {}
        accessor = getattr(domain, "subtypes", None)
        if is_pack and callable(accessor):
            try:
                specs = list(accessor())
            except Exception:
                specs = []
            for spec in specs:
                of, key, label = getattr(spec, "of", None), getattr(spec, "key", None), spec.name
                if isinstance(of, str) and isinstance(key, str) and isinstance(label, str):
                    subtypes.setdefault(_key(label), (of, key))
        return cls(
            packs=(name,) if is_pack and isinstance(name, str) else (),
            subtypes=subtypes,
            aliases=domain_type_aliases(domain),
        )

    @classmethod
    def from_config(
        cls,
        config: Mapping[str, Any] | TypeVocabulary | None,
        *,
        types: Iterable[str] | None = None,
        keywords: Mapping[str, Iterable[str]] | None = None,
    ) -> TypeVocabulary:
        """The vocabulary a plan's ``type_vocabulary`` JSON encodes, with its type list and the floor's keywords.

        ``types`` (the plan's ``valid_entity_type_names``) defaults to the
        core types; ``keywords`` (its ``normalization_rules``) to none. A
        ``TypeVocabulary`` passed as ``config`` is taken as it is, with
        ``types``/``keywords`` applied when given.
        """
        base = config if isinstance(config, TypeVocabulary) else cls._decode(config)
        type_list = _ordered_types(types) if types else base.types
        return replace(
            base,
            types=type_list,
            keywords=_core_keywords(keywords, type_list) if keywords else base.keywords,
        )

    @classmethod
    def _decode(cls, config: Mapping[str, Any] | None) -> TypeVocabulary:
        """The vocabulary a plan's ``type_vocabulary`` JSON encodes (:meth:`as_config`), the empty one for none."""
        if not config:
            return cls()
        subtypes = {
            str(k): (str(v[0]), str(v[1]))
            for k, v in (config.get("subtypes") or {}).items()
            if isinstance(v, (list, tuple)) and len(v) == 2
        }
        pack_keywords = tuple(
            (str(row[0]), str(row[1]) if row[1] else None, _rescue_words(row[2]))
            for row in config.get("pack_keywords") or ()
            if isinstance(row, (list, tuple)) and len(row) == 3
        )
        return cls(
            packs=tuple(str(p) for p in config.get("packs") or ()),
            subtypes=subtypes,
            aliases={str(k): str(v) for k, v in (config.get("aliases") or {}).items()},
            pack_keywords=pack_keywords,
        )

    def as_config(self) -> dict[str, Any]:
        """The JSON-safe part a plan stores (packs, subtypes, aliases, pack keywords); the inverse of :meth:`from_config`."""
        return {
            "packs": list(self.packs),
            "subtypes": {k: list(v) for k, v in sorted(self.subtypes.items())},
            "aliases": dict(sorted(self.aliases.items())),
            "pack_keywords": [[core, sub, list(words)] for core, sub, words in self.pack_keywords],
        }

    # ------------------------------------------------------------ reading

    def is_listed(self, written: str | None) -> bool:
        """Whether ``written`` is exactly one of :attr:`types` (as written, case and all)."""
        return (written or "").strip() in self.types

    def resolve(self, written: str | None) -> tuple[str, str | None] | None:
        """``(type, subtype)`` for a written type name from the list, the aliases and the tables; else None.

        Tiers 1 and 3 of the module docstring (no keyword is read).
        """
        name = (written or "").strip()
        key = _key(name)
        if key in _UNTYPED:
            return None
        target = self.aliases.get(name)
        if target is None:
            target = next((t for a, t in self.aliases.items() if _key(a) == key), None)
        if target is not None and _key(target) != key:
            found = self._lookup(target)
            if found is not None:
                core, subtype = found
                if subtype is None:
                    own = self._lookup(name)
                    if own is not None and own[0] == core:
                        subtype = own[1]
                return core, subtype
        return self._lookup(name)

    def _stated(self, name: str) -> Rescue | None:
        """The type as written: a listed type (any case), or ``Core/subtype``; None for any other.

        Tier 1 of the module docstring: ``Core/subtype`` with a subtype the
        packs offer is that subtype's core type and the subtype (the core type
        written before the slash gives way to the subtype's own); one they do
        not offer is the written core type, ``unplaced``.
        """
        listed = {t.lower(): t for t in self.types}
        if _key(name) in listed:
            return Rescue("listed", listed[_key(name)])
        split = split_qualified_type(name)
        if split is None or split[0] not in self.types:
            return None
        core, slug = split
        offered = self.subtypes.get(_key(slug))
        if offered is not None and offered[0] in self.types:
            return Rescue("subtyped", *offered)
        return Rescue("unplaced_subtype", core, unplaced=True)

    def place(self, written: str | None) -> Rescue | None:
        """The decision on the written type alone (no record, no keyword): listed, subtyped, mapped.

        What a reader with no record to salvage applies (the commit's
        :func:`committed_type`, the finalize's
        ``type_normalizer.canonicalize_entity_types``): the tiers of
        :meth:`decide` that read nothing but the written type.
        """
        name = (written or "").strip()
        stated = self._stated(name)
        if stated is not None:
            return stated
        mapped = self.resolve(name)
        return Rescue("mapped", *mapped) if mapped is not None else None

    def _lookup(self, name: str) -> tuple[str, str | None] | None:
        """The list, then the mapping table (``core_type_of``'s one precedence), then the packs' own subtypes."""
        listed = {t.lower(): t for t in self.types}
        key = _key(name)
        if key in listed:
            return listed[key], None
        try:
            row: tuple[str, str | None] | None = core_type_of(name, self.packs)
        except KeyError:
            row = None
        if row is None or row[0] not in self.types:
            row = self.subtypes.get(key)
        return row if row is not None and row[0] in self.types else None

    def _keyword_match(
        self, entries: Iterable[KeywordEntry], written: str | None, description: str | None
    ) -> tuple[str, str | None] | None:
        """The first entry whose keyword the description writes, or the written name is, holds or is held in."""
        name = (written or "").strip()
        typed = _key(name) not in _UNTYPED
        text = description or ""
        for core, subtype, words in entries:
            if core not in self.types:
                continue
            if text and any(has_phrase(text, kw) for kw in words):
                return core, subtype
            if typed and any(has_phrase(name, kw) or has_phrase(kw, name) for kw in words):
                return core, subtype
        return None

    def keyword_type(self, written: str | None, description: str | None) -> str | None:
        """The floor keyword tier alone (tier 6): the type its keywords give, else None."""
        entries = ((core, None, words) for core, words in self.keywords.items())
        found = self._keyword_match(entries, written, description)
        return found[0] if found is not None else None

    def decide(  # noqa: PLR0911 - one return per tier, in the tiers' order
        self,
        written: str | None,
        *,
        record: Mapping[str, Any] | None = None,
        description: str | None = None,
        conventions: NameConventions | None = None,
        salvage: bool = False,
        absorb: Callable[[], bool] | None = None,
    ) -> Rescue | None:
        """The one ordered decision for a written type (the module docstring's tiers); None: no type.

        Args:
            written: The type the model wrote.
            record: The entity (its name for the junk tier, the whole record
                for the reference tier; its description unless given).
            description: The text the keyword tiers read.
            conventions: The source's naming conventions (what a reference is).
            salvage: Run the junk tier (``FilteringConfig.enable_type_rescue``).
            absorb: The absorption tier's hook (the pipeline's): True when
                the record was absorbed into another's property. Called only
                with ``salvage``.
        """
        name = (written or "").strip()
        text = (
            description if description is not None else str((record or {}).get("description") or "")
        )
        stated = self._stated(name)
        if stated is not None:
            return stated
        if (
            salvage
            and record is not None
            and _is_junk_entity(str(record.get("name") or "").strip(), name)
            and not _junk_carries_evidence(record)
        ):
            return Rescue("junk")
        mapped = self.resolve(name)
        if mapped is not None:
            return Rescue("mapped", *mapped)
        if salvage and absorb is not None and absorb():
            return Rescue("absorbed")
        found = self._keyword_match(self.pack_keywords, name, text)
        if found is not None:
            return Rescue("pack_keyword", *found)
        floor = self.keyword_type(name, text)
        if floor is not None:
            return Rescue("keyword", floor)
        if record is not None and "Person" in self.types and _reference_person(record, conventions):
            return Rescue("reference_person", "Person")
        return None

    def rescue(
        self,
        written: str | None,
        *,
        description: str | None = None,
        record: Mapping[str, Any] | None = None,
        conventions: NameConventions | None = None,
    ) -> tuple[str, str | None] | None:
        """``(type, subtype)`` the decision gives a written type (no salvage tier), else None."""
        decision = self.decide(
            written, record=record, description=description, conventions=conventions
        )
        if decision is None or decision.core is None:
            return None
        return decision.core, decision.subtype


FLOOR_TYPE_VOCABULARY = TypeVocabulary()
"""The core types and the mapping table alone: no pack, no keyword (a caller with no plan)."""


def _pack_keywords(pack: Any) -> list[KeywordEntry]:
    """A pack's rescue keyword lists: each subtype's, then its core-type ones (``core_rescue_keywords``)."""
    out: list[KeywordEntry] = []
    for spec in pack.subtypes():
        words = _rescue_words(getattr(spec, "rescue_keywords", ()) or ())
        if words:
            out.append((spec.of, spec.key, words))
    accessor = getattr(pack, "core_rescue_keywords", None)
    if callable(accessor):
        for core, raw in accessor().items():
            words = _rescue_words(raw)
            if words and core in CORE_TYPES:
                out.append((core, None, words))
    return out


def _ordered_types(types: Iterable[str]) -> tuple[str, ...]:
    """A type list in the core types' order, then any other names sorted (a set carries no order)."""
    given = {str(t) for t in types}
    return tuple(t for t in CORE_TYPES if t in given) + tuple(sorted(given - set(CORE_TYPES)))


def _core_keywords(
    rules: Mapping[str, Iterable[str]] | None, types: Iterable[str]
) -> dict[str, tuple[str, ...]]:
    """The keyword rules whose target is one of ``types``, in rule order, pronouns left out."""
    allowed = set(types)
    out = {t: _rescue_words(kws) for t, kws in (rules or {}).items() if t in allowed}
    return {t: words for t, words in out.items() if words}


def domain_type_aliases(domain: Any) -> dict[str, str]:
    """A domain's ``type_aliases`` as ``{str: str}``, or {} (no accessor, a raising one, a mock)."""
    accessor = getattr(domain, "get_type_aliases", None)
    if not callable(accessor):
        return {}
    try:
        raw = accessor()
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}
    return {
        str(k): str(v)
        for k, v in raw.items()
        if isinstance(k, str) and isinstance(v, str) and k and v
    }


def rescue_type(
    written: str,
    stack: Any,
    *,
    description: str | None = None,
    record: Mapping[str, Any] | None = None,
    conventions: NameConventions | None = None,
) -> tuple[str, str | None] | None:
    """The core type and subtype a written type name is rescued to, or None (the entity is dropped).

    ``stack`` is a ``DomainStack`` or the :class:`TypeVocabulary` a plan
    carries for one. :meth:`TypeVocabulary.decide` without the salvage
    tiers: the per-chunk pipeline (:func:`rescue_invalid_entity_types`) runs
    the same decision with them. ``record`` is the entity, for the reference
    tier (and its description, unless ``description`` is given).
    """
    vocabulary = stack if isinstance(stack, TypeVocabulary) else TypeVocabulary.from_stack(stack)
    return vocabulary.rescue(
        written, description=description, record=record, conventions=conventions
    )


def set_subtype(entity: dict[str, Any], subtype: str | None) -> None:
    """Record ``subtype`` under ``properties.entity_subtype`` unless the record already carries one."""
    if not subtype:
        return
    properties = entity.setdefault("properties", {})
    if isinstance(properties, dict) and not properties.get(SUBTYPE_PROPERTY):
        properties[SUBTYPE_PROPERTY] = subtype_key(subtype)


def committed_type(
    record: Mapping[str, Any], packs: Iterable[str] = ()
) -> tuple[str, str | None] | None:
    """The core type and pack subtype a record is committed under (``entity_type``, ``entity_subtype``).

    The one reading the commit makes (the template suggestions, the template
    matcher and the stored node, spec 2026-10-07 § 6): a record typed with a
    core type keeps it; any other written type is read through the floor's
    vocabulary (the mapping table, so an old ``Character`` is
    ``Person``/``character``), with ``packs`` (the packs the record's source
    was extracted with, in stack order) reading their pack-qualified rows
    first (``core_types.core_type_of``'s one precedence). The record's own
    ``entity_subtype`` wins over the table's. None when no core type answers
    (no template; the matcher's fallback). A written ``Core/subtype`` is read
    by :meth:`TypeVocabulary.place` like every other: this vocabulary offers
    no pack subtype, so one the per-chunk rescue did not already place is
    its written core type (:func:`core_typed` flags it ``unplaced_type``).
    """
    decision = _commit_decision(record, packs)
    if decision is None or decision.core is None:
        return None
    properties = record.get("properties")
    carried = properties.get(SUBTYPE_PROPERTY) if isinstance(properties, Mapping) else None
    if isinstance(carried, str) and carried:
        return decision.core, carried
    subtype = decision.subtype
    return decision.core, subtype_key(subtype) if subtype else None


def _commit_decision(record: Mapping[str, Any], packs: Iterable[str]) -> Rescue | None:
    """:meth:`TypeVocabulary.place` of ``record``'s written type, with ``packs``' qualified rows."""
    names = tuple(packs)
    vocabulary = TypeVocabulary(packs=names) if names else FLOOR_TYPE_VOCABULARY
    return vocabulary.place(str(record.get("type") or ""))


def core_typed(record: Mapping[str, Any], packs: Iterable[str] = ()) -> dict[str, Any]:
    """``record`` (``type``, ``properties``) on the core vocabulary: the one placing of a stored node's type.

    What a node is stored under, wherever it is written (the commit, the ccx
    import of a bundle from before the core types): the written ``type`` read
    by :func:`committed_type` (with ``packs``) becomes its core type, the
    table's subtype set under ``properties.entity_subtype`` when the record
    carries none (:func:`set_subtype`); a type no core type answers is kept
    as ``Concept`` flagged ``unplaced_type`` with the written type under
    ``written_type`` (:func:`flag_unplaced_type`, the rule the extraction's
    type rescue applies), and a ``Core/subtype`` with a subtype no pack
    offers is kept on its core type, flagged the same way. A record already
    on a core type comes back equal;
    a record with no written type is returned unchanged (a copy). The input
    is never modified.
    """
    written = str(record.get("type") or "").strip()
    stored = record.get("properties")
    properties = dict(stored) if isinstance(stored, Mapping) else {}
    if not written:
        return dict(record) if stored is None else {**record, "properties": properties}
    decision = _commit_decision(record, packs)
    if decision is None or decision.core is None:
        return flag_unplaced_type({**record, "properties": properties})
    if decision.unplaced:
        return flag_unplaced_type({**record, "properties": properties}, core=decision.core)
    typed = committed_type(record, packs)
    core, subtype = typed if typed is not None else (decision.core, None)
    placed: dict[str, Any] = {**record, "type": core, "properties": properties}
    set_subtype(placed, subtype)
    return placed


def _reference_person(entity: Mapping[str, Any], conventions: NameConventions | None) -> bool:
    """An untyped reference that says something of its own and whose own words name a person."""
    from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
        DEFAULT_NAME_CONVENTIONS,
        reference_names_a_person,
    )
    from chaoscypher_core.services.sources.engine.extraction.utils.entity_cleaner import (
        kept_as_reference,
    )

    conv = conventions or DEFAULT_NAME_CONVENTIONS
    untyped = {**entity, "type": None}
    label = str(entity.get("name") or "")
    return kept_as_reference(untyped, conv) and reference_names_a_person(label, conv)


_REMOVED_REASONS = {
    "junk": "Junk entity (tier 1)",
    "absorbed": "Absorbed as property (tier 2)",
    UNKNOWN_TYPE_KEY: "No type of the caller's list answers to the written type (dropped)",
}

_UNPLACED_REASON = (
    "No core type answers to the written type: kept as Concept, flagged unplaced_type"
)

_UNPLACED_SUBTYPE_REASON = (
    "The written subtype is none the packs offer: kept on its core type, flagged unplaced_type"
)


def flag_unplaced_type(
    entity: Mapping[str, Any], *, core: str = UNPLACED_CORE_TYPE
) -> dict[str, Any]:
    """``entity`` whose written type no core type places, kept: typed ``core``, flagged ``unplaced_type``.

    The one writer of the flag (``name_identity.UNPLACED_TYPE_KEY``): the
    copy is typed ``core`` (``name_identity.UNPLACED_CORE_TYPE`` when no core
    type answers; the written core type of a ``Core/subtype`` whose subtype
    no pack offers), keeps the written
    type as ``type_rescued_from`` (as every retyped entity does) and in its
    properties under ``name_identity.WRITTEN_TYPE_KEY`` (which the commit
    stores), and carries the flag.
    """
    from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
        UNPLACED_TYPE_KEY,
        WRITTEN_TYPE_KEY,
        with_pipeline_flag,
    )

    written = str(entity.get("type") or "").strip()
    stored = entity.get("properties")
    props = with_pipeline_flag(
        stored if isinstance(stored, Mapping) else None,
        UNPLACED_TYPE_KEY,
        on=True,
        **{WRITTEN_TYPE_KEY: written},
    )
    return {**entity, "type": core, "type_rescued_from": written, "properties": props}


_STAT_OF_TIER = {
    "listed": "subtype_mapped",
    "subtyped": "subtyped",
    "unplaced_subtype": "unplaced_subtype",
    "mapped": "subtype_mapped",
    "junk": "tier1_junk",
    "absorbed": "tier2_absorbed",
    "pack_keyword": "pack_keyword",
    "keyword": "tier3_remapped",
    "reference_person": "reference_person",
    UNKNOWN_TYPE_KEY: UNKNOWN_TYPE_KEY,
}
"""Each tier's ``rescue_invalid_entity_types`` stat (the names predate the decision), and ``unknown_type`` for no tier (:func:`_no_type`)."""


def _no_type(vocabulary: TypeVocabulary) -> Rescue:
    """What becomes of an entity no tier types: a ``Concept`` kept flagged, or dropped.

    Dropped only under a caller's own type list with no ``Concept`` (a
    probe's), which has nowhere to keep it.
    """
    if UNPLACED_CORE_TYPE in vocabulary.types:
        return Rescue(UNKNOWN_TYPE_KEY, UNPLACED_CORE_TYPE, unplaced=True)
    return Rescue(UNKNOWN_TYPE_KEY)


def rescue_invalid_entity_types(
    entities: list[dict[str, Any]],
    relationships: list[dict[str, Any]],
    vocabulary: TypeVocabulary = FLOOR_TYPE_VOCABULARY,
    property_type_mapping: dict[str, dict[str, str]] | None = None,
    filtering_log: FilteringLog | None = None,
    *,
    conventions: NameConventions | None = None,
    salvage: bool = True,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[int, int | None],
    dict[str, int],
]:
    """Type every entity as one of ``vocabulary.types``: :meth:`TypeVocabulary.decide` per entity.

    An entity whose type is exactly one of the list passes untouched; every
    other one takes the decision (with the salvage tiers when ``salvage``:
    junk, and absorption through ``property_type_mapping``, the only ones
    that drop). A retyped entity keeps the written type as
    ``type_rescued_from`` and its subtype under ``properties.entity_subtype``
    unless it carries one. One no tier types is kept as a ``Concept`` flagged
    ``unplaced_type`` (:func:`flag_unplaced_type`; keep, flag, never delete,
    2026-10-07) and counted under ``unknown_type``; only a caller's own type
    list without ``Concept`` (a probe's) has nowhere to keep it, and drops it.
    A ``Core/subtype`` is placed by its subtype (``subtyped``), or kept on
    its core type flagged ``unplaced_type`` when no pack offers the subtype
    (``unplaced_subtype``).

    Args:
        entities: The chunk's entities.
        relationships: Relationships over them (int ``source``/``target``).
        vocabulary: The source's :class:`TypeVocabulary`.
        property_type_mapping: invalid type -> ``{"target_type", "property"}``.
        filtering_log: Collector for the ``type_rescue`` stage.
        conventions: The source's naming conventions (what a reference is).
        salvage: ``FilteringConfig.enable_type_rescue``: the junk and
            absorption tiers. The type is always rescued.

    Returns:
        ``(entities, relationships, index_mapping, stats)``: the kept
        entities, the relationships remapped to them, old index -> new index
        (None when dropped) and the outcome counts (``total_invalid``,
        ``tier1_junk``, ``subtype_mapped``, ``tier2_absorbed``,
        ``pack_keyword``, ``tier3_remapped``, ``reference_person``,
        ``subtyped``, ``unplaced_subtype``, ``unknown_type``).
    """
    stats = {"total_invalid": 0, **dict.fromkeys(dict.fromkeys(_STAT_OF_TIER.values()), 0)}
    if not entities:
        return entities, relationships, {}, stats

    invalid_indices = [i for i, e in enumerate(entities) if not vocabulary.is_listed(e.get("type"))]
    if not invalid_indices:
        identity: dict[int, int | None] = {i: i for i in range(len(entities))}
        if filtering_log is not None:
            filtering_log.add_stage("type_rescue", input_count=len(entities), removed_count=0)
        return entities, relationships, identity, stats
    stats["total_invalid"] = len(invalid_indices)

    # Which entities are connected (the absorption tier reads it).
    rel_targets_for_entity: dict[int, list[int]] = {}
    rel_sources_for_entity: dict[int, list[int]] = {}
    for rel in relationships:
        src = rel.get("source")
        tgt = rel.get("target")
        if isinstance(src, int) and isinstance(tgt, int):
            rel_targets_for_entity.setdefault(src, []).append(tgt)
            rel_sources_for_entity.setdefault(tgt, []).append(src)

    absorption = property_type_mapping or {}
    dropped: dict[
        int, str
    ] = {}  # idx -> "junk" | "absorbed" | UNKNOWN_TYPE_KEY (no Concept listed)
    retyped: dict[int, tuple[str, str | None]] = {}
    unplaced: dict[int, str] = {}  # idx -> the tier that kept it flagged (unknown or a subtype)

    for idx in invalid_indices:
        entity = entities[idx]
        entity_type = (entity.get("type") or "").strip()

        def _absorb(
            idx: int = idx, entity: dict[str, Any] = entity, written: str = entity_type
        ) -> bool:
            return bool(absorption) and _try_absorb_as_property(
                idx,
                entity,
                entities,
                written,
                absorption,
                rel_targets_for_entity,
                rel_sources_for_entity,
            )

        decision = vocabulary.decide(
            entity_type, record=entity, conventions=conventions, salvage=salvage, absorb=_absorb
        )
        if decision is None:
            decision = _no_type(vocabulary)
        stats[_STAT_OF_TIER[decision.tier]] += 1
        if decision.core is None:
            dropped[idx] = decision.tier
            continue
        retyped[idx] = (decision.core, decision.subtype)
        if decision.unplaced:
            unplaced[idx] = decision.tier

    rescued: list[dict[str, Any]] = []
    index_mapping: dict[int, int | None] = {}
    removed_items: list[FilteredItem] = []

    for old_idx, entity in enumerate(entities):
        if old_idx in dropped:
            index_mapping[old_idx] = None
            removed_items.append(_tier_item(entity, dropped[old_idx], kept=False))
            continue
        if old_idx in unplaced:
            removed_items.append(_tier_item(entity, unplaced[old_idx], kept=True))
        kept = _rescued(entity, unplaced=old_idx in unplaced, retyped=retyped.get(old_idx))
        index_mapping[old_idx] = len(rescued)
        rescued.append(kept)

    from chaoscypher_core.services.sources.engine.deduplication import EntityProcessor

    rescued_relationships = EntityProcessor.remap_relationship_indices(relationships, index_mapping)

    logger.info("type_rescue_complete", **stats, kept=len(rescued), original=len(entities))

    if filtering_log is not None:
        filtering_log.add_stage(
            "type_rescue",
            input_count=len(entities),
            removed_count=len(dropped),
            items=removed_items,
            counts={
                "junk": stats["tier1_junk"],
                "absorbed": stats["tier2_absorbed"],
                UNKNOWN_TYPE_KEY: stats[UNKNOWN_TYPE_KEY],
                "mapped": stats["subtype_mapped"],
                "pack_keyword": stats["pack_keyword"],
                "keyword": stats["tier3_remapped"],
                "reference_person": stats["reference_person"],
                "subtyped": stats["subtyped"],
                "unplaced_subtype": stats["unplaced_subtype"],
            },
        )

    return rescued, rescued_relationships, index_mapping, stats


_KEPT_REASONS = {UNKNOWN_TYPE_KEY: _UNPLACED_REASON, "unplaced_subtype": _UNPLACED_SUBTYPE_REASON}
"""Why a kept, flagged entity is an item of the ``type_rescue`` stage, by the tier that kept it."""


def _tier_item(entity: Mapping[str, Any], tier: str, *, kept: bool) -> FilteredItem:
    """The ``type_rescue`` stage's item for an entity a tier dropped, or kept flagged."""
    from chaoscypher_core.services.sources.engine.extraction.utils.filtering_log import (
        FilteredItem,
    )

    return FilteredItem(
        item_type="entity",
        name=(entity.get("name") or "").strip() or "(empty)",
        entity_type=(entity.get("type") or "").strip(),
        reason=(_KEPT_REASONS if kept else _REMOVED_REASONS)[tier],
        details={"tier": tier, "kept": True} if kept else {"tier": tier},
    )


def _rescued(
    entity: dict[str, Any], *, unplaced: bool, retyped: tuple[str, str | None] | None
) -> dict[str, Any]:
    """``entity`` as the rescue keeps it: flagged unplaced (on its core type), retyped (with its subtype), or as it is."""
    if retyped is None:
        return entity
    core, subtype = retyped
    if unplaced:
        return flag_unplaced_type(entity, core=core)
    kept = {**entity, "type": core, "type_rescued_from": (entity.get("type") or "").strip()}
    if isinstance(entity.get("properties"), dict):
        kept["properties"] = dict(entity["properties"])
    set_subtype(kept, subtype)
    return kept


def _junk_carries_evidence(record: Mapping[str, Any]) -> bool:
    """Keep, flag, never delete at the junk tier: a named record with evidence is not junk.

    ``entity_cleaner.carries_evidence`` is the one reading of "has evidence"
    (a description or an evidence sentence); a record with no name at all
    stays junk whatever it carries, there is nothing to keep it under. Until
    2026-10-12 the junk tier ran before the record's description was read,
    so ``{"name": "Moscow", "type": "Moscow", "description": "The old
    capital…"}`` was dropped uncounted (the module docstring's rule 2 says a
    junk name is dropped; the keep rule says a described record is kept
    flagged, and the later tiers do that).
    """
    from chaoscypher_core.services.sources.engine.extraction.utils.entity_cleaner import (
        carries_evidence,
    )

    return bool(str(record.get("name") or "").strip()) and carries_evidence(record)


def _is_junk_entity(name: str, entity_type: str) -> bool:
    """Check if an entity is obvious junk that should be dropped.

    Junk entities include:
    - Empty or whitespace-only names
    - Single common/stop words
    - Entities where name equals type (e.g., name="Event", type="Event")

    Args:
        name: Entity name (already stripped).
        entity_type: Entity type (already stripped).

    Returns:
        True if entity is junk and should be dropped.

    """
    if not name:
        return True

    # Name equals type (case-insensitive)
    if name.lower() == entity_type.lower():
        return True

    # Single common word (too generic to be useful)
    _common_single_words = frozenset(
        {
            "the",
            "a",
            "an",
            "it",
            "is",
            "was",
            "were",
            "be",
            "been",
            "and",
            "or",
            "but",
            "not",
            "no",
            "yes",
            "he",
            "she",
            "they",
            "him",
            "her",
            "his",
            "this",
            "that",
            "these",
            "those",
            "who",
            "what",
            "where",
            "when",
            "how",
            "why",
        }
    )
    return name.lower() in _common_single_words


def _try_absorb_as_property(
    entity_idx: int,
    entity: dict[str, Any],
    all_entities: list[dict[str, Any]],
    entity_type: str,
    property_type_mapping: dict[str, dict[str, str]],
    rel_targets: dict[int, list[int]],
    rel_sources: dict[int, list[int]],
) -> bool:
    """Try to absorb an entity as a property on a target entity.

    Looks up the entity type in ``property_type_mapping``, finds a suitable
    target entity (via relationship or proximity), and applies the entity's
    name as a property value on the target.

    Args:
        entity_idx: Index of the entity to absorb.
        entity: The entity dict.
        all_entities: Full entity list for target lookup.
        entity_type: The entity's type string.
        property_type_mapping: Maps invalid types to target_type + property name.
        rel_targets: Mapping of entity_idx → list of target indices via relationships.
        rel_sources: Mapping of entity_idx → list of source indices via relationships.

    Returns:
        True if the entity was absorbed as a property on a target.

    """
    mapping = property_type_mapping.get(entity_type)
    if not mapping:
        return False

    target_type = mapping.get("target_type", "")
    property_name = mapping.get("property", "")
    if not target_type or not property_name:
        return False

    target_type_lower = target_type.lower()
    entity_name = (entity.get("name") or "").strip()
    if not entity_name:
        return False

    # Strategy (a): Find target via relationship
    connected_indices = set(rel_targets.get(entity_idx, []) + rel_sources.get(entity_idx, []))
    for connected_idx in connected_indices:
        if connected_idx < 0 or connected_idx >= len(all_entities):
            continue
        connected = all_entities[connected_idx]
        connected_type = (connected.get("type") or "").strip()
        if connected_type.lower() == target_type_lower:
            _apply_property(connected, property_name, entity_name)
            logger.debug(
                "type_rescue_property_absorbed",
                entity_name=entity_name,
                entity_type=entity_type,
                target_name=connected.get("name"),
                property_name=property_name,
                method="relationship",
            )
            return True

    # Strategy (b): Find target via proximity (same chunk, within 3 indices)
    entity_chunk = entity.get("chunk_index")
    for offset in range(1, 4):
        for candidate_idx in [entity_idx - offset, entity_idx + offset]:
            if candidate_idx < 0 or candidate_idx >= len(all_entities):
                continue
            candidate = all_entities[candidate_idx]
            candidate_type = (candidate.get("type") or "").strip()
            candidate_chunk = candidate.get("chunk_index")
            if candidate_type.lower() == target_type_lower and candidate_chunk == entity_chunk:
                _apply_property(candidate, property_name, entity_name)
                logger.debug(
                    "type_rescue_property_absorbed",
                    entity_name=entity_name,
                    entity_type=entity_type,
                    target_name=candidate.get("name"),
                    property_name=property_name,
                    method="proximity",
                )
                return True

    return False


def _apply_property(entity: dict[str, Any], property_name: str, value: str) -> None:
    """Apply a property value to an entity, appending with semicolon if exists.

    Args:
        entity: Target entity dict (modified in-place).
        property_name: Property key to set.
        value: Property value to set.

    """
    if "properties" not in entity:
        entity["properties"] = {}
    existing = entity["properties"].get(property_name)
    if existing:
        entity["properties"][property_name] = f"{existing}; {value}"
    else:
        entity["properties"][property_name] = value


def _try_remap_type(
    entity: dict[str, Any],
    entity_type: str,
    normalization_rules: dict[str, list[str]],
    valid_types: set[str],
) -> str | None:
    """The keyword tiers alone (:meth:`TypeVocabulary.keyword_type`) over ``normalization_rules``.

    Args:
        entity: The entity dict (its ``description`` is read).
        entity_type: The written type.
        normalization_rules: Target type -> keywords.
        valid_types: The types a target must be one of.

    Returns:
        The target type, or None.
    """
    vocabulary = TypeVocabulary.from_config(
        None, types=valid_types or CORE_TYPES, keywords=normalization_rules
    )
    return vocabulary.keyword_type(entity_type, entity.get("description"))


__all__: list[str] = [
    "DROPPING_TIERS",
    "FLOOR_TYPE_VOCABULARY",
    "SUBTYPES_PROPERTY",
    "SUBTYPE_PROPERTY",
    "TIERS",
    "UNKNOWN_TYPE_KEY",
    "Rescue",
    "TypeVocabulary",
    "committed_type",
    "core_typed",
    "domain_type_aliases",
    "flag_unplaced_type",
    "has_phrase",
    "rescue_invalid_entity_types",
    "rescue_type",
    "set_subtype",
]
