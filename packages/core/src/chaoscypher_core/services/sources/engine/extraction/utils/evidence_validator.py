# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Evidence validation for extraction output.

Validates that extracted entities and relationships are supported by
sentence-level evidence from the source text. Filters out unsupported
triples to ensure extraction quality.

The ``evidence_validation_mode`` setting picks a rung of one ladder; each
rung accepts everything the rung below it accepts (strict ⊂ standard ⊂
narrative), by construction (:func:`_entity_named`):

- **strict**: an entity is named when its full name or an alias appears in
  the cited sentences as consecutive whole words; a relationship needs both
  endpoints named.
- **standard** (default): strict, or a naming word (>= 4 letters) of the
  name or an alias appears as a whole word, read through the domain's
  naming conventions: titles are no naming words, a family name matches in
  the surname form of the same gender (``name_identity.names_person``, the
  predicate the deduplication keeps two names apart by: "Rostóva" names
  Countess Rostóva, never Count Rostóv, and "Dólokhova" never names her son)
  and in the plural, which names any member ("the Rostóvs"); a text word
  also names by its part after an elision or a hyphen ("d'Enghien" names
  Enghien); "Ann" never names Anna, nor "prince" a princess; or a title
  written alone ("the prince", "Monsieur", never the title of a name such as
  "Prince Hippolyte") when the entity is the one candidate of the chunk
  holding it (the title rung: ``NameIndex.sole_title_holder``; "Monsieur"
  names neither Monsieur Pierre nor Monsieur Buonaparte when both are
  candidates). A relationship needs
  both endpoints named, or one endpoint plus a word or phrase of the source
  domain's ``relation_vocabulary`` stating the relation, as whole words
  (:mod:`~chaoscypher_core.services.sources.engine.extraction.utils.relation_vocabulary`;
  under the literary domain "sister" states ``sibling_of``, "your son"
  ``parent_of``, "engaged to" ``betrothed_to``). The words of the label
  itself are never evidence (until 2026-10-06 they were matched as
  substrings: "with" of ``interacts_with`` stood in almost every sentence,
  ``heir_of`` passed on "their", ``parent_of`` on "apparent"), nor is a
  function word; a label the vocabulary does not state needs both
  endpoints named. Until audit wave 6 the entity half matched name words
  as substrings too, titles included: "prince" inside "princess" named
  every prince of a chunk where a princess spoke.
- **narrative**: standard, or a significant word of one of the entity's
  ``descriptors`` (epithets such as "the abbé", "the little princess")
  appears as a whole word: the way pronoun- and epithet-heavy prose refers
  to people whose extracted names are their most complete form.
  Relationships follow the standard rule with the narrative naming: both
  endpoints, or one endpoint plus a relation word; never one endpoint alone.
- **relaxed**: a valid ``sent_ref`` in bounds is sufficient; no name matching.

Names and text are compared through the one fold of names
(``name_identity.fold``: accents, case, possessives and format characters;
"Pavlovna" matches "Pávlovna").
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from functools import cached_property
from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.services.quality.counters import (
    QualityCounter,
    increment_quality_counter,
)
from chaoscypher_core.services.sources.engine.deduplication import name_identity
from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
    DEFAULT_NAME_CONVENTIONS,
    EVIDENCE_REASON_KEY,
    JUSTIFICATIONS_KEY,
    UNEVIDENCED_KEY,
    NameConventions,
    NameIndex,
    edge_justifications,
    fold,
    name_record,
    name_words,
    with_pipeline_flag,
)
from chaoscypher_core.services.sources.engine.extraction.utils.relation_vocabulary import (
    RelationVocabulary,
)
from chaoscypher_core.services.sources.engine.extraction.utils.sentence_splitter import (
    parse_sent_ref,
    sentences_at,
)


if TYPE_CHECKING:
    from chaoscypher_core.services.sources.engine.extraction.utils.filtering_log import (
        FilteringLog,
    )
    from chaoscypher_core.services.sources.engine.extraction.utils.known_entities import (
        KnownEntities,
    )


logger = structlog.get_logger(__name__)

MIN_SIGNIFICANT_WORD_LENGTH = 4
"""The fewest letters of a naming word that counts as a name in standard mode (the filter's default).

The evidence filter's and the known-entities name guard's (``KnownEntities.guard``) one default."""

# The naming rungs of the ladder, weakest evidence last (see the module docstring).
_NAMING_LEVELS: tuple[str, ...] = ("strict", "standard", "narrative")


def _naming_level(mode: str) -> str:
    """The naming rung a validation ``mode`` uses; unknown modes fall back to strict."""
    return mode if mode in _NAMING_LEVELS else "strict"


def as_name_conventions(
    conventions: NameConventions | Mapping[str, Any] | None,
) -> NameConventions:
    """The naming conventions the validator reads: given, from a plan's JSON, or the universal ones."""
    if isinstance(conventions, NameConventions):
        return conventions
    if conventions:
        return NameConventions.from_dict(conventions)
    return DEFAULT_NAME_CONVENTIONS


ELIDED_PARTICLES = ("dell", "nell", "all", "d", "l")
"""The articles and particles a name elides before an apostrophe, written lowercase (``d'``, ``l'``,
``dell'``, ``nell'``, ``all'``).

A capital before the apostrophe opens a surname, not an elision: ``O'Neil``,
``D'Angelo`` and ``D'Artagnan`` are one name each (as ``McNeil`` and
``MacLeod`` are), so ``Tip O'Neil`` names no ``Neil`` and ``D'Angelo`` no
``Angelo``. Until audit wave 8 any one or two letters before an apostrophe
were an elision."""

_ELISION = re.compile("^(?:" + "|".join(ELIDED_PARTICLES) + r")['\u2019](?P<rest>[^\W_].*)$")
"""A token opening on an elided article or particle (:data:`ELIDED_PARTICLES`): ``d'Enghien``, ``l'abbé``."""

_OPENING_MARKS = '("\u201c\u2018\u00ab['
"""Opening marks stripped before the elision test (an opening quote before ``d'Enghien``)."""

_BREAK_AFTER = ',;:.!?)]}"\u201d\u2019\u00bb\u2014\u2013'
"""Marks that end a token's run of words: a title written before one of them stands alone."""


def _variants(token: str) -> list[str]:
    """The folded words a text token names by: its words, and the parts after an elision or a hyphen.

    ``d'Enghien`` is ``denghien`` and ``enghien``, ``d'Artagnan`` ``dartagnan``
    and ``artagnan``, ``Anne-Marie`` ``anne-marie`` and ``marie`` (audit wave
    7: the fold drops an apostrophe, so ``d'Enghien`` named no ``Enghien``);
    ``O'Neil`` and ``D'Angelo`` are one word each (:data:`ELIDED_PARTICLES`).
    """
    words = fold(token).split()
    match = _ELISION.match(token.lstrip(_OPENING_MARKS))
    if match is not None:
        words.extend(fold(match.group("rest")).split())
    for word in list(words):
        words.extend(part for part in word.split("-")[1:] if part)
    return words


def _capitalised(token: str) -> bool:
    """A token written as a name: its first letter, or the first after an elision, is a capital.

    ``Hippolyte``, ``d'Enghien`` and ``D'Angelo`` are; ``said`` and ``l'abbé`` are not.
    """
    bare = token.lstrip(_OPENING_MARKS)
    match = _ELISION.match(bare)
    for text in (bare, match.group("rest") if match is not None else ""):
        first = next((ch for ch in text if ch.isalpha()), "")
        if first.isupper():
            return True
    return False


def _plural_stem(word: str, conventions: NameConventions) -> str | None:
    """The family stem a folded plural stands for (``rostovs`` -> ``rostov``), or None."""
    if len(word) > MIN_SIGNIFICANT_WORD_LENGTH and word.endswith("s") and not word.endswith("ss"):
        return name_identity.identity_word(word[:-1], conventions)  # the Rostóvs, the Kurágins'
    return None


