# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The words a sentence uses to state a relationship: a domain's ``relation_vocabulary``.

One vocabulary, two readers: the evidence validator's one-endpoint rule (a
relationship whose cited sentences name one endpoint is supported when they
also state the relation: "With her elder sister I was stricter" supports
``sibling_of`` with only Véra named) and the graph scan tool's kinship
checks (a ``sibling_of`` edge justified by "his cousin" names another
relation). A label's own words ("sibling", "parent") rarely appear in
prose; the kin words ("sister", "father", "your son") do.

Every domain's vocabulary is the universal kin and social vocabulary
(``name_identity.UNIVERSAL_RELATION_VOCABULARY``, English, language-level:
parents, siblings, spouses, cousins, grandparents, uncles and aunts, kin,
friends) extended by what the domain's ``.jsonld`` declares as relation name
-> ``{"labels": [...], "words": [...]}`` (a declared relation of the same
name adds labels and words to the universal one; the literary domain adds its
genre's godparents, betrothals, patrons, heirs and hosts), read by
``ConfigurableDomain.get_relation_vocabulary`` and carried to the validator
by the extraction plan (:func:`domain_relation_vocabulary`). One source, so
the name rule's descriptor words and the validator's kin words cannot drift
(until 2026-10-06 the universal list lived beside a copy in the literary
domain, and a domain that declared none had no kin words at all).

Words are lowercase and accent-free (``name_identity.fold``, the one fold of
names and relation words: until audit wave 6 this module kept its own, which
split a word at a soft hyphen and kept U+2010), and match as whole words, hyphenated words whole (``name_identity.WORD_PATTERN``): "son"
does not match "Sónya" or "person", "father" does not match "godfather" (a
godfather is not a parent), "sister" does not match "sister-in-law". An
entry of several words is a phrase and matches as consecutive whole words
("engaged to" states ``betrothed_to``; "engaged in conversation" does not).
A relation's words name it in either direction ("a son" states ``parent_of``
as much as ``child_of``), so every label of one relation shares its words.

The vocabulary is the only evidence a relation is stated: never the words of
the edge label itself (until 2026-10-06 the label's words were matched as
substrings, so "with" of ``interacts_with`` stood in almost every sentence,
``heir_of`` passed on "their" and ``member_of`` on "remember": audit wave 5),
and never a function word (:data:`FUNCTION_WORDS`: an entry made only of
them states nothing and is refused). A label no vocabulary entry states needs
both endpoints named.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import cached_property
from typing import Any

from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
    UNIVERSAL_RELATION_VOCABULARY,
    WORD_PATTERN,
    fold,
)


FUNCTION_WORDS = frozenset(
    {
        "a", "an", "the", "of", "to", "in", "on", "at", "by", "for", "with", "without", "from",
        "into", "onto", "upon", "about", "over", "under", "between", "among", "through", "after",
        "before", "and", "or", "nor", "but", "as", "than", "that", "this", "these", "those",
        "is", "are", "was", "were", "be", "been", "being", "has", "have", "had", "do", "does",
        "did", "he", "she", "it", "they", "we", "you", "i", "him", "her", "them", "us", "me",
        "his", "hers", "its", "their", "our", "your", "my", "who", "whom", "whose", "which",
        "not", "no", "so", "if", "then", "there", "here", "s",
    }
)  # fmt: skip
"""English words that state no relation by themselves (articles, prepositions, pronouns, auxiliaries).

Never relation evidence (a vocabulary entry made only of them is refused:
:func:`is_function_phrase`), and never what makes a relationship label match
a template (``entity_cleaner``'s fuzzy edge lookup)."""


def text_words(text: str) -> tuple[str, ...]:
    """The whole words of ``text`` in order, folded (:data:`~name_identity.WORD_PATTERN`)."""
    return tuple(WORD_PATTERN.findall(fold(text)))


def is_function_phrase(entry: str) -> bool:
    """``entry`` has no word but function words (:data:`FUNCTION_WORDS`): it states no relation."""
    return all(w in FUNCTION_WORDS for w in text_words(entry))


_PAST_FORMS = frozenset(
    {"wrote", "led", "sent", "built", "born", "found", "set", "held", "made", "won", "fought",
     "taught", "brought", "bought", "drew", "began", "gave", "took", "met", "left", "ran", "saw",
     "spoke", "stole", "struck", "swore", "told", "went", "overthrew", "rode"}
)  # fmt: skip
"""Irregular past forms a label may be built on (``wrote``, ``led``): verbs, never relation nouns."""

