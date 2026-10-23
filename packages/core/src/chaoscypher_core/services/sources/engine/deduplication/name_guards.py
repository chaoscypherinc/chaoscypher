# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The guards that shape a graph, registered: where each lives, what turning it off means, who needs it.

Both recipes (``benchmark.extraction_recipe`` and the resolver's
``resolution.recipe``) run the name rule over :func:`guard_samples`, the
records every registered guard brings, and hash the one merge predicate's
verdicts on them (:func:`merge_verdicts`), so turning any registered guard
off moves the cache keys it names (:attr:`NameGuard.recipes`). A guard test
turns each one off in turn (:attr:`NameGuard.off` at :attr:`NameGuard.target`)
and asserts those recipes move; it also reads every module of
:data:`GUARDED_MODULES` and fails on a predicate that is neither registered
here nor listed in :data:`NOT_GUARDS`, so a new guard cannot ship without
the samples that make the recipes see it. Until audit wave 5 the guard list
lived in that test: the ``merged_names`` guard was never in it, and turning
it off moved no recipe. Since wave 6 the registry also holds what shapes a
graph outside the name rule: the survivor ranking, the evidence validator's
entity naming, the relation vocabulary's label normaliser and the parser's
possessive reading (each moved no recipe when replaced by a constant).

Each guard's records share no naming word with another guard's (one
given name, ``Lise``, is shared on purpose: a holder whose given name is
another's is no holder), so one guard's samples never decide another's.
"""

# Each ``off`` is the guard's stand-in: it takes the guard's arguments and ignores them.
# ruff: noqa: ARG005

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from chaoscypher_core.services.sources.engine.deduplication import name_identity as ni


@dataclass(frozen=True)
class GuardSample:
    """One record a guard needs: its label, given aliases, merged-in names, core type, support, subtype."""

    label: str
    aliases: tuple[str, ...] = ()
    merged: tuple[str, ...] = ()
    type: str = "Person"
    """A core type (``core_types.CORE_TYPES``): what an entity leaves the per-chunk filters as."""
    support: int = 0
    """The mentions behind the record (the extraction recipe writes as many source chunks) and
    its degree (the resolver recipe draws as many edges): what the survivor ranking's support
    reads. Zero is one mention and no edge."""
    subtype: str | None = None
    """The record's ``entity_subtype`` (``core_types.subtype_key`` form), or None."""


GUARDED_MODULES: dict[str, str] = {
    "name_identity": "chaoscypher_core.services.sources.engine.deduplication.name_identity",
    "evidence_validator": (
        "chaoscypher_core.services.sources.engine.extraction.utils.evidence_validator"
    ),
    "relation_vocabulary": (
        "chaoscypher_core.services.sources.engine.extraction.utils.relation_vocabulary"
    ),
    # spec 2026-10-11 D8 (final review 2 of 2026-10-12, M2): the known-entities list, its name
    # rules and its guards; until then their recipe cases were a hand list in a test
    "known_entities": "chaoscypher_core.services.sources.engine.extraction.utils.known_entities",
}
"""Each module whose predicates the guard test reads, by the name a target is written with.

A target without a module (``"is_bare_role"``, ``"NameIndex.sole_holder"``)
lives in ``name_identity``; any other is written ``"module:attribute"``
(``"evidence_validator:named_by_name"``). A module that holds a guard of
the graph gets an entry here, so its predicates are scanned too."""

RECIPES = ("extraction", "resolution")
"""The recipes a guard can move: the extraction recipe (every arm) and the resolver's."""


@dataclass(frozen=True)
class NameGuard:
    """One guard of the graph.

    ``target`` is the attribute it lives at (``Class.attribute`` or a
    function; ``module:`` first for a module of :data:`GUARDED_MODULES`
    other than ``name_identity``); ``off`` is what replacing it turns the
    guard off with; ``samples`` are records whose grouping the guard
    decides (none when the recipes' own samples reach it: the evidence
    validator's and the parser's); ``recipes`` are the recipes turning it
    off must move.
    """

    name: str
    target: str
    off: Callable[..., Any] | property
    samples: tuple[GuardSample, ...]
    recipes: tuple[str, ...] = RECIPES


def _s(
    label: str,
    *aliases: str,
    merged: tuple[str, ...] = (),
    kind: str = "Person",
    support: int = 0,
    subtype: str | None = "character",
) -> GuardSample:
    """A sample record (shorthand): a literary character unless ``kind`` and ``subtype`` say otherwise."""
    return GuardSample(label, tuple(aliases), merged, kind, support, subtype)


def _no_guard_stats() -> Any:
    """An empty ``known_entities.GuardStats`` (imported here: the module reads this one's package)."""
    from chaoscypher_core.services.sources.engine.extraction.utils.known_entities import (
        GuardStats,
    )

    return GuardStats()


def _every_word(profiles: Any) -> frozenset[str]:
    """Every naming word of ``profiles``: the women's-given-name check turned off."""
    return frozenset().union(*(p.all_words for p in profiles))


NAME_GUARDS: tuple[NameGuard, ...] = (
    NameGuard(
        "a bare title or role names no one",
        "is_bare_role",
        lambda label: False,
        # a lowercase role word, no title of any domain: "the miller" names no one
        (_s("the miller"), _s("Miller Hovden")),
    ),
    NameGuard(
        "a possessive or a relation of a name names someone else",
        "descriptor_possessor",
        lambda name, conventions: None,
        (_s("Marshal Oudry's envoy"), _s("Marshal Oudry")),
    ),
    NameGuard(
        "a family name's base form is no given name",
        "_family_base",
        lambda word, conv: False,
        # the base of a feminine form ("Morósova") is a family name: two lone
        # words, one a family name, are no two given names
        (_s("Morósov"), _s("Ilse")),
    ),
    NameGuard(
        "opposite genders or generations are two people",
        "_opposed",
        lambda a, b: False,
        (_s("Count Vadim", "Vadímovna"), _s("Vadímovna")),
    ),
    NameGuard(
        "two titles of one gender are two people",
        "_titles_clash",
        lambda a, b: False,
        (_s("Mrs Ada Fenwick"), _s("Miss Ada Fenwick")),
    ),
    NameGuard(
        "each with a given name the other lacks",
        "_given_apart",
        lambda a, b: False,
        (_s("Princess Bolkónskaya", "Lise"), _s("Princess Mary Bolkónskaya")),
    ),
    NameGuard(
        "a relation of a name is not its bearer",
        "_describes",
        lambda a, b: False,
        (_s("Countess Shinshiná", "Natálya's mother"), _s("Natálya Shinshiná")),
    ),
    NameGuard(
        "a gendered title fits only a name that can be the titled one",
        "_title_fit",
        lambda titled, other, womens_given=frozenset(): "fits",
        (
            _s("Prince Hippolyte"),
            _s("Hippolyte Trubetskóy"),
            _s("Mrs Brandt"),
            _s("Otto Brandt"),
        ),
    ),
    NameGuard(
        "a title word is a title only in title position",
        "name_parts",
        lambda text, titles: [
            (w, token, w not in titles) for token in text.split() for w in ni.fold(token).split()
        ],
        # "Major" after a capitalised naming word is a surname: "Oskar Major" is no "Oskar ..."
        (_s("Oskar Major"), _s("Oskar Lindqvist")),
    ),
    NameGuard(
        "a title the language writes as a given name, on one word, may be the bearer's given name",
        "_title_may_be_a_name",
        lambda titled, other: False,
        (_s("Duke Halvard"), _s("Halvard Brennick")),
    ),
    NameGuard(
        "a woman's title on a lone word needs it shown to be her given name",
        "_womens_given_names",
        _every_word,
        (_s("Lady Imogen"), _s("Imogen")),
    ),
    NameGuard(
        "a generation one name states says there may be two",
        "_generation_fits",
        lambda a, b: True,
        (_s("old Prince Volkónski"), _s("Prince Sergéy Volkónski")),
    ),
    NameGuard(
        "the same name words checked as the subset step checks",
        "NameIndex._same_words_family",
        lambda self, i, j, a=None, b=None: (),
        (_s("old Countess Zinaída Lánskaya"), _s("Zinaída Lánskaya")),
    ),
    NameGuard(
        "a family name in two forms is two people",
        "_forms_clash",
        lambda a, b: False,
        (_s("Dólokhov"), _s("Márya Ivánovna Dólokhova")),
    ),
    NameGuard(
        "a fuller alias names someone else",
        "_fuller_alias_elsewhere",
        lambda short, long_words: False,
        (
            _s("Lord Tarn", "Ulrich Konrad Tarn"),
            _s("Felix Tarn"),
            # an epithet alias names by its capitalised word alone (2026-10-07): "young
            # scapegrace Konrad" adds no naming word, so Konrad still joins Konrad Vorob
            _s("Konrad", "young scapegrace Konrad"),
            _s("Konrad Vorob"),
        ),
    ),
    NameGuard(
        "one title's spellings are one title",
        "NameConventions.title_class",
        property(lambda self: {}),
        (_s("Mme Duval"), _s("Madame Duval")),
    ),
    NameGuard(
        "a short name joins only the one record that could hold it",
        "NameIndex.sole_holder",
        lambda self, short, long_, words=None: True,
        (
            _s("Bezúkhov"),
            _s("Pierre Bezúkhov"),
            _s("Cyril Vladímirovich Bezúkhov"),
            _s("Kurnákova", "Lise"),
            _s("Lise Kurnákova"),
            _s("Princess Mary Kurnákova"),
        ),
    ),
    NameGuard(
        "two short forms join only when no fuller name could hold either",
        "NameIndex.no_fuller_holder",
        lambda self, i, j, words=None: True,
        (
            _s("Bagratión"),
            _s("the Bagratión"),
            _s("Prince Peter Bagratión"),
            _s("Prince Iván Bagratión"),
        ),
    ),
    NameGuard(
        "a title on a name is the family's when the family shows",
        "NameIndex.family_free",
        lambda self, titled, other, words=None: True,
        (_s("Mr Mélyukov"), _s("Mítya Mélyukov"), _s("Sónya Mélyukova")),
    ),
    NameGuard(
        "an alias is evidence only when one name gave it",
        "NameIndex.alias_evidence",
        lambda self, owner, alias, named: True,
        (
            _s("Vasíli Rennov", "Rennov"),
            _s("Anatole Rennov", "Rennov"),
            _s("Rennov"),
            # mutual aliases are evidence (2026-10-07): a record giving its own name gives
            # it to no one else, and the named record is never a second giver
            _s("Ansel", "Ansel", "Ansel Dufresne"),
            _s("Ansel Dufresne", "Ansel", "Ansel Dufresne"),
            # a reference giving the alias is no second name (2026-10-07: "the countess"
            # given "Nataly" kept Countess Rostóva from the Nataly she was given)
            _s("Lady Wrenna Cole", "Wren"),
            _s("the lady", "Wren"),
            _s("Wren"),
        ),
    ),
    NameGuard(
        "a chain of joins never makes one person of two",
        "NameIndex.groups_apart",
        lambda self, a, b: False,
        (
            _s("Prince Ippolit", "Ippolit"),
            _s("Ippolit", "Princess Ippolita"),
            _s("Princess Ippolita"),
        ),
    ),
    NameGuard(
        "only a person is named through someone else's name",
        "NameConventions.is_person",
        lambda self, entity_type: True,
        (
            _s("Caesar's Commentaries", kind="Work", subtype="literary_work"),
            _s("Commentaries", kind="Work", subtype="literary_work"),
        ),
    ),
    NameGuard(
        "a merge without name evidence needs every guard a join needs",
        "NameIndex.withheld",
        lambda self, i, j: None,
        (_s("old Count Strelsky"), _s("Count Dmitri Strelsky")),
    ),
    NameGuard(
        "a merged entity is guarded by every name merged into it",
        "NameIndex._merged_profiles",
        lambda self, i: [],
        (_s("Ellis", merged=("Countess Ellis Varne",)), _s("Count Ellis Varne")),
    ),
    NameGuard(
        "different family names or patronymics withhold a merge without name evidence",
        "_family_names_differ",
        lambda a, b, given=frozenset(): False,
        (_s("Countess Oberlin"), _s("Countess Pradova")),
    ),
    NameGuard(
        "one survivor ranking: own name, attested spelling, the fuller name, support, accents",
        "survivor_key",
        lambda label, conventions, **kw: (0,),
        (_s("Hans Ahlberg", "Hans Ahlborg"), _s("Hans Ahlborg")),
    ),
    NameGuard(
        "a merge's support is the mentions behind each member, and outranks accents and length",
        "record_support",
        lambda item: 1,
        # 2026-10-07: the name five mentions write keeps the label over a one-mention
        # nickname with an accent and more letters, as Pierre over Pétya (the resolver
        # reads support as degree: the recipe draws the edges, so it pins the rank too)
        (_s("Taras", "Tárasik", support=5), _s("Tárasik", "little Tárasik", support=1)),
        ("extraction",),
    ),
    NameGuard(
        "an alias is evidence only when its own text writes it and its output names no one else by it",
        "evidence_aliases",
        lambda aliases, attested, others=(): [str(a) for a in aliases if a],
        (),
        ("extraction",),
    ),
    NameGuard(
        "one survivor of a group, whatever order its members come in",
        "pick_survivor",
        lambda candidates, conventions: 0,
        (_s("Ingrid Solberg"), _s("Ingrid Sólberg")),
    ),
    NameGuard(
        "a woman's surname form never names a man, nor a man's a woman",
        "surname_key",
        lambda word, conventions=ni.DEFAULT_NAME_CONVENTIONS: (
            ni.identity_word(word, conventions),
            False,
        ),
        (_s("Sémenov"), _s("Agáfya Petróvna Sémenova")),
    ),
    NameGuard(
        "one family name in its surname forms is one stem",
        "identity_word",
        lambda word, conventions=ni.DEFAULT_NAME_CONVENTIONS: word,
        (_s("Fyódor Zarétski"), _s("Fyódor Zarétsky")),
    ),
    NameGuard(
        "a given name of a record is no family name",
        "_given_names",
        lambda label, aliases, conv: frozenset(),
        (_s("Princess Kalmýkova", "Zóya"), _s("Princess Olga Kalmýkova")),
    ),
    NameGuard(
        "evidence is read over every name of a record, never its label alone",
        "NameIndex.views",
        lambda self, i: [self._p[i]] if self._p[i].words else [],
        (
            _s("Count Grechin"),
            _s("Lev Nikoláyevich Grechin", merged=("Count Lev Nikoláyevich Grechin",)),
            _s("Natálya Grechina"),
        ),
    ),
    NameGuard(
        "the corpus's own spelling is stamped on every extracted entity",
        "attested_names",
        lambda names, texts: [],
        (),
        ("extraction",),
    ),
    NameGuard(
        "a possessive alias is a descriptor, never a name",
        "has_possessive",
        lambda label: False,
        (),
        ("extraction",),
    ),
    NameGuard(
        "a bare title, a role or a descriptor is a reference: anonymous ones written alike are one, "
        "a name always keeps the label, and none joins a name on vectors alone",
        "reference_key",
        lambda label, conventions=ni.DEFAULT_NAME_CONVENTIONS, entity_type=None: None,
        # 2026-10-07: "Emperor" and "the Emperor" were two nodes, a bare title giving an alias
        # out-ranked the name it gave on ``own``, and nothing withheld "the vicomte" from a
        # semantic merge into a name its own text never gave it
        (
            _s("Marquis"),
            _s("the Marquis"),
            _s("the Baroness", "Edda Lorne"),
            _s("Edda Lorne"),
        ),
    ),
    NameGuard(
        "a reference is the name its own names identify, never the anonymous ones written alike",
        "NameIndex.referent",
        lambda self, i: None,
        # 2026-10-07: one mention of "the Duchess" its own alias names joins Oda Brenning;
        # the anonymous "the Duchess" stays a reference of its own (two duchesses may be meant).
        # 2026-10-08 (the v5b literary extraction): two references each given "Natásha" are
        # both Natásha; each was a holder in the other's alias check, so each asked the other's
        # referent with no end (a reference holding a name only as its own alias is no holder:
        # ``NameIndex.holders``; a resolution that needs itself resolves to no one)
        (
            _s("the Duchess", "Oda Brenning"),
            _s("the Duchess"),
            _s("Oda Brenning"),
            _s("a girl of thirteen", "Natásha"),
            _s("the younger girl", "Natásha"),
            _s("Natásha"),
        ),
    ),
    NameGuard(
        "a bare title's own sentences name it when they write it with one name",
        "text_referent_names",
        lambda label, texts, conventions=ni.DEFAULT_NAME_CONVENTIONS: [],
        (),
        ("extraction",),
    ),
    NameGuard(
        "a bare title names only the one candidate holding it",
        "NameIndex.sole_title_holder",
        lambda self, i, title, among=None: True,
        (),
        ("extraction",),
    ),
    NameGuard(
        "a name names an entity only as whole words",
        "evidence_validator:CitedText.has_phrase",
        lambda self, name: " ".join(ni.fold(name).split()) in " ".join(self.words),
        (),
        ("extraction",),
    ),
    NameGuard(
        "a naming word names an entity only whole, titles left out",
        "evidence_validator:named_by_name",
        lambda cited, names, conventions, min_len: any(
            w in " ".join(cited.words) for n in names for w in ni.fold(n).split() if len(w) >= 4
        ),
        (),
        ("extraction",),
    ),
    NameGuard(
        "a naming word names an entity in its own surname form, or as the family's plural",
        "evidence_validator:CitedText.names_word",
        lambda self, name_word, conventions: (
            ni.identity_word(name_word, conventions) in {stem for stem, _ in self.surnames}
            or ni.identity_word(name_word, conventions) in self.families
        ),
        (),
        ("extraction",),
    ),
    NameGuard(
        "a family name in the plural names any member of the family",
        "evidence_validator:_plural_stem",
        lambda word, conventions: None,
        (),
        ("extraction",),
    ),
    NameGuard(
        "an elided or hyphenated name names by its last part",
        "evidence_validator:_variants",
        lambda token: ni.fold(token).split(),
        (),
        ("extraction",),
    ),
    NameGuard(
        "the title rung reads a bare title only, never one opening a name",
        "evidence_validator:_bare_titles",
        lambda text, conventions: frozenset(
            conventions.title_class.get(w, w)
            for w in ni.fold(text).split()
            if conventions.title_class.get(w, w) in conventions.naming_titles
        ),
        (),
        ("extraction",),
    ),
    NameGuard(
        "a title followed by a capitalised word is that name's title",
        "evidence_validator:_followed_by_a_name",
        lambda run, title_words: [False] * len(run),
        (),
        ("extraction",),
    ),
    NameGuard(
        "a title followed by a capitalised word opens a name",
        "evidence_validator:_capitalised",
        lambda token: True,
        (),
        ("extraction",),
    ),
    NameGuard(
        "a holder the text names by name counts for the title rung",
        "evidence_validator:NamingContext.named_by_name",
        lambda self, cited: frozenset(),
        (),
        ("extraction",),
    ),
    NameGuard(
        "a malformed sent_ref is repaired only from its S-numbers",
        "evidence_validator:_try_best_effort_parse",
        lambda raw_sent_ref: None,
        (),
        ("extraction",),
    ),
    NameGuard(
        "one canonical edge label for the commit, the strict filter and the vocabulary",
        "relation_vocabulary:canonical_edge_label",
        lambda raw: raw or "",
        (),
        ("extraction",),
    ),
    NameGuard(
        "a function word is never relation evidence",
        "relation_vocabulary:is_function_phrase",
        lambda entry: False,
        (),
        ("extraction",),
    ),
    # The known-entities list (spec 2026-10-11, D1-D3 as D8 amends them): the extraction
    # recipe's known-entities sample (``extraction_recipe._known_entities_sample``) reaches each.
    NameGuard(
        "the block lists the source's named entities",
        "known_entities:KnownEntities.lines",
        lambda self: [],
        (),
        ("extraction",),
    ),
    NameGuard(
        "the alias and name guards check pass 1 against the list",
        "known_entities:KnownEntities.guard",
        lambda self, entities, sentences, **kw: (entities, _no_guard_stats()),
        (),
        ("extraction",),
    ),
    NameGuard(
        "rule 1: a known entity is named by name, with a one-sentence look-back",
        "known_entities:KnownEntities.named",
        lambda self, key, record_name, ref, **kw: True,
        (),
        ("extraction",),
    ),
    NameGuard(
        "rules 1 and 2 are the one reading of a known record, the guard's and the edge rule's",
        "known_entities:KnownEntities.identified",
        lambda self, key, record, ref, **kw: True,
        (),
        ("extraction",),
    ),
    NameGuard(
        "rule 2: a distinctive alias of one entry carries, written whole",
        "known_entities:KnownEntities._carried",
        lambda self, key, cited, phrases, min_word_length: False,
        (),
        ("extraction",),
    ),
    NameGuard(
        "a record's written reference phrases condition rule 2 and are what it is demoted to",
        "known_entities:KnownEntities._phrases",
        lambda self, record, cited, entity_type, min_word_length: [],
        (),
        ("extraction",),
    ),
    NameGuard(
        "a reference is no known entity, whoever gave it as an alias",
        "known_entities:KnownEntities.resolves",
        lambda self, name, entity_type="": None,
        (),
        ("extraction",),
    ),
    NameGuard(
        "a label names a known entry by its name or an alias one entry alone gave",
        "known_entities:KnownEntities._resolve",
        lambda self, folded: None,
        (),
        ("extraction",),
    ),
    NameGuard(
        "an entry's names are its name and its aliases that read as a name",
        "known_entities:KnownEntities._name_labels",
        lambda self, name, entity_type, aliases: [name],
        (),
        ("extraction",),
    ),
    NameGuard(
        "a reference is never listed",
        "known_entities:_is_reference",
        lambda name, entity_type, conventions: False,
        (),
        ("extraction",),
    ),
    NameGuard(
        "a single word is no name of a known entry",
        "known_entities:_multi_word",
        lambda read: True,
        (),
        ("extraction",),
    ),
    NameGuard(
        "an alias that opens with an article or a possessive is a description, never a name",
        "known_entities:_describes",
        lambda label: False,
        (),
        ("extraction",),
    ),
    NameGuard(
        "an alias that opens with a possessive is a speaker's phrase",
        "known_entities:_speaker_relative",
        lambda label: False,
        (),
        ("extraction",),
    ),
    NameGuard(
        "rule 1 names a known entry by its multi-word non-reference name aliases",
        "known_entities:_names_by_rule_one",
        lambda alias, read, entity_type, conventions: False,
        (),
        ("extraction",),
    ),
    NameGuard(
        "the list offers a multi-word name alias",
        "known_entities:multi_word_name",
        lambda alias, conventions: None,
        (),
        ("extraction",),
    ),
    NameGuard(
        "the block offers no single-word or speaker-relative alias",
        "known_entities:offered_alias",
        lambda alias, conventions: True,
        (),
        ("extraction",),
    ),
    NameGuard(
        "a record the name guard flagged is never listed",
        "is_referent_unevidenced",
        lambda properties: False,
        (),
        ("extraction",),
    ),
    NameGuard(
        "a known name the guard kept flagged names no edge; a demoted one reads by its phrase",
        "known_entities:KnownEntities.kept_unevidenced",
        lambda *record: False,  # a staticmethod: set on the class, an instance call passes self too
        (),
        ("extraction",),
    ),
)
"""Every guard of the name rule, in the order the recipes read their samples."""

NOT_GUARDS: dict[str, str] = {
    "is_descriptor": "a wrapper of descriptor_possessor",
    "is_relation_alias": "a wrapper of is_descriptor for a person (descriptor_possessor is registered)",
    "_single": "a helper (one value of a set)",
    "_clash": "composes _opposed, _titles_clash and _describes",
    "_profiles_apart": "composes _clash and _given_apart over groups",
    "_apart": "composes _clash and _given_apart",
    "NameIndex._same_type": "the typed switch, not a name guard",
    "NameIndex.pair": "a cache of NameIndex._pair",
    "NameIndex._pair": "composes the registered pairwise guards",
    "NameIndex.apart": "composes _apart and the merged names",
    "NameIndex._all_joined": "a helper of the holder guards",
    "NameIndex._bears_exactly": "a helper of sole_holder (merged names read by _merged_profiles)",
    "NameIndex.alias_naming": "a lookup",
    "NameIndex.reference": "a lookup (reference_key is registered)",
    "reference_names_a_person": (
        "the type rescue's reading of an untyped reference (type_rescue), not the name rule; "
        "the extraction recipe's filter sample reaches it"
    ),
    "NameIndex._referent": "the uncached NameIndex.referent (registered)",
    "NameIndex.alias_evidence_between": "composes alias_naming and alias_evidence",
    "NameIndex.stands": "composes the registered holder and alias guards",
    "NameIndex.stands_pair": "a cache of NameIndex._stands_pair",
    "NameIndex._stands_pair": "composes stands and no_fuller_holder",
    "NameIndex.allows_merge": "the one merge predicate: composes groups_apart, stands_pair and withheld",
    "same_entity": "an entry point",
    "NameIndex.title_holders": "a lookup of NameIndex.sole_title_holder",
    "record_attested_names": "a lookup",
    "is_reference_record": (
        "a reader of the stored reference flag for retrieval (the retrieval recipe pins it: "
        "graphrag_handlers._seedable); the flag is written from reference_key (registered)"
    ),
    "is_orphan_record": (
        "a reader of the stored orphan flag the commit writes for retrieval's ranking (the "
        "retrieval recipe pins it: graphrag_handlers._by_standing); no name rule reads it"
    ),
    "pipeline_flag": (
        "the one reader of a stored pipeline flag (True exactly); every flag reader composes it "
        "(is_reference_record, is_orphan_record, is_unevidenced_record, node_standing, "
        "edge_walk_weight, triple_standing: the retrieval recipe pins them); no name rule "
        "reads it"
    ),
    "labelled_by_reference": (
        "composes reference_key (registered) with is_bare_role for a non-person: what the "
        "reference flag and the per-chunk name filters read (the extraction recipe's filter "
        "and dedup samples pin it: kept_as_reference, publish_descriptor_references); the "
        "name rule reads reference_key alone"
    ),
    "node_standing": (
        "composes is_reference_record, is_orphan_record and doubted_by_filters into "
        "retrieval's sort key (the retrieval recipe pins it: graphrag_handlers._by_standing)"
    ),
    "doubted_by_filters": (
        "composes pipeline_flag over DOUBTFUL_NODE_FLAG_KEYS: the one reading of the per-chunk "
        "filters' doubt flags (node_standing and is_doubted_record compose it); no name rule "
        "reads it"
    ),
    "is_doubted_record": (
        "composes doubted_by_filters with is_referent_unevidenced (registered): what "
        "KnownEntities.add_group leaves out of the list (the extraction recipe's known-entities "
        "sample pins it); no name rule reads it"
    ),
    "is_unevidenced_record": (
        "composes pipeline_flag: a reader of the stored edge flag the evidence filter writes "
        "(the extraction recipe "
        "pins the filter, the retrieval recipe its reader: graphrag_blocks._edge_in_context); "
        "no name rule reads it"
    ),
    "edge_justifications": (
        "a lookup of an edge's justifications (merge_edge_properties folds them; both recipes "
        "sample the fold); no name rule reads it"
    ),
    "evidence_validator:RelationshipEvidence.evidenced": "a lookup of the verdict (no failure)",
    "evidence_validator:CitedSentences.reason": (
        "the sentence-reference failure's one-sentence reason (a message, no name verdict)"
    ),
    "evidence_validator:RelationshipEvidence.dropped": "a lookup of the verdict (invalid endpoint)",
    "evidence_validator:EntityEvidence.evidenced": (
        "a lookup of the entity verdict (no failure; entity_evidence composes the registered "
        "naming rungs, and the extraction recipe's evidence sample pins the entity filter)"
    ),
    "evidence_validator:_fact_key": (
        "the within-chunk duplicate key the unevidenced fold uses (the extraction recipe's "
        "sorting sample pins it), no name verdict"
    ),
    "_given_words": "a helper of _family_names_differ (the given names a set of names shows)",
    "evidence_validator:_entity_named": "composes the registered naming rungs",
    "evidence_validator:NamingContext.names_by_title": "composes NameIndex.sole_title_holder",
    "evidence_validator:validate_entity_evidence": "an entry point",
    "evidence_validator:validate_relationship_evidence": "an entry point",
    "relation_vocabulary:_contains_phrase": "a helper of states_relation",
    "relation_vocabulary:RelationVocabulary.states_relation": "an entry point",
    "relation_vocabulary:states_relation": "a wrapper of RelationVocabulary.states_relation",
    "relation_vocabulary:RelationVocabulary.single_valued_labels": (
        "a listing the memory path's supersession reads, not the extraction"
    ),
    # wave 7: the scan reads int, frozenset[int], frozenset[str], tuple and list returns too
    "NameConventions.forms": "a table (the conventions' name forms, longest ending first)",
    "NameConventions.gendered_titles": "a table (the feminine and masculine titles)",
    "NameConventions.naming_titles": "a table the title rung reads (sole_title_holder is registered)",
    "NameConventions.not_given": "a table (the words never a given name)",
    "NameIndex._alias_profiles": "a lookup NameIndex.views reads",
    "NameIndex._all_names_of": "a helper of NameIndex.views",
    "NameIndex._names_of": "a helper of the group check (merged names: _merged_profiles)",
    "NameIndex._words_pair": "composes the registered pairwise guards (as NameIndex._pair does)",
    "NameIndex.holders": "the lookup of sole_holder, family_free and no_fuller_holder (registered)",
    "NameIndex.words_of": "a lookup",
    "_Profile.all_words": "a lookup",
    "_accented_letters": "a count survivor_key ranks by (survivor_key is registered)",
    "_folded_titles": "a fold of a title set",
    "_title_set": "a fold of a title set",
    "_possessives": "the tokenizer of has_possessive and descriptor_possessor (both registered)",
    "_tokens": "a tokenizer",
    "_single_of": "a helper (one value of a set)",
    "_merged_groups": "the build of title_equivalents (NameConventions.title_class is registered)",
    "identity_words": "composes name_words and identity_word (identity_word is registered)",
    "names_person": "surname_key's readings compared (surname_key is registered; callers look them up)",
    "plan_name_pairs": "an entry point (composes stands_pair and allows_merge)",
    "record_descriptors": "a lookup",
    "record_given_aliases": "a lookup",
    "record_merged_names": "a lookup",
    "record_order_key": "the join order, never a verdict (plan_name_pairs sorts by it)",
    "vocabulary_words": "a table (a vocabulary's words in order)",
    "evidence_validator:_names": "a lookup (an entity's name and aliases: its phrases)",
    "evidence_validator:_word_names": (
        "a lookup (an entity's name and its aliases that read as a name: alias_name)"
    ),
    "evidence_validator:_named_by_names": (
        "composes the registered phrase and word rungs (CitedText.has_phrase, named_by_name) "
        "over _names and _word_names"
    ),
    "evidence_validator:alias_name": (
        "composes alias_as_name and name_words: an alias whose reading holds no naming word is "
        "no name (spec D8); the extraction recipe's evidence and known-entities samples pin it "
        "(test_extraction_recipe: turning it off moves the recipe)"
    ),
    "evidence_validator:_word_run": "a tokenizer (the cited text's words, case and breaks)",
    "evidence_validator:title_words": "a fold of a title set",
    "evidence_validator:filter_entities_by_evidence": "an entry point",
    "evidence_validator:filter_relationships_by_evidence": "an entry point",
    "relation_vocabulary:RelationVocabulary._phrases": "a table (the multi-word entries)",
    "relation_vocabulary:RelationVocabulary.words": "a table (every word of every relation)",
    "relation_vocabulary:RelationVocabulary.relation_words": "a lookup (a label's relation words)",
    "relation_vocabulary:relation_words": "a wrapper of RelationVocabulary.relation_words",
    "relation_vocabulary:text_words": "the shared fold (name_identity.fold) as words",
    "relation_vocabulary:label_evidence_words": (
        "the template audit's reading of a label (two_endpoints_only_reason), no extraction verdict"
    ),
    "known_entities:_texts": "a reader of a record's list field (a lone string is one item)",
    "known_entities:KnownEntities.enabled": (
        "a lookup of the limit (extraction.known_entities_limit is in the recipe's settings)"
    ),
    "known_entities:KnownEntities.__len__": "a count (the list's size: KnownEntities.stats)",
    "known_entities:KnownEntities._reading": (
        "a cache of a label's alias_name reading and its naming words (alias_name is pinned by "
        "test_extraction_recipe, the naming words are the evidence validator's)"
    ),
    "known_entities:KnownEntities._ranked": (
        "the block's order and bound (KnownEntities.lines is registered; the recipe's sample "
        "re-cites names in a second group, so the ranking by groups cited moves it)"
    ),
}
"""Predicates of the scanned modules (:data:`GUARDED_MODULES`) that are not guards of their own, and why.

The guard test reads it."""


def guard_samples() -> tuple[GuardSample, ...]:
    """Every registered guard's samples, in registry order: what both recipes run the rule over."""
    return tuple(sample for guard in NAME_GUARDS for sample in guard.samples)


def guard_records() -> list[ni.NameRecord]:
    """:func:`guard_samples` as name-rule records."""
    return [ni.NameRecord(g.label, g.type, g.aliases, merged=g.merged) for g in guard_samples()]


def merge_verdicts(conventions: ni.NameConventions) -> list[tuple[int, int, bool]]:
    """``NameIndex.allows_merge`` on every pair of one guard's samples, over all the samples.

    The one merge predicate read directly, evidence or none (the semantic
    dedup's question): both recipes hash these verdicts beside the joins, so
    a guard that only decides a merge without name evidence (the given-name
    check on two lone words, the withholding guards) still moves both.
    """
    index = ni.NameIndex(guard_records(), conventions=conventions)
    out: list[tuple[int, int, bool]] = []
    start = 0
    for guard in NAME_GUARDS:
        block = range(start, start + len(guard.samples))
        out.extend((i, j, index.allows_merge([i], [j])) for i in block for j in block if i < j)
        start += len(guard.samples)
    return out


def target_module(target: str) -> tuple[Any, str]:
    """The module of :data:`GUARDED_MODULES` a target lives in, and the target within it."""
    import importlib

    module, _, inner = target.rpartition(":")
    return importlib.import_module(GUARDED_MODULES[module or "name_identity"]), inner


def guard_target(guard: NameGuard | str) -> tuple[Any, str]:
    """``(owner, attribute)`` of a guard's target (or a target string), for a test to replace."""
    module, inner = target_module(guard if isinstance(guard, str) else guard.target)
    owner, _, attr = inner.rpartition(".")
    return (getattr(module, owner) if owner else module), attr


__all__ = [
    "GUARDED_MODULES",
    "NAME_GUARDS",
    "NOT_GUARDS",
    "RECIPES",
    "GuardSample",
    "NameGuard",
    "guard_records",
    "guard_samples",
    "guard_target",
    "merge_verdicts",
    "target_module",
]