def _bare_titles(text: str, conventions: NameConventions) -> frozenset[str]:
    """The naming titles ``text`` writes alone, one spelling each: never one opening a name.

    ``the prince said``, ``Monsieur,`` and ``l'abbé`` are bare; ``Prince
    Hippolyte spoke`` and ``Vicomte de Mortemart`` are a titled name (the
    first word after the title, its articles and particles skipped, is
    capitalised), which names its own bearer and never another holder of the
    title (audit wave 7: "Prince Hippolyte spoke with Pierre" named Prince
    Vasíli through the title rung). A mark that closes a phrase (``,``,
    ``.``, a closing quote) after a word ends the run.
    """
    naming_titles = conventions.naming_titles
    if not naming_titles:
        return frozenset()
    run = _word_run(text, _variants)
    followed = _followed_by_a_name(run, title_words(conventions))
    return frozenset(
        title
        for (word, _, _), named in zip(run, followed, strict=True)
        if (title := conventions.title_class.get(word, word)) in naming_titles and not named
    )


def title_words(conventions: NameConventions) -> frozenset[str]:
    """The conventions' title words, folded: what ``name_identity.alias_as_name`` reads a name by.

    The evidence filter's cited-text read and the known-entities name guard share it.
    """
    return frozenset(fold(t) for t in conventions.titles)


_DASH_BREAK = re.compile("([\u2014\u2015])(?=\\S)")
"""A dash written between two words without spaces (``Vicomte—I``): it ends the first word's phrase."""


def _word_run(
    text: str, words_of: Callable[[str], list[str]] | None = None
) -> list[tuple[str, bool, bool]]:
    """``text`` as ``(folded word, written capitalised, a break after it)``, one per word in order.

    ``words_of`` reads a token's words (:func:`_variants` for the title rung,
    which reads the part after an elision too); None is the fold's own words,
    the cited text's :attr:`CitedText.words`.
    """
    run: list[tuple[str, bool, bool]] = []
    for token in _DASH_BREAK.sub("\\1 ", text).split():
        words = words_of(token) if words_of is not None else fold(token).split()
        capitalised = _capitalised(token)
        ends = token[-1:] in _BREAK_AFTER
        run.extend(
            (word, capitalised, ends and k == len(words) - 1) for k, word in enumerate(words)
        )
    return run


def _followed_by_a_name(
    run: Sequence[tuple[str, bool, bool]], titles: frozenset[str]
) -> list[bool]:
    """For each word of ``run``: the next word that is no title word or particle is capitalised.

    With no mark closing the phrase between (:data:`_BREAK_AFTER`). A title
    word so followed opens a name (``Prince Hippolyte``, ``Vicomte de
    Mortemart``, ``the Prince of Wales``): it is the named one's title, never
    a name of its own. The title rung reads only the others
    (:func:`_bare_titles`), and a name of title words alone is not written
    there (:meth:`CitedText.has_phrase`). The one title-position reading of a
    text (the name rule's of a name is ``name_identity.name_parts``).
    """
    out: list[bool] = []
    for i, (_word, _capitalised_word, ends) in enumerate(run):
        named, broken, j = False, ends, i + 1
        while not broken and j < len(run):
            following, capitalised, following_ends = run[j]
            if following not in titles:
                named = capitalised
                break
            broken, j = following_ends, j + 1
        out.append(named)
    return out


@dataclass(frozen=True)
class CitedText:
    """The cited sentences as the naming rungs read them: folded once, per chunk and relationship."""

    words: tuple[str, ...]
    """The folded words in order (the strict rung's phrases)."""
    surnames: frozenset[tuple[str, bool]]
    """Every word as a naming word (``name_identity.surname_key``: its stem and whether its form
    says a woman), the parts after an elision or a hyphen included (:func:`_variants`)."""
    families: frozenset[str]
    """The family stems the text's plurals stand for (``the Rostóvs``): a plural names any member."""
    titles: frozenset[str]
    """The naming titles the text writes alone, one spelling each (the title rung, :func:`_bare_titles`)."""
    opening: tuple[bool, ...] = ()
    """For each of :attr:`words`: a title word opening a name (:func:`_followed_by_a_name`)."""

    @classmethod
    def read(cls, text: str, conventions: NameConventions) -> CitedText:
        """Fold ``text`` once under ``conventions``."""
        variants = [v for token in text.split() for v in _variants(token)]
        run = _word_run(text)
        titles = title_words(conventions)
        followed = _followed_by_a_name(run, titles)
        return cls(
            words=tuple(word for word, _, _ in run),
            surnames=frozenset(name_identity.surname_key(v, conventions) for v in variants),
            families=frozenset(
                stem for v in variants if (stem := _plural_stem(v, conventions)) is not None
            ),
            titles=_bare_titles(text, conventions),
            opening=tuple(
                word in titles and named for (word, _, _), named in zip(run, followed, strict=True)
            ),
        )

    def names_word(self, name_word: str, conventions: NameConventions) -> bool:
        """An entity's folded naming word is written here: ``name_identity.names_person`` on the text.

        One family stem in one surname form ("Dólokhova" never names
        Dólokhov, "Drubetskóy" never Princess Drubetskáya), or the family's
        plural ("the Rostóvs" names Countess Rostóva).
        """
        key = name_identity.surname_key(name_word, conventions)
        return key in self.surnames or key[0] in self.families

    def has_phrase(self, name: str) -> bool:
        """``name`` occurs as consecutive whole words, and not as the title of another name.

        A name of title words alone (``Prince``, ``The Doctor``) is written
        where it stands alone (``Prince recorded at Paisley Park``), never
        where its words open another name (``Prince Harry visited Paisley
        Park``: :attr:`opening`). Until audit wave 8 the phrase rung read
        ``Prince Harry`` as naming Prince the musician.
        """
        phrase = tuple(fold(name).split())
        n = len(phrase)
        if not n:
            return False
        opening = self.opening or (False,) * len(self.words)
        return any(
            self.words[i : i + n] == phrase and not all(opening[i : i + n])
            for i in range(len(self.words) - n + 1)
        )


def alias_name(alias: str, conventions: NameConventions) -> str | None:
    """``alias`` read as the name it writes (``name_identity.alias_as_name``), or None: no name.

    None when the reading holds no naming word (``name_words``, titles left
    out): ``My wife`` reads as ``My`` and ``his wife`` as ``his``, a title or
    function word alone that :meth:`CitedText.has_phrase` finds in every
    sentence writing "my" or "his", and whose raw words would name by
    "wife" (spec D8, the join audit: an entry carrying "My wife" passed the
    name rule on "I told my sister."). The one reading of an alias as a
    name: the evidence filter's naming words (:func:`_word_names`), the
    known-entities list's given names and name rule (``KnownEntities``), and
    the join audit.
    """
    read = name_identity.alias_as_name(alias, title_words(conventions)).strip()
    return read if read and name_words(read, conventions.titles) else None


def _names(entity: Mapping[str, Any]) -> list[str]:
    """An entity's name and aliases, the non-empty strings among them: its phrases (the strict rung)."""
    out = [entity.get("name")]
    out.extend(entity.get("aliases") or [])
    return [str(n) for n in out if isinstance(n, str) and n.strip()]


def _word_names(entity: Mapping[str, Any], conventions: NameConventions) -> list[str]:
    """An entity's name and its aliases that read as a name (:func:`alias_name`): its naming words' source.

    Every alias stays phrase evidence (:func:`_names`: ``the count`` names the
    record that gives it, as before); only the word rung leaves out an alias
    whose reading holds no naming word, so ``wife`` of ``my wife`` never names.
    """
    name = entity.get("name")
    out = [name] if isinstance(name, str) and name.strip() else []
    out.extend(
        a
        for a in entity.get("aliases") or []
        if isinstance(a, str) and a.strip() and alias_name(a, conventions) is not None
    )
    return out


def _named_by_names(
    cited: CitedText, entity: Mapping[str, Any], conventions: NameConventions, min_len: int
) -> bool:
    """The strict and word rungs on one entity: a phrase of its names, or a naming word.

    A phrase of :func:`_names`, or :func:`named_by_name` over
    :func:`_word_names`: the one reading of both rungs, the filter's and the
    title rung's candidates'.
    """
    if any(cited.has_phrase(n) for n in _names(entity)):
        return True
    return named_by_name(cited, _word_names(entity, conventions), conventions, min_len)


