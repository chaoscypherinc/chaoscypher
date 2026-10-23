# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""The known entities of a source so far: what pass 1 is told the earlier groups named.

Each extraction group is extracted on its own, so "the count", "Mamma" and
"the regiment" become nodes of their own or hang their facts on the wrong
named entity (spec 2026-10-11, role references). The list here is built
from the earlier groups' filtered pass-1 records and pass-2 edges, ranked
by how many groups cite each name, bounded, and rendered as the KNOWN
ENTITIES block of the pass-1 prompt. References (a label the name rule
reads as a title, role, descriptor or an article plus lowercase words) are
never listed: the list exists to resolve them. Nor is a record the pipeline
doubts (``name_identity.is_doubted_record``): the filters keep and flag it,
but it is no name the source evidenced.

The same list guards the next group's pass-1 records (:meth:`KnownEntities.guard`,
spec D3 as amended by D8): an alias that is another known entry's name is
dropped, and a known name its cited sentences do not evidence is demoted to
the phrase they write. Its name rules (:meth:`KnownEntities.identified`) are also
what the relationship evidence filter names a known endpoint by: a reference
phrase never names a known entity on its own.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from chaoscypher_core.exceptions import ValidationError
from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
    ALIAS_CROSSED_KNOWN_KEY,
    ARTICLES,
    DEFAULT_NAME_CONVENTIONS,
    REFERENT_UNEVIDENCED_KEY,
    UNIVERSAL_DETERMINERS,
    UNIVERSAL_PARTICLES,
    NameConventions,
    fold,
    identity_word,
    is_doubted_record,
    is_referent_unevidenced,
    is_relation_alias,
    labelled_by_reference,
)
from chaoscypher_core.services.sources.engine.extraction.utils.evidence_validator import (
    MIN_SIGNIFICANT_WORD_LENGTH,
    CitedSentences,
    CitedText,
    alias_name,
    as_name_conventions,
    naming_words,
)


if TYPE_CHECKING:
    from chaoscypher_core.settings import EngineSettings


KNOWN_ENTITIES_HEADER = "KNOWN ENTITIES (named earlier in this source; write them by these names):"
KNOWN_GRAPH_HEADER = (
    "KNOWN ENTITIES (in the graph already, or named earlier; write them by these names):"
)
"""The header of a list seeded from the graph (the memory path, spec 2026-10-20 decision 2)."""
MAX_RELATIONS_PER_LINE = 3
MIN_LINE_LIMIT = 24
"""The shortest ``line_limit`` taken: a line's head (``- Name (Type)``) of a short name fits."""
_PHRASES_SEPARATOR = "; "
"""How the name guard joins a kept record's written reference phrases under ``referent_unevidenced``."""
_RELATION_PROPERTY_KEYS = ("family_role", "title", "role")
"""P| keys whose values are relation phrases worth a line slot ("father of Natásha")."""


@dataclass(frozen=True)
class KnownEntry:
    """One named entity of the source so far, as the block renders it."""

    name: str
    entity_type: str
    aliases: tuple[str, ...]
    relations: tuple[str, ...]
    groups: int
    first_seen: int


@dataclass
class GuardStats:
    """What :meth:`KnownEntities.guard` did to one group's records."""

    aliases_crossed: int = 0
    """Aliases dropped for naming another known entry (``alias_crossed_known``)."""
    names_demoted: int = 0
    """Known names demoted to the one phrase their sentences write that names no one (a reference
    such as "the count", or a description such as "the Lord of Dunmere")."""
    names_flagged: int = 0
    """Known names kept as written but flagged ``referent_unevidenced`` (no such phrase, or several).

    Counted with :attr:`names_demoted` on ``referents_unevidenced_demoted``; the relationship
    evidence filter names such a record by no rung."""


@dataclass
class _Acc:
    """One known entry while the list is being built: its names, ties and how many groups cite it."""

    name: str
    entity_type: str
    first_seen: int
    groups: int = 0
    aliases: list[str] = field(default_factory=list)
    relations: list[str] = field(default_factory=list)


def _core_type(entity_type: str) -> str:
    """The core type of a ``Core/subtype`` type string (``Person/character`` → ``Person``)."""
    return (entity_type or "Entity").split("/", 1)[0]