_AGENT_ENDINGS = ("al", "or", "er", "ist", "ian", "ant", "ent", "ee", "ess", "ary", "ier")
"""Endings of a noun naming a person by the part they play (``rival``, ``mentor``, ``patron``-like)."""


def label_evidence_words(label: str) -> tuple[str, ...]:
    """The words of an edge label that prose plausibly uses to state the relation itself.

    The head noun of a relational label (``member`` of ``member_of``,
    ``subsidiary`` of ``subsidiary_of``, ``staff`` and ``member`` of
    ``staff_member``) and a one-word label that is a person noun in the
    plural or a verb that is one (``rivals``, ``mentors``; its singular ends
    like a noun of a part played: :data:`_AGENT_ENDINGS`). Never a function
    word, a past or ``-ing`` form, or the verb a label starts with
    (``interacts`` of ``interacts_with``). Over-inclusive on purpose: a word
    found here makes a two-endpoints-only template say why
    (``two_endpoints_only_reason``), since audit wave 6 found ``rivals`` and
    ``member_of`` marked two-endpoints-only for want of a vocabulary entry
    while "the rivals" and "a member of" stood in the cited sentences.

    Args:
        label: An edge label such as ``member_of`` (any case).

    Returns:
        The plausible evidence words, in label order; empty for none.
    """
    parts = [p for p in canonical_edge_label(label).split("_") if p]
    out: list[str] = []
    for i, word in enumerate(parts):
        if word in FUNCTION_WORDS or word.endswith(("ed", "ing")) or word in _PAST_FORMS:
            continue
        if len(parts) == 1:
            singular = word[:-1] if word.endswith("s") and not word.endswith("ss") else word
            if singular.endswith(_AGENT_ENDINGS):
                out.append(word)
        elif i == 0:
            if not (word.endswith("s") and not word.endswith("ss")):
                out.append(word)  # the head noun of member_of, part_of_system
        elif word.endswith(_AGENT_ENDINGS):
            out.append(word)  # a person noun after the head: staff_member
    return tuple(out)


TWO_ENDPOINTS_REASONS: dict[str, str] = {
    "verb": (
        "The label is a verb ('X delivers Y'): its word beside one named end says what that "
        "end did, never who or what the other end is."
    ),
    "unmeasured": (
        "No corpus of this domain has been replayed with the word as evidence: a one-endpoint "
        "rule is admitted only where a replay keeps true edges and adds no false one (audit "
        "wave 6), so both ends stay required until one does."
    ),
}
"""The shared reasons a two-endpoints-only edge template may give by name (its
``two_endpoints_only_reason``); a template with a reason of its own writes it out."""


def _contains_phrase(words: tuple[str, ...], phrase: tuple[str, ...]) -> bool:
    """``phrase`` occurs in ``words`` as consecutive whole words."""
    n = len(phrase)
    return any(words[i : i + n] == phrase for i in range(len(words) - n + 1))


_LABEL_SEPARATORS = re.compile(r"[\s_]+")


def canonical_edge_label(raw: str | None) -> str:
    """A relationship label in its one canonical form: lowercase, spaces and underscores one ``_``.

    ``Confides In``, ``confides_in`` and ``  CONFIDES   IN  `` are all
    ``confides_in``. The one label normaliser: the commit persists labels
    in this form, the strict edge-type filter matches templates in it and
    the relation vocabulary keys labels by it (until audit wave 5 the three
    normalised differently, so "betrothed to" was dropped by the strict
    filter though the commit would have stored it as ``betrothed_to``).
    Empty for ``None`` or a blank label.
    """
    if not raw:
        return ""
    return _LABEL_SEPARATORS.sub("_", raw.strip().lower()).strip("_")


def _label_key(label: str) -> str:
    """A relationship label as the vocabulary keys it (:func:`canonical_edge_label`)."""
    return canonical_edge_label(label)


SINGLE_VALUED_KEY = "single_valued"
"""The entry key declaring a relation single-valued (spec 2026-10-20 decision 6)."""