class NamingContext:
    """The entities of one chunk, for the title rung: who else holds a title the text states.

    ``entities`` are the chunk's entities; the index over them is built once,
    on the first title the rung needs. The candidates of a cited text are
    the entities in question (the relationship's endpoints) and every entity
    of the chunk the text names by name (:meth:`named_by_name`): a title
    alone names the one of them that holds it.
    """

    def __init__(
        self,
        entities: Sequence[Mapping[str, Any]] | None,
        conventions: NameConventions,
        min_word_length: int = MIN_SIGNIFICANT_WORD_LENGTH,
    ) -> None:
        """Keep the entities; nothing is indexed until a title needs it."""
        self.entities = list(entities or [])
        self.conventions = conventions
        self.min_word_length = min_word_length
        self._named: dict[tuple[str, ...], frozenset[int]] = {}

    @cached_property
    def _index(self) -> NameIndex:
        """The name rule's index over the chunk's entities (pairwise evidence and title holders)."""
        return NameIndex(
            [name_record(dict(e)) for e in self.entities], conventions=self.conventions
        )

    def named_by_name(self, cited: CitedText) -> frozenset[int]:
        """The chunk's entities ``cited`` names by a name or a naming word (never by a title alone)."""
        if cited.words not in self._named:
            self._named[cited.words] = frozenset(
                k
                for k, e in enumerate(self.entities)
                if _named_by_names(cited, e, self.conventions, self.min_word_length)
            )
        return self._named[cited.words]

    def names_by_title(
        self, position: int | None, cited: CitedText, alongside: Sequence[int] = ()
    ) -> bool:
        """The entity at ``position`` is the one candidate holding a title ``cited`` states.

        Args:
            position: The entity's index among the chunk's entities.
            cited: The cited text, read once.
            alongside: The other entities in question (a relationship's other
                endpoint), candidates whether or not the text names them.
        """
        if position is None or not cited.titles or not 0 <= position < len(self.entities):
            return False
        index = self._index
        among = {*alongside, *self.named_by_name(cited)}
        return any(index.sole_title_holder(position, t, among=among) for t in sorted(cited.titles))


def naming_words(names: Sequence[str], conventions: NameConventions, min_len: int) -> set[str]:
    """The naming words of ``names`` (titles left out) of at least ``min_len`` letters, folded.

    The words the word rung reads (:func:`named_by_name`), and the words the
    known-entities name rule counts claimants of (``KnownEntities.named``).
    """
    return {w for n in names for w in name_words(n, conventions.titles) if len(w) >= min_len}


def named_by_name(
    cited: CitedText, names: Sequence[str], conventions: NameConventions, min_len: int
) -> bool:
    """A name of ``names`` as whole words, or one of their naming words (the strict and word rungs).

    A naming word is written when the text holds it in the same surname form
    (``name_identity.names_person``, read by :meth:`CitedText.names_word`).
    The name rung of the evidence filter (:meth:`NamingContext.named_by_name`)
    and of the known-entities name guard (``KnownEntities.guard``).
    """
    if any(cited.has_phrase(n) for n in names):
        return True
    return any(cited.names_word(w, conventions) for w in naming_words(names, conventions, min_len))


def _descriptor_words(descriptors: Sequence[str], min_len: int) -> set[str]:
    """Every word of the descriptors of at least ``min_len`` letters (folded; titles count here)."""
    return {w for d in descriptors for w in fold(d).split() if len(w) >= min_len}


def _entity_named(
    cited: CitedText,
    entity: Mapping[str, Any],
    level: str,
    min_word_length: int = MIN_SIGNIFICANT_WORD_LENGTH,
    *,
    naming: NamingContext | None = None,
    position: int | None = None,
    alongside: Sequence[int] = (),
) -> bool:
    """Whether ``entity`` is named in ``cited`` at naming rung ``level``.

    The one naming test of the ladder: each rung is the rung below it OR one
    more kind of evidence, so strict ⊂ standard ⊂ narrative holds by
    construction.

    - strict: the full name or an alias, as consecutive whole words.
    - standard: strict, or a naming word of the name or an alias as a whole
      word (family stems: surname forms and plurals), or a title the text
      states when the entity is the one candidate holding it (``naming``,
      the chunk's candidates, with the entity at ``position``).
    - narrative: standard, or a significant word of a descriptor, whole.

    Args:
        cited: The cited sentences, read once (:meth:`CitedText.read`).
        entity: Entity dict (``name``, optional ``aliases``/``descriptors``).
        level: ``"strict"``, ``"standard"`` or ``"narrative"``.
        min_word_length: Minimum length of a naming or descriptor word.
        naming: The chunk's entities for the title rung; None: no title rung.
        position: The entity's index among ``naming``'s entities.
        alongside: The other entities in question (a relationship's other
            endpoint): title-rung candidates even when the text does not name them.

    Returns:
        True when the rung finds the entity named in the text.

    """
    if level == "strict":
        return any(cited.has_phrase(n) for n in _names(entity))
    conventions = naming.conventions if naming is not None else DEFAULT_NAME_CONVENTIONS
    if _named_by_names(cited, entity, conventions, min_word_length):
        return True
    if naming is not None and naming.names_by_title(position, cited, alongside):
        return True
    if level == "standard":
        return False
    descriptors = [d for d in entity.get("descriptors") or [] if isinstance(d, str)]
    return bool(_descriptor_words(descriptors, min_word_length) & set(cited.words))


def entity_evidence(
    entity: Mapping[str, Any],
    sentences: list[str],
    mode: str = "strict",
    min_significant_word_length: int = MIN_SIGNIFICANT_WORD_LENGTH,
    *,
    conventions: NameConventions | Mapping[str, Any] | None = None,
    candidates: Sequence[Mapping[str, Any]] | None = None,
    _naming: NamingContext | None = None,
) -> EntityEvidence:
    """Whether an entity has sentence-level evidence, and why not when it has none.

    A valid, in-bounds ``sent_ref`` is required in every mode; then the
    entity must be named in the cited sentences at the mode's naming rung
    (:func:`_entity_named`): strict, standard or narrative. ``relaxed``
    needs no name. Unknown modes validate as strict. The rules are the ones
    the filter always read; the verdict adds the reason (keep, flag, never
    delete, 2026-10-08: an entity without evidence that carries a
    description is kept flagged ``unevidenced`` with it,
    :func:`sort_entities_by_evidence`).

    Args:
        entity: Entity dict with optional ``sent_ref`` field.
        sentences: Full sentence list from the source chunk.
        mode: Validation mode (``strict``, ``standard``, ``narrative`` or
            ``relaxed``).
        min_significant_word_length: Minimum character length for a word to
            count as significant in standard mode.
        conventions: The source domain's naming conventions (or their JSON,
            the plan's ``name_conventions``); None for the universal ones.
        candidates: The chunk's entities, ``entity`` among them, for the
            title rung (a bare title names the one candidate holding it);
            None: no title rung.
        _naming: The candidates already indexed (the filter's, built once per chunk).

    Returns:
        The verdict: evidenced, or the failure and its one-sentence reason.

    """
    ref = CitedSentences.of(entity.get("sent_ref"), sentences)
    if ref.failure is not None:
        return EntityEvidence(ref.failure, ref.reason)

    if mode == "relaxed":
        return _ENTITY_EVIDENCED

    naming = (
        _naming
        if _naming is not None
        else NamingContext(
            candidates, as_name_conventions(conventions), min_significant_word_length
        )
    )
    position = next((k for k, c in enumerate(naming.entities) if c is entity), None)
    if _entity_named(
        ref.read(naming.conventions),
        entity,
        _naming_level(mode),
        min_word_length=min_significant_word_length,
        naming=naming,
        position=position,
    ):
        return _ENTITY_EVIDENCED
    name = entity.get("name") or entity.get("label") or "the entity"
    return EntityEvidence(EvidenceFailure.NOT_NAMED, f'"{name}" is not named in {ref.sent_ref}')