def _is_reference(name: str, entity_type: str, conventions: NameConventions) -> bool:
    """Whether ``name`` is a reference (a title, role, descriptor or article-plus-lowercase label), never listed."""
    # ``labelled_by_reference`` reads a person's reference through ``reference_key``
    # and any other type's by its shape (an article and lowercase words). The type
    # is passed so that, under conventions that declare person types, a work's
    # title such as ``Caesar's Commentaries`` is not read as a person's descriptor.
    return labelled_by_reference(name, conventions, entity_type or None)


def _words(label: str) -> str:
    """A relation label as words (``child_of`` → ``child of``) for a line's relation phrase."""
    return label.replace("_", " ")


def _text(value: Any) -> str:
    """A record's text field stripped, or "" when it is not a string (``None`` is no alias)."""
    return value.strip() if isinstance(value, str) else ""


def _texts(value: Any) -> list[str]:
    """A record's list field: a lone string is one item, a non-string item is skipped."""
    if isinstance(value, str):
        value = [value]
    elif not isinstance(value, Sequence):
        return []
    return [t for t in (_text(v) for v in value) if t]


def _index(value: Any, size: int) -> int | None:
    """An edge endpoint as a record index, or None (a ``bool`` is no index)."""
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value < size:
        return value
    return None


def _multi_word(read: str) -> bool:
    """A name reading (``alias_name``'s) of two words or more beside articles, possessives, particles."""
    return sum(fold(w) not in UNIVERSAL_PARTICLES for w in read.split()) >= 2


def multi_word_name(alias: str, conventions: NameConventions) -> str | None:
    """``alias`` read as a name (``evidence_validator.alias_name``) of two words or more, or None.

    The words counted leave out articles, possessives and particles
    (``UNIVERSAL_PARTICLES``): ``Count Oswin`` and ``the Hermit of Vell`` are
    two, ``Oswin``, ``my dear Lela`` (read ``my Lela``) and ``My wife`` (no
    name) are none. Spec D8: a single-word alias (a diminutive, a given name
    alone) is no name of a known entry, and carries by no rule. The list's
    reading of a single word (:func:`offered_alias`).
    """
    read = alias_name(alias, conventions)
    return read if read is not None and _multi_word(read) else None


def _speaker_relative(label: str) -> bool:
    """``label`` opens with a possessive determiner (``my wife``, ``his father``): a speaker's phrase.

    The one reading of the possessive opener: a description (:func:`_describes`)
    and an alias the list never offers (:func:`offered_alias`).
    """
    words = fold(label).split()
    return bool(words) and words[0] in UNIVERSAL_DETERMINERS


def _describes(label: str) -> bool:
    """``label`` opens with an article or a possessive determiner (``the Hermit of Vell``, ``my wife``).

    A description, never a name: the articles, and the possessive opener
    (:func:`_speaker_relative`).
    """
    words = fold(label).split()
    return bool(words) and (words[0] in ARTICLES or _speaker_relative(label))


def _names_by_rule_one(
    alias: str, read: str | None, entity_type: str, conventions: NameConventions
) -> bool:
    """Whether an alias, read as ``read`` (``alias_name``), is one of rule 1's names (spec D8).

    No reference (``labelled_by_reference``), no description (it opens with
    an article or a possessive determiner: ``the Hermit of Vell`` is an
    epithet whose ``Vell`` would name the bearer wherever the place is
    written), and a multi-word name (:func:`_multi_word`). A description
    carries only as rule 2's whole phrase.
    """
    return (
        read is not None
        and _multi_word(read)
        and not _describes(alias)
        and not _is_reference(alias, entity_type, conventions)
    )


def offered_alias(alias: str, conventions: NameConventions) -> bool:
    """Whether the KNOWN ENTITIES block offers ``alias`` on its entry's line: never a single word.

    Spec D8 (the list): no single-word alias is offered, a diminutive, a
    given name alone or a bare title (``Lela``, ``Mamma``), since the NAME
    rule tells the model to write a known name the passage names, and a
    single word is the kind one wrong resolution certifies the next by; the
    entry's own name carries its words. A single word is one
    whitespace-delimited token, or an alias that reads as a name
    (``evidence_validator.alias_name``) of one word beside articles,
    possessives and particles (``my dear Lela`` reads ``my Lela``: not
    :func:`multi_word_name`). Nor is an alias that opens with a possessive
    determiner (``My wife``, ``his father``): it is relative to a speaker of
    one passage. A reference of several words (``the hostess``, ``the
    count``), which reads as no name, is offered. The entry keeps every
    alias: the alias guard's owners and :meth:`KnownEntities._resolve` read
    them all.
    """
    if len(fold(alias).split()) < 2 or _speaker_relative(alias):
        return False
    return alias_name(alias, conventions) is None or multi_word_name(alias, conventions) is not None