@dataclass(frozen=True)
class RelationVocabulary:
    """A domain's relations: each one's edge labels and the words prose uses for it.

    Build one from a domain's ``relation_vocabulary`` with :meth:`from_config`.
    Hashable, and :meth:`as_config` gives back the JSON shape (what the
    extraction plan stores in a job's ``extraction_config``).
    """

    relations: tuple[tuple[str, frozenset[str], frozenset[str]], ...] = ()
    """``(relation, labels, words)`` per relation, labels and words folded."""
    single_valued: frozenset[str] = frozenset()
    """The relations (by name) whose entry declared ``single_valued``: one object at a time."""

    @classmethod
    def from_config(
        cls, config: Mapping[str, Any] | RelationVocabulary | None
    ) -> RelationVocabulary:
        """The vocabulary a domain's ``relation_vocabulary`` mapping declares (empty for None)."""
        if isinstance(config, RelationVocabulary):
            return config
        relations = []
        single: set[str] = set()
        for name, entry in sorted((config or {}).items()):
            labels = frozenset(_label_key(str(x)) for x in (entry or {}).get("labels") or [])
            words = frozenset(
                " ".join(text_words(str(x)))
                for x in (entry or {}).get("words") or []
                if not is_function_phrase(str(x))
            )
            if labels and words:
                relations.append((str(name), labels, words))
                if (entry or {}).get(SINGLE_VALUED_KEY):
                    single.add(str(name))
        return cls(tuple(relations), frozenset(single))

    def as_config(self) -> dict[str, dict[str, Any]]:
        """The ``relation_vocabulary`` JSON shape, sorted (inverse of :meth:`from_config`).

        An entry carries ``single_valued`` only when it declared it, so a vocabulary that
        declares none (the universal one) reads back as before.
        """
        return {
            name: {
                "labels": sorted(labels),
                "words": sorted(words),
                **({SINGLE_VALUED_KEY: True} if name in self.single_valued else {}),
            }
            for name, labels, words in self.relations
        }

    @cached_property
    def single_valued_labels(self) -> frozenset[str]:
        """The edge labels of the single-valued relations."""
        return frozenset(
            label
            for name, labels, _ in self.relations
            if name in self.single_valued
            for label in labels
        )

    @cached_property
    def label_relation(self) -> dict[str, str]:
        """Each edge label and the relation it states."""
        return {label: name for name, labels, _ in self.relations for label in labels}

    @cached_property
    def word_relation(self) -> dict[str, str]:
        """Each word and the relation it names."""
        return {word: name for name, _, words in self.relations for word in words}

    @cached_property
    def _words(self) -> dict[str, frozenset[str]]:
        """Each relation's words."""
        return {name: words for name, _, words in self.relations}

    def relation_words(self, label: str) -> frozenset[str]:
        """The words beyond the label's own that state ``label``, or none.

        Args:
            label: A relationship label such as ``sibling_of`` (any case).

        Returns:
            The words of the label's relation; empty for a label the
            vocabulary does not name.
        """
        relation = self.label_relation.get(_label_key(label))
        return self._words.get(relation, frozenset()) if relation else frozenset()

    def states_relation(self, text: str, label: str) -> bool:
        """Whether ``text`` names ``label``'s relation by one of its words or phrases, whole.

        A one-word entry matches a whole word of ``text``; a phrase matches
        as consecutive whole words. The label's own words are never read. A
        word standing inside a phrase of another relation is that relation's
        and states nothing else ("proposed marriage" states a betrothal; its
        "marriage" is no spouse word there).

        Args:
            text: The text to read (folded here; already-folded text is fine).
            label: A relationship label such as ``parent_of``.

        Returns:
            True when a word or phrase of the label's relation appears in ``text``.
        """
        relation = self.label_relation.get(_label_key(label))
        entries = self._words.get(relation, frozenset()) if relation else frozenset()
        if not entries:
            return False
        words = text_words(text)
        taken = self._phrase_positions(words, besides=relation)
        for entry in entries:
            phrase = tuple(entry.split())
            if len(phrase) == 1:
                if any(w == phrase[0] and i not in taken for i, w in enumerate(words)):
                    return True
            elif _contains_phrase(words, phrase):
                return True
        return False

    def _phrase_positions(self, words: tuple[str, ...], *, besides: str | None) -> set[int]:
        """Positions of ``words`` inside a phrase of a relation other than ``besides``."""
        taken: set[int] = set()
        for name, phrase in self._phrases:
            if name == besides:
                continue
            n = len(phrase)
            for i in range(len(words) - n + 1):
                if words[i : i + n] == phrase:
                    taken.update(range(i, i + n))
        return taken

    @cached_property
    def _phrases(self) -> tuple[tuple[str, tuple[str, ...]], ...]:
        """``(relation, phrase)`` for every entry of two or more words."""
        return tuple(
            (name, tuple(w.split()))
            for name, _, words in self.relations
            for w in sorted(words)
            if " " in w
        )

    @cached_property
    def words(self) -> frozenset[str]:
        """Every word of every relation."""
        return frozenset(w for _, _, words in self.relations for w in words)