def validate_entity_evidence(
    entity: dict[str, Any],
    sentences: list[str],
    mode: str = "strict",
    min_significant_word_length: int = MIN_SIGNIFICANT_WORD_LENGTH,
    *,
    conventions: NameConventions | Mapping[str, Any] | None = None,
    candidates: Sequence[Mapping[str, Any]] | None = None,
    _naming: NamingContext | None = None,
) -> bool:
    """Whether an entity has sentence-level evidence (:func:`entity_evidence`'s verdict)."""
    return entity_evidence(
        entity,
        sentences,
        mode,
        min_significant_word_length,
        conventions=conventions,
        candidates=candidates,
        _naming=_naming,
    ).evidenced


def _try_best_effort_parse(raw_sent_ref: str) -> str | None:
    r"""Attempt best-effort extraction of sentence references from malformed sent_ref.

    Handles cases where ``parse_sent_ref`` returns None but the raw value
    contains recognizable ``S\d+`` tokens (e.g. ``"S3 to S5"``, ``"S2;S4"``).

    Args:
        raw_sent_ref: The raw sent_ref string that failed strict parsing.

    Returns:
        A cleaned sent_ref string that ``parse_sent_ref`` can handle,
        or None if no references could be extracted.

    """
    import re as _re

    nums = [int(x) for x in _re.findall(r"S(\d+)", raw_sent_ref)]
    if not nums or not all(n >= 1 for n in nums):
        return None
    if len(nums) == 1:
        return f"S{nums[0]}"
    return f"S{min(nums)}-S{max(nums)}"


class EvidenceFailure(StrEnum):
    """Why a cited sentence was not read as evidence (:func:`relationship_evidence`, :func:`entity_evidence`).

    Every failure but :attr:`INVALID_ENDPOINT` leaves a relationship between
    two entities of its chunk: it is kept, flagged ``unevidenced``, with the
    explanation as its ``evidence_reason`` (keep, flag, never delete,
    2026-10-07). An invalid endpoint names no entity, so the relationship
    carries no fact anyone can read and is dropped. An entity's verdict is
    one of the three sentence-reference failures or :attr:`NOT_NAMED`; it is
    kept flagged the same way when it carries a description (2026-10-08).
    """

    NO_SENT_REF = "no_sent_ref"
    """It cites no sentence."""
    UNREADABLE_SENT_REF = "unreadable_sent_ref"
    """Its sentence reference names no sentence, even read best-effort."""
    SENT_REF_OUT_OF_RANGE = "sent_ref_out_of_range"
    """It cites sentences outside its chunk."""
    INVALID_ENDPOINT = "invalid_endpoint"
    """An endpoint is no entity of its chunk: dropped."""
    ENDPOINTS_NOT_NAMED = "endpoints_not_named"
    """Neither endpoint is named in the cited sentences."""
    ENDPOINT_NOT_NAMED = "endpoint_not_named"
    """Strict mode: one endpoint named, and strict needs both."""
    RELATION_NOT_STATED = "relation_not_stated"
    """One endpoint named, and no word of the label's relation vocabulary in the sentence."""
    LABEL_NEEDS_BOTH_ENDPOINTS = "label_needs_both_endpoints"
    """One endpoint named, and the vocabulary has no words for the label (``two_endpoints_only``)."""
    NOT_NAMED = "not_named"
    """An entity's cited sentences do not name it at the mode's naming rung."""
    KNOWN_ENDPOINT_NOT_NAMED = "known_endpoint_not_named"
    """An endpoint whose record name is a known entity of the source is not named by the
    known-entities name rules (``KnownEntities.identified``), and the remaining rungs do not state
    the relationship (spec 2026-10-11 D8: a title or the record's own reference aliases never
    name a known entity); or the name guard kept the endpoint's known name but flagged it
    (``KnownEntities.kept_unevidenced``), which no rung names."""


@dataclass(frozen=True)
class CitedSentences:
    """A record's ``sent_ref`` read against its chunk: the sentences it cites, or why it cites none.

    The one reading of a ``sent_ref`` every evidence check makes (an entity's,
    a relationship's, the known-entities name guard's): a valid reference
    whose sentences are in the chunk, else the sentence-reference failure
    (:class:`EvidenceFailure`) and its one-sentence :attr:`reason`.
    """

    sent_ref: str
    """The reference as read (after the best-effort repair, when one was asked for)."""
    sentences: tuple[str, ...] = ()
    """The cited sentences, in order; empty on a failure."""
    failure: EvidenceFailure | None = None
    """None when the reference names sentences of the chunk."""
    chunk_size: int = 0
    """How many sentences the chunk has (the out-of-range reason says so)."""
    preceding: str = ""
    """The one sentence before the first cited one ("" when the first cited is the chunk's first,
    or on a failure): the look-back the known-entities name rule reads (spec D8: "Prince Orlan
    entered. The prince sat down." cites the second and is named by the first)."""

    @classmethod
    def of(cls, raw: Any, sentences: Sequence[str], *, repair: bool = False) -> CitedSentences:
        """Read ``raw`` (a record's ``sent_ref``) against the chunk's ``sentences``.

        ``repair`` reads an unparsable reference best-effort (``"S3 to S5"`` is
        ``S3-S5``), as the relationship check does; the entity check does not.
        """
        size = len(sentences)
        if not raw:
            return cls("", failure=EvidenceFailure.NO_SENT_REF, chunk_size=size)
        sent_ref = str(raw)
        indices = parse_sent_ref(sent_ref)
        if not indices:
            repaired = _try_best_effort_parse(sent_ref) if repair else None
            indices = parse_sent_ref(repaired) if repaired else None
            if not repaired or not indices:
                return cls(sent_ref, failure=EvidenceFailure.UNREADABLE_SENT_REF, chunk_size=size)
            sent_ref = repaired
        cited = sentences_at(indices, sentences)
        if not cited:
            return cls(sent_ref, failure=EvidenceFailure.SENT_REF_OUT_OF_RANGE, chunk_size=size)
        preceding = sentences[indices[0] - 2] if indices[0] > 1 else ""
        return cls(sent_ref, tuple(cited), chunk_size=size, preceding=preceding)

    @property
    def reason(self) -> str | None:
        """Why the reference cites nothing (the verdict's ``evidence_reason``), None when it does."""
        if self.failure is EvidenceFailure.NO_SENT_REF:
            return "it cites no sentence"
        if self.failure is EvidenceFailure.UNREADABLE_SENT_REF:
            return f"its sentence reference {self.sent_ref!r} names no sentence"
        if self.failure is EvidenceFailure.SENT_REF_OUT_OF_RANGE:
            return f"it cites {self.sent_ref}, outside its chunk's {self.chunk_size} sentences"
        return None

    @property
    def text(self) -> str:
        """The cited sentences as one text."""
        return " ".join(self.sentences)

    def read(self, conventions: NameConventions, *, look_back: bool = False) -> CitedText:
        """The cited sentences as the naming rungs read them, folded once.

        ``look_back`` reads :attr:`preceding` before them: the one reading of
        the look-back, the known-entities name rule's (``KnownEntities.named``)
        for the name guard and the relationship filter alike.
        """
        text = f"{self.preceding} {self.text}" if look_back and self.preceding else self.text
        return CitedText.read(text, conventions)


@dataclass(frozen=True)
class RelationshipEvidence:
    """The validator's verdict on one relationship: the one decision the filter and the recipe read."""

    failure: EvidenceFailure | None = None
    """None when the cited sentences state the relationship."""
    reason: str | None = None
    """One sentence saying why (the edge's ``evidence_reason``), None when evidenced."""

    @property
    def evidenced(self) -> bool:
        """The cited sentences state the relationship."""
        return self.failure is None

    @property
    def dropped(self) -> bool:
        """The relationship names no entity at one end and carries nothing: the filter drops it."""
        return self.failure is EvidenceFailure.INVALID_ENDPOINT


_EVIDENCED = RelationshipEvidence()
_INVALID_ENDPOINT = RelationshipEvidence(
    EvidenceFailure.INVALID_ENDPOINT, "an endpoint is no entity of its chunk"
)


@dataclass(frozen=True)
class EntityEvidence:
    """The validator's verdict on one entity: the one decision the entity filter and the recipe read."""

    failure: EvidenceFailure | None = None
    """None when the cited sentences name the entity."""
    reason: str | None = None
    """One sentence saying why (the entity's ``evidence_reason``), None when evidenced."""

    @property
    def evidenced(self) -> bool:
        """The cited sentences name the entity."""
        return self.failure is None