def _with_property(record: dict[str, Any], key: str, value: str) -> None:
    """Write the guard property ``key`` on ``record``'s own copy of its properties."""
    props = record.get("properties")
    record["properties"] = {**(props if isinstance(props, Mapping) else {}), key: value}


def _add_folded(items: list[str], value: str, *taken: str) -> None:
    """Append ``value`` unless it folds to an item already there or to one of ``taken``."""
    folded = fold(value)
    if folded and folded not in taken and all(fold(i) != folded for i in items):
        items.append(value)


class KnownEntities:
    """The running list: ``add_group`` after each group, ``block`` before the next."""

    def __init__(
        self,
        *,
        conventions: NameConventions = DEFAULT_NAME_CONVENTIONS,
        limit: int = 80,
        line_limit: int = 160,
        header: str = KNOWN_ENTITIES_HEADER,
    ) -> None:
        """A list of at most limit lines (0: off) of at most line_limit characters (>= 24).

        ``header`` is the block's first line: the source's own names by default, the graph's
        for a seeded list.
        """
        if line_limit < MIN_LINE_LIMIT:
            msg = f"line_limit must be at least {MIN_LINE_LIMIT}, got {line_limit}"
            raise ValidationError(msg, field="line_limit")
        self._conventions = conventions
        self._header = header
        self._limit = max(0, limit)
        self._line_limit = line_limit
        self._by_key: dict[str, _Acc] = {}
        self._alias_keys: dict[str, set[str]] = {}
        """Folded alias → the keys of every entry that gave it (one: it joins; more: ambiguous)."""
        self._readings: dict[tuple[str, int], tuple[str, frozenset[str]] | None] = {}
        """``(label, naming-word length)`` → its name reading (:meth:`_reading`), kept for good."""
        self._claims_by_length: dict[int, dict[str, frozenset[str]]] = {}
        """Naming-word length → family stem → the keys of the entries claiming it (:meth:`_claims`);
        emptied by :meth:`add_group`."""
        self.groups = 0

    @classmethod
    def for_plan(
        cls,
        chunk_kwargs: Mapping[str, Any],
        settings: EngineSettings,
        *,
        seed: Sequence[Mapping[str, Any]] = (),
    ) -> KnownEntities:
        """The empty list of a source extracting with a plan's ``chunk_kwargs``.

        The plan's naming conventions (its JSON, the ones the per-group
        evidence filter reads) and ``extraction.known_entities_limit``: the
        one constructor of every extraction path's list.

        ``seed`` (the memory path): records already known, the graph's nodes
        the text names and the speaker, folded in before any group under
        :data:`KNOWN_GRAPH_HEADER`. The seed is no group: ``groups`` stays 0.
        """
        known = cls(
            conventions=as_name_conventions(chunk_kwargs.get("name_conventions")),
            limit=settings.extraction.known_entities_limit,
            header=KNOWN_GRAPH_HEADER if seed else KNOWN_ENTITIES_HEADER,
        )
        if seed:
            known.add_group(seed, [])
            known.groups = 0
        return known

    @property
    def conventions(self) -> NameConventions:
        """The naming conventions the list reads every name by (its plan's)."""
        return self._conventions

    @property
    def enabled(self) -> bool:
        """False at limit 0: no block, no guard, and nothing worth reading to fold in."""
        return self._limit > 0

    def __len__(self) -> int:
        """How many named entities are known (every one, not only the rendered ones)."""
        return len(self._by_key)

    def stats(self) -> dict[str, int]:
        """The list's size as every path reports it: the groups folded in, the entities named."""
        return {"groups": self.groups, "entities": len(self)}

    def add_group(
        self,
        entities: Sequence[Mapping[str, Any]],
        relationships: Sequence[Mapping[str, Any]],
    ) -> None:
        """Fold one group's filtered pass-1 records and pass-2 edges into the list.

        A reference is never listed, nor a record the pipeline doubts
        (``name_identity.is_doubted_record``: a per-chunk filter's flag or the
        name guard's ``referent_unevidenced``): the per-group filters keep and
        flag such records, and the list is what the next groups are told the
        source named; a doubted record's aliases would also be the name
        guard's evidence for its own name in the next group. Neither counts a
        group nor carries an edge. A record the alias guard repaired
        (``alias_crossed_known``) is listed: the crossed alias is gone.
        """
        self.groups += 1
        if not self.enabled:
            return
        self._claims_by_length.clear()
        names = [_text(e.get("name")) for e in entities]
        keys: list[str | None] = []
        seen_this_group: set[str] = set()
        for e, name in zip(entities, names, strict=True):
            etype = _text(e.get("type"))
            if (
                not fold(name)
                or _is_reference(name, etype, self._conventions)
                or is_doubted_record(e.get("properties"))
            ):
                keys.append(None)
                continue
            key = self._resolve(fold(name))
            if key is None:
                key = fold(name)
                self._by_key[key] = _Acc(
                    name=name, entity_type=_core_type(etype), first_seen=len(self._by_key)
                )
            acc = self._by_key[key]
            if key not in seen_this_group:
                acc.groups += 1
                seen_this_group.add(key)
            if key != fold(name):
                # Named by a known alias: the group is counted, nothing added. Its edges are
                # dropped on both sides, since the alias may belong to another bearer.
                keys.append(None)
                continue
            keys.append(key)
            self._fold_record(acc, key, e, etype)
        for rel in relationships:
            src = _index(rel.get("source"), len(entities))
            tgt = _index(rel.get("target"), len(entities))
            if src is None or tgt is None or src == tgt:
                continue
            src_key, tgt_key = keys[src], keys[tgt]
            if src_key is None or tgt_key is None or src_key == tgt_key:
                continue
            target = self._by_key[tgt_key].name
            rel_type = _words(_text(rel.get("type")) or "related_to")
            _add_folded(self._by_key[src_key].relations, f"{rel_type} {target}")

    def _fold_record(self, acc: _Acc, key: str, record: Mapping[str, Any], etype: str) -> None:
        """Add a record's aliases and relation properties to its entry ``acc`` (keyed ``key``)."""
        # The parser files a reference alias ("the count") under ``descriptors``
        # (``line_parser._separate_descriptors``): both are the entry's aliases,
        # except a relation of someone else ("Prince Andrew's father"), which
        # names no one this entry is (``is_relation_alias``).
        for alias in (*_texts(record.get("aliases")), *_texts(record.get("descriptors"))):
            if is_relation_alias(alias, self._conventions, etype):
                continue
            _add_folded(acc.aliases, alias, key)
            if fold(alias) and fold(alias) != key:
                self._alias_keys.setdefault(fold(alias), set()).add(key)
        props = record.get("properties")
        if not isinstance(props, Mapping):
            props = {}
        for pkey in _RELATION_PROPERTY_KEYS:
            value = _text(props.get(pkey))
            if value:
                _add_folded(acc.relations, value)

    def _resolve(self, folded: str) -> str | None:
        """The key of the entry a folded label names, or None.

        A label names an entry by its own name, or by an alias that exactly one entry gave.
        An alias given by two entries (``Anna`` of two Annas) is ambiguous and joins neither.
        """
        if folded in self._by_key:
            return folded
        claimants = self._alias_keys.get(folded, set())
        return next(iter(claimants)) if len(claimants) == 1 else None

    def resolves(self, name: Any, entity_type: Any = "") -> str | None:
        """The key of the known entry a record's NAME is, or None.

        None when the list is off, the name is empty, or the name is a
        reference (``the count`` is no known entity, whoever gave it as an
        alias): the records the name guard checks, and the endpoints the
        relationship evidence filter names only by :meth:`identified`.
        """
        label = _text(name)
        if not self.enabled or not fold(label):
            return None
        if _is_reference(label, _text(entity_type), self._conventions):
            return None
        return self._resolve(fold(label))

    def _reading(self, label: str, min_word_length: int) -> tuple[str, frozenset[str]] | None:
        """``label`` read as a name once: its ``alias_name`` reading and that reading's naming words.

        None when the reading holds no naming word (``My wife``). The one
        reading of a label in this list (the claims, rule 1, rule 2 and the
        demotion phrases all read it), computed once per label and length:
        it depends only on the conventions.
        """
        cache_key = (label, min_word_length)
        if cache_key not in self._readings:
            read = alias_name(label, self._conventions)
            self._readings[cache_key] = (
                None
                if read is None
                else (read, frozenset(naming_words([read], self._conventions, min_word_length)))
            )
        return self._readings[cache_key]

    def _claims(self, min_word_length: int) -> Mapping[str, frozenset[str]]:
        """Family stem → the entries that claim it: a naming word of the entry's name or names.

        An entry's names are its name and its aliases that are names
        (:meth:`_name_labels`), read once (:meth:`_reading`); a word is
        claimed by its family stem (``name_identity.identity_word``, the stem
        ``names_person`` compares), so ``Varn`` and ``Varna`` are one word
        where the conventions fold the surname forms. Built once per list
        state and naming-word length.
        """
        claims = self._claims_by_length.get(min_word_length)
        if claims is None:
            found: dict[str, set[str]] = {}
            for key, acc in self._by_key.items():
                for label in self._name_labels(acc.name, acc.entity_type, acc.aliases):
                    reading = self._reading(label, min_word_length)
                    for word in reading[1] if reading is not None else ():
                        found.setdefault(identity_word(word, self._conventions), set()).add(key)
            claims = {stem: frozenset(keys) for stem, keys in found.items()}
            self._claims_by_length[min_word_length] = claims
        return claims

    def _distinct(self, words: Iterable[str], min_word_length: int) -> set[str]:
        """The naming words fewer than two known entries claim: a word two claim names neither (D8)."""
        claims = self._claims(min_word_length)
        conv = self._conventions
        return {w for w in words if len(claims.get(identity_word(w, conv), ())) < 2}

    def named(
        self,
        key: str,
        record_name: str,
        ref: CitedSentences,
        *,
        min_word_length: int = MIN_SIGNIFICANT_WORD_LENGTH,
        phrases_only: bool = False,
    ) -> bool:
        """Rule 1 of the name guard (spec D8): the entry at ``key`` is named by name in ``ref``.

        ``ref``'s cited sentences, or the one sentence before the first of
        them (:meth:`CitedSentences.read` with ``look_back``), write the
        record's name, the entry's name, or one of the entry's aliases that
        is a rule-1 name (:func:`_names_by_rule_one`: multi-word, no
        reference, no description), each read as a name (:meth:`_reading`):

        - as a phrase, unless every naming word of it is one that two or more
          known entries claim (the entry's own name always counts);
        - or by a naming word of at least ``min_word_length`` letters
          (``CitedText.names_word``: the surname form) that fewer than two
          known entries claim (:meth:`_distinct`); ``phrases_only`` (the
          relationship filter's strict rung) reads no naming word.

        The record's name is read only when it is the entry's own name (it
        folds to ``key``): a record named by an alias of the entry is read by
        the entry's names alone. A single-word alias of the entry (a
        diminutive, a given name alone) is no name here, nor a reference or a
        description (``the Hermit of Vell`` carries only as rule 2's whole
        phrase), nor a title, nor a label whose reading holds no naming word
        (``My wife`` reads as ``My``): the record's own aliases never name it. The one reading of
        rule 1, called through :meth:`identified` by the name guard and the
        relationship evidence filter's known endpoints.
        """
        acc = self._by_key[key]
        conv = self._conventions
        cited = ref.read(conv, look_back=True)
        # A record named by one of the entry's aliases (``the King of Valdria``) is not read by
        # its written name: that may be a description whose place word names no one. Its names
        # are the entry's; the written name carries only as rule 2's whole phrase.
        labels = [record_name, acc.name] if fold(record_name) == key else [acc.name]
        for alias in self._name_labels(acc.name, acc.entity_type, acc.aliases)[1:]:
            reading = self._reading(alias, min_word_length)
            read = reading[0] if reading is not None else None
            if _names_by_rule_one(alias, read, acc.entity_type, conv):
                labels.append(alias)
        for label in labels:
            reading = self._reading(label, min_word_length)
            if reading is None:  # "My wife" reads as "My": no name
                continue
            read, words = reading
            distinct = self._distinct(words, min_word_length)
            if cited.has_phrase(read) and (distinct or not words or fold(label) == key):
                return True
            if not phrases_only and any(cited.names_word(w, conv) for w in distinct):
                return True
        return False

    def identified(
        self,
        key: str,
        record: Mapping[str, Any],
        ref: CitedSentences,
        *,
        min_word_length: int = MIN_SIGNIFICANT_WORD_LENGTH,
        phrases_only: bool = False,
    ) -> bool:
        """Whether ``ref`` evidences ``record`` as the known entry at ``key``: rule 1 or rule 2 (D8).

        Rule 1, named by name (:meth:`named`, ``phrases_only`` passed on), or
        rule 2, a distinctive known reference carried as a whole phrase
        (:meth:`_carried`). The one reading of both rules: the name guard
        and the relationship evidence filter's known endpoints call it.
        """
        name = _text(record.get("name"))
        if self.named(key, name, ref, min_word_length=min_word_length, phrases_only=phrases_only):
            return True
        cited = ref.read(self._conventions)
        etype = _text(record.get("type")) or self._by_key[key].entity_type
        return self._carried(
            key, cited, self._phrases(record, cited, etype, min_word_length), min_word_length
        )

    def _phrases(
        self, record: Mapping[str, Any], cited: CitedText, entity_type: str, min_word_length: int
    ) -> list[str]:
        """The reference phrases ``record`` gives that ``cited`` writes, its name aside.

        A label the name rule reads as a reference, a description (``the
        Hermit of Vell``), or a label naming only by words two entries claim.
        Rule 2's condition and the name guard's demotion phrases.
        """
        conv = self._conventions
        phrases: list[str] = []
        name = fold(_text(record.get("name")))
        for label in (*_texts(record.get("aliases")), *_texts(record.get("descriptors"))):
            if fold(label) != name and cited.has_phrase(label):
                reading = self._reading(label, min_word_length)
                words = reading[1] if reading is not None else frozenset()
                if (
                    _is_reference(label, entity_type, conv)
                    or _describes(label)
                    or (words and not self._distinct(words, min_word_length))
                ):
                    _add_folded(phrases, label)
        return phrases

    def _carried(
        self, key: str, cited: CitedText, phrases: Sequence[str], min_word_length: int
    ) -> bool:
        """Rule 2 (spec D8): ``cited`` writes, whole, a distinctive alias of the entry at ``key``.

        A multi-word alias this entry alone gave (``_resolve``'s reading: an
        alias two entries gave names neither) that holds a naming word no two
        entries claim (``the King of Valdria``), and every one of ``phrases``
        (the record's written reference phrases, :meth:`_phrases`) is such an
        alias. A phrase of title, kinship, article, pronoun and possessive
        words only holds no naming word and never carries.
        """
        own_aliases = []
        for alias in self._by_key[key].aliases:
            reading = self._reading(alias, min_word_length)
            if (
                reading is not None
                and _multi_word(reading[0])
                and self._alias_keys.get(fold(alias)) == {key}
                and self._distinct(reading[1], min_word_length)
            ):
                own_aliases.append(alias)
        known = {fold(a) for a in own_aliases}
        return any(cited.has_phrase(a) for a in own_aliases) and all(
            fold(p) in known for p in phrases
        )

    def guard(
        self,
        entities: list[dict[str, Any]],
        sentences: Sequence[str],
        *,
        min_word_length: int = MIN_SIGNIFICANT_WORD_LENGTH,
    ) -> tuple[list[dict[str, Any]], GuardStats]:
        """The alias guard and the name guard on one group's pass-1 records (spec D3, D8).

        Off (the records returned unchanged) when the limit is 0 or nothing is
        known yet. Every record is copied; the inputs are never changed.

        **Alias guard.** An alias of a record that folds to the name or a given
        name (an alias that is a name, not a reference) of exactly one known
        entry other than the record's own is dropped, and the dropped aliases
        are written under ``alias_crossed_known`` joined by ``; ``. The
        record's own entry is the one its name resolves to (its name, or an
        alias exactly one entry gave); an alias two entries gave crosses
        neither.

        **Name guard.** A record whose name is a name (not a reference) and
        resolves to a known entry (:meth:`resolves`) must be evidenced by its
        cited sentences, by one of these, in order (spec D8):

        1. Named by name (:meth:`named`): the cited sentences, or the one
           before the first of them, write the record's name (when it is the
           entry's own; a record named by an alias of the entry is read by
           the entry's names alone), the entry's name or one of the entry's
           multi-word aliases that is no reference and no description
           (:func:`_names_by_rule_one`), as a phrase or by a naming word of at least
           ``min_word_length`` letters (``evidence_validator``'s name rung,
           the length the evidence filter reads by) that no two known
           entries claim. A single-word alias of the entry is no name here.
        2. A distinctive known reference: the cited sentences write, as a
           whole phrase, a multi-word alias earlier groups gave this entry
           alone that holds a naming word no two entries claim (``the
           Hermit of Vell``), and every reference phrase the record gives
           that the sentences write is one of them. A phrase of title,
           kinship, article, pronoun and possessive words only ("the
           count", "Mamma") never carries; the record's own aliases are
           never evidence on their own.

        A bare title never names a known entry, even its one holder (D3's
        rule 3, removed by D8). Unevidenced, the record is demoted to the one
        phrase its sentences write that names no one (:meth:`_phrases`: a
        label the name rule reads as a reference, a description such as "the
        King of Valdria", or a label whose every naming word two entries
        claim: "the young Lady Varn"), whatever its shape, since a record kept
        under the known name still commits onto that entity;
        ``referent_unevidenced`` holds the original name. With none or several
        it is kept as written and flagged (the phrases joined by ``; ``, or
        the name), and the relationship evidence filter names it by no rung.
        Both are counted (:class:`GuardStats`). Rules 1 and 2 are
        :meth:`identified`. A record citing no readable sentence is left to
        the evidence filter.
        """
        stats = GuardStats()
        if not self.enabled or not self._by_key:
            return entities, stats
        entries = self.names()
        owners: dict[str, set[str]] = {}
        for key, entry in entries.items():
            for given in self._given_names(entry):
                owners.setdefault(given, set()).add(key)
        out: list[dict[str, Any]] = []
        for original in entities:
            record = dict(original)
            name = _text(record.get("name"))
            key = self._resolve(fold(name)) or fold(name)
            self._guard_aliases(record, key, owners, stats)
            own_key = self.resolves(name, record.get("type"))
            if own_key is not None:
                self._guard_name(
                    record, own_key, entries[own_key], sentences, min_word_length, stats
                )
            out.append(record)
        return out, stats

    def _name_labels(self, name: str, entity_type: str, aliases: Iterable[str]) -> list[str]:
        """An entry's name and its aliases that are names, name first.

        An alias is a name when it is no reference and reads as a name that
        holds a naming word (``evidence_validator.alias_name``: ``My wife``
        reads as ``My``, no name): the given names the alias guard reads, the
        words the name rule counts claimants of, and the aliases it names by.
        """
        conv = self._conventions
        return [
            name,
            *(
                a
                for a in aliases
                if not _is_reference(a, entity_type, conv) and alias_name(a, conv) is not None
            ),
        ]

    def _given_names(self, entry: KnownEntry) -> set[str]:
        """The folded labels a record may not borrow from ``entry``: its name, its aliases that are names."""
        return {fold(n) for n in self._name_labels(entry.name, entry.entity_type, entry.aliases)}

    @staticmethod
    def kept_unevidenced(record: Mapping[str, Any]) -> bool:
        """Whether the name guard KEPT ``record``'s known name and flagged it, rather than demoting it.

        The guard's own reading of what it wrote (:meth:`_guard_name`): kept,
        ``referent_unevidenced`` holds the record's name (no phrase written) or
        the two or more reference phrases it gives, joined; demoted, it holds
        the original known name, and the record's name is now the phrase. The
        relationship evidence filter refuses a kept record's every edge, and
        reads a demoted one by its new name as any record written so
        (coordinator's correction to final review 2, I1).
        """
        props = record.get("properties")
        if not isinstance(props, Mapping) or not is_referent_unevidenced(props):
            return False
        value = str(props[REFERENT_UNEVIDENCED_KEY]).strip()
        if fold(value) == fold(_text(record.get("name"))):
            return True
        parts = [fold(p) for p in value.split(_PHRASES_SEPARATOR)]
        labels = {
            fold(t) for t in (*_texts(record.get("aliases")), *_texts(record.get("descriptors")))
        }
        return len(parts) >= 2 and all(p in labels for p in parts)

    @staticmethod
    def _guard_aliases(
        record: dict[str, Any], key: str, owners: Mapping[str, set[str]], stats: GuardStats
    ) -> None:
        """The alias guard: drop an alias that is another known entry's name, flag the record, count it."""
        aliases = record.get("aliases")
        if isinstance(aliases, str) or not isinstance(aliases, Sequence):
            return
        kept: list[Any] = []
        crossed: list[str] = []
        for alias in aliases:
            claimants = owners.get(fold(alias)) if isinstance(alias, str) else None
            if claimants is not None and len(claimants) == 1 and key not in claimants:
                crossed.append(alias.strip())
            else:
                kept.append(alias)
        if crossed:
            record["aliases"] = kept
            _with_property(record, ALIAS_CROSSED_KNOWN_KEY, "; ".join(crossed))
            stats.aliases_crossed += len(crossed)

    def _guard_name(
        self,
        record: dict[str, Any],
        key: str,
        entry: KnownEntry,
        sentences: Sequence[str],
        min_word_length: int,
        stats: GuardStats,
    ) -> None:
        """The name guard: a known name its cited sentences do not support is demoted or flagged (D8)."""
        ref = CitedSentences.of(record.get("sent_ref"), sentences)
        if ref.failure is not None:
            return
        if self.identified(key, record, ref, min_word_length=min_word_length):  # rules 1 and 2
            return
        etype = _text(record.get("type")) or entry.entity_type
        phrases = self._phrases(record, ref.read(self._conventions), etype, min_word_length)
        if len(phrases) == 1:
            _with_property(record, REFERENT_UNEVIDENCED_KEY, record["name"])
            record["name"] = phrases[0]
            for field_name in ("aliases", "descriptors"):
                labels = record.get(field_name)
                if isinstance(labels, list):
                    record[field_name] = [
                        a
                        for a in labels
                        if not (isinstance(a, str) and fold(a) == fold(phrases[0]))
                    ]
            stats.names_demoted += 1
        else:
            _with_property(
                record, REFERENT_UNEVIDENCED_KEY, _PHRASES_SEPARATOR.join(phrases) or record["name"]
            )
            stats.names_flagged += 1

    def _ranked(self) -> list[_Acc]:
        """The entries the block renders: most groups cited first, then first seen, cut at the limit."""
        return sorted(self._by_key.values(), key=lambda a: (-a.groups, a.first_seen))[: self._limit]

    def names(self) -> dict[str, KnownEntry]:
        """Folded name → entry, for the guards (every entry, not only the rendered ones)."""
        return {
            key: KnownEntry(
                a.name, a.entity_type, tuple(a.aliases), tuple(a.relations), a.groups, a.first_seen
            )
            for key, a in self._by_key.items()
        }

    def _line(self, a: _Acc) -> str:
        """One rendered line, cut to the line limit with the aliases given up before the relations.

        Only the aliases the block offers (:func:`offered_alias`: no single word).
        """
        head = f"- {a.name} ({a.entity_type})"
        relations = a.relations[:MAX_RELATIONS_PER_LINE]
        aliases = [alias for alias in a.aliases if offered_alias(alias, self._conventions)]
        while True:
            parts: list[str] = []
            if aliases:
                parts.append("also: " + ", ".join(aliases))
            parts.extend(relations)
            line = head + (" — " + "; ".join(parts) if parts else "")
            if len(line) <= self._line_limit or not (aliases or relations):
                return line[: self._line_limit]
            if aliases:  # the ties outrank the aliases: cut the aliases first
                aliases.pop()
            else:
                relations.pop()

    def lines(self) -> list[str]:
        """The block's lines, ranked by groups cited then first appearance, bounded and cut."""
        return [self._line(a) for a in self._ranked()]

    def block(self) -> str:
        """The KNOWN ENTITIES block, or "" when nothing is known or the limit is 0."""
        lines = self.lines()
        return self._header + "\n" + "\n".join(lines) if lines else ""


def known_entities_from_rows(
    rows: Iterable[Mapping[str, Any]], *, known: KnownEntities
) -> KnownEntities:
    """Fold stored chunk-task rows into ``known``, one ``add_group`` per row, and return it.

    The queue path's list (spec 2026-10-11): each chunk task rebuilds it at
    its start from the job's completed lower rows
    (``get_completed_chunk_entities``), whose ``raw_entities`` and
    ``raw_relationships`` are the group's filtered records and its edges by
    chunk-local index, the shape ``add_group`` reads. ``known`` is the empty
    list :meth:`KnownEntities.for_plan` made. A completed row with no output
    is still a group seen.
    """
    for row in rows:
        known.add_group(row.get("raw_entities") or [], row.get("raw_relationships") or [])
    return known


__all__ = [
    "ALIAS_CROSSED_KNOWN_KEY",
    "KNOWN_ENTITIES_HEADER",
    "MAX_RELATIONS_PER_LINE",
    "MIN_LINE_LIMIT",
    "REFERENT_UNEVIDENCED_KEY",
    "GuardStats",
    "KnownEntities",
    "KnownEntry",
    "known_entities_from_rows",
    "multi_word_name",
    "offered_alias",
]
