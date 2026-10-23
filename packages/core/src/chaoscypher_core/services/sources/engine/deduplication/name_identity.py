# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""When two names are one entity: the one name-evidence rule.

Used by the pre-commit deduplication (the exact dedup's alias keys and
``EntityProcessor.resolve_hierarchical_names``) and by the post-commit
identity resolver (``services.graph.resolution``), so a pair either pass
would merge is a pair the other would merge too.

Evidence, not word overlap. Two same-type names are one entity when

* they are the same name (``same``), or have the same naming words, titles
  and articles left out (``Prince Andrew`` and ``Andrew``, ``same``);
* one name is an alias the other was given (``alias``); or
* one name is the other with words left out and nothing added (``Mlle
  Schérer`` in ``Anna Pávlovna Schérer``, ``subset``).

The rule is generic; its knowledge tables are the source domain's
(:class:`NameConventions`, built by ``domain_resolver.domain_name_conventions``
from the domain's ``.jsonld``) over a universal floor every domain starts from
(the English and French address titles and kinship vocatives, ``Mamma`` and
``Uncle``, with their gender and spellings: :data:`UNIVERSAL_TITLE_WORDS`;
:data:`DEFAULT_NAME_CONVENTIONS` when a source names no domain). The examples
below are the literary domain's.

Names are read through one fold (:func:`fold`: any script's letters,
accents stripped, hyphenated words whole, so ``Anne-Marie Dupont`` holds no
``Marie Dupont``). Naming words are compared after a small fold of surname forms (the domain's
``name_forms``; the literary domain's Russian ones make ``Rostóva`` and
``Rostóv``, ``Bolkónskaya`` and ``Bolkónski``, ``Drubetskáya`` and
``Drubetskóy`` one family name).

Everything but the same name is withheld when the names say it could be
someone else (a withheld merge leaves two nodes; a wrong one loses a
person):

* **Gender and generation.** A gendered title or name form (the
  conventions' ``feminine_titles`` and ``masculine_titles``, the gender of a
  :class:`NameForm`) never joins the opposite one (``King``/``Queen of
  Prussia``, ``Count Rostóv``/``Countess Rostóva``), two names stating
  different titles of one gender are two people (``Mrs`` and ``Miss Mary
  Smith``; one title's spellings, ``title_equivalents``, are one title), and
  ``old`` never joins ``young`` (``generation_words``). A generation word is
  folded only into a name that says the same. A gendered title fits a name
  that states the same gender by its title; a man's title also fits a name
  that states no gender (``Prince Vasíli`` is ``Vasíli Kurágin``, ``Mr
  Darcy`` is ``Fitzwilliam Darcy``) unless someone else bears the name
  (``Count Rostóv`` is not ``Nicholas Rostóv`` beside ``Natásha Rostóva``:
  the name is a family's); a woman's title on a family name never fits a
  name that does not say a woman, since a woman may be named by her
  husband's name (``Mrs John Smith``, ``Countess Bezúkhov``), and fits one
  whose form says a woman, or whose given names are all the titled name's
  naming words (``Princess Mary`` and ``Mary``), only as a man's title fits
  an unmarked one. These checks apply on every step, the same name words
  included (``old Countess Natálya Rostóva`` is not ``Natálya Rostóva``).
  A title that states no gender is not compared: ``Captain Smith`` and
  ``Colonel Smith`` share their naming words and may join (a documented
  limit; a rank changes over a story, so two ranks are not two people).
* **Descriptors.** A name that names its bearer through someone else's name
  (any possessive followed by a word: ``Natásha's mother``, ``Count
  Rostóv's valet``, ``Kutúzov's envoy``; or a relation or role word joined
  by ``of``: ``the father of Pierre``; :func:`descriptor_possessor`) names
  that person's relation, never the person: it gives no naming word, given
  name or holder, and says its bearer is someone else than the person it
  names.
* **One holder.** A short name is folded into a fuller one only when that
  is the one node that could hold it, counting every same-type node whose
  name or given alias contains its naming words, except one whose family
  name is written in the other form (``Márya Ivánovna Dólokhova`` holds no
  ``Dólokhov``); ``Count Bezúkhov`` beside ``Pierre Bezúkhov`` and ``Cyril
  Vladímirovich Bezúkhov`` names someone, but nothing says whom. Two short
  forms with the same naming words join only when no fuller name could
  hold either.
* **Alias keys.** An alias is evidence only when one entity gave it, no
  other node holds that name, and it names someone (a bare title such as
  ``the prince`` is never a key): ``Kurágin`` on Anatole and on Vasíli
  joins neither to a node called ``Kurágin``.
* **Names of their own.** A short name carrying a fuller alias that adds a
  word the candidate lacks (``Princess Bolkónskaya`` given ``Lise
  Meinen``), or a given name the candidate does not have where the
  candidate puts a different one (``Princess Bolkónskaya`` given ``Lise``
  beside ``Princess Mary Bolkónskaya``), is someone else.
* **References.** A person written under a bare title, a role or a
  descriptor (``the count``, ``Emperor``, ``the little princess``, ``Prince
  Andrew's wife``: :func:`reference_key`) is a reference, not a name. It
  is the named person its own names say it is (a name its own output gave
  it, or the one name its own sentences write with its title:
  :meth:`NameIndex.referent`, :func:`text_referent_names`), never a person
  its output lists beside it (the extractor that lists ``the countess``
  beside ``Countess Véra`` has said they are two) nor one found elsewhere
  or by frequency (Book One has two emperors). A reference no name of its
  own identifies is anonymous: it joins only the anonymous references
  written alike (``Emperor``, ``the Emperor``), never a name without its
  own evidence (:meth:`NameIndex.withheld`), and keeps a node of its own,
  flagged (:data:`DESCRIPTOR_REFERENCE_KEY`). A name always survives a
  reference (:func:`survivor_key`).

Pairs are joined strongest evidence first, and a join that would put two
names the rule keeps apart into one group is not made, so a chain of
merges never joins what the rule splits.
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from functools import cached_property, lru_cache
from itertools import combinations
from types import MappingProxyType
from typing import Any, Literal, NamedTuple

from chaoscypher_core.exceptions import ValidationError
from chaoscypher_core.services.sources.engine.deduplication.similarity_matcher import (
    core_type_key,
)


_LETTER_FOLDS = str.maketrans(
    {"ø": "o", "ł": "l", "đ": "d", "ħ": "h", "\u0131": "i", "æ": "ae", "œ": "oe", "ß": "ss",
     "þ": "th", "ð": "d", "\u2010": "-", "\u2011": "-"}
)  # fmt: skip
"""Letters NFKD leaves whole, as their base letters (``Søren`` is ``soren``), and hyphens as ``-``."""

WORD_PATTERN = re.compile(r"[^\W_]+(?:-[^\W_]+)*")
"""A word of folded text: letters and digits of any script, hyphenated parts kept together.

``sister-in-law`` and ``Anne-Marie`` are one word each; ``Наташа`` and ``毛泽东``
are words (until 2026-10-06 only ``[a-z0-9]`` counted, so a Cyrillic or CJK
name had no word and ``ø`` split ``Søren`` in two)."""


class _FormatTable(dict[int, str]):
    """``str.translate`` table for the format characters (category ``Cf``), filled on first sight.

    A format character inside a word is dropped (a soft hyphen or a zero-width
    joiner splits no word: ``Bolkón`` + U+00AD + ``ski`` is ``bolkonski``); the
    zero-width space is a word break, so it becomes a space. Any other
    character is kept.
    """

    def __missing__(self, codepoint: int) -> str:
        """Map a code point not seen before, and remember it."""
        char = chr(codepoint)
        value = char if unicodedata.category(char) != "Cf" else ""
        self[codepoint] = value
        return value


_FORMAT_FOLDS = _FormatTable({0x200B: " "})
"""Format characters as :func:`fold` reads them: dropped, the zero-width space a word break."""

_EN_DASH_IN_WORD = re.compile(r"(?<=[^\W_])\u2013(?=[^\W_])")
"""An en dash between two letters (``Bolkónski`` + U+2013 + ``Kurágin``): a hyphen, as the scorers read it."""


@lru_cache(maxsize=65536)
def fold(text: str) -> str:
    """Lowercase, accents stripped, possessives and apostrophes dropped, one space between words.

    Words are :data:`WORD_PATTERN`'s: any script's letters, hyphenated words
    whole. The one fold of names and relation words: the name rule, the
    evidence validator (entity naming and the relation vocabulary) and the
    retrieval matcher read text through it. The Unicode hyphens (U+2010,
    U+2011) and an en dash between letters are hyphens; a format character
    is dropped (a soft hyphen splits no word) and a zero-width space is a
    word break (until audit wave 6 the relation vocabulary folded with its
    own copy, which split a word at a soft hyphen and kept U+2010).

    Memoized: a pure function of ``text``, and the evidence validator folds the
    same few hundred strings millions of times (one extraction recipe: 1.9M
    calls, 785 distinct; 4.1 s became 1.5 s, the digest unchanged).
    """
    bare = "".join(
        c
        for c in unicodedata.normalize("NFKD", text.lower().translate(_FORMAT_FOLDS))
        if not unicodedata.combining(c)
    ).translate(_LETTER_FOLDS)
    bare = _EN_DASH_IN_WORD.sub("-", bare)
    bare = re.sub(r"['\u2019]s\b", "", bare)
    return " ".join(WORD_PATTERN.findall(bare.replace("'", "").replace("\u2019", "")))


UNIVERSAL_DETERMINERS = frozenset({"my", "our", "your", "his", "her", "its", "their"})
"""The possessive determiners of English (folded): ``our Emperor`` names no one, ``our dear
Wintzingerode`` is named by its other words. Until 2026-10-07 ``our`` was a naming word, and in
the v4 generic arm ``Emperor`` (given ``our Emperor``) joined ``Wintzingerode`` (given ``our
dear Wintzingerode``) as a name with words left out."""

UNIVERSAL_PARTICLES = (
    frozenset({"the", "of", "de", "du", "von", "van", "le", "la"}) | UNIVERSAL_DETERMINERS
)
"""The articles, particles and determiners a name may carry (folded): never a naming word, in any position.

``Ludwig van Beethoven``, ``Vicomte de Mortemart``, ``Peter the Great``: unlike
a title (:func:`name_parts`), a particle inside a name is no word of it
whatever its case (``Ludwig Van Beethoven`` is ``Ludwig van Beethoven``). A
documented limit: a leading particle is dropped too, so ``Van Morrison`` is
named by ``Morrison`` alone."""

UNIVERSAL_TITLE_WORDS = frozenset(
    {
        # English address titles and nobility, ranks and clergy
        "mr", "mrs", "ms", "miss", "mister", "master", "madam", "sir", "dame", "lord", "lady",
        "dr", "doctor", "prince", "princess", "count", "countess", "duke", "duchess", "baron",
        "baroness", "marquis", "earl", "viscount", "king", "queen", "emperor", "empress",
        "general", "colonel", "captain", "major", "lieutenant", "sergeant", "father", "reverend",
        # French address titles and nobility (never ``M.``: it is an initial as often)
        "madame", "mademoiselle", "mlle", "mme", "monsieur", "duc", "vicomte", "abbé", "abbe",
        # kinship vocatives: a family's word for one of its own, never that person's name
        "mamma", "mama", "maman", "papa", "uncle", "aunt", "auntie", "grandmamma", "grandmama",
        "grandpapa",
    }
) | UNIVERSAL_PARTICLES  # fmt: skip
"""The language-level title words of English and French: the floor every domain's titles start from.

The one universal title table (audit wave 7). :meth:`NameConventions.build`
unions it with a domain's own ``title_words``, so a domain only adds its
genre's (the literary domain's ``dowager``, ``vicomtesse``, ``frau``, its
generation words and Russian name forms): ``Monsieur Pierre`` is named by
``Pierre`` under every domain, and ``prince`` inside a name is no naming word
under the generic one. Until wave 7 two universal lists stood here (a
``TITLES`` default for :func:`name_words` and this one), and the generic
domain fell back to thirteen words, so ``prince``, ``count`` and ``monsieur``
were naming words under generic and its arm kept 13 edges the literary arm
drops, all four false ones among them. Compared folded (``abbé`` is
``abbe``); the gendered titles and generation words of a domain (or the
universal ones below) join the set too, and so do the particles
(:data:`UNIVERSAL_PARTICLES`). A title word is a title only in title
position (:func:`name_parts`: ``John Major`` and ``Carole King`` keep their
surnames). Until audit wave 8 the floor held ``m.`` (Monsieur), which folds
to ``m`` and dropped the initial of ``M. Smith``, so ``M. Smith`` joined ``J.
Smith``; ``Monsieur`` and ``Mr`` stay.

The kinship vocatives (``Mamma``, ``Maman``, ``Papa``, ``Uncle``, ``Aunt``,
``Grandpapa``) are titles too: they address a person, never name one, so
``Uncle Pierre`` is named by ``Pierre`` and a bare ``Mamma`` is a reference
(:func:`reference_key`). Until 2026-10-11 ``Mamma`` had no title reading, so it
was a given name, and on the v5d literary pack ``the countess`` given
``Mamma`` folded onto Borís's unflagged ``Mamma`` node. Accents need no
spelling of their own (``papá`` folds to ``papa``).
"""

UNIVERSAL_FEMININE_TITLES = frozenset(
    {
        "mrs", "ms", "miss", "madam", "dame", "lady", "princess", "countess", "duchess",
        "baroness", "queen", "empress", "madame", "mme", "mademoiselle", "mlle",
        "mamma", "mama", "maman", "aunt", "auntie", "grandmamma", "grandmama",
    }
)  # fmt: skip
"""The titles of :data:`UNIVERSAL_TITLE_WORDS` that say a woman (English and French, folded).

Every domain's ``feminine_titles`` add to these (the literary domain's
``dowager``, ``vicomtesse``, ``signora``): a title the floor drops from a name
always says the gender it says in the language (audit wave 7: with
``countess`` a title but no gender under the generic domain, ``Countess
Thomas`` joined ``Thomas Rostóv``)."""

UNIVERSAL_MASCULINE_TITLES = frozenset(
    {
        "mr", "mister", "master", "sir", "lord", "prince", "count", "duke", "duc", "baron",
        "marquis", "earl", "viscount", "vicomte", "king", "emperor", "monsieur", "father",
        "abbe", "papa", "uncle", "grandpapa",
    }
)  # fmt: skip
"""The titles of :data:`UNIVERSAL_TITLE_WORDS` that say a man (folded); every domain's add to these."""

UNIVERSAL_GIVEN_NAME_TITLES = frozenset(
    {"prince", "earl", "duke", "count", "king", "baron", "marquis"}
)  # fmt: skip
"""The men's titles of the floor the language also writes as a given name (folded).

``Earl Warren``, ``Prince Fielder``, ``Duke Ellington``, ``Count Basie``,
``King Vidor``: before one other word, such a word is a title or the bearer's
given name, and nothing in the name says which. A name so written is never
folded into a fuller name that states no gender (:func:`_title_may_be_a_name`:
``Earl Warren`` is not ``Warren Burger``, ``Prince Fielder`` not ``Cecil
Fielder``); an address title has no such reading (``Mr Darcy`` is
``Fitzwilliam Darcy``). A domain that declares the word a title of its own
(``title_words``, ``masculine_titles``) says its texts write it as one: under
the literary domain ``Prince Vasíli`` is ``Vasíli Kurágin``
(:attr:`NameConventions.given_name_titles`). Until audit wave 8 the floor made
both pairs one person under the generic domain, which declares none of them."""

UNIVERSAL_GENERATION_WORDS: dict[str, str] = {
    "elder": "elder", "senior": "elder", "sr": "elder",
    "younger": "younger", "junior": "younger", "jr": "younger",
}  # fmt: skip
"""Generation words when a domain declares no ``generation_words`` (folded word -> generation)."""

UNIVERSAL_RELATION_VOCABULARY: dict[str, dict[str, list[str]]] = {
    "parent": {
        "labels": ["parent_of", "child_of"],
        "words": [
            "father", "fathers", "mother", "mothers", "parent", "parents", "son", "sons",
            "daughter", "daughters", "child", "children", "papa", "mama", "mamma", "maman",
            "stepfather", "stepmother", "stepson", "stepdaughter", "stepchild",
        ],
    },
    "sibling": {
        "labels": ["sibling_of"],
        "words": [
            "brother", "brothers", "sister", "sisters", "sibling", "siblings", "half-brother",
            "half-sister", "stepbrother", "stepsister",
        ],
    },
    "spouse": {
        "labels": ["spouse_of", "married_to"],
        "words": ["husband", "husbands", "wife", "wives", "spouse", "spouses", "married", "widow",
                  "widower", "marriage", "marriages", "marry", "marries", "wed", "weds", "wedded"],
    },
    "cousin": {"labels": ["cousin_of"], "words": ["cousin", "cousins"]},
    "grandparent": {
        "labels": ["grandparent_of", "grandchild_of"],
        "words": [
            "grandfather", "grandfathers", "grandmother", "grandmothers", "grandparent",
            "grandparents", "grandson", "grandsons", "granddaughter", "granddaughters",
            "grandchild", "grandchildren", "grandpapa", "grandmamma", "grandmama",
        ],
    },
    "uncle": {
        "labels": ["uncle_aunt_of", "nephew_niece_of"],
        "words": ["uncle", "uncles", "aunt", "aunts", "auntie", "nephew", "nephews", "niece",
                  "nieces"],
    },
    "kin": {
        "labels": ["related_to"],
        "words": [
            "relative", "relatives", "kinsman", "kinsmen", "kinswoman", "kinswomen",
            "mother-in-law", "father-in-law", "sister-in-law",
            "brother-in-law", "son-in-law", "daughter-in-law",
        ],
    },
    "friend": {
        "labels": ["friend_of", "friendship"],
        "words": ["friend", "friends", "friendship", "friendships"],
    },
    "member": {"labels": ["member_of"], "words": ["member", "members"]},
    "rival": {"labels": ["rivals"], "words": ["rival", "rivals", "rivalry"]},
}  # fmt: skip
"""The kin and social relations every domain's ``relation_vocabulary`` extends (English, language-level).

The one source of the kin words: the evidence validator's one-endpoint rule
reads them through ``relation_vocabulary.domain_relation_vocabulary`` (a
domain's declared entries add labels and words to these, relation by
relation), and the name rule's descriptor words are their words plus the role
words (:data:`UNIVERSAL_RELATION_WORDS`). A domain declares only its genre's
extensions (the literary domain's godparents and betrothals). The kinship
vocatives of the title floor (:data:`UNIVERSAL_TITLE_WORDS`: ``mamma``,
``maman``, ``auntie``, ``grandpapa``) are relation words here too, every
spelling: a bare one in a text states its relation and never names the one
holder of the title (:attr:`NameConventions.naming_titles` leaves relation
words out); until 2026-10-11 only ``papa``, ``mama``, ``uncle`` and ``aunt``
were (``mamma`` and ``maman`` were the literary and biographical domains' own
entries), and ``Grandpapa laughed`` named the chunk's one ``Grandpapa Ilyá``.
Hyphenated words are one word (``sister-in-law`` is not ``sister``). A word that
mostly says something else is no evidence of the relation: "relation" ("in
relation to") and "connection" ("in connection with") left the kin entry in
audit wave 5, as "engaged" ("engaged in conversation") and "promised" ("promised
him a visit") left the literary betrothal entry for the phrases "engaged to" and
"promised in marriage". A word inside a phrase of another relation is that
relation's ("marriage" of "proposed marriage" states a betrothal, not a
marriage: ``RelationVocabulary.states_relation``). Membership and rivalry joined
in audit wave 6: the literary ``member_of`` and ``rivals`` had been marked
two-endpoints-only for want of an entry, and two true edges on gold paths
dropped ("as a member of the household", "the rivals recognized one another");
replayed on v3, the two words bring exactly those two edges back and no false
one.
"""

UNIVERSAL_ROLE_WORDS = frozenset(
    {
        "servant", "valet", "maid", "steward", "secretary", "nurse", "tutor", "governess",
        "companion", "orderly",
    }
)  # fmt: skip
"""Words naming a role one person has to another, for every domain (a domain's ``role_words`` add to them)."""


def vocabulary_words(vocabulary: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """Every word of a ``relation_vocabulary`` mapping, in declaration order."""
    return [str(w) for entry in vocabulary.values() for w in (entry or {}).get("words") or []]


UNIVERSAL_RELATION_WORDS = frozenset(
    w
    for w in (
        fold(x) for x in [*vocabulary_words(UNIVERSAL_RELATION_VOCABULARY), *UNIVERSAL_ROLE_WORDS]
    )
    if w
)
"""Relation and role words when a domain declares none: ``the mother of X``, ``the valet of X``.

The words of :data:`UNIVERSAL_RELATION_VOCABULARY` and :data:`UNIVERSAL_ROLE_WORDS`
(folded). ``the mother of X`` (a relation or role word, ``of``, a name) is a
descriptor; a possessive (``X's mother``, ``X's envoy``) is one whatever word
follows it (:func:`descriptor_possessor`)."""

UNIVERSAL_TITLE_EQUIVALENTS: tuple[frozenset[str], ...] = (
    frozenset({"mr", "mister", "monsieur"}),
    frozenset({"mrs", "madam", "madame", "mme"}),
    frozenset({"miss", "mademoiselle", "mlle"}),
    frozenset({"dr", "doctor"}),
    frozenset({"duke", "duc"}),
    frozenset({"viscount", "vicomte"}),
    frozenset({"mamma", "mama", "maman"}),
    frozenset({"aunt", "auntie"}),
    frozenset({"grandmamma", "grandmama"}),
)
"""Spellings of one title in English and French (folded): every domain's ``title_equivalents``
add to these (a group sharing a spelling with one of these is one group with it)."""

UNIVERSAL_PERSON_TYPES = frozenset({"person"})
"""Entity types every domain's names of a person include (folded), beside its own marked ones.

The core type Person (spec 2026-10-07 § 6): a type is read as its core type
(:func:`~chaoscypher_core.services.sources.engine.deduplication.similarity_matcher.core_type_key`),
so a Person of any subtype, and an old snapshot's ``Character``, names a
person. Until 2026-10-07 it was ``{person, character}`` and each domain's
marked template names."""

Gender = Literal["f", "m"]
"""A name's gender as its title or form says it."""


@dataclass(frozen=True)
class NameForm:
    """A word ending that marks a name's form: the stem both forms fold to, and the gender it says.

    ``ending`` is replaced by ``stem_ending`` to give the family stem
    (``rostova`` -> ``rostov``); ``gender`` is what the ending says of the
    bearer, or None when the form is the unmarked base.
    """

    ending: str
    stem_ending: str
    gender: Gender | None


MIN_STEM = 2
"""Letters a word keeps before its ending for the ending to count (``Lvóvna`` is, ``Nina`` is not)."""


@dataclass(frozen=True)
class NameTables:
    """The naming tables one member of a stack declares: the floor's, or one pack's.

    What a domain's ``.jsonld`` says (``domain_resolver.name_tables_of_domain``
    reads it), nothing derived: an empty table declares nothing.
    :meth:`NameConventions.build` unions the universal tables with every
    member's, so the conventions of the floor plus any packs are one call.
    A pack cannot contradict the floor (spec 2026-10-07 § 2): the union
    resolves no conflict, and where two members map one word (a generation
    word) the first member's, the floor's, is kept.
    """

    titles: Sequence[str] = ()
    """Words a name may add or drop without naming anyone else (``title_words``)."""
    feminine_titles: Sequence[str] = ()
    """Titles that say a woman."""
    masculine_titles: Sequence[str] = ()
    """Titles that say a man."""
    generation_words: Mapping[str, str] | None = None
    """Word -> generation (``old`` -> ``elder``)."""
    name_forms: Mapping[str, Sequence[NameForm | Mapping[str, Any]]] | None = None
    """Language -> its name-form endings (a :class:`NameForm` or the ``.jsonld`` mapping)."""
    relation_words: Sequence[str] = ()
    """Words naming a relation or a role (the member's relation vocabulary words and role words)."""
    title_equivalents: Sequence[Sequence[str]] = ()
    """Groups of spellings of one title."""
    person_types: Sequence[str] | None = None
    """Entity types whose names name a person (the member's marked templates and subtypes)."""


@dataclass(frozen=True)
class NameConventions:
    """The knowledge tables the name rule reads: one domain's naming conventions.

    The rule itself is generic; what a title is, which titles and name
    endings say a gender, and which words say a generation are the domain's
    (``title_words``, ``feminine_titles``, ``masculine_titles``,
    ``generation_words`` and ``name_forms`` in its ``.jsonld``), read once by
    ``domain_resolver.domain_name_conventions``. Build one with
    :meth:`build`, which starts the titles from the universal ones
    (:data:`UNIVERSAL_TITLE_WORDS`) and falls back table by table for the
    rest (:data:`UNIVERSAL_FEMININE_TITLES`,
    :data:`UNIVERSAL_MASCULINE_TITLES`, :data:`UNIVERSAL_GENERATION_WORDS`,
    and no name forms). Hashable, so it can key a cache.
    """

    titles: frozenset[str]
    """Words a name may add or drop without naming anyone else (lowercase; folded when compared)."""
    feminine_titles: frozenset[str]
    """Titles that say a woman (folded spelling)."""
    masculine_titles: frozenset[str]
    """Titles that say a man (folded spelling)."""
    generation_words: tuple[tuple[str, str], ...]
    """``(folded word, generation)`` pairs: which of two same-named people is meant."""
    name_forms: tuple[tuple[str, tuple[NameForm, ...]], ...]
    """``(language, forms)`` pairs, folded spelling; every language's forms apply."""
    relation_words: frozenset[str] = UNIVERSAL_RELATION_WORDS
    """Words naming a relation or a role (folded): the domain's ``relation_vocabulary`` words and ``role_words``."""
    title_equivalents: tuple[frozenset[str], ...] = UNIVERSAL_TITLE_EQUIVALENTS
    """Groups of folded titles that are one title spelled differently (``mme``, ``madame``)."""
    given_name_titles: frozenset[str] = UNIVERSAL_GIVEN_NAME_TITLES
    """The floor's men's titles that may be a given name here (:data:`UNIVERSAL_GIVEN_NAME_TITLES`
    less the ones the domain declares a title of its own), folded."""
    person_types: frozenset[str] | None = None
    """Entity types (folded) whose names name a person; None (no domain) is every type.

    Only a person's name can name its bearer through someone else's
    (``Natásha's mother``): a possessive in another type's name is part of
    the name (``Caesar's Commentaries``, a work, keeps ``Caesar``). A
    domain's are its node templates marked ``"names_a_person": true`` and
    :data:`UNIVERSAL_PERSON_TYPES`; a domain that marks none is the
    universal table (every type), like every other table it leaves empty."""

    def is_person(self, entity_type: str | None) -> bool:
        """Whether a name of ``entity_type`` names a person (an untyped name is taken to).

        The type is read as its core type (``core_type_key``): ``Person``, and
        an old snapshot's ``Character`` or ``Historical Figure``, are; a
        ``Work`` of any subtype is not.
        """
        if self.person_types is None or not entity_type:
            return True
        return fold(core_type_key(entity_type)) in self.person_types

    @classmethod
    def build(
        cls,
        *members: NameTables,
        given_name_titles: Iterable[str] | None = None,
        **tables: Any,
    ) -> NameConventions:
        """Conventions from the members' tables (the floor's, then each pack's) over the universal ones.

        ``build(floor, *packs)``: every member's tables are unioned, and the
        universal tables are the floor under all of them. ``tables`` given as
        keywords (``titles=``, ``feminine_titles=``, ... the fields of
        :class:`NameTables`) are one more member, the form for a single set
        of tables (:meth:`from_dict`, a test's own conventions).

        The universal tables are never replaced: the members' ``titles``,
        gendered titles and title equivalents are unioned with
        :data:`UNIVERSAL_TITLE_WORDS`, :data:`UNIVERSAL_FEMININE_TITLES`,
        :data:`UNIVERSAL_MASCULINE_TITLES` and
        :data:`UNIVERSAL_TITLE_EQUIVALENTS` (the language-level tables every
        domain starts from); the generation words, the relation words and the
        person types are the union of the members that declare them, and the
        universal ones when none does. Name forms are unioned language by
        language, each form once.

        The title set always holds the gendered titles and the generation
        words (folded): a word that says a gender or a generation is never a
        naming word. Every word is folded except the members' own titles,
        which the rule folds when it compares (a caller's own lookups keep
        their spelling). ``given_name_titles`` is
        :data:`UNIVERSAL_GIVEN_NAME_TITLES` less every member's own
        ``titles`` and ``masculine_titles``; given (a plan's JSON,
        :meth:`from_dict`), it is taken as it is.
        """
        if tables:
            members = (*members, NameTables(**tables))
        own_titles = [t for m in members for t in m.titles or ()]
        own_masculine = [t for m in members for t in m.masculine_titles or ()]
        forms_by_lang: dict[str, dict[NameForm, None]] = {}
        for member in members:
            for lang, table in (member.name_forms or {}).items():
                slot = forms_by_lang.setdefault(str(lang), {})
                slot.update(dict.fromkeys(_name_form_of(f) for f in table))
        forms = tuple((lang, tuple(forms_by_lang[lang])) for lang in sorted(forms_by_lang))
        declared_generation: dict[str, str] = {}
        for member in members:
            for word, gen in (member.generation_words or {}).items():
                declared_generation.setdefault(word, gen)
        generation = declared_generation or UNIVERSAL_GENERATION_WORDS
        # The floor's gendered titles always say their gender; the members' add to them.
        feminine = UNIVERSAL_FEMININE_TITLES | frozenset(
            fold(t) for m in members for t in m.feminine_titles or ()
        )
        masculine = UNIVERSAL_MASCULINE_TITLES | frozenset(fold(t) for t in own_masculine)
        generation_pairs = tuple(sorted((fold(w), g) for w, g in generation.items()))
        # The universal title words are the floor: the members' ``title_words`` add to them.
        base_titles = UNIVERSAL_TITLE_WORDS | frozenset(t.lower() for t in own_titles)
        relation = frozenset(
            w for w in (fold(x) for m in members for x in m.relation_words or ()) if w
        )
        declared_person_types = [t for m in members for t in m.person_types or ()]
        return cls(
            # A gendered title or a generation word is a title word whatever the members'
            # ``title_words`` lists say: it names no one (``Mister``, ``Jr``, ``Vicomte``).
            titles=base_titles | feminine | masculine | {w for w, _ in generation_pairs},
            feminine_titles=feminine,
            masculine_titles=masculine,
            generation_words=generation_pairs,
            name_forms=tuple((lang, table) for lang, table in forms if table),
            relation_words=relation or UNIVERSAL_RELATION_WORDS,
            title_equivalents=_merged_groups(
                [
                    *UNIVERSAL_TITLE_EQUIVALENTS,
                    *(
                        frozenset(fold(t) for t in grp)
                        for m in members
                        for grp in m.title_equivalents or ()
                    ),
                ]
            ),
            person_types=frozenset(fold(t) for t in declared_person_types) | UNIVERSAL_PERSON_TYPES
            if declared_person_types
            else None,
            given_name_titles=frozenset(fold(t) for t in given_name_titles)
            if given_name_titles is not None
            else UNIVERSAL_GIVEN_NAME_TITLES - {fold(t) for t in (*own_titles, *own_masculine)},
        )

    @cached_property
    def generation(self) -> dict[str, str]:
        """:attr:`generation_words` as a lookup."""
        return dict(self.generation_words)

    @cached_property
    def forms(self) -> tuple[NameForm, ...]:
        """Every language's forms, longest ending first."""
        return tuple(
            sorted((f for _, table in self.name_forms for f in table), key=lambda f: -len(f.ending))
        )

    @cached_property
    def gendered_titles(self) -> frozenset[str]:
        """Every title that says a gender (folded)."""
        return self.feminine_titles | self.masculine_titles

    @cached_property
    def title_class(self) -> dict[str, str]:
        """Each spelling of an equivalent title and the one spelling it compares as."""
        return {t: min(group) for group in self.title_equivalents for t in group}

    @cached_property
    def not_given(self) -> frozenset[str]:
        """Words never a given name, whatever the title words leave in a name (``Mlle``)."""
        return self.feminine_titles | self.masculine_titles | frozenset(self.generation)

    @cached_property
    def naming_titles(self) -> frozenset[str]:
        """Titles that alone may name someone in a text (``the prince``, ``Monsieur``), one spelling each.

        The gendered titles (folded, :attr:`title_class` spelling), but never
        a word that names a relation (``mother``, ``brother``: the domain's
        relation words) nor an article or a particle: the evidence
        validator's title rung reads these (a bare title names the one
        candidate that holds it: :meth:`NameIndex.sole_title_holder`).
        """
        return frozenset(
            self.title_class.get(t, t)
            for t in self.gendered_titles
            if t not in self.relation_words and t not in ARTICLES
        )

    @classmethod
    def from_dict(cls, tables: Mapping[str, Any]) -> NameConventions:
        """The conventions :meth:`as_dict` wrote (a plan's JSON): the inverse, table for table."""
        forms = {
            str(lang): [{"ending": e, "stem": st, "gender": g} for e, st, g in table]
            for lang, table in (tables.get("name_forms") or {}).items()
        }
        return cls.build(
            titles=tables.get("titles") or None,
            feminine_titles=tables.get("feminine_titles") or None,
            masculine_titles=tables.get("masculine_titles") or None,
            generation_words={str(w): str(g) for w, g in tables.get("generation_words") or []}
            or None,
            name_forms=forms or None,
            relation_words=tables.get("relation_words") or None,
            title_equivalents=tables.get("title_equivalents") or None,
            person_types=tables.get("person_types"),
            given_name_titles=tables.get("given_name_titles"),
        )

    def as_dict(self) -> dict[str, Any]:
        """Every table as sorted plain JSON values (what a recipe hashes)."""
        return {
            "titles": sorted({fold(t) for t in self.titles}),
            "feminine_titles": sorted(self.feminine_titles),
            "masculine_titles": sorted(self.masculine_titles),
            "generation_words": [list(p) for p in self.generation_words],
            "name_forms": {
                lang: [[f.ending, f.stem_ending, f.gender] for f in table]
                for lang, table in self.name_forms
            },
            "relation_words": sorted(self.relation_words),
            "title_equivalents": [sorted(g) for g in self.title_equivalents],
            "person_types": None if self.person_types is None else sorted(self.person_types),
            "given_name_titles": sorted(self.given_name_titles),
        }


def _merged_groups(groups: Iterable[frozenset[str]]) -> tuple[frozenset[str], ...]:
    """Groups of one title's spellings, two sharing a spelling joined into one, sorted."""
    merged: list[set[str]] = []
    for group in groups:
        if len(group) < 2:
            continue
        joined = set(group)
        for other in [m for m in merged if m & joined]:
            joined |= other
            merged.remove(other)
        merged.append(joined)
    return tuple(sorted((frozenset(m) for m in merged), key=sorted))


def _name_form_of(spec: NameForm | Mapping[str, Any]) -> NameForm:
    """A :class:`NameForm` from itself or from its ``{"ending", "stem", "gender"}`` mapping (folded)."""
    if isinstance(spec, NameForm):
        return spec
    gender = spec.get("gender")
    return NameForm(
        fold(str(spec["ending"])),
        fold(str(spec.get("stem", ""))),
        gender if gender in ("f", "m") else None,
    )


def as_conventions(
    conventions: NameConventions | None = None, titles: Iterable[str] | None = None
) -> NameConventions:
    """The conventions a rule entry point runs with: the ones given, else built from ``titles``.

    ``titles`` alone is the convenience form (a domain's title words, every
    other table universal); passing both is a mistake that would silently
    drop one of them.

    Raises:
        ValidationError: When both are given.
    """
    if conventions is not None:
        if titles is not None:
            msg = "pass conventions or titles, not both"
            raise ValidationError(msg)
        return conventions
    if titles is None:
        return DEFAULT_NAME_CONVENTIONS
    return _conventions_for_titles(titles if isinstance(titles, frozenset) else frozenset(titles))


@lru_cache(maxsize=64)
def _conventions_for_titles(titles: frozenset[str]) -> NameConventions:
    """:func:`as_conventions` for a title set (cached: callers pass the same few)."""
    return NameConventions.build(titles=titles)


GIVEN_ALIASES_KEY = "aliases_given"
"""Entity (and stored node property) key: the aliases given under the entity's own name.

Stamped on every extracted mention at aggregation (the aliases its own text
writes and its output lists as no other entity: :func:`evidence_aliases`)
and kept by ``EntityProcessor.merge_entities``; absent on an entity neither
touched (a node a person wrote), whose ``aliases`` are then all given. Merges grow ``aliases`` with
the names and aliases of every entity folded in; only the given ones are
evidence that a later name is this entity (an alias picked up from a
merged-in name is that other name's word, not this one's). Written onto
every entity by ``publish_given_aliases`` when deduplication ends, kept on
the committed node's properties, and read back by the post-commit identity
resolver, so both passes judge a name by the same aliases.
"""


EvidenceKind = Literal["same", "alias", "subset"]
"""How two names were found to be one entity."""

_STRENGTH = {"same_name": 0, "alias": 1, "same_words": 2, "subset": 3}
"""Join order in :func:`plan_name_pairs`: the strongest evidence first."""


@lru_cache(maxsize=64)
def _folded_titles(titles: frozenset[str]) -> frozenset[str]:
    """``titles`` in folded spelling (cached: callers pass the same few sets)."""
    return frozenset(fold(t) for t in titles)


def _title_set(titles: Iterable[str]) -> frozenset[str]:
    """``titles`` folded, as a frozenset."""
    return _folded_titles(titles if isinstance(titles, frozenset) else frozenset(titles))


DEFAULT_NAME_CONVENTIONS = NameConventions.build()
"""The universal conventions: what the rule runs with when no domain names its own."""


_ANY_POSITION = UNIVERSAL_PARTICLES | frozenset(UNIVERSAL_GENERATION_WORDS)
"""Title words that are never a naming word, wherever they stand: particles and generation words.

``Ludwig Van Beethoven`` and ``Martin Luther King Jr.``: a particle or a
generation word after a naming word, capitalised or not, is no naming word
(:func:`name_parts`)."""


def name_parts(text: str, titles: frozenset[str]) -> list[tuple[str, str, bool]]:
    """Each folded word of a name's ``text``, the token it was written in, and whether it names its bearer.

    The one reading of which words of a name are its titles. A word of
    ``titles`` (folded) is a title only in title position: before the first
    naming word written capitalised (``Prince Andrew``, ``the old Count
    Rostóv``, ``the young Princess Bolkónskaya`` under a domain where
    ``young`` is no title) or written lowercase after it (``Anna, the
    countess``). After a capitalised naming word a capitalised title word is
    a naming word: ``John Major``, ``Carole King``,
    ``Stephen King`` and ``Martin Luther King`` keep their surnames, and
    ``Major`` and ``King`` say no rank and no gender there. Particles and
    generation words are never naming words (:data:`_ANY_POSITION`). Until
    audit wave 8 a title word was a title in any position, so under the
    universal floor ``John Major`` joined ``John Smith``, ``Carole King``
    ``Carole Lombard`` and ``Stephen King`` ``Stephen Fry``.

    Every caller of the name rule reads a name through this (:func:`name_words`,
    the survivor ranking, the given names and the rule's profile of a record).
    """
    out: list[tuple[str, str, bool]] = []
    named = False
    for token in _tokens(text):
        first = next((c for c in token if c.isalpha()), "")
        lowercase = first.islower()
        for word in fold(token).split():
            naming = word not in titles or (named and not lowercase and word not in _ANY_POSITION)
            out.append((word, token, naming))
            named = named or (naming and not lowercase)
    return out


def alias_as_name(alias: str, titles: frozenset[str]) -> str:
    """``alias`` as the name it writes: its lowercase naming words left out, titles and particles kept.

    An alias is read for the name in it: ``the famous Prince Andrew`` writes
    ``Prince Andrew``, ``prodigal son Anatole`` writes ``Anatole``, ``our dear
    Wintzingerode`` writes ``Wintzingerode`` and ``our Emperor`` the bare title
    ``Emperor`` (no name at all). A lowercase word that :func:`name_parts` reads
    as naming is an epithet in an alias, never a word of a fuller name that
    points at someone else (:func:`_fuller_alias_elsewhere`), nor a name of its
    own (``our`` joined ``our Emperor`` to ``our dear Wintzingerode`` in the v4
    generic arm). Titles (``the young Princess Bolkónskaya``) and words of a
    script without case are kept. ``titles`` are folded. Until 2026-10-07
    (commit 929853a51 said so, but the reading still kept the lowercase words)
    every alias carrying an epithet withheld the merge of its record with the
    fuller name it is short for.
    """
    tokens = _tokens(alias)
    parts = iter(name_parts(alias, titles))
    kept: list[str] = []
    for token in tokens:
        words = [next(parts) for _ in fold(token).split()]
        if words and all(naming for _, _, naming in words) and token[:1].isalpha():
            first = next((c for c in token if c.isalpha()), "")
            if first.islower():
                continue
        kept.append(token)
    return " ".join(kept)


def name_words(label: str, titles: Iterable[str] | None = None) -> set[str]:
    """The words of a name that name its bearer: titles (in title position) and particles left out.

    ``titles`` are compared in folded form, so ``abbé`` and ``abbe`` both drop;
    None is the universal conventions' (:data:`DEFAULT_NAME_CONVENTIONS`: the
    one universal title table and the universal gendered and generation words).
    A title word after a naming word, capitalised, names (``John Major``:
    :func:`name_parts`). A possessive and the words before it name someone
    else (``Kutúzov's envoy`` is named by ``envoy``, never by ``Kutúzov``:
    :func:`bearer_text`). Words keep their spelling (see
    :func:`identity_words` for the folded family stems the rule compares).
    """
    if is_bare_role(label):
        return set()
    skip = _title_set(DEFAULT_NAME_CONVENTIONS.titles if titles is None else titles)
    return {w for w, _, naming in name_parts(bearer_text(label), skip) if naming}


ATTESTED_NAMES_KEY = "attested_names"
"""Entity (and stored node property) key: the entity's names written verbatim in the text it was read from.

Stamped at aggregation (``orchestration.aggregate_chunk_results``: the names
and aliases of each extracted entity found, case and accents as written, in
its extraction group's text), unioned by every merge, stored by the commit
and read back by the resolver: the survivor ranking prefers a spelling the
corpus writes (:func:`survivor_key`)."""


_WORD_JOINERS = "'\u2019\\-\u2010\u2011"
"""Marks inside one token: an apostrophe (``d'Enghien``) and the hyphens (``Anne-Marie``)."""


@lru_cache(maxsize=4096)
def whole_token_pattern(name: str) -> re.Pattern[str]:
    """``name`` written as whole tokens: the one test of "this spelling is written".

    A token is what the fold reads as one word: letters and digits joined by
    an apostrophe or a hyphen (``d'Enghien``, ``Anne-Marie``), a possessive
    ``'s`` left off (``Pierre's`` writes ``Pierre``). ``Artagnan`` is not
    written by ``D'Artagnan`` nor ``Marie`` by ``Anne-Marie`` (a substring of
    a token), ``Anna Pávlovna`` not by ``Anna Pávlovnas``; ``Pierre`` is by
    ``Pierre's`` and ``the Rostóvs'`` writes ``Rostóvs``. Case and accents
    are compared as given (callers normalise both sides to NFC). The
    survivor ranking's attestation (:func:`attested_names`) reads this; a
    name a text *names* is the evidence validator's question, which also
    reads the part after an elision or a hyphen (audit wave 7).
    """
    joiners = _WORD_JOINERS
    return re.compile(
        rf"(?<![^\W_])(?<![^\W_][{joiners}])"
        + re.escape(name)
        + rf"(?![^\W_])(?![{joiners}](?!s(?![^\W_]))[^\W_])"
    )


def attested_names(names: Iterable[str], texts: Iterable[str]) -> list[str]:
    """The ``names`` written verbatim in one of ``texts``, in ``names`` order, each once.

    Verbatim: case and accents as written (NFC on both sides), as whole
    tokens (:func:`whole_token_pattern`: ``Anna Pávlovna`` is not attested by
    ``Anna Pávlovnas``, nor ``Artagnan`` by ``D'Artagnan``); an unaccented
    spelling the extractor invented (``Anna Pavlovna Scherer``) is not
    attested by a text that writes ``Anna Pávlovna Schérer``.
    """
    import unicodedata as _ud

    blob = "\x00".join(_ud.normalize("NFC", t) for t in texts if t)
    out: list[str] = []
    for name in names:
        n = _ud.normalize("NFC", str(name or "")).strip()
        if not n or n in out:
            continue
        if whole_token_pattern(n).search(blob):
            out.append(n)
    return out


def evidence_aliases(
    aliases: Iterable[str], attested: Iterable[str], others: Iterable[str] = ()
) -> list[str]:
    """The aliases one mention gives as evidence of who it is: written in its own text, and no one else.

    ``attested`` are the mention's names its own text writes
    (:func:`attested_names` over its extraction group's text, the
    :data:`ATTESTED_NAMES_KEY` stamp); ``others`` are the names of the other
    entities the same extraction output lists. An alias is evidence only when

    * its own text writes it (verbatim, whole words): the extractor read it
      there, it did not supply it (``Anatole Kurágin`` given to ``Anatole``
      in a chunk that writes only ``Anatole``, ``Person`` written into the
      alias field); and
    * the same output does not list it as an entity of its own: the extractor
      that read ``Natásha`` and the doll ``Mimi`` as two entities of one text
      has said ``Mimi`` is not Natásha, whatever alias it wrote beside her.

    Any other alias stays on the entity (a hint for search and display) but
    is not given (:data:`GIVEN_ALIASES_KEY`), so no merge step reads it as
    evidence. Stamped once per extraction group at aggregation
    (``orchestration``), the one place the evidence of an alias is decided;
    every merge step and the resolver read it through
    :func:`record_given_aliases`. Until 2026-10-07 every alias the extractor
    wrote was evidence: the v4 literary arm merged the doll into Natásha.
    """
    import unicodedata as _ud

    written = {_ud.normalize("NFC", str(n)) for n in attested if n}
    elsewhere = {fold(str(n)) for n in others if n}
    out: list[str] = []
    for alias in aliases:
        text = str(alias or "").strip()
        if not text or text in out:
            continue
        if _ud.normalize("NFC", text) in written and fold(text) not in elsewhere:
            out.append(text)
    return out


def record_attested_names(item: Mapping[str, Any]) -> frozenset[str]:
    """The attested names of an entity or a committed node (on it or in its properties), NFC."""
    import unicodedata as _ud

    raw = item.get(ATTESTED_NAMES_KEY)
    if raw is None:
        raw = (item.get("properties") or {}).get(ATTESTED_NAMES_KEY)
    if not isinstance(raw, (list, tuple, set, frozenset)):
        return frozenset()
    return frozenset(_ud.normalize("NFC", str(n)) for n in raw if n)


def _accented_letters(label: str) -> int:
    """Letters of ``label`` written beyond ASCII (accents kept: ``Pávlovna`` 1, ``Pavlovna`` 0)."""
    return sum(1 for c in label if c.isalpha() and not c.isascii())


def survivor_key(
    label: str,
    conventions: NameConventions,
    *,
    own: bool = True,
    attested: bool = False,
    support: int = 0,
) -> tuple[int, ...]:
    """Who of a merge group keeps its label: the higher key wins. The one survivor ranking.

    The exact deduplication, the semantic and the hierarchical steps and the
    post-commit resolver all pick the survivor by this key
    (:func:`pick_survivor`), so a group keeps one label whichever pass
    merged it. In order:

    1. a name over a reference (:func:`reference_key`: a descriptor such as
       ``Kutúzov's envoy``, a bare title or role such as ``the count`` never
       survives a name; until 2026-10-07 only a descriptor ranked here, so a
       bare title the corpus writes could out-rank an unattested name on
       ``attested`` and a given one on ``own``);
    2. ``own``: the entity's own name over a given alias (``Natásha``,
       given ``Natalie``, keeps the label over a mention that writes only
       ``Natalie``: :func:`pick_survivor` says which members are a given
       alias);
    3. ``attested``: the spelling the corpus writes (:data:`ATTESTED_NAMES_KEY`)
       over one it does not (``Anna Pávlovna Schérer`` over the extractor's
       ``Anna Pavlovna Scherer``);
    4. the fuller name: the most naming words written capitalised, then the
       fewest written lowercase (an epithet such as ``prodigal son Anatole``
       is no fuller name than ``Anatole``);
    5. ``support``: the name written by more mentions (or, for a node, the
       better connected): :func:`pick_survivor` passes the support of every
       member written under the same name (:func:`fold`), so two names
       otherwise alike go to the one the text uses (``Pierre``, 26 mentions,
       over ``Pétya``, one);
    6. the longer label (``Prince Andrew`` over ``Andrew`` written as often);
    7. accents kept: more letters beyond ASCII over a stripped spelling.

    Accents are last because they only tell two spellings of one name apart
    (``Anna Pávlovna Schérer`` and ``Anna Pavlovna Scherer`` fold alike and
    tie on every rank above): between two names they say nothing. Until
    2026-10-07 they ranked fourth, above the fuller name and support, so the
    one ``Pétya`` mention (an accent) took the label from the 26 ``Pierre``
    mentions the alias step had joined it to, and the protagonist of the v4
    War and Peace packs had no node of his own. Never the codepoint order of
    two spellings, nor the order the mentions came in (until audit wave 6 ties
    fell to codepoint order, which prefers an unaccented ``a`` to ``á``: v3's
    Anna Pávlovna became "Anna Pavlovna Scherer", and the hierarchical step
    and the resolver ranked by naming-word count, so "prodigal son Anatole"
    beat "Anatole"). A caller breaks a tie left after all seven only to make
    its order total.
    """
    skip = _title_set(conventions.titles)
    naming = list(dict.fromkeys(token for _, token, n in name_parts(bearer_text(label), skip) if n))
    capitalised = sum(1 for t in naming if t[:1].isupper())
    lowercase = sum(1 for t in naming if t[:1].islower())
    return (
        0 if reference_key(label, conventions) is not None else 1,
        1 if own else 0,
        1 if attested else 0,
        capitalised,
        -lowercase,
        support,
        len(label),
        _accented_letters(label),
    )


def record_support(item: Mapping[str, Any]) -> int:
    """The mentions behind an entity: the extraction groups it was read from (at least one).

    ``source_chunk_indices`` (every merge unions them); a mention read from
    one group, or a bare record, is one. What every deduplication
    step passes the survivor ranking as ``support`` (:func:`survivor_candidate`);
    the resolver passes a node's degree instead. Until 2026-10-07 the steps
    passed how many members of the group wrote the same label, which after the
    exact step is one for every member: support never decided a survivor in
    the semantic or the hierarchical step.
    """
    chunks = item.get("source_chunk_indices")
    if isinstance(chunks, (list, tuple, set, frozenset)) and chunks:
        return len({str(c) for c in chunks})
    return 1


@dataclass(frozen=True)
class SurvivorCandidate:
    """One member of a merge group as the survivor ranking reads it."""

    label: str
    given: tuple[str, ...] = ()
    """The aliases given under this member's name (:func:`record_given_aliases`)."""
    attested: frozenset[str] = frozenset()
    """This member's attested names (:func:`record_attested_names`)."""
    support: int = 0
    """Mentions behind this member (:func:`record_support`), or a node's degree."""
    tie: str = ""
    """What orders two members equal on every rank: only to make the order total."""


def survivor_candidate(
    item: Mapping[str, Any], *, support: int | None = None, tie: str | None = None
) -> SurvivorCandidate:
    """An entity or a node (``name`` or ``label``) as a :class:`SurvivorCandidate`.

    ``support`` defaults to the mentions behind the entity (:func:`record_support`):
    every deduplication step reads it here; the resolver passes a node's degree.
    """
    label = str(item.get("name") or item.get("label") or "").strip()
    return SurvivorCandidate(
        label=label,
        given=tuple(record_given_aliases(item)),
        attested=record_attested_names(item),
        support=record_support(item) if support is None else support,
        tie=label if tie is None else tie,
    )


def pick_survivor(candidates: Sequence[SurvivorCandidate], conventions: NameConventions) -> int:
    """The index of the member of a merge group that keeps its label (:func:`survivor_key`).

    A member is a given alias, not a name of its own, when it gives no alias
    itself and another member was given its name as an alias: a mention that
    writes only what another mention offered as an alias (``Pierre Bezukhov``
    or ``Monsieur Pierre`` beside ``Pierre``, who gave both). A member that
    gives aliases of its own is a name of its own whoever else lists it
    (``Mlle Schérer`` giving ``Anna Pávlovna Schérer`` does not make
    the hostess's own full name an alias of hers). A name is attested when
    any member's attested names hold it (the group's mentions are one
    entity's, read from all their texts). A name's support is the support
    of every member written under it (the same :func:`fold`): the exact
    step's members are single mentions, so ``Pierre`` written by 26 of them
    has 26 against the one of ``Pétya``.
    """
    import unicodedata as _ud

    given_by: dict[str, set[int]] = defaultdict(set)
    written_by: dict[str, int] = defaultdict(int)
    for k, c in enumerate(candidates):
        for alias in c.given:
            given_by[fold(alias)].add(k)
        written_by[fold(c.label)] += c.support
    attested = frozenset().union(*(c.attested for c in candidates))

    def _key(k: int) -> tuple[Any, ...]:
        """The ranking of member ``k``, then its tie value."""
        c = candidates[k]
        own = bool(c.given) or not (given_by.get(fold(c.label), set()) - {k})
        written = _ud.normalize("NFC", c.label) in attested
        support = written_by[fold(c.label)]
        rank = survivor_key(c.label, conventions, own=own, attested=written, support=support)
        return (rank, c.tie)

    best = 0
    for k in range(1, len(candidates)):
        if _key(k) > _key(best):
            best = k
    return best


def name_form(
    word: str, conventions: NameConventions = DEFAULT_NAME_CONVENTIONS
) -> NameForm | None:
    """The :class:`NameForm` of ``conventions`` a folded word ends in, or None."""
    for form in conventions.forms:
        if word.endswith(form.ending) and len(word) - len(form.ending) >= MIN_STEM:
            return form
    return None


def identity_word(word: str, conventions: NameConventions = DEFAULT_NAME_CONVENTIONS) -> str:
    """A folded naming word as its family stem (under Russian forms ``rostova`` -> ``rostov``)."""
    form = name_form(word, conventions)
    return word if form is None else word[: len(word) - len(form.ending)] + form.stem_ending


def surname_key(
    word: str, conventions: NameConventions = DEFAULT_NAME_CONVENTIONS
) -> tuple[str, bool]:
    """A folded naming word as the family stem it names and whether it says a woman by its form.

    ``("dolokhov", True)`` for ``dolokhova``, ``("dolokhov", False)`` for
    ``dolokhov``: a feminine surname form (a :class:`NameForm` of gender
    ``f`` that changes its ending; a patronymic keeps its ending and is no
    family name) against every other spelling of the stem. The one reading
    of a surname's form: :func:`names_person` compares these, the name rule's
    family-form check (:func:`_forms_clash`) and the evidence validator's
    naming words both read them.
    """
    form = name_form(word, conventions)
    feminine = form is not None and form.gender == "f" and form.stem_ending != form.ending
    return identity_word(word, conventions), feminine


def names_person(
    word: str, name_word: str, conventions: NameConventions = DEFAULT_NAME_CONVENTIONS
) -> bool:
    """A folded word written in a text (or another name) names a bearer of the naming word ``name_word``.

    One family stem in one surname form (:func:`surname_key`): ``Dólokhova``
    names Márya Ivánovna Dólokhova, never her son ``Dólokhov``, and
    ``Drubetskóy`` names Borís, never Princess ``Drubetskáya``. The one
    gender-aware surname predicate: the deduplication keeps two names apart
    on it (:func:`_forms_clash`: ``Márya Ivánovna Dólokhova`` holds no
    ``Dólokhov``) and the evidence validator names an entity by it (until
    audit wave 7 the validator compared the stems alone, so "Márya Ivánovna
    Dólokhova" in a sentence named Dólokhov and kept "Dólokhov located_in
    Moyka Canal", q114). A family name in the plural names any member
    (``the Rostóvs`` names the countess): that is the validator's reading
    of a plural, not a form of this predicate.
    """
    return surname_key(word, conventions) == surname_key(name_word, conventions)


def identity_words(
    label: str, conventions: NameConventions = DEFAULT_NAME_CONVENTIONS
) -> frozenset[str]:
    """The naming words of ``label`` as family stems: what the rule compares."""
    return frozenset(identity_word(w, conventions) for w in name_words(label, conventions.titles))


_POSSESSIVE = re.compile(r"^(?P<owner>.+?)(?:['\u2019]s|s['\u2019])$")
"""A token written in the possessive: ``Natásha's``, ``Kurágins'``, ``Rostóv`` + U+2019 + ``s``.

A bare trailing apostrophe after another letter is no possessive (``O'`` of
``O' Reilly``, ``d'`` of ``d' Enghien``: audit wave 5)."""

_TOKEN_EDGES = '.,;:!?()"\u201c\u201d'
"""Punctuation stripped from a label's tokens before the possessive test."""


def _tokens(label: str) -> list[str]:
    """``label``'s whitespace tokens, edge punctuation stripped, empty ones dropped."""
    return [t for t in (t.strip(_TOKEN_EDGES) for t in label.split()) if t]


def _possessives(tokens: Sequence[str]) -> list[tuple[int, str]]:
    """``(position, owner)`` of each possessive token a word follows (``Kutúzov's`` in ``Kutúzov's envoy``)."""
    return [
        (i, m.group("owner"))
        for i, token in enumerate(tokens[:-1])
        if (m := _POSSESSIVE.match(token)) is not None and m.group("owner")
    ]


def has_possessive(label: str) -> bool:
    """``label`` has a token in the possessive that another word follows (``Boris's mother``).

    Either apostrophe (``'`` or U+2019) and the plural form (``the Kurágins'
    set``); the one reading of a possessive the parser, the cleaners and the
    name rule share.
    """
    return bool(_possessives(_tokens(label)))


def bearer_text(label: str) -> str:
    """The part of ``label`` that names its bearer: what follows its last possessive.

    ``Prince Bolkónski's architect`` -> ``architect``; a label without a
    possessive followed by a word is its own bearer text.
    """
    tokens = _tokens(label)
    found = _possessives(tokens)
    return " ".join(tokens[found[-1][0] + 1 :]) if found else label


ARTICLES = frozenset({"the", "a", "an"})
"""Words that open a description, not a name (English, language-level)."""


def is_bare_role(label: str) -> bool:
    """``label`` is a title or a role with no name: an article and lowercase words only.

    ``the abbé``, ``the old prince``, ``the hostess``: a bare role names no
    one, so it has no naming word (it never merges into ``Abbé Morio``, and
    as an alias it is never a key), under every domain's conventions, whatever
    the domain's title words. A capitalised word is a name (``the
    Bagratión``); a script without case is never a bare role.
    """
    tokens = _tokens(bearer_text(label))
    return len(tokens) >= 2 and fold(tokens[0]) in ARTICLES and all(t.islower() for t in tokens[1:])


@lru_cache(maxsize=4096)
def descriptor_possessor(name: str, conventions: NameConventions) -> frozenset[str] | None:
    """The naming words of the person a descriptor names by relation, or None when ``name`` is a name.

    A descriptor names its bearer through someone else's name: any possessive
    followed by a word (``Natásha's mother``, ``Count Rostóv's valet``,
    ``Kutúzov's envoy``, ``Prince Bolkónski's architect``, ``the Kurágins'
    set``), whatever the word, or a relation or role word of ``conventions``
    joined to a name by ``of`` (``the mother of Natásha``; ``the Duke of
    Wellington`` is a name). It says who the bearer is to that person, so it
    is never evidence that the bearer *is* that person: the rule reads no
    naming word, given name or holder from it, and a record described as a
    relation of a name is someone else than the bearer of that name
    (:func:`_apart`). Until 2026-10-06 a possessive was a descriptor only
    before a relation or role word, so ``Kutúzov's envoy`` kept ``Kutúzov``
    as a naming word and absorbed him (audit W4-6).

    Returns the possessor's naming words as family stems (the first
    possessive's: ``Natásha's mother's cousin`` is described through
    Natásha; empty when the possessor is only titles, as in ``the old count's
    valet``).
    """
    tokens = _tokens(name)
    found = _possessives(tokens)
    if found:
        first, owner = found[0]
        return identity_words(" ".join([*tokens[:first], owner]), conventions)
    for i, token in enumerate(tokens[:-1]):
        if (
            fold(token) in conventions.relation_words
            and fold(tokens[i + 1]) == "of"
            and len(tokens) > i + 2
        ):
            return identity_words(" ".join(tokens[i + 2 :]), conventions)
    return None


def is_descriptor(name: str, conventions: NameConventions = DEFAULT_NAME_CONVENTIONS) -> bool:
    """``name`` names someone's relation, not a person (:func:`descriptor_possessor`)."""
    return descriptor_possessor(name, conventions) is not None


def is_relation_alias(
    alias: str,
    conventions: NameConventions,
    entity_type: str | None = None,
    *,
    person: bool | None = None,
) -> bool:
    """An alias of an entity of ``entity_type`` that names someone else's relation, never the entity.

    A person's ``Natásha's mother`` or ``the mother of Natásha`` (:func:`is_descriptor`)
    says whose mother, never who; on a work ``Caesar's Commentaries`` is a name
    (``conventions.is_person``). The one reading behind the cleaner moving such an
    alias to ``descriptors`` (``entity_cleaner.move_relation_descriptors``), the name
    rule reading none as a name, and the known-entities list never listing one.
    ``person`` is ``conventions.is_person(entity_type)`` already read (the name
    index's cached ``_Profile.person``); given, ``entity_type`` is not read.
    """
    if person is None:
        person = conventions.is_person(entity_type)
    return person and is_descriptor(alias, conventions)


DESCRIPTOR_REFERENCE_KEY = "descriptor_reference"
"""Entity property (and stored node property): the record is labelled by a reference, not a name.

``True`` on an entity, or a committed node, whose label is a bare title, a
role or a descriptor (:func:`reference_key`) and that no name of its own
identifies (:meth:`NameIndex.referent`): ``the count`` where its text never
says which count, ``Prince Andrew's wife`` where hers never names her. The
node keeps what its mentions say (the facts are not dropped), and the flag
says its label is a placeholder a later source, the resolver or a reader can
fold into the person once a referent appears. Written from the final label
when deduplication ends (``deduplication.service.publish_descriptor_references``)
and never taken from a merged-in record (a named survivor is no reference:
``EntityProcessor._merge_properties``)."""


def is_reference_record(properties: Mapping[str, Any] | None) -> bool:
    """Whether stored properties carry the reference flag (:data:`DESCRIPTOR_REFERENCE_KEY`).

    The one reader of the flag: ``True`` exactly, as
    ``deduplication.service.publish_descriptor_references`` writes it; any
    other value, a missing key or no properties is no flag. Retrieval reads it through
    :func:`node_standing` to keep a reference out of the name match, and out of
    the vector seeds unless no named node is closer (``graphrag_handlers._seedable``).
    """
    return pipeline_flag(properties, DESCRIPTOR_REFERENCE_KEY)


def pipeline_flag(properties: Mapping[str, Any] | None, key: str) -> bool:
    """Whether stored properties carry the pipeline flag ``key``: ``True`` exactly.

    The one reading of every pipeline flag (:data:`PIPELINE_FLAG_KEYS`,
    :data:`PIPELINE_EDGE_FLAG_KEYS`): an extracted property of the text's
    under the same key (``"yes"``, ``"true"``, ``1``, ``"Niece"``) is what the
    text says, never a flag, and missing properties carry none.
    """
    return isinstance(properties, Mapping) and properties.get(key) is True


def with_pipeline_flag(
    properties: Mapping[str, Any] | None, key: str, *, on: bool, **companions: Any
) -> dict[str, Any]:
    """A copy of ``properties`` with the flag ``key`` set (``on``) or a set flag cleared.

    The one writer of a per-chunk pipeline flag. Setting writes ``True`` (an
    extracted value of the text's under the key gives way: the flag is what
    the readers read, :func:`pipeline_flag`) and the ``companions`` beside it
    (the plausibility score, the written type); clearing removes only a
    ``True``, so a text value under the key stays.
    """
    props = dict(properties or {})
    if on:
        props[key] = True
        props.update(companions)
    elif props.get(key) is True:
        del props[key]
    return props


ORPHAN_KEY = "orphan"
"""Stored node property: the entity had no relationship when its source was committed.

``True`` on a committed node the commit kept although no edge touched it
(keep, flag, never delete, 2026-10-07): it carries evidence
(``entity_cleaner.carries_evidence``), so its description is kept and its
consumers decide; until then the commit's orphan filter deleted it, description
and all ("Márya Lvóvna Karágina", Julie's mother, on the v4b generic arm).
Written by the commit (``commit.service.flag_orphan_entities``), which also
clears a stale ``True`` from an entity that has an edge; an extracted
``orphan`` property of any other value is the text's and is left alone. A
resolver merge keeps it only when every merged node carried it
(``EntityProcessor._merge_properties``): the edges of each move onto the kept
node."""


def is_orphan_record(properties: Mapping[str, Any] | None) -> bool:
    """Whether stored properties carry the orphan flag (:data:`ORPHAN_KEY`): ``True`` exactly."""
    return pipeline_flag(properties, ORPHAN_KEY)


LOW_PLAUSIBILITY_KEY = "low_plausibility"
"""Entity property (and stored node property): the name scored below the plausibility bar.

``True`` on an entity the per-chunk plausibility filter
(``entity_cleaner.filter_implausible_entities``) scored below its threshold
but kept because it carries evidence (``entity_cleaner.carries_evidence``:
keep, flag, never delete, 2026-10-07); its score is beside it under
:data:`PLAUSIBILITY_SCORE_KEY`. Until then the filter dropped it,
description and all (``Dining Room``, ``drawing room``, ``conservatory``,
``doll`` in the v4 literary replay). One carrying nothing is still dropped.
A merge keeps the flag only when every merged mention carried it
(``deduplication.service.merge_unanimous_flags``): a name another mention
wrote plausibly is plausible."""

PLAUSIBILITY_SCORE_KEY = "plausibility"
"""Entity property beside :data:`LOW_PLAUSIBILITY_KEY`: the score the name got (rounded, 0-1)."""

UNPLACED_TYPE_KEY = "unplaced_type"
"""Entity property (and stored node property): no core type answers to the type the model wrote.

``True`` on an entity the type rescue (``type_rescue.rescue_invalid_entity_types``)
could not place (:meth:`TypeVocabulary.decide` returned None): it is typed
``Concept`` and keeps the written type under :data:`WRITTEN_TYPE_KEY`
(keep, flag, never delete, 2026-10-07; until then it was dropped and counted
under ``unknown_type``, which now counts the flagged ones). A merge keeps
the flag only when every merged mention carried it."""

WRITTEN_TYPE_KEY = "written_type"
"""Entity property beside :data:`UNPLACED_TYPE_KEY`: the type column as the model wrote it."""

UNPLACED_CORE_TYPE = "Concept"
"""The core type an entity whose written type no core type answers to is committed as."""


UNEVIDENCED_KEY = "unevidenced"
"""Stored node and edge property (and extracted entity and relationship property): no cited sentence evidences it.

One key for both, read through :func:`pipeline_flag` like every flag.

- **An edge**: ``True`` on a relationship whose two endpoints are entities of
  its chunk but whose cited sentence (``sent_ref``) the evidence validator
  could not read as stating it (``evidence_validator.relationship_evidence``:
  an endpoint not named there, or one named and no word of the domain's
  relation vocabulary for its label). Keep, flag, never delete (2026-10-07):
  until then the validator deleted it, justification and all (162
  relationships on the B0 literary pack, 105 on the generic one, a third of
  what the model wrote). The edge is committed like any other with the reason
  it failed (:data:`EVIDENCE_REASON_KEY`); the chat context leaves it out
  unless ``graphrag.include_unevidenced_edges`` is on, and the canvas draws
  it dashed. Two copies of one fact (the commit's stable edge, a resolver
  merge) are unevidenced only when every copy is
  (``deduplication.service.merge_edge_properties``).
- **A node** (2026-10-08): ``True`` on an entity whose cited sentence does not
  name it (``evidence_validator.entity_evidence``) but which carries a
  description (``entity_cleaner.carries_evidence``), kept instead of dropped
  with the reason beside it (:data:`EVIDENCE_REASON_KEY`); one carrying
  nothing is still dropped. It ranks doubtful (:attr:`NodeStanding.doubtful`)
  and sits in the canvas's "Unresolved" region; a merge keeps it only when
  every merged mention carried it (a mention its sentence names evidences
  the entity)."""

EVIDENCE_REASON_KEY = "evidence_reason"
"""Stored property beside :data:`UNEVIDENCED_KEY`: why the validator found no evidence (one sentence)."""

EXCLUDED_NAME_KEY = "excluded_name"
"""Entity property (and stored node property): the name is one of the domain's exclusion examples.

``True`` on an entity the per-chunk exclusion filter
(``entity_cleaner.filter_excluded_entities``) matched but kept because it
carries evidence or is a reference that says something of its own (keep,
flag, never delete, 2026-10-08): until then a non-person excluded name was
dropped, description and all. The rule that matched is beside it
(:data:`EXCLUDED_BY_KEY`). One carrying nothing is still dropped. It ranks
doubtful (:attr:`NodeStanding.doubtful`); a merge keeps it only when every
merged mention carried it (another mention's name is no excluded one)."""

EXCLUDED_BY_KEY = "excluded_by"
"""Entity property beside :data:`EXCLUDED_NAME_KEY`: the description of the exclusion rule that matched."""

ALIAS_CROSSED_KNOWN_KEY = "alias_crossed_known"
"""Entity property (and stored node property): the aliases the alias guard took off the record.

The pass-1 record gave as aliases the name or a given name of another entry
of the source's known entities (``known_entities.KnownEntities.guard``, spec
2026-10-11 D3): *Ilyá* on Count Cyril. The aliases are dropped and kept here,
joined by ``; ``. Written by the pipeline (:data:`PIPELINE_WRITTEN_KEYS`),
never something the text says of the entity."""

REFERENT_UNEVIDENCED_KEY = "referent_unevidenced"
"""Entity property (and stored node property): the name guard found the record's known name unevidenced.

The record's name is a known entry its cited sentences do not identify by
spec D8's rules 1 and 2 (``KnownEntities.identified``: named by name, or a
distinctive alias of the entry written whole), so ``KnownEntities.guard``
demoted it to the one phrase they write that names no one (a reference or a
description), the original name held here, or kept it as written with the
phrases (or the name) here (none or several). Read through
:func:`is_referent_unevidenced`. Written by the pipeline
(:data:`PIPELINE_WRITTEN_KEYS`)."""


class NodeStanding(NamedTuple):
    """A stored node's pipeline flags, in the order retrieval ranks by (a sort key: lower first).

    A named connected node ``(False, False, False)`` first, then a named
    orphan, then a connected reference, then an orphan reference; within each,
    a node the pipeline doubted (:data:`DOUBTFUL_NODE_FLAG_KEYS`) ranks last.
    """

    reference: bool
    """Labelled by a reference, not a name (:func:`is_reference_record`)."""
    orphan: bool
    """No edge when its source was committed (:func:`is_orphan_record`)."""
    doubtful: bool = False
    """The pipeline doubted it (:data:`DOUBTFUL_NODE_FLAG_KEYS`): its name scored below the
    plausibility bar, no core type answered to its written type, its cited sentence does not
    name it, or its name is one of the domain's exclusion examples."""


PIPELINE_FLAG_KEYS: tuple[str, ...] = (
    ORPHAN_KEY,
    DESCRIPTOR_REFERENCE_KEY,
    LOW_PLAUSIBILITY_KEY,
    UNPLACED_TYPE_KEY,
    UNEVIDENCED_KEY,
    EXCLUDED_NAME_KEY,
)
"""Every stored node property the pipeline writes as a flag (``True`` exactly), in display order.

What the graph canvas projects for its "Unresolved" region (cortex
``GraphService.get_canvas_data``); the interface reads them through one
table (``packages/interface/src/utils/nodeFlags.ts``, ``NODE_FLAGS``), which
lists the same keys (a cortex test pins the two). A new flag joins both lists."""

DOUBTFUL_NODE_FLAG_KEYS: tuple[str, ...] = (
    LOW_PLAUSIBILITY_KEY,
    UNPLACED_TYPE_KEY,
    UNEVIDENCED_KEY,
    EXCLUDED_NAME_KEY,
)
"""The node flags :func:`node_standing` reads as :attr:`NodeStanding.doubtful`: the per-chunk
filters' doubts about a name, a type or its evidence (the orphan and reference flags have
their own fields)."""

PIPELINE_WRITTEN_KEYS: frozenset[str] = frozenset(
    {
        *PIPELINE_FLAG_KEYS,
        PLAUSIBILITY_SCORE_KEY,
        WRITTEN_TYPE_KEY,
        EVIDENCE_REASON_KEY,
        EXCLUDED_BY_KEY,
        ALIAS_CROSSED_KNOWN_KEY,
        REFERENT_UNEVIDENCED_KEY,
    }
)
"""The entity properties the pipeline's flags write (each flag and the companions beside it): none
of them is something the text says of the entity (``entity_cleaner.kept_as_reference``)."""

UNANIMOUS_FLAG_KEYS: tuple[str, ...] = (
    ORPHAN_KEY,
    LOW_PLAUSIBILITY_KEY,
    UNPLACED_TYPE_KEY,
    UNEVIDENCED_KEY,
    EXCLUDED_NAME_KEY,
)
"""The node flags a merge keeps only when every merged record carried them.

An orphan merged into a connected node is connected; a name another
mention wrote plausibly, a type another mention placed, a mention its
sentence names, or a name no exclusion lists, is no longer in doubt. (The
reference flag is not one of them: it is written from the final label,
``deduplication.service.publish_descriptor_references``.) Every edge flag
(:data:`PIPELINE_EDGE_FLAG_KEYS`) is unanimous too, by the same rule
(``deduplication.service.merge_unanimous_flags``)."""

CAPPED_KEY = "capped"
"""Stored edge property: the relationship was past a relationship cap when its source was extracted.

``True`` on an edge ``entity_cleaner.enforce_relationship_limits`` kept
although it exceeded the same-source-and-type, entity-degree or total cap
(keep, flag, never delete, 2026-10-07; until then the cap dropped it,
justification and all). It walks at :data:`CAPPED_EDGE_WEIGHT`
(:func:`edge_walk_weight`), so a capped hub edge carries a tenth of an edge's
PageRank mass, and the canvas draws it thin. Two copies of one fact are
capped only when every copy is (``deduplication.service.merge_edge_properties``:
one copy within the caps makes the fact within them)."""

UNMATCHED_LABEL_KEY = "unmatched_label"
"""Stored edge property: no label of the domain's vocabulary answered to the one the model wrote.

``True`` on a relationship the strict type-constraint check
(``entity_cleaner.validate_relationship_type_constraints``) found no edge
template for, even by the near-miss match, kept as the fallback label
(``core_types.FALLBACK_LABEL``, ``related_to``) with the label as written
beside it (:data:`WRITTEN_LABEL_KEY`). Keep, flag, never delete
(2026-10-08): until then it was dropped, justification and all. It ranks
with a capped line (:attr:`TripleStanding.doubted_edge`) and the canvas
draws it thin."""

WRITTEN_LABEL_KEY = "written_label"
"""Stored edge property beside :data:`UNMATCHED_LABEL_KEY`: the relationship label as the model wrote it."""

TYPE_MISMATCH_KEY = "type_mismatch"
"""Stored edge property: its endpoints are not core types its label's template allows.

``True`` on a relationship the strict type-constraint check kept although
its source or target type is not one the template allows, either way round
(or would fit only reversed while direction correction is off), its label
unchanged and the reason beside it (:data:`TYPE_MISMATCH_REASON_KEY`). Keep,
flag, never delete (2026-10-08): until then it was dropped. It ranks with a
capped line (:attr:`TripleStanding.doubted_edge`) and the canvas draws it
thin."""

TYPE_MISMATCH_REASON_KEY = "type_mismatch_reason"
"""Stored edge property beside :data:`TYPE_MISMATCH_KEY`: which endpoint's type the template refused."""

JUSTIFICATIONS_KEY = "justifications"
"""Stored edge property: every justification the copies of one edge carried, the edge's own first.

Written when two edges collapse into one (the identity resolver's merge moves
an edge onto a node that already has it): the dropped copy's justification is
kept here, so no evidence sentence is lost (keep, flag, never delete)."""

INVERSE_OF_KEY = "inverse_of"
"""Stored edge property of a synthetic inverse edge: the forward relation it mirrors.

The commit writes the inverse (``commit.relation``, for traversal in both
directions, when ``enable_inverse_relationships`` is on) with the forward
edge's properties, its endpoints swapped and no citation of its own: the
citation step passes it over (``commit.service``) and the aggregate block
counts it against its forward twin (``graphrag_blocks.aggregate_blocks``)."""


PIPELINE_EDGE_FLAG_KEYS: tuple[str, ...] = (
    CAPPED_KEY,
    UNEVIDENCED_KEY,
    UNMATCHED_LABEL_KEY,
    TYPE_MISMATCH_KEY,
)
"""Every stored edge property the pipeline writes as a flag (``True`` exactly), in display order.

The edge counterpart of :data:`PIPELINE_FLAG_KEYS`, and the one edge-flag
table: what the canvas projects on its edges (cortex
``GraphService.get_canvas_data``, through ``edges_with_true_properties``), what
the minimal edge listing reads in SQL, and what a merge of two copies of one
fact keeps only when every copy carries it
(``deduplication.service.merge_edge_properties``). The interface reads them
through one table (``packages/interface/src/utils/edgeFlags.ts``,
``EDGE_FLAGS``), which lists the same keys; a cortex test pins the two. Edges
have no region: an unevidenced edge is drawn dashed, the others thin. A new
edge flag joins both lists."""

DOUBTED_EDGE_FLAG_KEYS: tuple[str, ...] = (CAPPED_KEY, UNMATCHED_LABEL_KEY, TYPE_MISMATCH_KEY)
"""The edge flags :func:`triple_standing` reads as :attr:`TripleStanding.doubted_edge`: past a
cap, a label no vocabulary entry matched, endpoints of the wrong core types (one tier, after
an unevidenced line's)."""

EDGE_FLAG_REASON_KEYS: Mapping[str, str] = MappingProxyType(
    {UNEVIDENCED_KEY: EVIDENCE_REASON_KEY, TYPE_MISMATCH_KEY: TYPE_MISMATCH_REASON_KEY}
)
"""Each edge flag's reason: the property saying why it was set, which goes with its flag (a
merge that clears the flag drops the reason, ``merge_edge_properties``)."""

EDGE_FLAG_DETAIL_KEYS: Mapping[str, str] = MappingProxyType(
    {**EDGE_FLAG_REASON_KEYS, UNMATCHED_LABEL_KEY: WRITTEN_LABEL_KEY}
)
"""The property shown beside each edge flag (its reason, or the label the model wrote): what the
canvas carries with the flags (``GraphService.get_canvas_data``) and the interface's
``EDGE_FLAGS`` names as each flag's ``reasonKey`` (a cortex test pins the two). The written
label is data, not a reason: a merge keeps it."""

DEFAULT_EDGE_WEIGHT = 1.0
"""The weight an edge walks at in PageRank (``graph.engine.algorithms.calculate_pagerank``)."""

CAPPED_EDGE_WEIGHT = 0.1 * DEFAULT_EDGE_WEIGHT
"""The weight a capped edge (:data:`CAPPED_KEY`) walks at: a tenth of the default."""


def edge_walk_weight(properties: Mapping[str, Any] | None) -> float:
    """The weight an edge walks at: :data:`CAPPED_EDGE_WEIGHT` when capped, else the default.

    The one reading of an edge's pipeline flags for the walk: PageRank
    (``traversal.build_digraph``) weighs each edge by it, so a relationship
    past a cap ranks after the ones within it and does not make its hub.
    """
    return CAPPED_EDGE_WEIGHT if pipeline_flag(properties, CAPPED_KEY) else DEFAULT_EDGE_WEIGHT


def is_unevidenced_record(properties: Mapping[str, Any] | None) -> bool:
    """Whether stored edge properties carry the unevidenced flag (:data:`UNEVIDENCED_KEY`): ``True`` exactly.

    Read through :func:`pipeline_flag`, the one reading of every pipeline
    flag: retrieval leaves such an edge out of the chat context and PageRank
    by default (``graphrag_handlers``), ranks it after the evidenced ones
    when it is let in, and the property merge keeps the flag only when every
    merged copy carries it.
    """
    return pipeline_flag(properties, UNEVIDENCED_KEY)


def edge_justifications(properties: Mapping[str, Any]) -> list[str]:
    """Every justification an edge's properties carry, its own first, each once (blank ones none)."""
    listed = properties.get(JUSTIFICATIONS_KEY)
    texts = [*(listed if isinstance(listed, list) else []), properties.get("justification")]
    own = str(properties.get("justification") or "").strip()
    ordered = [own, *(str(t or "").strip() for t in texts)]
    return list(dict.fromkeys(t for t in ordered if t))


def doubted_by_filters(properties: Mapping[str, Any] | None) -> bool:
    """Whether properties carry a :data:`DOUBTFUL_NODE_FLAG_KEYS` flag: a per-chunk filter doubted them.

    The one reading of those flags (each through :func:`pipeline_flag`):
    :attr:`NodeStanding.doubtful` (:func:`node_standing`) and
    :func:`is_doubted_record` both compose it, so retrieval and the
    known-entities list cannot read a filter's doubt two ways.
    """
    return any(pipeline_flag(properties, key) for key in DOUBTFUL_NODE_FLAG_KEYS)


def is_doubted_record(properties: Mapping[str, Any] | None) -> bool:
    """Whether the pipeline doubts a pass-1 record's name: a filter's flag or the name guard's.

    :func:`doubted_by_filters`, or a non-empty :data:`REFERENT_UNEVIDENCED_KEY`
    (the name guard found the record's known name unevidenced). What
    ``KnownEntities.add_group`` leaves out of the list (final review of
    2026-10-11, I1): since keep, flag, never delete the per-group filters keep
    the records they doubt, and the list tells the next groups' model "named
    earlier in this source", whose aliases count as the name guard's
    evidence; a record flagged in one group would clear its own doubt in the
    next. :data:`ALIAS_CROSSED_KNOWN_KEY` is no doubt (spec D3): the alias
    guard has already taken the crossed alias off, and what remains is a
    correctly named record. Retrieval reads :func:`doubted_by_filters` alone:
    the guard flag is one mention's, and a merge carries it onto a node the
    other mentions name (it is no unanimous flag).
    """
    return doubted_by_filters(properties) or is_referent_unevidenced(properties)


def is_referent_unevidenced(properties: Mapping[str, Any] | None) -> bool:
    """Whether the name guard flagged a record: a non-empty :data:`REFERENT_UNEVIDENCED_KEY`.

    The one reading of the flag: :func:`is_doubted_record` (the list leaves
    the record out) and ``KnownEntities.kept_unevidenced`` (the relationship
    evidence filter never names an endpoint kept under its known name but
    flagged, final review 2 of 2026-10-12, I1) both compose it.
    """
    if not isinstance(properties, Mapping):
        return False
    value = properties.get(REFERENT_UNEVIDENCED_KEY)
    return isinstance(value, str) and bool(value.strip())


def node_standing(properties: Mapping[str, Any] | None) -> NodeStanding:
    """A stored node's standing among retrieval candidates (:class:`NodeStanding`).

    The one reading of the pipeline flags retrieval reads: the seed rules
    read ``reference`` (``graphrag_handlers._seedable``: an orphan seeds by
    name, written label or vector like any named node), and the entity and
    provenance ranking sort by the whole standing
    (``graphrag_handlers._by_standing``: an orphan ranks after every
    connected candidate of its standing, a node the pipeline doubted,
    :data:`DOUBTFUL_NODE_FLAG_KEYS`, after every undoubted one).
    """
    return NodeStanding(
        is_reference_record(properties),
        is_orphan_record(properties),
        doubted_by_filters(properties),
    )


class TripleStanding(NamedTuple):
    """A relationship line's standing among the graph context's lines (a sort key: lower first).

    Its endpoints' :class:`NodeStanding` first, in the node order (the worse
    of the two: named before reference, connected before orphan, undoubted
    before doubted), then the edge's own flags in the edge order (evidenced
    before unevidenced, then undoubted before doubted: capped, an unmatched
    label or a type mismatch, :data:`DOUBTED_EDGE_FLAG_KEYS`): the one order
    the triple summary's budget cuts by and the ``max_triples`` selection
    ranks by before its cap.
    """

    reference: bool
    """An endpoint is labelled by a reference (:func:`is_reference_record`)."""
    orphan: bool
    """An endpoint had no edge when its source was committed (:func:`is_orphan_record`)."""
    doubtful: bool
    """An endpoint the pipeline doubted (:attr:`NodeStanding.doubtful`)."""
    unevidenced: bool
    """No cited sentence states the relationship (:func:`is_unevidenced_record`)."""
    doubted_edge: bool
    """The relationship was past a cap, its label matched no vocabulary entry, or its endpoints
    are not core types its template allows (:data:`DOUBTED_EDGE_FLAG_KEYS`)."""


def triple_standing(
    edge_properties: Mapping[str, Any] | None, source: NodeStanding, target: NodeStanding
) -> TripleStanding:
    """A relationship line's :class:`TripleStanding`: its edge's stored properties, its endpoints' standings.

    The endpoints come as :class:`NodeStanding` (``node_standing`` of their
    stored properties, read by the caller), so the one reading of the node
    flags stays :func:`node_standing`; the edge's flags are read through
    :func:`pipeline_flag`.
    """
    return TripleStanding(
        source.reference or target.reference,
        source.orphan or target.orphan,
        source.doubtful or target.doubtful,
        is_unevidenced_record(edge_properties),
        any(pipeline_flag(edge_properties, key) for key in DOUBTED_EDGE_FLAG_KEYS),
    )


def reference_key(
    label: str,
    conventions: NameConventions = DEFAULT_NAME_CONVENTIONS,
    entity_type: str | None = None,
) -> str | None:
    """What a person's label refers by when it names no one, or None when it is a name.

    A reference is a person's label with no naming word, or a descriptor: a
    bare title (``Emperor``, ``the count``, ``our Emperor``, ``the little
    princess``), a role (``the visitor``: :func:`is_bare_role`) or a
    relation of someone else (``Prince Andrew's wife``:
    :func:`descriptor_possessor`). The key is its words folded, the articles,
    particles and determiners left out and each title in its one spelling
    (``title_class``), so ``Emperor``, ``the Emperor`` and ``our Emperor``
    refer alike. Only a person is named by reference (``entity_type`` is
    read by ``conventions.is_person``; None is a person): ``the army`` is a
    group's name. The one reading of a reference: the name rule's profile,
    the survivor ranking, the per-chunk filters that must not drop one
    (``entity_cleaner``) and the flag (:data:`DESCRIPTOR_REFERENCE_KEY`).
    Until 2026-10-07 a bare title was read nowhere: the per-chunk
    plausibility filter dropped ``the count`` (and with it the Archives fact
    a v4 question needs), ``Emperor`` and ``the Emperor`` stayed two nodes,
    and a descriptor label survived as a node of its own unflagged.
    """
    if not conventions.is_person(entity_type) or not fold(label):
        return None
    if not is_descriptor(label, conventions) and name_words(label, conventions.titles):
        return None
    words = [
        conventions.title_class.get(w, w)
        for w in fold(label).split()
        if w not in UNIVERSAL_PARTICLES and w not in ARTICLES
    ]
    return " ".join(words) or None


def labelled_by_reference(
    label: str,
    conventions: NameConventions = DEFAULT_NAME_CONVENTIONS,
    entity_type: str | None = None,
) -> bool:
    """Whether an entity of ``entity_type`` is labelled by a reference rather than a name, any type.

    A person's reference is :func:`reference_key`'s (a bare title, a role, a
    descriptor). Any other type is labelled by a reference when its label is
    an article and lowercase words only (:func:`is_bare_role`): ``the army``,
    ``the government``, ``the village``, ``the war``; a capitalised word is a
    name (``the Preobrazhénsky regiment``), and ``Caesar's Commentaries`` on
    a work is a title, not someone's relation. The one reading of "labelled by
    a reference" behind the flag (:data:`DESCRIPTOR_REFERENCE_KEY`,
    ``deduplication.service.publish_descriptor_references``) and the name
    filters that must not drop one (``entity_cleaner.kept_as_reference``).
    Until 2026-10-07 only a person was: ``the army`` with a description went
    to the plausibility filter's named-referent bar and was dropped. The name
    rule still reads a reference of a person only (:func:`reference_key`): a
    group's reference merges by its words like any name.
    """
    if reference_key(label, conventions, entity_type) is not None:
        return True
    return not conventions.is_person(entity_type) and is_bare_role(label)


def reference_names_a_person(
    label: str, conventions: NameConventions = DEFAULT_NAME_CONVENTIONS
) -> bool:
    """A reference (:func:`reference_key`, read untyped) whose own words say it is a person.

    A title (``the count``, ``our Emperor``) or a relation or role word
    (``Natásha's mother``, ``the valet``) of ``conventions`` in the label: what
    the type rescue reads to type a reference record whose type column is
    empty as a Person (``type_rescue``). ``the army`` is a reference by its
    shape, but none of its words names a person.
    """
    if reference_key(label, conventions) is None:
        return False
    words = {
        w
        for w in WORD_PATTERN.findall(fold(label))
        if w not in ARTICLES and w not in UNIVERSAL_PARTICLES
    }
    return bool(words & (_title_set(conventions.titles) | conventions.relation_words))


def text_referent_names(
    label: str, texts: Iterable[str], conventions: NameConventions = DEFAULT_NAME_CONVENTIONS
) -> list[str]:
    """The one name a bare title's own sentences write with its title: who its text says it is.

    ``the Emperor`` cited on "... said the Emperor Alexander ..." is
    ``Emperor Alexander`` there: its gendered title (one spelling of it,
    ``title_class``) followed by capitalised naming words, particles inside
    kept (``Vicomte de Mortemart``), up to the first punctuation. Only a bare
    title reads its sentences (a descriptor names by relation, a role states
    no title), and only when they write its title as one name and nowhere
    bare: ``the Emperor Alexander`` beside ``the Emperor Napoleon`` says
    nothing of which the reference is, and "the countess turned to Countess
    Véra" writes the countess bare beside another countess's name (in the
    v4 War and Peace outputs every title-holder written beside a reference
    was someone else). ``texts`` are the reference's own cited sentences, never
    another chunk's: the referent comes from the reference's text, never from
    elsewhere nor from how often a name is written. Returns ``[name]`` or
    ``[]``; a caller gives the name as an alias of the reference
    (``deduplication.service.publish_text_referents``), so the alias rule
    (:meth:`NameIndex.referent`) decides whom it joins.
    """
    if reference_key(label, conventions) is None or is_descriptor(label, conventions):
        return []
    own = {
        conventions.title_class.get(w, w)
        for w in fold(label).split()
        if w in conventions.gendered_titles
    }
    if not own:
        return []
    skip = _title_set(conventions.titles)
    found: dict[str, str] = {}
    bare_title = False
    for text in texts:
        raw = str(text or "").split()
        for k, token in enumerate(raw):
            word = fold(token.strip(_TOKEN_EDGES))
            if conventions.title_class.get(word, word) not in own:
                continue
            if token[-1] in _TOKEN_EDGES:
                bare_title = True
                continue
            name = [token.strip(_TOKEN_EDGES)]
            named = False
            for nxt in raw[k + 1 :]:
                bare = nxt.strip(_TOKEN_EDGES)
                owner = _POSSESSIVE.match(bare)
                bare = owner.group("owner") if owner else bare
                folded = fold(bare)
                if not folded:
                    break
                if folded in UNIVERSAL_PARTICLES and not owner:
                    name.append(bare)
                elif bare[:1].isupper() and folded not in skip:
                    name.append(bare)
                    named = True
                else:
                    break
                if owner or nxt[-1] in _TOKEN_EDGES:
                    break  # a possessive or a punctuation mark ends the name
            while name and fold(name[-1]) in UNIVERSAL_PARTICLES:
                name.pop()
            if named:
                written = " ".join(name)
                found.setdefault(fold(written), written)
            else:
                bare_title = True
    return list(found.values()) if len(found) == 1 and not bare_title else []


@dataclass(frozen=True)
class NameRecord:
    """One entity as the name rule sees it: its name, its type, the aliases it was given.

    ``descriptors`` are what else it is called that names no one (the
    entity's ``descriptors``): one naming another person's relation
    (``Natásha's mother``) says the entity is not that person.
    """

    label: str
    type: str
    aliases: tuple[str, ...] = ()
    descriptors: tuple[str, ...] = ()
    merged: tuple[str, ...] = ()
    """Names an earlier merge folded into this record (:data:`MERGED_NAMES_KEY`).

    Never evidence that a later name is this record (a merged-in name is the
    merge's, not given under this name), but each one is this record's for
    the guards: a name the rule keeps apart from any of them is kept apart
    from the record, so a chain of merges across passes (semantic, then the
    hierarchical pass, then the resolver) never joins what one pass would
    have kept apart."""
    description: str = ""
    """What the entity's or node's description says (:func:`record_description`): never evidence,
    only what orders two records otherwise alike (:func:`record_order_key`)."""


MERGED_NAMES_KEY = "merged_names"
"""Entity (and stored node property) key: the names merges folded into the entity, first seen first.

Written by ``EntityProcessor.merge_entities`` and the resolver's
``apply_resolution``, stored by the commit, read back by
:func:`record_merged_names` so every pass guards a merged entity by all its
names (:attr:`NameRecord.merged`)."""


NAME_RECORD_KEYS: tuple[str, ...] = (
    GIVEN_ALIASES_KEY,
    MERGED_NAMES_KEY,
    ATTESTED_NAMES_KEY,
    "descriptors",
)
"""What the name rule reads off an entity besides its name, type and aliases: every list a stage
that rebuilds an entity must carry over, and the commit stores on the node.

The one list of them: ``preprocessor.normalize_entities`` (the shape every
extraction path hands the commit) and the commit's node properties
(``commit.entity``) read it. Until 2026-10-07 the normalizer kept a list of
its own without these four, so no committed node carried its given aliases,
merged-in names, attested spellings or descriptors, and the resolver read
every alias as given and no node as merged."""


def record_merged_names(item: Mapping[str, Any]) -> tuple[str, ...]:
    """The names merges folded into an entity, or a committed node (on it or in its properties)."""
    raw = item.get(MERGED_NAMES_KEY)
    if raw is None:
        raw = (item.get("properties") or {}).get(MERGED_NAMES_KEY)
    return tuple(str(n) for n in raw or () if n) if isinstance(raw, (list, tuple)) else ()


def record_given_aliases(item: Mapping[str, Any]) -> list[str]:
    """The aliases given an entity, or a committed node, under its own name: its alias evidence.

    :data:`GIVEN_ALIASES_KEY` on the item or in its ``properties`` when a
    merge or the commit recorded it (an alias a merge acquired is the
    merged-in name's word, not evidence); an item carrying the key with no
    list was given none; otherwise every alias was given (an entity no merge
    has touched, a node a person wrote). The one reading the deduplication
    and the resolver share.
    """
    props = item.get("properties") or {}
    value = item[GIVEN_ALIASES_KEY] if GIVEN_ALIASES_KEY in item else props.get(GIVEN_ALIASES_KEY)
    if isinstance(value, list):
        given = [str(a) for a in value if a]
        # A given alias a later step took off (a descriptor moved to ``descriptors``,
        # an alias the cleaner dropped) is no longer this entity's evidence.
        current = item.get("aliases") if "aliases" in item else props.get("aliases")
        if isinstance(current, list):
            kept = {fold(str(a)) for a in current if a}
            given = [a for a in given if fold(a) in kept]
        return given
    if GIVEN_ALIASES_KEY in item:
        return []
    return [str(a) for a in item.get("aliases") or () if a]


NODE_DESCRIPTION_KEYS = ("definition", "description", "content")
"""Where a stored node keeps its description, first found wins: the commit writes ``definition``,
a package may carry ``description``, a template ``content``."""


def node_description(properties: Mapping[str, Any]) -> Any:
    """A stored node's description: the first of :data:`NODE_DESCRIPTION_KEYS` it holds, else ``""``."""
    return next((properties[k] for k in NODE_DESCRIPTION_KEYS if properties.get(k)), "")


def record_description(item: Mapping[str, Any]) -> str:
    """The description of an entity (``description``) or a committed node (:func:`node_description`)."""
    own = item.get("description")
    if own:
        return str(own)
    return str(node_description(item.get("properties") or {}) or "")


def name_record(item: Mapping[str, Any]) -> NameRecord:
    """An entity or a committed node as the name rule sees it: name, type, given aliases, descriptors.

    The one construction every caller of the rule uses (the exact,
    semantic and hierarchical deduplication and the resolver's planner).
    """
    return NameRecord(
        label=str(item.get("name") or item.get("label") or "").strip(),
        type=str(item.get("type") or ""),
        aliases=tuple(record_given_aliases(item)),
        descriptors=record_descriptors(item),
        merged=record_merged_names(item),
        description=record_description(item),
    )


def record_descriptors(item: Mapping[str, Any]) -> tuple[str, ...]:
    """The ``descriptors`` of an entity, or of a committed node (on it or in its properties)."""
    raw = item.get("descriptors")
    if raw is None:
        raw = (item.get("properties") or {}).get("descriptors")
    return tuple(str(d) for d in raw or () if d) if isinstance(raw, (list, tuple)) else ()


@dataclass(frozen=True)
class NameEvidence:
    """Why two records are one entity."""

    kind: EvidenceKind
    why: str


@dataclass(frozen=True)
class _Profile:
    """What the rule reads of one record, computed once."""

    type: str
    """The record's core type (``core_type_key``): what the typed index compares."""
    folded: str
    words: frozenset[str]
    alias_folds: frozenset[str]
    alias_words: tuple[frozenset[str], ...]
    alias_fuller: tuple[frozenset[str], ...]
    """Each alias's naming words written capitalised (:func:`alias_as_name`): what a fuller alias
    names (:func:`_fuller_alias_elsewhere`), its epithets left out."""
    named: frozenset[str]
    """The label's naming words written capitalised (:func:`alias_as_name`): what the label names
    when it is read as one of a record's aliases (:func:`_as_group`)."""
    title_gender: Gender | None
    gender: Gender | None
    generation: str | None
    given: frozenset[str]
    surnames: frozenset[tuple[str, bool]]
    """Each naming word as :func:`surname_key` reads it: its stem, and whether its form says a woman."""
    titles: frozenset[str] = frozenset()
    """The gendered titles the label states, each as its one spelling (``title_class``)."""
    described: tuple[frozenset[str], ...] = ()
    """Possessors the label or a given alias describes the record by (``Natásha's mother``)."""
    inner: frozenset[str] = frozenset()
    """Stems of every naming word but the last of a label of two or more (``mary`` of ``Princess Mary Bolkónskaya``)."""
    last: str | None = None
    """Stem of the last naming word of a label of two or more (``bolkonsk``): where a family name stands."""
    person: bool = True
    """The record's type names a person (``NameConventions.is_person``): a descriptor can name it."""
    families: frozenset[str] = frozenset()
    """Stems of the words the label writes as a family name: one in a family form or the base a
    feminine form folds to (``Rostóva``, ``Kurágin``), and the last naming word of a label of
    two or more unless it is a patronymic."""
    patronymics: frozenset[str] = frozenset()
    """Naming words in a patronymic form (``Fëdorovna``, ``Vladímirovich``)."""
    lone_titled: str | None = None
    """Stem of the one naming word of a titled one-word name (``Countess Véra``): a family name
    unless the names show it to be a given one (:func:`_family_names_differ`)."""
    given_name_title: bool = False
    """The label is one naming word after a title the language also writes as a given name
    (``Earl Warren``, ``Prince Fielder``: :attr:`NameConventions.given_name_titles`)."""
    reference: str | None = None
    """What the label refers by when it names no one (:func:`reference_key`), else None."""

    @property
    def all_words(self) -> frozenset[str]:
        """Naming words of the label and of every given alias."""
        return self.words.union(*self.alias_words)


def _single(values: set[str]) -> str | None:
    """The one value of ``values``, or None when there is none or they disagree."""
    return next(iter(values)) if len(values) == 1 else None


def _family_base(word: str, conv: NameConventions) -> bool:
    """``word`` is the base a feminine surname form folds to (``bezukhov`` of ``bezukhova``)."""
    return any(
        f.gender == "f"
        and f.stem_ending != f.ending
        and word.endswith(f.stem_ending)
        and len(word) - len(f.stem_ending) >= MIN_STEM
        for f in conv.forms
    )


def _given_names(label: str, aliases: Sequence[str], conv: NameConventions) -> frozenset[str]:
    """The given-name words a record claims, as family stems.

    Every naming word but the last of a label of two or more (``Anna
    Pávlovna`` of ``Anna Pávlovna Schérer``); a label's single naming word
    unless it has a surname form or is the base a feminine surname form
    folds to (``Mary``, not ``Bolkónskaya`` nor ``Bezúkhov``, the base of
    ``Bezúkhova``); and a one-word alias written as a name (``Lise``,
    ``Bóry``; not ``the hostess``).
    """
    skip = _title_set(conv.titles) | conv.not_given
    words = [w for w, _, naming in name_parts(bearer_text(label), skip) if naming]
    out: set[str] = set()
    if len(words) >= 2:
        out.update(identity_word(w, conv) for w in words[:-1])
    elif len(words) == 1 and name_form(words[0], conv) is None and not _family_base(words[0], conv):
        out.add(words[0])
    for alias in aliases:
        named = [(w, token) for w, token, naming in name_parts(alias, skip) if naming]
        if len(named) == 1 and named[0][1][:1].isupper():
            out.add(identity_word(named[0][0], conv))
    return frozenset(out)


def _profile(record: NameRecord, conv: NameConventions) -> _Profile:
    """Read ``record`` once: folded names, family stems, gender, generation, given names.

    A descriptor label (``Pierre's father``) has no naming words and a
    descriptor alias is left out: neither names the person they mention
    (:func:`descriptor_possessor`); both are kept as what they describe.
    """
    skip = _title_set(conv.titles)
    folded = fold(record.label)
    person = conv.is_person(record.type)
    # What the label says of its bearer is read from the bearer's own words: "Count Rostóv's
    # valet" states no title, gender or generation of the valet. Only a person is named
    # through someone else's name: "Caesar's Commentaries" (a work) is its whole label.
    parts = name_parts(bearer_text(record.label) if person else record.label, skip)
    # What the label states (its titles, their gender, its generation) is read from its
    # title words only: "Carole King" states no man's title (:func:`name_parts`).
    stated = [w for w, _, naming in parts if not naming]
    label_possessor = descriptor_possessor(record.label, conv) if person else None
    names_no_one = label_possessor is not None or is_bare_role(record.label)
    naming = [] if names_no_one else [w for w, _, n in parts if n]
    title_genders: set[str] = {
        g
        for w in stated
        for g, table in (("f", conv.feminine_titles), ("m", conv.masculine_titles))
        if w in table
    }
    form_genders: set[str] = {
        f.gender for w in naming if (f := name_form(w, conv)) is not None and f.gender is not None
    }
    described: list[frozenset[str]] = [] if label_possessor is None else [label_possessor]
    aliases: list[str] = []
    for a in record.aliases:
        if not a or not fold(a):
            continue
        possessor = descriptor_possessor(a, conv) if person else None
        if possessor is None:
            aliases.append(a)
        else:
            described.append(possessor)
    if person:
        described.extend(
            possessor
            for d in record.descriptors
            if d and (possessor := descriptor_possessor(d, conv)) is not None
        )
    # An alias's naming words come from the same reading as the label's (name_parts):
    # "prodigal son Anatole" names by "anatole" alone, so it is not a fuller alias that
    # names someone else (2026-10-07: every v4 pair carried such an alias and nothing merged).
    alias_words = tuple(
        w
        for w in (
            frozenset(identity_word(x, conv) for x, _, n in name_parts(a, skip) if n)
            for a in aliases
        )
        if w
    )
    # What a fuller alias names besides the record: its capitalised naming words only
    # (:func:`alias_as_name`), so an epithet ("the famous Prince Andrew") points at no one.
    alias_fuller = tuple(
        w
        for w in (
            frozenset(
                identity_word(x, conv) for x, _, n in name_parts(alias_as_name(a, skip), skip) if n
            )
            for a in aliases
        )
        if w
    )
    generation = conv.generation
    families: set[str] = set()
    patronymics: set[str] = set()
    for w in naming:
        form = name_form(w, conv)
        if form is not None and form.gender is not None and form.stem_ending == form.ending:
            patronymics.add(w)  # a form that keeps its ending: a patronymic, no family name
        elif (form is not None and form.stem_ending != form.ending) or _family_base(w, conv):
            families.add(identity_word(w, conv))
    if len(naming) >= 2 and naming[-1] not in patronymics:
        families.add(identity_word(naming[-1], conv))
    return _Profile(
        type=core_type_key(record.type),
        folded=folded,
        words=frozenset(identity_word(w, conv) for w in naming),
        alias_folds=frozenset(fold(a) for a in aliases),
        alias_words=alias_words,
        alias_fuller=alias_fuller,
        named=frozenset()
        if names_no_one
        else frozenset(
            identity_word(w, conv)
            for w, _, n in name_parts(
                alias_as_name(bearer_text(record.label) if person else record.label, skip), skip
            )
            if n
        ),
        title_gender=_single(title_genders),  # type: ignore[arg-type]  # values are "f"/"m"
        gender=_single(title_genders | form_genders),  # type: ignore[arg-type]  # values are "f"/"m"
        generation=_single({generation[w] for w in stated if w in generation}),
        given=frozenset() if names_no_one else _given_names(record.label, aliases, conv),
        person=person,
        surnames=frozenset(surname_key(w, conv) for w in naming),
        titles=frozenset(conv.title_class.get(w, w) for w in stated if w in conv.gendered_titles),
        described=tuple(d for d in described if d),
        inner=frozenset(identity_word(w, conv) for w in naming[:-1])
        if len(naming) >= 2
        else frozenset(),
        last=identity_word(naming[-1], conv) if len(naming) >= 2 else None,
        families=frozenset(families) if person else frozenset(),
        patronymics=frozenset(patronymics) if person else frozenset(),
        lone_titled=identity_word(naming[0], conv)
        if person and len(naming) == 1 and title_genders
        else None,
        given_name_title=person
        and len(naming) == 1
        and any(w in conv.given_name_titles for w in stated),
        reference=reference_key(record.label, conv, record.type) if person else None,
    )


def _womens_given_names(profiles: Sequence[_Profile]) -> frozenset[str]:
    """Words a set of names shows to be a woman's given name: never a family name.

    A naming word other than the last of a label of two or more naming
    words whose record says a woman (by title or form: ``Mary`` of ``Princess
    Mary Bolkónskaya``, ``Natásha`` of ``Natásha Rostóva``), and the last
    word of no label of two or more (``Martin Luther`` makes ``Martin`` no
    family name, ``Anne Martin`` makes it one). A one-word label says
    nothing (``Bingley`` is no given name for being alone). What
    :func:`_title_fit` needs before a woman's title on a lone word fits a
    name that does not say a woman (audit wave 5: ``Miss Bingley`` joined
    ``Bingley`` and ``Mrs Henry`` joined ``Henry Crawford``).
    """
    given: set[str] = set()
    for p in profiles:
        if p.gender == "f":
            given |= p.inner
    final = {p.last for p in profiles if p.last}
    return frozenset(given - final)


def _given_words(profiles: Sequence[_Profile]) -> frozenset[str]:
    """Words a set of names shows to be a given name, never a family name.

    A non-final naming word of a label of two or more, the last word of none
    (``Vasíli`` of ``Vasíli Kurágin``), and no patronymic.
    """
    given: set[str] = set()
    for p in profiles:
        given |= p.inner
    final = {p.last for p in profiles if p.last}
    patronymic = {w for p in profiles for w in p.patronymics}
    return frozenset(given - final - patronymic)


def _family_names_differ(a: _Profile, b: _Profile, given: frozenset[str] = frozenset()) -> bool:
    """Two people whose names write different family names, or different patronymics.

    The family names are what each label writes in a family name's place
    (:attr:`_Profile.families`) and the one word of a titled one-word name
    unless the names show it to be a given one (``given``): ``Countess
    Rostóva`` and ``Countess Apráksina``, ``Count Rostóv`` and ``Count
    Bezúkhov``, are two people, and so are ``Countess Rostóva`` and ``Countess
    Véra`` where no ``Véra Rostóva`` shows Véra to be a given name. Two names
    that share a given name may spell one family name two ways (``Napoleon
    Bonaparte``, ``Napoleon Buonaparte``): the vectors decide those. Two
    patronymics differ whatever else is shared (``Márya Fëdorovna``,
    ``Márya Lvóvna``). Person names only, read by the domain's titles (under
    the universal ones ``Countess`` is a given name, so the generic domain
    reads ``Countess Rostóva`` as ``Napoleon Bonaparte``). A merge without
    name evidence (the semantic dedup's) is withheld on it
    (:meth:`NameIndex.withheld`; until audit wave 6 nothing withheld two
    titled family names, and at a fixed cosine of 1.0 the three countesses
    merged).
    """
    if not (a.person and b.person):
        return False
    if a.patronymics and b.patronymics and not a.patronymics & b.patronymics:
        return True

    def _slot(p: _Profile) -> frozenset[str]:
        """The family names of ``p``'s label."""
        lone = {p.lone_titled} if p.lone_titled and p.lone_titled not in given else set()
        return p.families | lone

    fa, fb = _slot(a), _slot(b)
    return bool(fa and fb and not fa & fb and not a.inner & b.inner)


def _opposed(a: str | None, b: str | None) -> bool:
    """Both say something, and not the same thing."""
    return a is not None and b is not None and a != b


def _clash(a: _Profile, b: _Profile) -> str | None:
    """What the two names state that cannot both be one person's, or None.

    Opposite genders or generations; gendered titles with none in common
    (``Mrs Mary Smith`` and ``Miss Mary Smith``; one title's spellings are
    one title, ``title_equivalents``); or one described as a relation of
    the other's name (``Natásha's mother`` beside ``Natásha Rostóva``).
    Read per record, so a group check reads it per member.
    """
    if _opposed(a.gender, b.gender):
        return "the names say different genders"
    if _opposed(a.generation, b.generation):
        return "the names say different generations"
    if _titles_clash(a, b):
        return "the names state different titles"
    if _describes(a, b) or _describes(b, a):
        return "one is described as a relation of the other"
    return None


def _titles_clash(a: _Profile, b: _Profile) -> bool:
    """Both state gendered titles and share none (``Mrs`` and ``Miss Mary Smith``)."""
    return bool(a.titles and b.titles and not a.titles & b.titles)


@dataclass(frozen=True)
class _Names:
    """What the given-name check reads of a record or a group: its given names and all its words."""

    given: frozenset[str]
    all_words: frozenset[str]


def _given_apart(a: _Profile | _Names, b: _Profile | _Names) -> bool:
    """Each has a given name the other's names lack (``Lise`` against ``Mary``)."""
    return bool(a.given - b.all_words and b.given - a.all_words)


def _profiles_apart(xs: Sequence[_Profile], ys: Sequence[_Profile]) -> bool:
    """Two groups of names would make one person of two.

    What the names state is read per member (:func:`_clash`); given names
    per group, since a group's names are one person's (``Bóry`` is
    ``Borís``'s, so ``Borís Drubetskóy`` takes both).
    """
    if any(_clash(x, y) is not None for x in xs for y in ys):
        return True
    return _given_apart(
        _Names(
            frozenset().union(*(x.given for x in xs)), frozenset().union(*(x.all_words for x in xs))
        ),
        _Names(
            frozenset().union(*(y.given for y in ys)), frozenset().union(*(y.all_words for y in ys))
        ),
    )


def _describes(a: _Profile, b: _Profile) -> bool:
    """``a`` is described by relation to a name ``b`` bears and ``a`` does not."""
    return any(d <= b.all_words and not d <= a.all_words for d in a.described)


def _apart(a: _Profile, b: _Profile) -> str | None:
    """Why ``a`` and ``b`` are two people whatever else their names share, or None.

    What the names state against each other (:func:`_clash`), or each with a
    given name the other's names lack (``Lise`` against ``Mary``).
    """
    clash = _clash(a, b)
    if clash is not None:
        return clash
    if _given_apart(a, b):
        return "each has a given name the other lacks"
    return None


Fit = Literal["fits", "family", "no"]
"""How a titled name fits another: it does, only if no one else holds the name, or never."""


def _title_fit(
    titled: _Profile, other: _Profile, womens_given: frozenset[str] = frozenset()
) -> Fit:
    """Whether ``titled``'s gendered title lets it be ``other``.

    A name that states the same gender by its title fits. A woman's title on
    a family name never fits a name that does not say a woman (a woman may
    be named by her husband's name: ``Mrs John Smith``, ``Countess
    Bezúkhov``); on given names of the other record it fits as a man's title
    does, since no one is named by her husband's given name (audit W4-2:
    ``Princess Mary`` and ``Mary`` beside ``Princess Mary Bolkónskaya``), but
    only when the names show the titled words to be a woman's given names
    (``womens_given``, :func:`_womens_given_names`): a lone word is a
    family name as often as a given one (``Miss Bingley`` is not
    ``Bingley``, ``Mrs Bennet`` not ``Bennet``, ``Mrs Henry`` not ``Henry
    Crawford``, ``Lady Russell`` not ``Russell Crowe``: audit wave 5, where
    any one-word label counted as a given name). A man's title on a name
    that states no gender, a woman's on a name whose form says a woman
    (``Natásha Rostóva``) or on the other's given names, fits only when no
    one else holds the name (``NameIndex.family_free``): unknown is
    compatible, but ``Count Rostóv`` beside ``Nicholas Rostóv`` and
    ``Natásha Rostóva`` names a family.
    """
    if titled.title_gender is None or other.title_gender == titled.title_gender:
        return "fits"
    if titled.title_gender == "f" and other.gender != "f":
        given_names_only = (
            bool(titled.words) and titled.words <= other.given and titled.words <= womens_given
        )
        return "family" if given_names_only else "no"
    if _title_may_be_a_name(titled, other):
        return "no"
    return "family"


def _title_may_be_a_name(titled: _Profile, other: _Profile) -> bool:
    """``titled``'s title may be its bearer's given name, and ``other`` names someone else then.

    ``Earl Warren`` and ``Prince Fielder`` are a title and a name, or a given
    name and a surname (:data:`UNIVERSAL_GIVEN_NAME_TITLES`); read as the
    second, ``other`` has a word they lack, and ``other`` states no gender
    that would say the title is one (``Warren Burger``, ``Cecil Fielder``). A
    man's title otherwise fits an unmarked name (``Mr Darcy`` is
    ``Fitzwilliam Darcy``; ``Prince Andrew`` and ``Andrew`` share their
    words), and so does a title the domain declares its own (under the
    literary domain ``Prince Vasíli`` is ``Vasíli Kurágin``). Until audit
    wave 8 ``Earl Warren`` joined ``Warren Burger`` under the generic domain.
    """
    return titled.given_name_title and other.gender is None and bool(other.words - titled.words)


def _generation_fits(a: _Profile, b: _Profile) -> bool:
    """``a`` says no generation, or ``b`` says the same one.

    ``old`` or ``young`` says there are two of the name; another name says
    which only if it says it too (``old Prince Bolkónski`` is not ``Prince
    Andrew Bolkónski``, nor ``old Countess Natálya Rostóva`` ``Natálya Rostóva``).
    """
    return a.generation is None or b.generation == a.generation


def _forms_clash(a: _Profile, b: _Profile) -> bool:
    """A family name one writes in its feminine form and the other does not.

    ``Dólokhov`` and ``Márya Ivánovna Dólokhova`` share the stem ``dolokhov``,
    but the son's name is not the mother's with words left out, and she is
    no holder of his name (``NameIndex.holders``): a shared stem whose two
    spellings :func:`names_person` keeps apart (their :func:`surname_key`
    readings differ), the one predicate the evidence validator names an
    entity by. Who else of the family bears the name still counts where a
    title asks for it (``NameIndex.family_free``: ``Countess Rostóva``
    beside ``Natásha Rostóva`` and ``Nicholas Rostóv``).
    """
    return any(x[0] == y[0] and x != y for x in a.surnames for y in b.surnames)


def _fuller_alias_elsewhere(short: _Profile, long_words: frozenset[str]) -> bool:
    """``short`` carries an alias of two or more naming words that adds a word the candidate lacks.

    "Princess Bolkónskaya" carrying the alias "Lise Meinen" is the little
    princess, not "Princess Mary Bolkónskaya", and "Count Bezúkhov" carrying
    "Cyril Vladímirovich Bezúkhov" is not "Pierre Bezúkhov". An alias's words
    are its capitalised naming words (:attr:`_Profile.alias_fuller`): "the
    famous Prince Andrew" adds no word to "Prince Andrew Bolkónski", nor
    "young scapegrace Konrad" to "Konrad Vorob".
    """
    return any(len(w) >= 2 and w - long_words for w in short.alias_fuller)


def _single_of(values: Iterable[str | None]) -> str | None:
    """The one value the non-None ``values`` state, or None when there is none or they disagree."""
    return _single({v for v in values if v is not None})


def _as_group(view: _Profile, group: Sequence[_Profile]) -> _Profile:
    """``view`` (one of a record's names) carrying what all the record's names state.

    The words stay the view's own; the title, gender and generation are the
    ones any name of the group states, the given names and fuller names are
    every name's. What a merge reads of a record is then the same whichever
    of its spellings is its label (audit wave 7: with the untitled survivor
    ``Cyril Vladímirovich Bezúkhov`` the count's title no longer fitted, and
    ``Count Bezúkhov`` stopped joining him, though the group holds ``Count
    Cyril Vladímirovich Bezúkhov``).
    """
    title_genders = [g.title_gender for g in group]
    genders = [g.gender for g in group]
    return replace(
        view,
        title_gender=_single_of(title_genders),  # type: ignore[arg-type]  # values are "f"/"m"
        gender=_single_of(genders),  # type: ignore[arg-type]  # values are "f"/"m"
        generation=_single_of(g.generation for g in group),
        titles=frozenset().union(*(g.titles for g in group)),
        given=frozenset().union(*(g.given for g in group)),
        alias_words=tuple(dict.fromkeys(w for g in group for w in (g.words, *g.alias_words) if w)),
        alias_fuller=tuple(
            dict.fromkeys(w for g in group for w in (g.named, *g.alias_fuller) if w)
        ),
    )


@dataclass(frozen=True)
class _Pair:
    """Pairwise evidence and which side is which."""

    evidence: NameEvidence
    step: str
    """One of :data:`_STRENGTH`'s keys."""
    owner: int = -1
    """For alias evidence: the record that carries the alias."""
    named: int = -1
    """For alias evidence: the record the alias names. For subset: the fuller record."""
    short: int = -1
    """For subset evidence: the shorter record."""
    family: tuple[int, ...] = ()
    """Titled records whose title fits only if no one else holds the name (:func:`_title_fit`)."""
    ends: tuple[int, int] = (-1, -1)
    """For same-words evidence: the two records."""
    words: frozenset[str] = frozenset()
    """For subset and same-words evidence: the naming words of the shorter name that gave it
    (any of the record's names, :meth:`NameIndex.views`): what the holder guards look up."""


class _ReferentCycle(Exception):  # noqa: N818 - a control signal, never an error that escapes
    """A reference's resolution reached the reference again (:meth:`NameIndex.referent`).

    Raised where the resolution asks for the referent being read and caught
    by that reference's own :meth:`NameIndex.referent`, which resolves it to
    no one; every pair and standing on the way is left uncached.
    """

    def __init__(self, reference: int) -> None:
        """Record the index of the reference whose resolution cycled."""
        super().__init__(reference)
        self.reference = reference


class NameIndex:
    """The rule over a set of records: pairwise evidence and the guards that need every name.

    ``typed`` restricts evidence and holders to records of one core type
    (``core_type_key``: a ``Character`` and a ``Person`` are one; the
    resolver and the hierarchical pass); the exact dedup matches names
    across types under its own type switch and builds the index untyped.
    ``conventions`` are the domain's tables (:class:`NameConventions`);
    ``titles`` alone builds the universal ones around those title words.
    """

    def __init__(
        self,
        records: Sequence[NameRecord],
        *,
        conventions: NameConventions | None = None,
        titles: Iterable[str] | None = None,
        typed: bool = True,
    ) -> None:
        """Profile every record and index its naming words, labels and given aliases."""
        self.records = list(records)
        self.conventions = conv = as_conventions(conventions, titles)
        self._p = [_profile(r, conv) for r in self.records]
        self._merged = [
            [_profile(NameRecord(name, r.type), conv) for name in r.merged if fold(name)]
            for r in self.records
        ]
        self._aliases = [
            [
                _profile(NameRecord(alias, r.type), conv)
                for alias in dict.fromkeys(r.aliases)
                if fold(alias)
                and fold(alias) != fold(r.label)
                and not is_relation_alias(alias, conv, person=self._p[i].person)
            ]
            for i, r in enumerate(self.records)
        ]
        self._views: dict[int, list[_Profile]] = {}
        self._typed = typed
        self._postings: dict[str, set[int]] = defaultdict(set)
        self._by_label: dict[str, set[int]] = defaultdict(set)
        self._by_reference: dict[str, set[int]] = defaultdict(set)
        self._givers: dict[str, set[int]] = defaultdict(set)
        self._title_postings: dict[str, set[int]] = defaultdict(set)
        for i, record in enumerate(self.records):
            if not self._p[i].person:
                continue
            for name in (record.label, *record.aliases, *record.merged):
                for w in fold(bearer_text(name)).split() if name else ():
                    t = conv.title_class.get(w, w)
                    if t in conv.naming_titles:
                        self._title_postings[t].add(i)
        for i, p in enumerate(self._p):
            for w in p.all_words.union(*(m.words for m in self._merged[i])):
                self._postings[w].add(i)
            self._by_label[p.folded].add(i)
            if p.reference is not None:
                self._by_reference[p.reference].add(i)
            for a in p.alias_folds:
                self._givers[a].add(i)
        self._womens_given = _womens_given_names(
            [*self._p, *(m for ms in self._merged for m in ms)]
        )
        self._given_words = _given_words([*self._p, *(m for ms in self._merged for m in ms)])
        self._pairs: dict[tuple[int, int], _Pair | None] = {}
        self._standing: dict[tuple[int, int], _Pair | None] = {}
        self._referents: dict[int, int | None] = {}
        self._resolving: set[int] = set()
        """The references whose referent is being read (:meth:`referent`)."""

    # -- pairwise ---------------------------------------------------------------

    def _same_type(self, i: int, j: int) -> bool:
        """Records ``i`` and ``j`` may be compared: one core type (always, when the index is untyped)."""
        return not self._typed or self._p[i].type == self._p[j].type

    def pair(self, i: int, j: int) -> _Pair | None:
        """Pairwise evidence that records ``i`` and ``j`` are one entity (no holder guard)."""
        key = (i, j) if i < j else (j, i)
        if key not in self._pairs:
            self._pairs[key] = self._pair(*key)
        return self._pairs[key]

    def _pair(self, i: int, j: int) -> _Pair | None:  # noqa: PLR0911 - one return per kind of evidence
        """See :meth:`pair`."""
        a, b = self._p[i], self._p[j]
        if not self._same_type(i, j) or not a.folded or not b.folded:
            return None
        la, lb = self.records[i].label, self.records[j].label
        references = a.reference is not None and b.reference is not None
        if references and self.referent(i) is None and self.referent(j) is None:
            # Two anonymous references are one by name only when they refer alike
            # ("Emperor", "the Emperor"); one a name of its own identifies is that name's.
            if a.reference != b.reference:
                return None
            return _Pair(NameEvidence("same", "the same reference"), "same_name")
        if a.folded == b.folded and not references:
            return _Pair(NameEvidence("same", "same name"), "same_name")
        if self.apart(i, j):
            return None
        if b.folded in a.alias_folds:
            return _Pair(NameEvidence("alias", f"{lb!r} is an alias of {la!r}"), "alias", i, j)
        if a.folded in b.alias_folds:
            return _Pair(NameEvidence("alias", f"{la!r} is an alias of {lb!r}"), "alias", j, i)
        best: _Pair | None = None
        for va in self.views(i):
            for vb in self.views(j):
                found = self._words_pair(i, j, va, vb)
                if found is not None and (
                    best is None or _STRENGTH[found.step] < _STRENGTH[best.step]
                ):
                    best = found
        return best

    def reference(self, i: int) -> str | None:
        """What record ``i``'s label refers by when it names no one (:func:`reference_key`), else None."""
        return self._p[i].reference

    def referent(self, i: int) -> int | None:
        """The one named record reference ``i``'s own names say it is, or None (it is anonymous).

        A reference (:func:`reference_key`) is the named person its own
        names are evidence for: a name its output gave it as an alias
        (``Bezúkhov's son``, given ``Pierre``) or the one name its own
        sentences write with its title (``the Emperor`` where they write
        ``the Emperor Alexander``: :func:`text_referent_names`, given as an
        alias), read as any alias is (:meth:`stands_pair`: an alias given to
        one name only, a holder that is the one who could hold it). Never a
        person its output lists beside it, nor one that gave the reference
        as an alias of its own (a bare title is never an alias key:
        :meth:`alias_evidence`): the extractor that lists ``the countess``
        beside ``Countess Véra`` has said they are two (both of the v4 War
        and Peace outputs in which a title rung over the output would have
        joined one did so wrongly), and ``the count`` given to Count
        Bezúkhov in one chunk and to Ilyá Rostóv in another names neither
        elsewhere. Several named records are a referent only when they are
        one by evidence. The exact deduplication reads this per mention, so
        an identified mention joins its referent, never the anonymous
        mentions written alike (:meth:`_pair`).

        Resolution terminates on any input. The referent is a named record,
        never another reference, and a reference holding a name only as its
        own alias is no other holder of it (:meth:`holders`): ``a girl of
        thirteen`` and ``the younger girl``, both given ``Natásha`` in the v5b
        literary extraction of War and Peace Book One, are both Natásha. Until
        2026-10-08 each was a holder in the other's alias check, a pair of
        references is read by their referents (:meth:`_pair`), and each asked
        the other's with no end: the extraction failed at finalization with a
        ``RecursionError``. A resolution that still comes to need itself is a
        cycle with no named record behind it, and the reference resolves to
        no one (it stays a flagged reference). Nothing is computed from a
        referent still being read: the resolution that reaches one is
        abandoned uncached (:class:`_ReferentCycle`) back to the reference it
        reached, so every cached pair, standing and referent holds on what
        the rest resolved to, and no resolution is read inside another
        without end.
        """
        if i in self._referents:
            return self._referents[i]
        if i in self._resolving:
            raise _ReferentCycle(i)
        self._resolving.add(i)
        try:
            found = self._referent(i)
        except _ReferentCycle as cycle:
            if cycle.reference != i:
                raise
            found = None
        finally:
            self._resolving.discard(i)
        self._referents[i] = found
        return found

    def _referent(self, i: int) -> int | None:
        """See :meth:`referent` (uncached)."""
        if self._p[i].reference is None:
            return None
        found = [
            j
            for j in sorted(self.candidates(i))
            if self._p[j].person
            and self._p[j].reference is None
            and self._p[j].words
            and self.stands_pair(i, j) is not None
        ]
        if not found:
            return None
        first = min(found, key=lambda j: record_order_key(self.records[j]))
        return first if self._all_joined(first, found) else None

    def views(self, i: int) -> list[_Profile]:
        """Record ``i``'s names as the word steps read them: its label, given aliases, merged-in names.

        Each carries what every name of the record states (:func:`_as_group`),
        the label first; a name with no naming word gives no view. Evidence
        is read over all of them, never the label alone, so which spelling
        a merge left as the label never decides a later join (audit wave 7).
        """
        if i not in self._views:
            group = self._all_names_of(i)
            out: list[_Profile] = []
            seen: set[str] = set()
            for view in group:
                if view.words and view.folded not in seen:
                    seen.add(view.folded)
                    out.append(_as_group(view, group))
            self._views[i] = out
        return self._views[i]

    def _words_pair(  # noqa: PLR0911 - one return per kind of evidence
        self, i: int, j: int, a: _Profile, b: _Profile
    ) -> _Pair | None:
        """Same-words or subset evidence between name ``a`` of record ``i`` and name ``b`` of ``j``."""
        if not a.words or not b.words or _forms_clash(a, b):
            return None
        if a.words == b.words:
            family = self._same_words_family(i, j, a, b)
            if family is None:
                return None
            return _Pair(
                NameEvidence("same", "same name words"),
                "same_words",
                family=family,
                ends=(i, j),
                words=a.words,
            )
        (s, short), (l_, long_) = (
            ((i, a), (j, b)) if len(a.words) < len(b.words) else ((j, b), (i, a))
        )
        if not short.words < long_.words or _fuller_alias_elsewhere(short, long_.words):
            return None
        fit = _title_fit(short, long_, self._womens_given)
        if fit == "no":
            return None
        if not _generation_fits(short, long_):
            return None
        why = (
            f"{' '.join(sorted(short.words))!r} is "
            f"{' '.join(sorted(long_.words))!r} with words left out"
        )
        family = (s,) if fit == "family" else ()
        return _Pair(
            NameEvidence("subset", why),
            "subset",
            named=l_,
            short=s,
            family=family,
            words=short.words,
        )

    def _same_words_family(
        self, i: int, j: int, a: _Profile | None = None, b: _Profile | None = None
    ) -> tuple[int, ...] | None:
        """The same-words step's checks (audit W3-4): None when they withhold the join.

        The same checks as the subset step, both ways: a generation or a
        gendered title on one name only says there may be two people.
        Otherwise the records whose title fits only if no one else holds the
        name (``family``, :func:`_title_fit`). ``a`` and ``b`` are the names
        of ``i`` and ``j`` compared (:meth:`views`; their labels when None).
        """
        a = self.views(i)[0] if a is None and self.views(i) else (a or self._p[i])
        b = self.views(j)[0] if b is None and self.views(j) else (b or self._p[j])
        if not (_generation_fits(a, b) and _generation_fits(b, a)):
            return None
        fits = {i: _title_fit(a, b, self._womens_given), j: _title_fit(b, a, self._womens_given)}
        if "no" in fits.values():
            return None
        return tuple(k for k, fit in fits.items() if fit == "family")

    def words_of(self, i: int) -> frozenset[str]:
        """Record ``i``'s naming words as family stems."""
        return self._p[i].words

    def _merged_profiles(self, i: int) -> list[_Profile]:
        """The profiles of the names merges folded into record ``i`` (:attr:`NameRecord.merged`).

        The one reading of merged names: the group check (:meth:`apart`,
        :meth:`groups_apart`) and the holders (:meth:`holders`,
        :meth:`sole_holder`) both read them here.
        """
        return self._merged[i]

    def _alias_profiles(self, i: int) -> list[_Profile]:
        """The profiles of the names record ``i`` was given as aliases (descriptors left out)."""
        return self._aliases[i]

    def _names_of(self, members: Iterable[int]) -> list[_Profile]:
        """The profiles of ``members`` and of every name merges folded into them (the group check's)."""
        return [p for x in members for p in (self._p[x], *self._merged_profiles(x))]

    def _all_names_of(self, i: int) -> list[_Profile]:
        """Record ``i``'s label, given aliases and merged-in names: what its evidence reads.

        Wider than the group check (:meth:`_names_of`): a wrong alias the
        extractor gave (``Princess Anna Mikháylovna`` on the hostess) must not
        keep a record apart from its own short name (``Mlle Schérer``), but
        any name the record was given may be the one a shorter name is
        written from.
        """
        return [self._p[i], *self._alias_profiles(i), *self._merged_profiles(i)]

    def apart(self, i: int, j: int) -> bool:
        """Records ``i`` and ``j`` are two people by name (see :func:`_apart`), merged-in names included."""
        if not self._merged_profiles(i) and not self._merged_profiles(j):
            return _apart(self._p[i], self._p[j]) is not None
        return _profiles_apart(self._names_of([i]), self._names_of([j]))

    def groups_apart(self, a: Sequence[int], b: Sequence[int]) -> bool:
        """Joining groups ``a`` and ``b`` would make one person of two.

        What the names state is read per member (:func:`_clash`: gender,
        generation, titles, a described relation) and per name a merge
        folded into a member (:attr:`NameRecord.merged`); given names per
        group, since a group's aliases make its names one person's (``Bóry``
        is ``Borís``'s, so ``Borís Drubetskóy`` takes both).
        """
        return _profiles_apart(self._names_of(a), self._names_of(b))

    # -- holders ----------------------------------------------------------------

    def holders(
        self, i: int, words: frozenset[str], *, fuller: bool = False, family: bool = False
    ) -> list[int]:
        """Records other than ``i`` that could hold a name of naming words ``words``.

        A record holds it when its label's naming words, a given alias's or a
        merged-in name's (:attr:`NameRecord.merged`: ``Pierre`` that absorbed
        ``Pierre Bezúkhov`` still holds ``Bezúkhov``) contain ``words`` (a
        label or merged name must add a word; with ``fuller`` an alias must
        too) and nothing in the two names keeps it apart from ``i``: not
        what they state (:func:`_apart`), nor a family name one writes in its
        feminine form and the other not (:func:`_forms_clash`: ``Márya
        Ivánovna Dólokhova`` holds no ``Dólokhov``). With ``family`` every
        same-type record bearing the words counts, whatever its name says and
        whether or not it adds a word (``Countess Rostóva`` bears the family
        name of ``Count Rostóv``): the family a titled short name may come from.

        A reference (:func:`reference_key`) that holds the name only as an
        alias its own output gave it is no holder: its label names no one, and
        the alias says who it is, as a reference giving an alias is no second
        giver (:meth:`alias_evidence`). Until 2026-10-08 it counted: ``a girl
        of thirteen`` and ``the younger girl``, both given ``Natásha`` in the v5b
        literary extraction of War and Peace Book One, each made the other's
        alias ambiguous, so neither was Natásha (and each asked the other's
        referent with no end: :meth:`referent`).
        """
        if not words:
            return []
        candidates = set.intersection(*(self._postings.get(w, set()) for w in words))
        out = []
        me = self._p[i]
        for j in sorted(candidates - {i}):
            p = self._p[j]
            if not self._same_type(i, j):
                continue
            if not family and (self.apart(i, j) or _forms_clash(me, p)):
                continue
            by_alias = any((words < w) if fuller else (words <= w) for w in p.alias_words)
            by_merged = any(
                (words <= m.words) if family else (words < m.words)
                for m in self._merged_profiles(j)
            )
            by_label = words <= p.words if family else words < p.words
            if p.reference is not None and not (by_label or by_merged):
                continue  # a reference holding it only as its own alias is that name's
            if by_label or by_alias or by_merged:
                out.append(j)
        return out

    def family_free(self, titled: int, other: int, words: frozenset[str] | None = None) -> bool:
        """No one but ``other`` (or who is ``other`` by evidence) bears ``titled``'s naming words.

        ``words`` are the titled name's (one of ``titled``'s names,
        :attr:`_Pair.words`); None reads the label's.

        Where a title fits a name only for want of anything against it
        (:func:`_title_fit`): ``Prince Vasíli`` is ``Vasíli Kurágin`` when no
        one else is a Vasíli, but ``Count Rostóv`` is not ``Nicholas Rostóv``
        while ``Natásha Rostóva`` shows a family the count may head.
        """
        found = self.holders(titled, self.words_of(titled) if words is None else words, family=True)
        return self._all_joined(other, (j for j in found if j != other))

    def title_holders(self, title: str) -> frozenset[int]:
        """The person records whose label, a given alias or a merged-in name states ``title``.

        ``title`` is folded; any spelling of it counts (``Monsieur`` is
        ``Mr``: :attr:`NameConventions.title_class`).
        """
        t = fold(title)
        return frozenset(self._title_postings.get(self.conventions.title_class.get(t, t), ()))

    def sole_title_holder(self, i: int, title: str, among: Iterable[int] | None = None) -> bool:
        """``i`` states ``title`` and every other record that does is ``i`` by pairwise evidence.

        When a bare title in a text (``the prince``, ``Monsieur``) names
        someone: the one candidate holding it (the evidence validator's title
        rung). ``Monsieur Pierre`` beside ``Monsieur Buonaparte`` holds
        ``monsieur`` with another, so ``Monsieur`` alone names neither;
        ``Prince Andrew`` beside ``Andrew Bolkónski, Prince Andrew`` is one
        holder twice. ``among``, when given, are the records that count as
        holders (the text's candidates); ``i`` always does.
        """
        holders = self.title_holders(title)
        if among is not None:
            holders = holders & {i, *among}
        return i in holders and self._all_joined(i, holders - {i})

    def _all_joined(self, anchor: int, others: Iterable[int]) -> bool:
        """Every record of ``others`` is ``anchor`` by pairwise evidence."""
        return all(j == anchor or self.pair(anchor, j) is not None for j in others)

    def sole_holder(self, short: int, long_: int, words: frozenset[str] | None = None) -> bool:
        """``long_`` is the one node that could hold ``short``'s name.

        Every holder must be ``long_`` by pairwise evidence, and so must every
        other short form with the same naming words: ``Prince Bolkónski``
        beside ``old Prince Bolkónski``, who is not ``Prince Andrew
        Bolkónski``, could be either. ``words`` are the short name's (one of
        ``short``'s names, :attr:`_Pair.words`); None reads the label's.
        """
        words = self.words_of(short) if words is None else words
        if not words:
            return True
        if not self._all_joined(long_, self.holders(short, words)):
            return False
        peers = set.intersection(*(self._postings.get(w, set()) for w in words)) - {short, long_}
        return all(
            self.pair(long_, j) is not None
            for j in peers
            if self._bears_exactly(j, words)
            and self._same_type(short, j)
            and not self.apart(short, j)
        )

    def _bears_exactly(self, j: int, words: frozenset[str]) -> bool:
        """Record ``j``'s label, or a name merged into it, has exactly the naming words ``words``."""
        return self._p[j].words == words or any(m.words == words for m in self._merged_profiles(j))

    def no_fuller_holder(self, i: int, j: int, words: frozenset[str] | None = None) -> bool:
        """No record but ``i`` and ``j`` has a fuller name holding their shared naming words.

        ``words`` are the shared ones (:attr:`_Pair.words`); None reads ``i``'s label.
        """
        words = self._p[i].words if words is None else words
        found = set(self.holders(i, words, fuller=True)) | set(self.holders(j, words, fuller=True))
        return not (found - {i, j})

    def alias_evidence(self, owner: int, alias: str, named: int) -> bool:
        """``alias``, given to ``owner``, is evidence that ``owner`` is ``named``.

        It names someone (not a bare title); no record with another name gave
        it (a reference that gave it is no other name: its alias says who it
        is, :meth:`referent`); every record that could hold the name (by label, given alias or
        merged-in name) is ``owner`` by pairwise evidence; and nothing in the
        two names keeps them apart.
        """
        key = fold(alias)
        if is_relation_alias(alias, self.conventions, person=self._p[owner].person):
            return False  # "Natásha's mother" says whose mother, never who
        words = identity_words(alias, self.conventions)
        if not words or not self._p[named].words or self.apart(owner, named):
            return False  # a bare title ("the prince") names no one
        # The record named is never another giver of its own name, and a record giving
        # its own name as an alias gives it to no one else: only a third record under
        # another name makes the key ambiguous (2026-10-07: with mutual aliases, the
        # named record counted as a second giver and no pair merged).
        # A reference giving the alias is no second name (2026-10-07): "the countess" given
        # "Nataly" says who the countess is, not that a second Nataly exists.
        giver_names = {
            self._p[g].folded
            for g in self._givers.get(key, set()) | {owner}
            if g != named and self._p[g].folded != key and self._p[g].reference is None
        }
        if len(giver_names) > 1:
            return False  # given to two different names: it says which family, not whom
        # A reference holding the name only as its own alias is no second holder either
        # (:meth:`holders`).
        same_name = self._by_label.get(self._p[named].folded, set())
        holders = [h for h in self.holders(named, words) if h not in same_name]
        return self._all_joined(owner, holders)

    def alias_naming(self, owner: int, named: int) -> str | None:
        """The alias given to record ``owner`` that is record ``named``'s name, or None."""
        target = self._p[named].folded
        return next((a for a in self.records[owner].aliases if fold(a) == target), None)

    def alias_evidence_between(self, owner: int, named: int) -> bool:
        """An alias given to ``owner`` is ``named``'s name and is evidence that they are one."""
        alias = self.alias_naming(owner, named)
        return alias is not None and self.alias_evidence(owner, alias, named)

    def stands(self, pr: _Pair) -> bool:
        """Pairwise evidence survives the guards that need every record."""
        if pr.step == "alias":
            return self.alias_evidence_between(pr.owner, pr.named)
        words = pr.words or None
        if pr.step == "subset":
            return self.sole_holder(pr.short, pr.named, words) and (
                not pr.family or self.family_free(pr.short, pr.named, words)
            )
        if pr.step == "same_words" and pr.family:
            i, j = pr.ends
            return all(self.family_free(t, j if t == i else i, words) for t in pr.family)
        return True

    def candidates(self, i: int) -> set[int]:
        """Records that could share evidence with ``i``: a naming word, a label, an alias or a reference."""
        p = self._p[i]
        out: set[int] = set(self._by_label.get(p.folded, set()))
        if p.reference is not None:
            out |= self._by_reference.get(p.reference, set())
        out |= self._givers.get(p.folded, set())
        for a in p.alias_folds:
            out |= self._by_label.get(a, set())
        for view in self.views(i):
            for w in view.words:
                out |= self._postings.get(w, set())
        out.discard(i)
        return out

    def stands_pair(self, i: int, j: int) -> _Pair | None:
        """Evidence for ``(i, j)`` that survives every guard but the group check, or None."""
        key = (i, j) if i < j else (j, i)
        if key not in self._standing:
            self._standing[key] = self._stands_pair(*key)
        return self._standing[key]

    def _stands_pair(self, i: int, j: int) -> _Pair | None:
        """See :meth:`stands_pair` (uncached)."""
        pr = self.pair(i, j)
        if pr is None:
            return None
        if pr.step == "same_words" and not self.no_fuller_holder(i, j, pr.words or None):
            return None
        return pr if self.stands(pr) else None

    # -- the one merge predicate ------------------------------------------------

    def allows_merge(self, a: Sequence[int], b: Sequence[int]) -> bool:
        """The names let groups ``a`` and ``b`` be one entity: the one predicate every merge step calls.

        Never when the group check keeps them apart (:meth:`groups_apart`).
        Then, when a pair across the groups has name evidence that survives
        its guards (:meth:`stands_pair`), the names allow it: the exact steps
        (``plan_name_pairs``, the exact dedup's alias keys) join on that
        evidence. A merge with no name evidence (the semantic dedup's, on
        close vectors) is allowed only when no pair across the groups is
        withheld by the guards the exact step applies before it joins
        (:meth:`withheld`): until audit wave 5 the semantic step applied the
        group check alone, so at a cosine of 0.98 ``old Prince Bolkónski``
        merged into ``Prince Andrew`` and ``Count Bezúkhov`` into ``Pierre``.
        """
        if self.groups_apart(a, b):
            return False
        if any(self.stands_pair(x, y) is not None for x in a for y in b if x != y):
            return True
        return all(self.withheld(x, y) is None for x in a for y in b if x != y)

    def _label_view(self, i: int) -> _Profile:
        """Record ``i``'s label carrying what all its names state (:func:`_as_group`)."""
        return _as_group(self._p[i], self._all_names_of(i))

    def withheld(self, i: int, j: int) -> str | None:  # noqa: PLR0911 - one return per guard
        """Why the exact step's guards would hold records ``i`` and ``j`` apart, or None.

        The guards a join needs besides the group check, read for a pair the
        names alone do not join: a generation one name states and the other
        does not (:func:`_generation_fits`), a family name written in two
        forms (:func:`_forms_clash`), two different family names or
        patronymics (:func:`_family_names_differ`), a gendered title that does not fit the
        other name or fits only a name no one else bears (:func:`_title_fit`,
        :meth:`family_free`), a fuller alias that names someone else
        (:func:`_fuller_alias_elsewhere`), and a name another record could
        hold (:meth:`sole_holder`: ``Count Bezúkhov`` beside ``Pierre
        Bezúkhov`` and ``Cyril Vladímirovich Bezúkhov`` is neither).
        """
        if (
            self._p[i].person
            and self._p[j].person
            and ((self._p[i].reference is None) != (self._p[j].reference is None))
        ):
            # A reference is a name only on its own names (``referent``): close vectors
            # are no evidence of whom "the vicomte" is.
            return "a reference joins a name only on its own names"
        if (
            self._p[i].reference is not None
            and self._p[j].reference is not None
            and (self.referent(i) is None) != (self.referent(j) is None)
        ):
            return "a reference its own names identify is that name's, not an anonymous one's"
        # What each record states is read over all its names (:meth:`views`), the words its label's.
        a, b = self._label_view(i), self._label_view(j)
        if not (_generation_fits(a, b) and _generation_fits(b, a)):
            return "one name states a generation the other does not"
        if _forms_clash(a, b):
            return "a family name written in two forms"
        if _family_names_differ(a, b, self._given_words):
            return "different family names or patronymics"
        for t, o in ((i, j), (j, i)):
            if not self._p[t].words:
                continue  # a bare title names no one: it has no name for the title to fit
            fit = _title_fit(self._label_view(t), self._label_view(o), self._womens_given)
            if fit == "no":
                return "a gendered title that does not fit the other name"
            if fit == "family" and not self.family_free(t, o):
                return "a title on a name someone else bears"
        for x, y in ((a, b), (b, a)):
            if x.words and x.words <= y.words and _fuller_alias_elsewhere(x, y.words):
                return "a fuller alias names someone else"
        for short, other in ((i, j), (j, i)):
            if not self.sole_holder(short, other):
                return "another record could hold the name"
        return None


def same_entity(
    a: NameRecord,
    b: NameRecord,
    *,
    conventions: NameConventions | None = None,
    titles: Iterable[str] | None = None,
) -> NameEvidence | None:
    """Why ``a`` and ``b`` are one entity by name, or None (see the module docstring).

    Pairwise only: the guards that need the other names (one holder, alias
    keys, no fuller holder for two short forms) live in :func:`plan_name_pairs`.
    """
    pr = NameIndex([a, b], conventions=conventions, titles=titles).pair(0, 1)
    return None if pr is None else pr.evidence


def ambiguous_short_names(
    records: Sequence[NameRecord],
    *,
    conventions: NameConventions | None = None,
    titles: Iterable[str] | None = None,
) -> set[int]:
    """Indices of records whose name more than one other node could hold.

    ``Count Bezúkhov`` beside ``Pierre Bezúkhov`` and ``Cyril Vladímirovich
    Bezúkhov``: two holders that are not one entity.
    """
    index = NameIndex(records, conventions=conventions, titles=titles)
    out: set[int] = set()
    for i in range(len(records)):
        holders = index.holders(i, index.words_of(i))
        if any(index.pair(x, y) is None for x, y in combinations(holders, 2)):
            out.add(i)
    return out


def record_order_key(record: NameRecord) -> tuple[Any, ...]:
    """What a record is, as a sort key: its folded name, type, aliases, merged names, descriptors, description.

    The name rule orders its joins by this, so the input order of two
    mentions never decides a grouping; the resolver orders a merge group's
    members by it, so which of two records alike in every name keeps its
    node, and the order their descriptions merge in, is what they say, never
    their node ids (until audit wave 8 the description was left out, and two
    such records fell to the id: one graph merged its descriptions in two
    orders in two databases).
    """
    return (
        fold(record.label),
        record.label,
        record.type,
        tuple(sorted(fold(a) for a in record.aliases)),
        tuple(sorted(fold(m) for m in record.merged)),
        tuple(sorted(record.descriptors)),
        record.description,
    )


def plan_name_pairs(
    records: Sequence[NameRecord],
    *,
    conventions: NameConventions | None = None,
    titles: Iterable[str] | None = None,
) -> list[tuple[int, int, NameEvidence]]:
    """Every pair ``(i, j, evidence)`` with ``i < j`` that the name rule joins.

    Pairwise evidence (:func:`same_entity`) that survives the guards needing
    every record (one holder, alias keys, no fuller holder), joined strongest
    first; a join that would put two records the rule keeps apart into one
    group is not made. The returned pairs, unioned, give those groups.
    """
    index = NameIndex(records, conventions=conventions, titles=titles)
    order = [record_order_key(r) for r in index.records]
    found: list[tuple[int, tuple[Any, ...], tuple[Any, ...], int, int, _Pair]] = []
    for i in range(len(records)):
        for j in sorted(index.candidates(i)):
            if j <= i:
                continue
            pr = index.stands_pair(i, j)
            if pr is not None:
                lo, hi = sorted((order[i], order[j]))
                found.append((_STRENGTH[pr.step], lo, hi, i, j, pr))
    parent = list(range(len(records)))
    members: dict[int, list[int]] = {i: [i] for i in range(len(records))}

    def _root(x: int) -> int:
        """Union-find root with path halving."""
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    pairs: list[tuple[int, int, NameEvidence]] = []
    # Strongest first, then by what the names are, never by where they stand in the
    # input: the group check makes the join order matter, and two orderings of the
    # same mentions must give one grouping (audit wave 5).
    for *_key, i, j, pr in sorted(found, key=lambda t: t[:3]):
        ri, rj = _root(i), _root(j)
        if ri != rj:
            if pr.step != "same_name" and not index.allows_merge(members[ri], members[rj]):
                continue  # the chain would join two people the rule keeps apart
            parent[rj] = ri
            members[ri].extend(members.pop(rj))
        pairs.append((i, j, pr.evidence))
    pairs.sort(key=lambda t: (t[0], t[1]))
    return pairs


__all__ = [
    "ALIAS_CROSSED_KNOWN_KEY",
    "ARTICLES",
    "ATTESTED_NAMES_KEY",
    "CAPPED_EDGE_WEIGHT",
    "CAPPED_KEY",
    "DEFAULT_EDGE_WEIGHT",
    "DEFAULT_NAME_CONVENTIONS",
    "DESCRIPTOR_REFERENCE_KEY",
    "DOUBTED_EDGE_FLAG_KEYS",
    "DOUBTFUL_NODE_FLAG_KEYS",
    "EDGE_FLAG_DETAIL_KEYS",
    "EDGE_FLAG_REASON_KEYS",
    "EVIDENCE_REASON_KEY",
    "EXCLUDED_BY_KEY",
    "EXCLUDED_NAME_KEY",
    "GIVEN_ALIASES_KEY",
    "INVERSE_OF_KEY",
    "JUSTIFICATIONS_KEY",
    "LOW_PLAUSIBILITY_KEY",
    "MERGED_NAMES_KEY",
    "MIN_STEM",
    "NAME_RECORD_KEYS",
    "NODE_DESCRIPTION_KEYS",
    "ORPHAN_KEY",
    "PIPELINE_EDGE_FLAG_KEYS",
    "PIPELINE_FLAG_KEYS",
    "PIPELINE_WRITTEN_KEYS",
    "PLAUSIBILITY_SCORE_KEY",
    "REFERENT_UNEVIDENCED_KEY",
    "TYPE_MISMATCH_KEY",
    "TYPE_MISMATCH_REASON_KEY",
    "UNANIMOUS_FLAG_KEYS",
    "UNEVIDENCED_KEY",
    "UNIVERSAL_DETERMINERS",
    "UNIVERSAL_FEMININE_TITLES",
    "UNIVERSAL_GENERATION_WORDS",
    "UNIVERSAL_GIVEN_NAME_TITLES",
    "UNIVERSAL_MASCULINE_TITLES",
    "UNIVERSAL_PARTICLES",
    "UNIVERSAL_PERSON_TYPES",
    "UNIVERSAL_RELATION_VOCABULARY",
    "UNIVERSAL_RELATION_WORDS",
    "UNIVERSAL_ROLE_WORDS",
    "UNIVERSAL_TITLE_EQUIVALENTS",
    "UNIVERSAL_TITLE_WORDS",
    "UNMATCHED_LABEL_KEY",
    "UNPLACED_CORE_TYPE",
    "UNPLACED_TYPE_KEY",
    "WORD_PATTERN",
    "WRITTEN_LABEL_KEY",
    "WRITTEN_TYPE_KEY",
    "EvidenceKind",
    "Gender",
    "NameConventions",
    "NameEvidence",
    "NameForm",
    "NameIndex",
    "NameRecord",
    "NameTables",
    "NodeStanding",
    "SurvivorCandidate",
    "TripleStanding",
    "alias_as_name",
    "ambiguous_short_names",
    "as_conventions",
    "attested_names",
    "bearer_text",
    "descriptor_possessor",
    "doubted_by_filters",
    "edge_justifications",
    "edge_walk_weight",
    "evidence_aliases",
    "fold",
    "has_possessive",
    "identity_word",
    "identity_words",
    "is_bare_role",
    "is_descriptor",
    "is_doubted_record",
    "is_orphan_record",
    "is_reference_record",
    "is_referent_unevidenced",
    "is_relation_alias",
    "is_unevidenced_record",
    "labelled_by_reference",
    "name_form",
    "name_parts",
    "name_record",
    "name_words",
    "names_person",
    "node_description",
    "node_standing",
    "pick_survivor",
    "pipeline_flag",
    "plan_name_pairs",
    "record_attested_names",
    "record_description",
    "record_descriptors",
    "record_given_aliases",
    "record_merged_names",
    "record_order_key",
    "record_support",
    "reference_key",
    "reference_names_a_person",
    "same_entity",
    "surname_key",
    "survivor_candidate",
    "survivor_key",
    "text_referent_names",
    "triple_standing",
    "vocabulary_words",
    "whole_token_pattern",
    "with_pipeline_flag",
]