_ENTITY_EVIDENCED = EntityEvidence()


def _entity_label(entities: Sequence[Mapping[str, Any]], idx: int) -> str:
    """An endpoint as a reason names it: its name, quoted."""
    return f'"{entities[idx].get("name") or f"entity {idx}"}"'


def relationship_evidence(
    rel: Mapping[str, Any],
    entities: list[dict[str, Any]],
    sentences: list[str],
    mode: str = "strict",
    min_significant_word_length: int = MIN_SIGNIFICANT_WORD_LENGTH,
    *,
    vocabulary: RelationVocabulary | Mapping[str, Any] | None = None,
    conventions: NameConventions | Mapping[str, Any] | None = None,
    known: KnownEntities | None = None,
    _naming: NamingContext | None = None,
) -> RelationshipEvidence:
    r"""Whether a relationship has sentence-level evidence, and why not when it has none.

    Each endpoint is looked for at the mode's naming rung
    (:func:`_entity_named`):

    - **strict**: both endpoints named.
    - **standard** and **narrative**: both endpoints named, or one endpoint
      named plus a word or phrase of the label's relation in ``vocabulary``
      as whole words (the other end referred to by pronoun or paraphrase;
      ``RelationVocabulary.states_relation``: under the literary domain "her
      elder sister" for ``sibling_of``, "a father" for ``parent_of``). The
      label's own words are never evidence; a label ``vocabulary`` does not
      state needs both endpoints (``two_endpoints_only``). One endpoint alone
      is never enough.
    - **relaxed**: valid sent_ref in bounds is sufficient.

    The ladder is monotone: whatever strict accepts, standard accepts, and
    whatever standard accepts, narrative accepts.

    When the primary ``parse_sent_ref`` fails but the raw value contains
    at least one ``S\d+`` token, a best-effort fallback extracts a usable
    range before giving up.

    **Known endpoints** (``known``, spec 2026-10-11 D8): an endpoint whose
    record name is a known entity of the source (``KnownEntities.resolves``)
    is named only by the name guard's two rules over the edge's cited
    sentences (``KnownEntities.identified``): rule 1 with the one sentence
    before them (``KnownEntities.named``: its name when that is the entry's
    own, the entry's name or a multi-word name-alias of the entry, by a
    naming word no two known entries claim; at strict, as a phrase only), or
    rule 2, a distinctive alias this entry alone gave written whole (``the
    King of Valdria``). Its own reference aliases, its descriptors and the
    title rung never name it.
    The edge then stands on the remaining rungs (the other endpoint named
    and the relation stated); left unevidenced, its failure is
    :attr:`EvidenceFailure.KNOWN_ENDPOINT_NOT_NAMED`. A known endpoint whose
    name the name guard KEPT and flagged (``KnownEntities.kept_unevidenced``:
    its sentences write no phrase to demote it to, or several) is never
    named, by no rule and no rung: the edge fails at once, whatever the other
    endpoint and the relation words (final review 2 of 2026-10-12, I1: "the
    count, her father" kept the count's known name and hung the daughter on
    him through the one-endpoint rung). A record the guard DEMOTED is read by
    its new name, as any record written under that phrase: a known alias
    through the two rules ("the King of Valdria", rule 2), a reference or no
    known entry through today's rungs, the title rung included.

    An endpoint that is no entity of ``entities`` (not an integer, out of
    bounds) is :attr:`EvidenceFailure.INVALID_ENDPOINT`, which the filter
    drops (:attr:`RelationshipEvidence.dropped`), in every mode but relaxed
    with a readable sentence, which reads no endpoint (as before; the
    structural validation ahead of the filter drops such a line first).

    Args:
        rel: Relationship dict with ``source``, ``target``, ``sent_ref``.
        entities: Full entity list (for name lookup by index).
        sentences: Full sentence list from the source chunk.
        mode: Validation mode (``strict``, ``standard``, ``narrative`` or
            ``relaxed``).
        min_significant_word_length: Minimum character length for a word to
            count as significant in standard mode.
        vocabulary: The source domain's relation vocabulary (a
            :class:`RelationVocabulary` or its ``relation_vocabulary`` JSON);
            None for no vocabulary (every relationship then needs both
            endpoints named).
        conventions: The source domain's naming conventions (or the plan's
            ``name_conventions`` JSON); None for the universal ones.
            ``entities`` are the candidates of the title rung.
        known: The source's known entities so far (the list the name guard
            read); None: no endpoint is read as known.
        _naming: The candidates already indexed (the filter's, built once per chunk).

    Returns:
        The verdict: evidenced, or the failure and its one-sentence reason.

    """
    source_idx = rel.get("source")
    target_idx = rel.get("target")
    endpoints_valid = all(
        isinstance(idx, int) and 0 <= idx < len(entities) for idx in (source_idx, target_idx)
    )
    # Strict parse first, then the best-effort fallback (``repair``).
    ref = CitedSentences.of(rel.get("sent_ref"), sentences, repair=True)
    if ref.failure is not None:
        if not endpoints_valid:
            return _INVALID_ENDPOINT
        return RelationshipEvidence(ref.failure, ref.reason)

    if mode == "relaxed":
        return _EVIDENCED

    if not endpoints_valid or not isinstance(source_idx, int) or not isinstance(target_idx, int):
        return _INVALID_ENDPOINT

    level = _naming_level(mode)
    naming = (
        _naming
        if _naming is not None
        else NamingContext(entities, as_name_conventions(conventions), min_significant_word_length)
    )
    cited = ref.read(naming.conventions)  # folded once for both endpoints
    keys = {
        idx: known.resolves(entities[idx].get("name"), entities[idx].get("type"))
        if known is not None
        else None
        for idx in (source_idx, target_idx)
    }
    # A known name the name guard kept but flagged is identified nowhere: no rung names it.
    unnamed_known = [
        idx
        for idx in (source_idx, target_idx)
        if keys[idx] is not None and known is not None and known.kept_unevidenced(entities[idx])
    ]
    if not unnamed_known:
        found = [
            _entity_named(
                cited,
                entities[idx],
                level,
                min_word_length=min_significant_word_length,
                naming=naming,
                position=idx,
                alongside=(other,),
            )
            if (key := keys[idx]) is None or known is None
            else known.identified(
                key,
                entities[idx],
                ref,
                min_word_length=min_significant_word_length,
                phrases_only=level == "strict",
            )
            for idx, other in ((source_idx, target_idx), (target_idx, source_idx))
        ]
        verdict = _named_verdict(
            rel, entities, ref, (source_idx, target_idx), found, level, vocabulary
        )
        unnamed_known = [
            idx
            for idx, named in zip((source_idx, target_idx), found, strict=True)
            if not named and keys[idx] is not None
        ]
        if verdict.evidenced or not unnamed_known:
            return verdict
    return _known_not_named(entities, unnamed_known, ref)


def _known_not_named(
    entities: Sequence[Mapping[str, Any]], unnamed: Sequence[int], ref: CitedSentences
) -> RelationshipEvidence:
    """The :attr:`EvidenceFailure.KNOWN_ENDPOINT_NOT_NAMED` verdict on the known endpoints ``unnamed``.

    Its reason is a sentence for the edge's ``evidence_reason`` (what the UI
    shows), as every other failure's is; the code is the verdict's ``failure``.
    """
    labels = [_entity_label(entities, idx) for idx in dict.fromkeys(unnamed)]
    who = (
        f"{labels[0]} is not named"
        if len(labels) == 1
        else f"neither {' nor '.join(labels)} is named"
    )
    return RelationshipEvidence(
        EvidenceFailure.KNOWN_ENDPOINT_NOT_NAMED,
        f"{who} in {ref.sent_ref} (a known entity needs its name in the sentence)",
    )