EMPTY_RELATION_VOCABULARY = RelationVocabulary()
"""No vocabulary at all: no words beyond each label's own (a job stored without one)."""


def extend_vocabulary(
    declared: Mapping[str, Any] | None,
    base: Mapping[str, Mapping[str, Any]] = UNIVERSAL_RELATION_VOCABULARY,
    *,
    exclusions: Iterable[str] = (),
) -> dict[str, dict[str, Any]]:
    """``base`` extended by ``declared``: a relation of the same name gains its labels and words.

    ``exclusions`` are labels the base does not state in this domain (a
    financial ``parent_of`` is a parent company); a base relation left with
    no label is dropped. Order is kept (the base's first), each label and word once.
    """
    excluded = {_label_key(x) for x in exclusions}
    out: dict[str, dict[str, Any]] = {}
    for name, entry in base.items():
        labels = [
            str(x) for x in (entry or {}).get("labels") or [] if _label_key(x) not in excluded
        ]
        if labels:
            out[str(name)] = {
                "labels": labels,
                "words": [str(x) for x in (entry or {}).get("words") or []],
            }
    for name, entry in (declared or {}).items():
        slot = out.setdefault(str(name), {"labels": [], "words": []})
        for key in ("labels", "words"):
            slot[key] = list(dict.fromkeys([*slot[key], *((entry or {}).get(key) or [])]))
        if (entry or {}).get(SINGLE_VALUED_KEY):
            slot[SINGLE_VALUED_KEY] = True
    return out


UNIVERSAL_VOCABULARY = RelationVocabulary.from_config(UNIVERSAL_RELATION_VOCABULARY)
"""The vocabulary of a domain that declares none (and of no domain): the universal one."""


def relation_words(label: str, vocabulary: RelationVocabulary | None = None) -> frozenset[str]:
    """``vocabulary.relation_words(label)`` (the empty vocabulary when None)."""
    return (vocabulary or EMPTY_RELATION_VOCABULARY).relation_words(label)


def states_relation(text: str, label: str, vocabulary: RelationVocabulary | None = None) -> bool:
    """``vocabulary.states_relation(text, label)`` (the empty vocabulary when None)."""
    return (vocabulary or EMPTY_RELATION_VOCABULARY).states_relation(text, label)


def domain_relation_config(domain: Any) -> dict[str, dict[str, list[str]]]:
    """A stack's (or one domain object's) whole ``relation_vocabulary``: the universal one extended.

    The universal vocabulary less every member's two-endpoints-only labels
    (``get_vocabulary_exclusions``: the floor's and each pack's), extended by
    every member's declared entries in member order (the floor's, then each
    pack's, sorted by name): a relation of the same name gains each member's
    labels and words. A pack cannot contradict the floor, so the union
    resolves no conflict. The one reading of a stack's relations: the
    validator's vocabulary (:func:`domain_relation_vocabulary`) and the name
    rule's descriptor words (``domain_resolver.name_tables_of_domain``) both
    come from it. No domain reads the universal vocabulary.
    """
    from chaoscypher_core.services.sources.engine.extraction.domains.stack import DomainStack

    # a stack's floor and packs, or the one domain object
    if isinstance(domain, DomainStack):
        members: tuple[Any, ...] = domain.members
    else:
        members = () if domain is None else (domain,)
    declared: list[Mapping[str, Any]] = []
    excluded: list[str] = []
    for member in members:
        getter = getattr(member, "get_relation_vocabulary", None)
        table = getter() if callable(getter) else None
        if isinstance(table, Mapping):
            declared.append(table)
        excluder = getattr(member, "get_vocabulary_exclusions", None)
        excluded.extend((excluder() if callable(excluder) else ()) or ())
    config = extend_vocabulary(None, exclusions=excluded)
    for table in declared:
        config = extend_vocabulary(table, config)
    return config


def domain_relation_vocabulary(domain: Any) -> RelationVocabulary:
    """The vocabulary a stack (or one domain object) reads: :func:`domain_relation_config`."""
    return RelationVocabulary.from_config(domain_relation_config(domain))


__all__ = [
    "EMPTY_RELATION_VOCABULARY",
    "FUNCTION_WORDS",
    "TWO_ENDPOINTS_REASONS",
    "UNIVERSAL_VOCABULARY",
    "RelationVocabulary",
    "canonical_edge_label",
    "domain_relation_config",
    "domain_relation_vocabulary",
    "extend_vocabulary",
    "is_function_phrase",
    "label_evidence_words",
    "relation_words",
    "states_relation",
    "text_words",
]