def _named_verdict(
    rel: Mapping[str, Any],
    entities: Sequence[Mapping[str, Any]],
    ref: CitedSentences,
    endpoints: tuple[int, int],
    found: Sequence[bool],
    level: str,
    vocabulary: RelationVocabulary | Mapping[str, Any] | None,
) -> RelationshipEvidence:
    """The verdict of :func:`relationship_evidence` once each endpoint is found named or not.

    Both named: evidenced; neither: not; one at strict: not; one at standard
    or narrative: evidenced when the domain's relation vocabulary states the
    label in the cited sentences (never the label's own words).
    """
    source_idx, target_idx = endpoints
    if all(found):
        return _EVIDENCED
    if not any(found):
        return RelationshipEvidence(
            EvidenceFailure.ENDPOINTS_NOT_NAMED,
            f"neither {_entity_label(entities, source_idx)} nor "
            f"{_entity_label(entities, target_idx)} is named in {ref.sent_ref}",
        )
    named, missing = (source_idx, target_idx) if found[0] else (target_idx, source_idx)
    one_named = (
        f"{_entity_label(entities, named)} is named in {ref.sent_ref} but "
        f"{_entity_label(entities, missing)} is not"
    )
    if level == "strict":
        return RelationshipEvidence(
            EvidenceFailure.ENDPOINT_NOT_NAMED, f"{one_named}, and strict mode needs both"
        )

    # One endpoint named: the relation must be stated too, by one of its words
    # or phrases in the domain's relation vocabulary (never the label's own words).
    raw_type = str(rel.get("type") or "")
    relation_vocabulary = RelationVocabulary.from_config(vocabulary)
    if relation_vocabulary.states_relation(ref.text, raw_type):
        return _EVIDENCED
    if not relation_vocabulary.relation_words(raw_type):
        return RelationshipEvidence(
            EvidenceFailure.LABEL_NEEDS_BOTH_ENDPOINTS,
            f"{one_named}, and {raw_type or 'its label'} needs both endpoints named "
            "(the domain's relation vocabulary has no words for it)",
        )
    return RelationshipEvidence(
        EvidenceFailure.RELATION_NOT_STATED,
        f"{one_named}, and no word of the domain's relation vocabulary for {raw_type} stands there",
    )


def validate_relationship_evidence(
    rel: dict[str, Any],
    entities: list[dict[str, Any]],
    sentences: list[str],
    mode: str = "strict",
    min_significant_word_length: int = MIN_SIGNIFICANT_WORD_LENGTH,
    *,
    vocabulary: RelationVocabulary | Mapping[str, Any] | None = None,
    conventions: NameConventions | Mapping[str, Any] | None = None,
    _naming: NamingContext | None = None,
) -> bool:
    """Whether a relationship has sentence-level evidence (:func:`relationship_evidence`'s verdict)."""
    return relationship_evidence(
        rel,
        entities,
        sentences,
        mode,
        min_significant_word_length,
        vocabulary=vocabulary,
        conventions=conventions,
        _naming=_naming,
    ).evidenced


@dataclass(frozen=True)
class EntityEvidenceSorting:
    """One chunk's entities after the evidence filter (:func:`sort_entities_by_evidence`)."""

    entities: list[dict[str, Any]]
    """Every entity kept, in input order: the evidenced ones as written, and the unevidenced ones
    that carry a description flagged (``properties.unevidenced``, ``properties.evidence_reason``)."""
    index_mapping: dict[int, int | None]
    """Input index -> kept index, or None for a dropped entity."""
    flagged: list[tuple[dict[str, Any], EntityEvidence]]
    """The kept unevidenced entities (as kept, flagged) and their verdicts."""
    dropped: list[tuple[dict[str, Any], EntityEvidence]]
    """The unevidenced entities that carry nothing, with their verdicts."""


def flag_unevidenced_entity(entity: Mapping[str, Any], verdict: EntityEvidence) -> dict[str, Any]:
    """``entity`` kept without evidence: a copy flagged ``unevidenced``, its reason beside it.

    The one writer of the entity flag (``name_identity.UNEVIDENCED_KEY``, the
    key the edges carry, with ``EVIDENCE_REASON_KEY``), through
    ``name_identity.with_pipeline_flag`` like every per-chunk node flag.
    """
    stored = entity.get("properties")
    props = with_pipeline_flag(
        stored if isinstance(stored, Mapping) else None,
        UNEVIDENCED_KEY,
        on=True,
        **{EVIDENCE_REASON_KEY: verdict.reason or str(verdict.failure)},
    )
    return {**entity, "properties": props}


def sort_entities_by_evidence(
    entities: list[dict[str, Any]],
    sentences: list[str],
    mode: str = "strict",
    min_significant_word_length: int = MIN_SIGNIFICANT_WORD_LENGTH,
    *,
    conventions: NameConventions | Mapping[str, Any] | None = None,
) -> EntityEvidenceSorting:
    """Keep, flag, never delete: one chunk's entities by the validator's verdict.

    The one decision of the entity evidence filter (2026-10-08; until then
    every entity whose cited sentence did not name it was dropped,
    description and all: 1 on the B0 literary pack, 0 on the generic one):

    - evidenced (:func:`entity_evidence`): kept as written;
    - unevidenced and carrying evidence of its own
      (``entity_cleaner.carries_evidence``: a description; a mention carries
      no evidence sentence before the aggregation stamps one): kept, flagged
      (:func:`flag_unevidenced_entity`: ``properties.unevidenced`` and the
      reason in ``properties.evidence_reason``), so pass 2 lists it and its
      relationships can attach to it; it ranks doubtful
      (``name_identity.NodeStanding.doubtful``);
    - unevidenced and carrying nothing: dropped.

    Args:
        entities: The chunk's entities (the title rung's candidates).
        sentences: The chunk's sentences.
        mode: Validation mode (:func:`entity_evidence`).
        min_significant_word_length: Minimum length of a significant word.
        conventions: The source domain's naming conventions.

    Returns:
        The kept entities in input order, the index mapping, and the flagged and dropped ones.

    """
    from chaoscypher_core.services.sources.engine.extraction.utils.entity_cleaner import (
        carries_evidence,
    )

    naming = NamingContext(entities, as_name_conventions(conventions), min_significant_word_length)
    kept: list[dict[str, Any]] = []
    index_mapping: dict[int, int | None] = {}
    flagged: list[tuple[dict[str, Any], EntityEvidence]] = []
    dropped: list[tuple[dict[str, Any], EntityEvidence]] = []
    for old_idx, entity in enumerate(entities):
        verdict = entity_evidence(
            entity,
            sentences,
            mode=mode,
            min_significant_word_length=min_significant_word_length,
            _naming=naming,
        )
        if verdict.evidenced:
            index_mapping[old_idx] = len(kept)
            kept.append(entity)
        elif carries_evidence(entity):
            index_mapping[old_idx] = len(kept)
            kept.append(flag_unevidenced_entity(entity, verdict))
            flagged.append((kept[-1], verdict))
        else:
            index_mapping[old_idx] = None
            dropped.append((entity, verdict))
    return EntityEvidenceSorting(kept, index_mapping, flagged, dropped)


ENTITY_UNEVIDENCED_STAGE_COUNT = "unevidenced_kept"
"""The ``entity_evidence_filter`` stage's count of the entities kept flagged ``unevidenced``."""


async def _increment_counter(
    adapter: Any | None,
    source_id: str | None,
    database_name: str | None,
    counter: QualityCounter,
    n: int,
) -> None:
    """One ``n=count`` write for *counter* instead of one per item.

    The increment is a blocking UPDATE+COMMIT with no suspension point, so
    per-item calls stalled the event loop for one commit per entity or
    relationship. A zero count writes nothing; a missing *adapter* or
    *source_id* skips the counter (best-effort, see the callers).
    """
    if n <= 0 or adapter is None or source_id is None:
        return
    await increment_quality_counter(
        adapter=adapter,
        source_id=source_id,
        database_name=database_name or "default",
        counter=counter,
        n=n,
    )


async def filter_entities_by_evidence(
    entities: list[dict[str, Any]],
    sentences: list[str],
    mode: str = "strict",
    filtering_log: FilteringLog | None = None,
    min_significant_word_length: int = MIN_SIGNIFICANT_WORD_LENGTH,
    *,
    adapter: Any | None = None,
    source_id: str | None = None,
    database_name: str | None = None,
    conventions: NameConventions | Mapping[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[int, int | None], dict[str, int]]:
    """Apply the evidence filter to one chunk's entities (:func:`sort_entities_by_evidence`).

    Keep, flag, never delete (2026-10-08): an entity whose cited sentence
    does not name it but that carries a description is kept flagged
    ``unevidenced`` and increments ``QualityCounter.ENTITIES_UNEVIDENCED_KEPT``;
    only one carrying nothing is dropped and increments
    ``QualityCounter.EVIDENCE_ENTITIES_DROPPED`` (until 2026-10-08 that
    counter counted every entity without evidence, all of them dropped). Each
    counter gets one write (``n`` = its count), not one per item
    (:func:`_increment_counter`). The increments are best-effort: a missing
    *adapter* or *source_id* skips them so callers without a database handle
    (tests, benchmarks) still work.

    Args:
        entities: Entity list to filter.
        sentences: Source chunk sentences.
        mode: Validation mode (``strict``, ``standard``, ``narrative`` or ``relaxed``).
        filtering_log: Optional log collector for pipeline diagnostics.
        min_significant_word_length: Minimum character length for a word to
            count as significant in standard mode.
        adapter: Storage adapter for quality counter increments (optional).
        source_id: Source row identifier for quality counter increments
            (optional).
        database_name: Database name for quality counter increments
            (optional, defaults to ``"default"`` when adapter is provided).
        conventions: The source domain's naming conventions
            (:func:`entity_evidence`); ``entities`` are the title rung's
            candidates.

    Returns:
        Tuple of (kept entities, index_mapping, stats): ``entities_checked``,
        ``entities_dropped`` (carrying nothing), ``entities_unevidenced``
        (kept flagged).

    """
    from chaoscypher_core.services.sources.engine.extraction.utils.filtering_log import (
        FilteredItem,
    )

    sorting = sort_entities_by_evidence(
        entities,
        sentences,
        mode=mode,
        min_significant_word_length=min_significant_word_length,
        conventions=conventions,
    )
    items: list[FilteredItem] = []
    outcomes = [(e, v, False) for e, v in sorting.dropped] + [
        (e, v, True) for e, v in sorting.flagged
    ]
    for entity, verdict, kept in outcomes:
        logger.debug(
            "entity_unevidenced_kept" if kept else "entity_evidence_dropped",
            entity_name=entity.get("name"),
            sent_ref=entity.get("sent_ref"),
            failure=str(verdict.failure),
            mode=mode,
        )
        if filtering_log is not None:
            items.append(
                FilteredItem(
                    item_type="entity",
                    name=entity.get("name", "?"),
                    entity_type=entity.get("type", "?"),
                    reason=(
                        f"{verdict.reason}: kept flagged (carries a description)"
                        if kept
                        else (
                            f"No valid sentence reference "
                            f"(sent_ref={entity.get('sent_ref')}, mode={mode})"
                        )
                    ),
                    details={
                        "sent_ref": entity.get("sent_ref"),
                        "mode": mode,
                        "failure": str(verdict.failure),
                        **({"kept": True} if kept else {}),
                    },
                )
            )

    await _increment_counter(
        adapter,
        source_id,
        database_name,
        QualityCounter.EVIDENCE_ENTITIES_DROPPED,
        len(sorting.dropped),
    )
    await _increment_counter(
        adapter,
        source_id,
        database_name,
        QualityCounter.ENTITIES_UNEVIDENCED_KEPT,
        len(sorting.flagged),
    )

    stats = {
        "entities_checked": len(entities),
        "entities_dropped": len(sorting.dropped),
        "entities_unevidenced": len(sorting.flagged),
    }

    if sorting.dropped or sorting.flagged:
        logger.info(
            "evidence_filtering_entities",
            checked=len(entities),
            kept=len(sorting.entities),
            unevidenced=len(sorting.flagged),
            dropped=len(sorting.dropped),
            mode=mode,
        )

    if filtering_log is not None:
        filtering_log.add_stage(
            "entity_evidence_filter",
            input_count=len(entities),
            removed_count=len(sorting.dropped),
            items=items,
            counts={ENTITY_UNEVIDENCED_STAGE_COUNT: len(sorting.flagged)},
        )

    return sorting.entities, sorting.index_mapping, stats


@dataclass(frozen=True)
class EvidenceSorting:
    """One chunk's relationships after the evidence filter (:func:`sort_relationships_by_evidence`)."""

    relationships: list[dict[str, Any]]
    """Every relationship kept, in input order: the evidenced ones, and the unevidenced ones
    flagged (``properties.unevidenced``, ``properties.evidence_reason``)."""
    flagged: list[tuple[dict[str, Any], RelationshipEvidence]]
    """The kept unevidenced relationships (as kept, flagged) and their verdicts."""
    folded: list[tuple[dict[str, Any], RelationshipEvidence]]
    """Unevidenced copies of an evidenced relationship of the chunk, folded into it (their
    justification kept in its ``properties.justifications``), with their verdicts."""
    dropped: list[tuple[dict[str, Any], RelationshipEvidence]]
    """The relationships with an endpoint that is no entity of the chunk, with their verdicts."""


def _fact_key(rel: Mapping[str, Any]) -> tuple[Any, Any, str]:
    """A relationship's ``(source, target, type)`` as the within-chunk duplicate collapse keys it."""
    return (rel.get("source"), rel.get("target"), str(rel.get("type") or "").strip().lower())


def flag_unevidenced(rel: Mapping[str, Any], verdict: RelationshipEvidence) -> dict[str, Any]:
    """``rel`` kept with the unevidenced flag and its reason in ``properties`` (a copy; the input is untouched)."""
    properties = dict(rel.get("properties") or {})
    properties[UNEVIDENCED_KEY] = True
    properties[EVIDENCE_REASON_KEY] = verdict.reason or str(verdict.failure)
    return {**rel, "properties": properties}


def _fold_justification(into: dict[str, Any], rel: Mapping[str, Any]) -> dict[str, Any]:
    """``into`` with ``rel``'s justification added to its ``properties.justifications``.

    A copy when a text is added; ``into`` itself when ``rel`` says nothing new.
    """
    properties = dict(into.get("properties") or {})
    own = {**properties, "justification": into.get("justification")}
    texts = list(dict.fromkeys([*edge_justifications(own), *edge_justifications(rel)]))
    if len(texts) <= len(edge_justifications(own)):
        return into
    properties[JUSTIFICATIONS_KEY] = texts
    return {**into, "properties": properties}


def sort_relationships_by_evidence(
    relationships: list[dict[str, Any]],
    entities: list[dict[str, Any]],
    sentences: list[str],
    mode: str = "strict",
    min_significant_word_length: int = MIN_SIGNIFICANT_WORD_LENGTH,
    *,
    vocabulary: RelationVocabulary | Mapping[str, Any] | None = None,
    conventions: NameConventions | Mapping[str, Any] | None = None,
    known: KnownEntities | None = None,
) -> EvidenceSorting:
    """Keep, flag, never delete: one chunk's relationships by the validator's verdict.

    The one decision of the evidence filter (2026-10-07; until then every
    relationship without evidence was dropped, justification and all, 162 on
    the B0 literary pack and 105 on the generic one):

    - evidenced (:func:`relationship_evidence`): kept as written;
    - unevidenced between two entities of the chunk: kept, flagged
      (:func:`flag_unevidenced`: ``properties.unevidenced`` and the reason in
      ``properties.evidence_reason``), committed like any edge; the chat
      context leaves it out unless ``graphrag.include_unevidenced_edges``;
    - an unevidenced copy of an evidenced relationship of the chunk (same
      source, target and type): folded into it, its justification kept in
      ``properties.justifications``, so the within-chunk duplicate collapse
      cannot keep the unevidenced copy over the evidenced one;
    - an endpoint that is no entity of the chunk: dropped (it carries nothing).

    Args:
        relationships: The chunk's relationships.
        entities: The chunk's entities (already evidence-filtered).
        sentences: The chunk's sentences.
        mode: Validation mode (:func:`relationship_evidence`).
        min_significant_word_length: Minimum length of a significant word.
        vocabulary: The source domain's relation vocabulary.
        conventions: The source domain's naming conventions.
        known: The source's known entities so far: an endpoint whose record
            name is one of them is named only by the known-entities name rule
            (:func:`relationship_evidence`, spec D8); None: none is.

    Returns:
        The kept relationships in input order, and the flagged, folded and dropped ones.

    """
    relation_vocabulary = RelationVocabulary.from_config(vocabulary)
    naming = NamingContext(entities, as_name_conventions(conventions), min_significant_word_length)
    verdicts = [
        relationship_evidence(
            rel,
            entities,
            sentences,
            mode=mode,
            min_significant_word_length=min_significant_word_length,
            vocabulary=relation_vocabulary,
            known=known,
            _naming=naming,
        )
        for rel in relationships
    ]
    evidenced_at: dict[tuple[Any, Any, str], int] = {}
    for position, (rel, verdict) in enumerate(zip(relationships, verdicts, strict=True)):
        if verdict.evidenced:
            evidenced_at.setdefault(_fact_key(rel), position)
    kept: dict[int, dict[str, Any]] = {}
    flagged: list[tuple[dict[str, Any], RelationshipEvidence]] = []
    folded: list[tuple[dict[str, Any], RelationshipEvidence]] = []
    dropped: list[tuple[dict[str, Any], RelationshipEvidence]] = []
    for position, (rel, verdict) in enumerate(zip(relationships, verdicts, strict=True)):
        if verdict.evidenced:
            kept.setdefault(position, rel)
        elif verdict.dropped:
            dropped.append((rel, verdict))
        elif (twin := evidenced_at.get(_fact_key(rel))) is not None:
            kept[twin] = _fold_justification(kept.get(twin, relationships[twin]), rel)
            folded.append((rel, verdict))
        else:
            kept[position] = flag_unevidenced(rel, verdict)
            flagged.append((kept[position], verdict))
    return EvidenceSorting(
        relationships=[kept[position] for position in sorted(kept)],
        flagged=flagged,
        folded=folded,
        dropped=dropped,
    )


def _relationship_endpoints(rel: Mapping[str, Any], entities: list[dict[str, Any]]) -> str:
    """``"source -> target"`` by entity name where an endpoint resolves, for the filtering log."""
    ends = []
    for idx in (rel.get("source", "?"), rel.get("target", "?")):
        if isinstance(idx, int) and 0 <= idx < len(entities):
            ends.append(str(entities[idx].get("name", f"Entity {idx}")))
        else:
            ends.append(str(idx))
    return " -> ".join(ends)


async def filter_relationships_by_evidence(
    relationships: list[dict[str, Any]],
    entities: list[dict[str, Any]],
    sentences: list[str],
    mode: str = "strict",
    filtering_log: FilteringLog | None = None,
    min_significant_word_length: int = MIN_SIGNIFICANT_WORD_LENGTH,
    *,
    adapter: Any | None = None,
    source_id: str | None = None,
    database_name: str | None = None,
    vocabulary: RelationVocabulary | Mapping[str, Any] | None = None,
    conventions: NameConventions | Mapping[str, Any] | None = None,
    known: KnownEntities | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Apply the evidence filter to one chunk's relationships (:func:`sort_relationships_by_evidence`).

    Keep, flag, never delete (2026-10-07): a relationship without evidence
    between two entities of the chunk is kept flagged ``unevidenced`` (or
    folded into its evidenced copy) and increments
    ``QualityCounter.RELATIONSHIPS_UNEVIDENCED_KEPT``; only one with an
    endpoint that is no entity of the chunk is dropped and increments
    ``QualityCounter.EVIDENCE_RELATIONSHIPS_DROPPED`` (until 2026-10-07 that
    counter counted every relationship without evidence, all of them
    dropped). Each counter gets one write (``n`` = its count), not one per
    item (:func:`_increment_counter`). The increments are best-effort: a
    missing *adapter* or *source_id* skips them so callers without a database
    handle (tests, benchmarks) still work.

    Args:
        relationships: Relationship list to filter.
        entities: Current entity list (already evidence-filtered).
        sentences: Source chunk sentences.
        mode: Validation mode (``strict``, ``standard``, ``narrative`` or ``relaxed``).
        filtering_log: Optional log collector for pipeline diagnostics.
        min_significant_word_length: Minimum character length for a word to
            count as significant in standard mode.
        adapter: Storage adapter for quality counter increments (optional).
        source_id: Source row identifier for quality counter increments
            (optional).
        database_name: Database name for quality counter increments
            (optional, defaults to ``"default"`` when adapter is provided).
        vocabulary: The source domain's relation vocabulary
            (:func:`relationship_evidence`).
        conventions: The source domain's naming conventions
            (:func:`relationship_evidence`).
        known: The source's known entities so far (:func:`relationship_evidence`);
            an edge it leaves unevidenced is kept flagged and counted as any other.

    Returns:
        Tuple of (kept relationships, stats): ``relationships_checked``,
        ``relationships_dropped`` (invalid endpoints only),
        ``relationships_unevidenced`` (kept flagged or folded).

    """
    from chaoscypher_core.services.sources.engine.extraction.utils.filtering_log import (
        FilteredItem,
    )

    sorting = sort_relationships_by_evidence(
        relationships,
        entities,
        sentences,
        mode=mode,
        min_significant_word_length=min_significant_word_length,
        vocabulary=vocabulary,
        conventions=conventions,
        known=known,
    )
    unevidenced = len(sorting.flagged) + len(sorting.folded)
    for rel, verdict in [*sorting.dropped, *sorting.flagged, *sorting.folded]:
        logger.debug(
            "relationship_evidence_dropped" if verdict.dropped else "relationship_unevidenced_kept",
            source=rel.get("source"),
            target=rel.get("target"),
            sent_ref=rel.get("sent_ref"),
            failure=str(verdict.failure),
            mode=mode,
        )

    await _increment_counter(
        adapter,
        source_id,
        database_name,
        QualityCounter.EVIDENCE_RELATIONSHIPS_DROPPED,
        len(sorting.dropped),
    )
    await _increment_counter(
        adapter,
        source_id,
        database_name,
        QualityCounter.RELATIONSHIPS_UNEVIDENCED_KEPT,
        unevidenced,
    )

    stats = {
        "relationships_checked": len(relationships),
        "relationships_dropped": len(sorting.dropped),
        "relationships_unevidenced": unevidenced,
    }

    if sorting.dropped or unevidenced:
        logger.info(
            "evidence_filtering_relationships",
            checked=len(relationships),
            kept=len(sorting.relationships),
            unevidenced=unevidenced,
            dropped=len(sorting.dropped),
            mode=mode,
        )

    if filtering_log is not None:
        filtering_log.add_stage(
            "relationship_evidence_filter",
            input_count=len(relationships),
            removed_count=len(sorting.dropped),
            items=[
                FilteredItem(
                    item_type="relationship",
                    name=_relationship_endpoints(rel, entities),
                    entity_type=rel.get("type", "?"),
                    reason=f"Endpoint is no entity of the chunk (mode={mode})",
                    details={"sent_ref": rel.get("sent_ref"), "mode": mode},
                )
                for rel, _ in sorting.dropped
            ],
            counts={
                "unevidenced_kept": len(sorting.flagged),
                "unevidenced_folded": len(sorting.folded),
            },
        )

    return sorting.relationships, stats


__all__: list[str] = [
    "ENTITY_UNEVIDENCED_STAGE_COUNT",
    "MIN_SIGNIFICANT_WORD_LENGTH",
    "CitedText",
    "EntityEvidence",
    "EntityEvidenceSorting",
    "EvidenceFailure",
    "EvidenceSorting",
    "NamingContext",
    "RelationshipEvidence",
    "alias_name",
    "as_name_conventions",
    "entity_evidence",
    "filter_entities_by_evidence",
    "filter_relationships_by_evidence",
    "flag_unevidenced",
    "flag_unevidenced_entity",
    "named_by_name",
    "naming_words",
    "relationship_evidence",
    "sort_entities_by_evidence",
    "sort_relationships_by_evidence",
    "title_words",
    "validate_entity_evidence",
    "validate_relationship_evidence",
]
