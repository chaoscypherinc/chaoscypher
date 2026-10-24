# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""ChatProbeScorer - pass/fail grounded-chat probes, no judge.

Every labelled query is a probe. A query passes only if every check passes;
the headline is the tier-weighted pass rate, the same formula and the same
verdict shape as the extraction probes so both tables read alike.

Bands map to tiers: single-hop and paraphrase are easy (weight 1), multi-hop
and fine-grained discrimination medium (2), out-of-scope and the retrieval
comparison's graph_advantage band hard (3). That band runs the in-scope
checks; the leaderboard's comparison section reports it in plain counts and
keeps it out of the chat column.

Checks (``ChatProbeScorer.CHECKS_BY_BAND`` says which band runs which):

- ``finish_stop`` - the answer ran to completion.
- ``answers_from_graph`` - every curated answer term is named (a term may be
  an any-of alias group, one member of which suffices).
- ``no_unsupported_names`` - every proper name is in the retrieved context or
  the question (possessives, plurals, headings and acronym expansions allowed).
- ``no_unsupported_numbers`` - every number is in the retrieved context or the
  question, so a right name with a wrong year still fails.
- ``must_not_contain`` - none of the query's declared wrong facts (the famous
  wrong year, the tempting wrong join) is stated.
- ``binds_roles`` - which-one-is-which questions: each role follows a person
  who holds it.
- ``no_wrong_members`` - a list answer's set holds none of the members its
  gold leaves out (scorer v8).
- ``leads_with_gold`` - fine-grained band: the gold entity comes first.
- ``declines_when_unsupported`` - out-of-scope band: declines, asserts no decoy.

One more check is reported, never gating: ``cites_the_answer``. The context
labels every chunk (``[CHUNK C3 | ...]``) and models cite those labels; the
check parses the citations and asks whether a cited chunk holds an answer
term. An answer that cites nothing is "uncited" (neither right nor wrong),
an out-of-scope answer is not judged. The verdict carries ``citation``
(``correct``, ``wrong``, ``uncited`` or None) and the row ``citations_cited``,
``citations_correct`` and ``citation_accuracy`` (percent, None when nothing
was cited), so a model that answers right but cites the wrong chunk is
visible before anyone decides whether it should lose the point. Making it
a gate is a later, deliberate scorer version bump.

A query flagged ``answerable: false`` in the fixture (the retrieved context
cannot answer it, so every model is guessing) is left out of the verdicts and
the totals; its id is listed under ``metrics["excluded"]``. Scorer v4
(2026-10-01) added that, pattern-based decline detection, and a tolerance
for a short gap inside an ``X of Y`` alias. ``ChatProbeScorer(ignore_answerable=True)``
scores the flagged questions too: the leaderboard's retrieval comparison
uses it, since a rung's retrieval miss is exactly what that section measures.

Scorer v5 (2026-10-03, after a six-reviewer audit of every question over the
21-model runs): citation markup (``[[cite:C1:S3|file]]``, ``[C2, S10-S12]``,
``C0:5``, ``KG:1``) is stripped before the name and number checks, which had
read two-digit sentence labels as invented names and cite offsets as
unsupported numbers; the name check straightens curly apostrophes and reads
accented capitals, so a quoted "He's" (curly or straight) or "Karágin" is no longer an
invented name; discourse openers ("Furthermore", "Among") are not names; a
multi-word plain term matches only as a whole phrase ("forty thousand" is not
met by "four thousand"); a number is supported by its words up to the
thousands ("eighty", "forty thousand"); an in-scope answer whose conclusion
declines fails ``answers_from_graph`` even when it repeats the terms, since
"the sources do not say, but she is called the niece" answers nothing; and
every answer term must be in the retrieved context, so a right answer from
memory does not count on a grounded benchmark.

Scorer v6 (2026-10-06, after the benchmark pipeline audit, H3/H12/H15):
``must_not_contain`` matches a phrase whole, with no plural added and not
when its last word goes on as a possessive ('niece of Nicholas's father' is
not 'niece of Nicholas'); the names check matches a name's words whole
('Ivan' is no longer supported by 'Ivánovna', nor 'Other' by 'mother') and
'Other'/'Others' are not names; the answer terms fold i/y as the names check
does ('Vasily' names 'Vasíli'), through one shared fold. Every row also
carries :func:`~chaoscypher_core.benchmark.scorers.chat_probe_recipe.scorer_recipe`,
a hash of the scorer's verdicts on fixed samples, because v5 changed its
verdicts without a version bump: the recipe moves whenever the verdicts do,
and a golden test fails until the version is bumped with it.

Scorer v6, wave 2 of the audit (2026-10-06, same version number, a new
recipe: no v6 row was archived under the first v6 recipe): ``must_not_contain``
matches a phrase's possessive again ('the French ambassador's' states 'French
ambassador'; the no-possessive rule passed that wrong answer, and q014's
"Denísov's", q063's "Old Prince Bolkónski's", q114's "Hippolyte's"), and the
fixture writes out the wrong statement where the possessive changes the
referent (q098 'niece of Nicholas Rostóv', q029, q022); decoys are matched
word-bounded, not as substrings; a common English word that opens a sentence
('State that ...', 'Even if ...', 'Claims about ...', 'Found in ...') is not
a name. The recipe now also hashes the scorer's module sources, so a code
change that moves no sample still moves it.

Scorer v6, wave 3 of the audit (2026-10-06, same version number, a new
recipe: still no v6 row archived): wave 2 bounded a name-final phrase with
punctuation ('niece of Nicholas,', 'Old Prince Bolkónski.'), so a citation
marker, a line break, a dash, a colon or the end of the answer after the
name escaped. A phrase now starts and ends with a word: punctuation at an
end is dropped (it bounds nothing) and refused by the fixture guard
(:func:`phrase_problems`), punctuation inside is matched as written, any
whitespace separates two words, and a phrase ending with
:data:`NO_POSSESSIVE_MARKER` ('niece of Nicholas|') is stated by whatever
follows its last word except that word's possessive. On the 28,022
archived answers only q026's 'Answer: The Guards.' moved (15 answers, none
of them a verdict: each fails its answer terms).

Scorer v6, wave 4 of the audit (2026-10-06, same version number, a new
recipe: still no v6 row archived): every check reads the answer through one
normaliser (:func:`normalise_answer`: citation markup, markdown emphasis
``*``/``**``/``_``/backticks, odd spaces and line breaks, curly apostrophes,
and the accent and i/y-w/v folds), where each had normalised on its own:
``must_not_contain`` dropped only ``**`` and had no i/y fold, so 'The
officer is *Nicholas*' and 'Prince Vasily' escaped a declared wrong fact;
decoys, the confusable entity and the role binding gained the i/y fold the
answer terms and the names check had. On the 27,437 archived answers one
verdict moved (Qwen3 14B, 2026-10-05-p2-u-v10-3090, q082 F->P: 'did
**not** receive' now meets 'did not receive') and two answers' failing
checks did (a bold 'young **Princess Bolkónskaya**' alias, a '*   4.' list
number), neither a verdict.

Scorer v6, wave 6 of the audit (2026-10-06, same version number, a new
recipe: still no v6 row archived): the -ii fold leaves a Roman numeral alone,
so "Louis XVII" is not "Louis XVI" (the names check would have accepted an
invented XVII for XVI; no archived verdict moved). Recipe sample s41. And a
zero-width space between two letters reads as a space, not dropped: it is a
word boundary (Unicode word segmentation), so 'Tíkhon\u200bkept' no longer
joins into one unsupported name (the soft hyphen and the word joiner, the
breaks inside a word, are still dropped; 1 zero-width space in 28,125
archived answers, between non-letters, so no archived verdict moved); and a
spaced en dash is a spaced hyphen, so 'Borís \u2013 An officer' meets the
phrase 'Borís - An officer' (q110; no archived answer affected). Recipe
samples s42 and s43 (s37 now a word joiner inside a name).

Scorer v7 (2026-10-08, Denis's ruling on the holdout's h008 and h018, where
a correct answer can hold a forbidden string inside a negation: 'he came on
inspection business, not for the name day'): a ``must_not_contain`` phrase
found inside a negated clause states nothing (:func:`negated_span`,
:func:`is_negated_hit`). The clause runs from the nearest preceding cue
(:data:`NEGATION_CUES`: 'not', "n't", 'never', 'no', 'nor', 'neither',
'cannot', 'rather than', 'instead of') to the next clause boundary ('.',
'!', '?', ';', ':', a line break, a dash, a word that opens a clause of its
own such as 'but' or an exceptive such as 'except', a comma or 'and'/'or'
before a new subject, the end of the answer). 'not only X' adds rather than
negates, except when the phrase begins with 'only' ('there is not only one
Pétya' denies 'only one Pétya'). A phrase that carries its own polarity
(:func:`carries_its_own_polarity`: a cue, an exceptive or inner punctuation
in its words, or the fixture's :data:`STATED_WHEN_NEGATED_MARKER`) is
stated wherever it stands, since the negation is then part of the wrong
fact ('no squadron commander beyond Schubert', 'was not made by Anna
Pávlovna', q126's wrong decline, now marked). A negated phrase is listed
in the check's reason as ``negated``. On the 35,460 archived chat answers
the rule moves one verdict (Qwen3 30B Instruct, 2026-10-03-chat-probes-v2,
q028 F->P: 'the text does not explicitly state that Anatole was sent back')
and one more answer's check reason (Qwen3.5 9B, 2026-10-05-p2-u-order-b-5090,
q023: 'waiting for Borís' negated, the apposition 'eldest son (Borís' still
stated; the verdict fails either way). Recipe samples s44-s51.

Scorer v8 (2026-10-23, after the Ollama docs answer review: fourteen reviewers
graded by hand all 9,372 answers of the 2.2 campaign on the second corpus,
internal/benchmarks/runs/2026-10-23-ollama-review-verdicts.json). A number
and its unit sign are one quantity however spaced ('8\u202fK' is '8K',
'64\u202f000' is '64000'). The numbers check reads a version after its 'v'
as the number ('v0.40.5' supports '0.40.5') and a size in k as m times 1024
or m times 1000 ('64k' supports 65536 and 64000). 'No.' ends a sentence
unless a number or '#' follows it ('No. The hosted API ...' is no name). The
names check takes for no name a short closed list of computing acronyms and
keyboard keys ('URL', 'API', 'CLI', 'JSON', 'Ctrl', 'Esc'), an identifier
made only of placeholder words ('VARIABLE_NAME', 'MY_VAR'), and an English
word that opens a sentence: from a closed list of determiners, prepositions
and sentence adverbs ('Your', 'Within', 'Next'), with a -tion or -sion
ending, an imperative (before an article, a pronoun or 'to': 'Locate the
file', 'Navigate to'), before a comma ('Alternatively,'), after an opening
bracket or quote, in a table cell or after a short bold label. A hyphenated
compound is read by its capitalised parts ('OpenAI-compatible' is supported
by 'OpenAI'). And a new check, :func:`no_wrong_members`, fails a list
answer whose set holds a member the gold leaves out (the fixture's
``wrong_members``; ``holders`` keep one holder's own list out of it):
term matching passed 'Both connect to WhatsApp, Telegram, Slack, Discord
and Signal'. On the review, with its fixture's first round of entries in
both, v8 takes the disagreements from 410 to 154: 263 answers move to the
reviewers' verdict and 7 away, each failed by the reviewers for a fault no
check tests (the known scorer misses, listed in
internal/benchmarks/runs/2026-10-23-ollama-review-known-misses.json). Over
the 74,795 archived chat answers 293 verdicts move, 270 of them on the
Ollama runs; the other 23 (Book One's board, older runs and holdouts) each
failed only for a word the names check read as a name ('Conversely',
'Every', 'No. The', 'Kurágin-related') and pass. Book One's reviewed set is
unchanged (its two standing disagreements, q026 and q028, predate v8).
Recipe samples s52-s61.
"""

from __future__ import annotations

import functools
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

from chaoscypher_core.benchmark.scorers.probes import TIER_WEIGHTS, drop_format_characters, fold
from chaoscypher_core.benchmark.types import ScoreResult


if TYPE_CHECKING:
    from collections.abc import Set as AbstractSet

    from chaoscypher_core.benchmark.queries import LabeledQuery, LabeledQuerySet
    from chaoscypher_core.benchmark.types import RawOutput

CHAT_PROBE_SCORER_VERSION = 8
"""Bumped whenever a verdict can change; the module docstring names what moved in each.

v8 (2026-10-23): the Ollama docs answer review's rules: a number and its unit
however spaced, a version without its 'v', a size in k in binary or decimal
thousands, 'No.' ending a sentence, computing acronyms, keys and placeholder
identifiers no names, English openers (a closed word list, -tion/-sion, an
imperative, a comma or 'to' after the word, a bracket, a table cell or a
label before it), a hyphenated compound read by its capitalised parts, and
the ``no_wrong_members`` check (``wrong_members``, ``holders``).

v7 (2026-10-08): a ``must_not_contain`` phrase inside a negated clause states
nothing (:func:`is_negated_hit`), unless it carries its own polarity
(:func:`carries_its_own_polarity`).

v6 (2026-10-06): whole-phrase ``must_not_contain`` without the plural
extension, word-bounded name support, the shared i/y and w/v fold for answer
terms, 'Other'/'Others' not names. Changelog within v6 (wave 2 of the audit,
the same day, before any v6 row was archived; the recipe moved, the number
did not): the possessive of a ``must_not_contain`` phrase matches again,
decoys are word-bounded, common English sentence openers are not names.
Wave 3, the same day: a ``must_not_contain`` phrase is bounded by its
words, never by punctuation, and may end with the no-possessive marker.
Wave 4, the same day: one answer normaliser (:func:`normalise_answer`) for
every check. Wave 5, the same day: the fold drops format characters (a
soft hyphen or zero-width space inside a name) and reads an en dash inside
a word as a hyphen; the transliteration fold reads the endings -iy and -ii
as -i; the support text is folded once per question. Wave 6, the same day:
a Roman numeral is not an -ii spelling; a zero-width space between two
letters is a space (a word boundary), a spaced en dash a spaced hyphen.
The golden recipe test (``tests/unit/benchmark/test_chat_probe_recipe.py``)
pins the verdict recipe of each version, so a behaviour change without a
bump fails there.
"""

BAND_TIER: dict[str, str] = {
    "factual_single_hop": "easy",
    "paraphrase": "easy",
    "multi_hop": "medium",
    "fine_grained_discrimination": "medium",
    "out_of_scope": "hard",
    "graph_advantage": "hard",
}

# What a grounded answer says when the sources do not support one.
DECLINE_PHRASES: tuple[str, ...] = (
    "not in the",
    "not contain",
    "does not mention",
    "doesn't mention",
    "do not mention",
    "no information",
    "no mention",
    "not mentioned",
    "not provided",
    "not specified",
    "not available",
    "not found in",
    "cannot determine",
    "can't determine",
    "cannot be determined",
    "unable to",
    "i don't have",
    "i do not have",
    "don't know",
    "do not know",
    "not covered",
    "outside the",
    "isn't in the",
    "is not in the",
    "not included",
    "does not include",
    "cannot answer",
    "can't answer",
    "insufficient",
    # "The context does not provide / specify / name ...", "the sources don't
    # say / give ...": declines the list missed until scorer v3 (2026-10-01),
    # which failed correct out-of-scope answers from a dozen models.
    "not present",
    "contain no",
    "contains no",
    *(
        f"{neg} {verb}"
        for neg in ("does not", "doesn't", "do not", "don't")
        for verb in ("provide", "specify", "name", "say", "give", "state")
    ),
)

# Words that start sentences or are capitalised for reasons other than being a name.
_NOT_A_NAME = {
    "the",
    "a",
    "an",
    "this",
    "that",
    "these",
    "those",
    "it",
    "its",
    "in",
    "on",
    "at",
    "of",
    "for",
    "to",
    "by",
    "and",
    "or",
    "but",
    "no",
    "not",
    "yes",
    "there",
    "here",
    "he",
    "she",
    "they",
    "we",
    "you",
    "i",
    "his",
    "her",
    "their",
    "our",
    "as",
    "if",
    "so",
    "however",
    "therefore",
    "based",
    "according",
    "unfortunately",
    "note",
    "answer",
    "question",
    "context",
    "retrieved",
    "sources",
    "source",
    "information",
    "first",
    "second",
    "third",
    "both",
    "one",
    "two",
    "three",
    "none",
    "only",
    "also",
    "after",
    "before",
    "during",
    "while",
    "when",
    "which",
    "what",
    "who",
    "where",
    "why",
    "how",
    "although",
    "though",
    "since",
    "because",
    "with",
    "without",
    "from",
    "into",
    "later",
    "earlier",
    "then",
    "thus",
    "hence",
    "additionally",
    "finally",
    "specifically",
    "notably",
    "instead",
    "rather",
    "given",
    "regarding",
    "per",
    # sentence openers that read as names when capitalised (scorer v3)
    "afterwards",
    "afterward",
    "meanwhile",
    "beyond",
    "despite",
    "nevertheless",
    "similarly",
    "perhaps",
    "maybe",
    "actually",
    "again",
    "multiple",
    "let",
    "does",
    "wait",
    "speaking",
    "saying",
    "looking",
    "checking",
    "searching",
    "scanning",
    # chatty labels and headings, not names
    "here's",
    "step-by-step explanation",
    "step-by-step",
    "explanation",
    "summary",
    "mentions",
    "email",
    "conclusion",
    "reasoning",
    "details",
    "overview",
    "key facts",
    "key points",
    "final answer",
    "short answer",
    # scorer v5: discourse openers, layout words and contractions the audit found read as names
    "moreover",
    "furthermore",
    "firstly",
    "secondly",
    "thirdly",
    "lastly",
    "further",
    "consequently",
    "among",
    "whether",
    "upon",
    "once",
    "overall",
    "end",
    "okay",
    "final",
    "chunk",
    "chunks",
    "sentence",
    "sentences",
    "knowledge",
    "knowledge-graph",
    "graph",
    "entry",
    "citation",
    "citations",
    "cite",
    "cited",
    "referred",
    "it's",
    "he's",
    "she's",
    "i'll",
    "i'm",
    "i've",
    "we'll",
    "we're",
    "they're",
    "there's",
    "that's",
    "what's",
    "let's",
    "isn't",
    "don't",
    "doesn't",
    "didn't",
    "wasn't",
    "can't",
    "cannot",
    "kg",
    # full pass 2026-10-03: adjectives of nationality and reference labels are not names
    "english",
    "french",
    "russian",
    "german",
    "italian",
    "austrian",
    "prussian",
    "reference",
    "references",
    # the source's own author, named in an aside ("War and Peace by Leo Tolstoy")
    "tolstoy",
    "leo",
    # hidden until scorer v6 by the substring rule ('other' inside 'mother', 'another')
    "other",
    "others",
    # sentence openers the Claude rows use (2026-10-03)
    "several",
    "fourth",
    "fifth",
    "sixth",
    "together",
    # determiners, prepositions and sentence adverbs that open a sentence (the Ollama docs
    # review, 2026-10-23)
    "your",
    "my",
    "any",
    "all",
    "every",
    "some",
    "more",
    "many",
    "such",
    "within",
    "unlike",
    "next",
    "otherwise",
    "besides",
    "likewise",
    # generic computing acronyms and keyboard keys: common nouns, not names (the Ollama
    # docs review, 2026-10-23); a short closed list, not every acronym. 'api' joined last:
    # until a hyphenated compound was read by its capitalised parts, a word that is no
    # name left 'OpenAI-compatible API' with no supported word
    "url",
    "api",
    "cli",
    "gui",
    "json",
    "http",
    "https",
    "ctrl",
    "shift",
    "esc",
    "alt",
    "tab",
    "enter",
    "cmd",
}

# An identifier made only of placeholder words ('VARIABLE_NAME', 'MY_VAR', 'YOUR_VARIABLE',
# 'VAR' in 'VAR=value'): the form of a variable, not one (the Ollama docs review,
# 2026-10-23). A real variable has a word of its own ('OLLAMA_HOST', 'MY_HOST').
_PLACEHOLDER_WORD = r"(?:VARIABLE|VAR|NAME|VALUE|KEY|MY|YOUR|SOME|ENV|FOO|BAR|EXAMPLE)"
_PLACEHOLDER = re.compile(_PLACEHOLDER_WORD + r"(?:_" + _PLACEHOLDER_WORD + r")*")

# Titles say what someone is, not who: "Countess Rastoptchin" is not supported by "Countess".
_TITLES = {
    "count",
    "countess",
    "prince",
    "princess",
    "baron",
    "baroness",
    "general",
    "colonel",
    "captain",
    "major",
    "emperor",
    "empress",
    "dowager",
    "abbe",
    "abbé",
    "vicomte",
    "duke",
    "duchess",
    "madame",
    "mademoiselle",
    "monsieur",
    "mlle",
    "mme",
    "lady",
    "lord",
    "sir",
    "doctor",
    "dr",
    "king",
    "queen",
    "tsar",
}


# Citation markup models write around the context's labels (scorer v5): the app's
# own marker, bracketed or parenthesised groups of chunk and sentence labels,
# bare 'S10' / 'C0:5' / 'KG:1' tokens, and the label words that introduce them.
_CITE_MARKER = re.compile(r"\[\[cite:[^\]]*\]\]")
_CITE_LABEL_GROUP = re.compile(
    r"\[(?=[^\[\]\n]*\b(?:C|S|KG)\d)[^\[\]\n]{1,80}\]|\((?=[^()\n]*\b(?:C|S|KG)\d)[^()\n]{1,80}\)"
)
_CITE_TOKEN = re.compile(
    r"\b(?:C\d{1,3}|S\d{1,3}|KG\d{0,3})(?:\s*[:\-\u2013\u2014]\s*(?:C|S)?\d{1,3})*\b"
)
_CITE_WORDS = re.compile(
    r"(?im)^\s*(?:citations?|sources?|cite|knowledge[- ]graph entry)\s*:?\s*$"
    r"|\b(?:citations?|cite|cited)\s*:"
    # written-out labels: "Sentence 5", "Sentences 7-8", "chunk 3" (Nemotron, 2026-10-03)
    r"|\b(?:chunks?|sentences?)\s+\d{1,3}(?:\s*[-\u2013\u2014,]\s*\d{1,3})*\b"
)


def _strip_citations(text: str) -> str:
    """Drop citation markup so labels and offsets are not read as names or numbers."""
    text = _CITE_MARKER.sub(" ", text)
    text = _CITE_LABEL_GROUP.sub(" ", text)
    text = _CITE_WORDS.sub(" ", text)
    return _CITE_TOKEN.sub(" ", text)


def _first_position(text: str, names: list[str]) -> int | None:
    """Earliest position of any of the names in the folded text, or None.

    A name counts from its whole form or from any of its significant words
    (word-bounded), matching how ``answers_from_graph`` accepts a surname
    alone: 'Cerf was on the BBN team; Kahn joined later.' names 'Vint Cerf'
    at position 0.
    """
    hits: list[int] = []
    for name in names:
        whole = text.find(fold(name))
        if whole >= 0:
            hits.append(whole)
        for word in _significant_words(name):
            match = re.search(r"(?<![a-z0-9])" + re.escape(fold(word)) + r"(?![a-z0-9])", text)
            if match:
                hits.append(match.start())
    return min(hits) if hits else None


_MINOR = {"of", "at", "and", "the", "for", "de", "in", "on"}


def _initialism(name: str) -> str:
    """'Stanford Research Institute' -> 'sri'; minor words are skipped."""
    words = [w for w in re.split(r"[\s/,-]+", name) if w and w.lower() not in _MINOR]
    return "".join(w[0] for w in words).lower()


def _significant_words(name: str) -> list[str]:
    """Words worth matching on: four letters or more, or an acronym like TCP."""
    return [
        w.lower()
        for w in re.split(r"[\s/,-]+", name)
        if w and (len(w) >= 4 or (w.isupper() and len(w) >= 2))
    ]


def _mentions_phrase(text: str, name: str) -> bool:
    """The whole name, word-bounded, in the folded text; a plural 's' is allowed.

    'arpa' is not inside 'arpanet'; 'packets' satisfies 'packet'.
    """
    folded = fold(name)
    return bool(re.search(r"(?<![a-z0-9])" + re.escape(folded) + r"s?(?![a-z0-9])", text))


def context_holds_terms(
    context: str,
    answer_terms: Sequence[str | Sequence[str]],
    *,
    alias_evidence: Mapping[str, Sequence[str]] | None = None,
) -> bool:
    """Whether every answer-term group has a specific member in ``context`` (folded, word-bounded).

    The one reading of "the context holds the answer" for the retrieval tools
    (``retrieval_mode_check``, ``embedder_retrieval_check``,
    ``chat_miss_classifier``) and for the fixture's ``answerable`` flag
    (:func:`context_holds_the_answer`, ``answerable_check``). A group is held when one of its members is
    written, as :func:`_mentions_phrase` matches it; a *bare* member (one
    lowercase word) of a group that also has specific members counts for
    nothing: "aunt" beside "left the aunt" matched any mention of the aunt and
    read a missing sentence as a join failure (q084 and q115, read 2026-10-12).
    A group of bare words only keeps them. Curly apostrophes and typographic
    hyphens read as straight ones (:func:`_straight_apostrophes`): a context
    writing the possessive with U+2019 holds a term written with ``'`` (final
    review 2 of 2026-10-12, found when ``answerable_check`` moved onto this
    reading). A member with ``alias_evidence`` (the question file's per-alias
    phrases, ``LabeledQuery.alias_evidence``) counts only when one of its
    phrases is in the context too: "Andrew" holds nothing where the context
    never ties him to the asked person (q029; the move of 2026-10-12 dropped
    this gate, the re-review's N1).
    """
    folded = fold(_straight_apostrophes(context))
    tied = alias_evidence or {}

    def ungated(member: str) -> bool:
        """Whether the member's evidence phrases, if it has any, are written."""
        needs = tied.get(member)
        return not needs or any(_mentions_phrase(folded, _straight_apostrophes(n)) for n in needs)

    for group in answer_terms:
        members = [m for m in ([group] if isinstance(group, str) else list(group)) if ungated(m)]
        if not members:
            return False
        specific = [m for m in members if not _is_bare_term(m)]
        candidates = specific or members
        if not any(_mentions_phrase(folded, _straight_apostrophes(m)) for m in candidates):
            return False
    return True


def _is_bare_term(term: str) -> bool:
    """A single lowercase word of letters: a common noun that any mention of its bearer matches.

    A number, in digits or in words ("25", "fifty"), or a hyphenated compound ("twenty-five")
    is no common noun: it is as specific as its group's phrases (until the final review 2 of
    2026-10-12 a number and a compound read as bare, and "twenty-five" in the context held no
    "twenty-five|twenty five|25").
    """
    word = term.strip()
    return word.isalpha() and word == word.lower() and word not in _NUMBER_WORD_SET


_OF_ALIAS = re.compile(r"^(\w+) of (\w+)$")


def _mentions_alias(text: str, alias: str) -> bool:
    """An alias-group member as a whole phrase, or ``X of Y`` with a short gap.

    'father of Anatole' is satisfied by 'the father of the "prodigal son
    Anatole"' (Opus 5.5, q022): up to four words may stand between X and Y,
    none of them ending a clause ('the Rostov boys, Anatole' does not match).
    """
    if _mentions_phrase(text, alias):
        return True
    m = _OF_ALIAS.fullmatch(fold(alias))
    if not m:
        return False
    head, tail = m.groups()
    gap = r"(?:[^\s.,;:!?]+\s+){0,4}[\"'\u201c\u201d\u2018\u2019(]*"
    return bool(
        re.search(
            r"(?<![a-z0-9])" + re.escape(head) + " of " + gap + re.escape(tail) + r"(?![a-z0-9])",
            text,
        )
    )


# What the sources are called in a decline.
_SOURCE_WORDS = (
    r"(?:sources?|context|passages?|excerpts?|texts?|material|documents?|chunks?|snippets?|"
    r"retrieved)"
)

# Declines no phrase list can enumerate (scorer v4): a negation near a source
# word, "does not <verb>" or "never <verb>" with up to two words between,
# "is not <participle>", "cannot <verb>", "no <noun>", "absent from".
_DECLINE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:not|never|no(?!\s+other\b)|nothing|nowhere|neither)\b[^.;!?]{0,60}?\b(?:in|from|by|within|among)"
        r"\s+(?:the|these|those|any|this|our|its)\s+(?:\w+\s+){0,2}" + _SOURCE_WORDS + r"\b"
    ),
    re.compile(
        r"\b(?:(?:does|do|did)\s*(?:n't|not)|never)\s+(?:\w+\s+){0,2}"
        r"(?:say|state|mention|provide|specify|name|give|identify|indicate|describe|contain|"
        r"include|record|tell|reveal|explain|list|show|address|cover|answer|establish|report|"
        r"note|offer|supply|disclose|confirm|clarify|appear|exist|occur|support)\w*\b"
    ),
    re.compile(
        r"\b(?:is|are|was|were|be|being|been)\s+(?:not|never)\s+(?:\w+\s+){0,2}"
        r"(?:mentioned|stated|provided|specified|available|found|given|named|identified|present|"
        r"included|covered|recorded|described|indicated|known|addressed|supported|answered|"
        r"established|revealed|disclosed|confirmed|shown|listed|offered|supplied|discussed|"
        r"explicit|specific|clear|possible|determinable|answerable)\b"
    ),
    re.compile(
        r"\b(?:cannot|can\s*not|can't|unable to|impossible to|not possible to|no way to|"
        r"not able to)\s+(?:be\s+)?(?:\w+\s+)?"
        r"(?:determine|answer|say|tell|identify|find|confirm|establish|verify|know|locate|"
        r"ascertain|infer|conclude|pin|name|give|provide|state)\w*\b"
    ),
    re.compile(
        r"\bno\s+(?:\w+\s+)?(?:information|mention|record|indication|evidence|reference|detail|"
        r"answer|passage|source|statement|data|basis|account|trace|sign)\w*\b"
    ),
    re.compile(r"\b(?:absent|missing|lacking)\s+(?:from|in)\b"),
    # "there is no person explicitly mentioned as appearing at both" (2026-10-03)
    re.compile(
        r"\b(?:no|none of the|not any)\s+(?:one|person|individual|character|people|name)\b"
        r"[^.;!?]{0,40}?\b(?:mentioned|named|identified|appears?|described|listed|stated)\b"
    ),
    re.compile(
        r"\b(?:insufficient|outside the|beyond the|not covered|unanswerable|unknown from|"
        r"i don't have|i do not have|don't know|do not know|not something i can)\b"
    ),
)


# A labelled final answer: '**Answer:** Schubert.', 'Answer: not in the sources'.
_ANSWER_LABEL = re.compile(r"(?m)^[\s*_#>-]*(?:final\s+)?answer[\s*_]*:[\s*_]*(.+?)\s*$")


def _matches_decline(text: str) -> bool:
    """A phrase from :data:`DECLINE_PHRASES` or any of the decline patterns."""
    return any(p in text for p in DECLINE_PHRASES) or any(p.search(text) for p in _DECLINE_PATTERNS)


def declines(text: str) -> bool:
    """Whether a folded, straight-apostrophe answer declines to answer.

    A decline phrase or pattern anywhere, unless the answer also labels a
    final answer that does not itself decline: 'There is no explicit mention
    of a squadron commander ... Answer: Schubert.' asserts Schubert (Qwen3 8B,
    q075), whatever it hedged on the way.
    """
    if not _matches_decline(text):
        return False
    for m in _ANSWER_LABEL.finditer(text):
        value = m.group(1).strip(" .*_")
        # A label whose value is not a word ('Answer: /think') answers nothing.
        if value and value[0].isalnum() and not _matches_decline(value):
            return False
    return True


def _straight_apostrophes(s: str) -> str:
    """Map curly single quotes to ``'`` and typographic hyphens to ``-`` so spellings compare equal.

    GPT-OSS and Granite write 'twenty\u2011five' with a non-breaking hyphen (U+2011),
    which missed the term 'twenty-five' (real answers, 2026-10-03).
    """
    return (
        s.replace("\u2019", "'")
        .replace("\u2018", "'")
        .replace("\u2010", "-")
        .replace("\u2011", "-")
        .replace("\u2012", "-")
    )


def _mentions(text: str, name: str, raw_text: str = "") -> bool:
    """Loose containment, as the extraction probes use it, plus acronym equivalence.

    The name itself, any significant word of it ('Kleinrock' satisfies
    'Leonard Kleinrock', 'TCP' and 'IP' satisfy 'TCP/IP'), the name's
    initialism ('SRI' for 'Stanford Research Institute'), or - when the name
    is an acronym - a capitalised phrase in the raw text whose initialism is
    the name ('University of California at Los Angeles' for 'UCLA').
    """
    if _mentions_phrase(text, name):
        return True
    words = _significant_words(name)
    if any(re.search(r"(?<![a-z0-9])" + re.escape(w) + r"(?![a-z0-9])", text) for w in words):
        return True
    ini = _initialism(name)
    if len(ini) >= 3 and re.search(r"(?<![a-z0-9])" + re.escape(ini) + r"(?![a-z0-9])", text):
        return True
    if raw_text and name.isupper() and len(name) >= 3:
        return any(_initialism(c) == name.lower() for c in _candidate_names(raw_text))
    return False


# A sentence ends at '.', '!' or '?' and a space, or at a line break. After a
# closing quote, bracket or bold mark it ends only before a capital: '...to
# Nicholas." Additional' ends one, 'etc.) do not' and '"I bet on Dólokhov!" -
# but' do not, and folded text (no capitals) never ends there.
_SENTENCE_END = re.compile(
    r"[.!?][ \t]+"
    r"|[.!?][\"'\u201d\u2019)\]*]+[ \t]+(?=[\"'\u201c\u2018(*]*[A-Z\u00c0-\u00d6\u00d8-\u00de])"
    r"|[ \t]*\n\s*"
)
# A period that ends an abbreviation or an initial ends no sentence: 'Dr. Lorrain',
# 'St. Natalia', 'J. C. R. Licklider'; a possessive's 's' is no initial ("Orlov's. However").
_ABBREVIATION = re.compile(
    r"(?i)(?<![\w.'\u2019])(?:dr|mr|mrs|st|mlle|mme|messrs|no|vol|ch|i\.e|e\.g|[a-z])\.$"
)
# 'No.' abbreviates 'number' only before one ('No. 5', 'No. #3'); before a word it is the
# answer 'No.' and ends its sentence ('No. The hosted API ...'; the Ollama docs review,
# 2026-10-23, where 16 answers read 'No. The', 'No. If', 'No. According' as one name).
_NUMBER_SIGN = re.compile(r"(?i)(?<![\w.'\u2019])no\.$")


def _sentences(text: str) -> list[str]:
    """The sentences of an answer, raw or folded, with bullet marks dropped.

    The one splitter every check uses, so a capitalised run never spans two
    sentences and the decline rules read the same sentences the names check
    does. Until 2026-10-04 there were two: the names check split only before
    a capital and never at a line break, the decline rules split folded text
    with the abbreviations' periods removed, and neither split after a
    closing quote ('...cousin to Nicholas." Additional descriptions'). Until
    2026-10-23 'No.' never ended one (:func:`_ends_abbreviation`).
    """
    out: list[str] = []
    start = 0
    for m in _SENTENCE_END.finditer(text):
        end = m.start() + 1
        if text[m.start()] == "." and _ends_abbreviation(text, end, m.end()):
            continue
        out.append(text[start : m.start() + len(m.group(0).rstrip())])
        start = m.end()
    out.append(text[start:])
    return [s.strip(" *-\u2022\t") for s in out if s.strip(" *-\u2022\t")]


def _ends_abbreviation(text: str, end: int, after: int) -> bool:
    """The period just before ``end`` ends an abbreviation or an initial, not a sentence.

    ``after`` is where the next sentence would start: 'No.' is an abbreviation
    only when a number or a number sign starts there (``_NUMBER_SIGN``).
    """
    if not _ABBREVIATION.search(text, max(0, end - 8), end):
        return False
    if _NUMBER_SIGN.search(text, max(0, end - 8), end):
        return re.match(r"[\d#]", text[after : after + 1]) is not None
    return True


def _candidate_names(answer: str, *, join: bool = True) -> list[str]:
    """Capitalised words that are not sentence-initial function words.

    A heuristic name extractor is enough here: a name is only flagged when
    *none* of its significant words appear in the context, so a stray
    sentence-start word costs nothing unless it is genuinely absent. The
    answer is split into sentences first, so '...at ARPA. The text' yields
    'ARPA', never 'ARPA. The'.
    """
    # a table cell is a sentence of its own: '| Used with | ...' opens with 'Used'
    sentences = [c for s in _sentences(answer) for c in re.split(r"\s*\|\s*", s) if c]
    mid_sentence = {m.group(1) for s in sentences for m in _MID_SENTENCE_CAPITAL.finditer(s)}
    lower_words = set(re.findall(r"(?<![\w'\u2019-])[a-z][a-z'\u2019-]*", answer))
    out: list[str] = []
    for sentence in sentences:
        out.extend(
            _sentence_candidates(
                sentence, join=join, mid_sentence=mid_sentence, lower_words=lower_words
            )
        )
    return out


# A capitalised word after another word or a punctuation mark in its sentence.
_MID_SENTENCE_CAPITAL = re.compile(r"(?<=[\w,;:)\]\"'\u2019\u201d] )([A-Z][\w'\u2019-]*)")
# A capitalised word with an English inflection or derivation: a participle, an
# adverb, an adjective or an abstract noun, which names rarely end in.
_ENGLISH_ENDING = re.compile(
    r"[A-Z][a-z]+(?:ed|ing|ly|al|ive|ous|ful|less|ness|ment|ity|able|ible|tion|sion|wards?|ever)"
)
# Common English words that open a sentence of an answer (most of them leaked
# reasoning: 'State that it is not in the sources', 'Even if he were', 'Claims
# about their son', 'Found in Chunk 3', 'Doc says') and were read as invented
# names until 2026-10-06 (wave 2 of the pipeline audit, from the archived
# answers). They join the English endings as the opener rule's word list; a
# word on neither list is still an opener when the same answer writes it in
# lower case elsewhere, which no name in the corpus is.
_COMMON_OPENERS = frozenset(
    {
        "addressed", "along", "appears", "better", "check", "claims", "combine", "conclude",
        "construct", "contains", "conversation", "correct", "correction", "could", "dialogue",
        "direct", "discusses", "discussion", "doc", "docs", "double", "education", "ends",
        "ensure", "entity", "even", "evidence", "filename", "follow", "found", "generic",
        "glances", "identifies", "identify", "implies", "inheritance", "instruction",
        "interaction", "irrelevant", "just", "keep", "key", "kisses", "lists", "location",
        "look", "make", "mention", "mentioned", "most", "never", "neither", "often", "omit",
        "prompt", "provide", "quote", "refers", "regardless", "rule", "safest", "say", "scan",
        "scene", "search", "sometimes", "state", "stick", "text", "usually",
    }
)  # fmt: skip


# A verb in the imperative: the opening word, then an article, a pronoun or 'to' ('Locate
# the file', 'Send a request', 'Place this in', 'Navigate to', 'Points to'), which a name
# does not take. 'to' also passes an adjective ('Similar to') and, the rule's cost, a name
# that opens a sentence before 'to' (the Ollama docs review, 2026-10-23).
_IMPERATIVE = re.compile(r"[A-Z][a-z]+ (?:the|a|an|this|that|these|those|your|its|it|them|to)\b")


def _is_sentence_opener(
    cand: str, sentence: str, mid_sentence: set[str], lower_words: AbstractSet[str]
) -> bool:
    """A capitalised word that opens the sentence only because it opens it.

    The word starts the sentence, a lower-case word or a comma follows it
    ('Alternatively, you can'; the comma since the Ollama docs review), the answer
    never capitalises it mid-sentence, and it is an English word: built with
    an English ending ('Lying on the sofa', 'Additional descriptions',
    'Described as'), on :data:`_COMMON_OPENERS`, or written in lower case
    elsewhere in the same answer, or an imperative (:data:`_IMPERATIVE`:
    'Locate the file', 'Send a request'). 'Karagin came' is still a name,
    and so are accented, bold and all-capitals words. The endings replaced
    adding each such opener to ``_NOT_A_NAME`` (2026-10-04); the word list
    and the lower-case rule followed (2026-10-06), the -tion and -sion
    endings and the imperative after them (the Ollama docs review, 2026-10-23).
    """
    if cand in mid_sentence or not re.match(r"[A-Z][a-z]+(?:, | [a-z])", sentence):
        return False
    word = cand.lower()
    return (
        bool(_ENGLISH_ENDING.fullmatch(cand))
        or word in _COMMON_OPENERS
        or word in lower_words
        or (" " not in cand and _IMPERATIVE.match(sentence) is not None)
    )


_OPENING_MARKS = "[(\"'\u201c\u2018"
"""Brackets and quotes before a sentence's first word, which still opens it."""


def _sentence_candidates(
    sentence: str,
    *,
    join: bool,
    mid_sentence: set[str],
    lower_words: AbstractSet[str] = frozenset(),
) -> list[str]:
    """Capitalised runs within one sentence; see ``_candidate_names``.

    With ``join``, runs separated only by ', ' are also offered joined, so
    'University of California, Los Angeles' is a candidate whose initialism
    is 'ucla'; each run is still offered on its own.
    """
    out: list[str] = []
    # `join` lets "University of California at Los Angeles" stay one name for
    # initialism matching; the unsupported-names check wants each capitalised
    # run on its own, or "Leo Beranek at MIT" would hide behind Beranek.
    connectors = r"(?:of|and|de|the|for|at|in|on|&)?" if join else ""
    runs: list[tuple[int, int, str]] = []
    # '[Supporting context: ...', '(Optional cloud connection)': the word still opens it
    opens = len(sentence) - len(sentence.lstrip(_OPENING_MARKS))
    # Accented capitals start names too (scorer v5): 'Karágin', 'Élisabeth'.
    for m in re.finditer(
        r"\b([A-Z\u00c0-\u00d6\u00d8-\u00de][\w.'\u2019-]*"
        r"(?:\s+" + connectors + r"\s*[A-Z\u00c0-\u00d6\u00d8-\u00de][\w.'\u2019-]*)*)",
        sentence,
    ):
        cand = m.group(1).strip().rstrip(".,;:!?'\"")
        if not cand or cand.lower() in _NOT_A_NAME or _PLACEHOLDER.fullmatch(cand):
            continue
        # An English word capitalised only because it opens the sentence.
        if m.start(1) == opens and _is_sentence_opener(
            cand, sentence[opens:], mid_sentence, lower_words
        ):
            continue
        # single ALL-CAPS acronyms count; single short capitalised words often don't
        if len(cand) < 3 and not cand.isupper():
            continue
        out.append(cand)
        runs.append((m.start(1), m.start(1) + len(cand), cand))
    if join:
        # 'California, Los Angeles': adjacent runs split only by a comma
        for i in range(len(runs) - 1):
            joined, end = runs[i][2], runs[i][1]
            for start, nxt_end, nxt in runs[i + 1 : i + 3]:
                if not re.fullmatch(r",\s+", sentence[end:start]):
                    break
                joined, end = f"{joined}, {nxt}", nxt_end
                out.append(joined)
    return out


# A whole line that is a label or heading, not a claim: '## Answer',
# '**Key Facts**', 'Mentions:'. Bullets and list numbers are stripped first.
_LIST_MARKER = re.compile(r"^\s*(?:[-*+\u2022]|\d+[.)])\s+")
_LABEL_PREFIX = re.compile(r"^(\*\*[^*\n]{1,60}\*\*\s*:|\*\*[^*\n]{1,60}:\*\*|[^:\n]{1,60}:)\s")


def _is_label(prefix: str, *, listed: bool) -> bool:
    """A line's text up to its colon is a label, not a clause (:func:`_strip_labels`).

    A bold lead of up to six words, as long as a label line may be ('**Ensure
    Apple Intelligence is Set Up**:'); the lead of a list item of up to five; or,
    on any line, a label word that is no name ('Answer:', 'Note:'; the Ollama docs
    review, 2026-10-23).
    """
    words = len(prefix.split())
    if prefix.startswith("**"):
        return words <= 6
    if listed:
        return words <= 5
    return fold(prefix.rstrip(":").strip()) in _NOT_A_NAME


def _strip_labels(answer: str) -> str:
    """Drop headings and label lines, and the label prefix of a list item.

    A model's formatting ('## Answer', '**Step-by-Step Explanation:**', a
    'Mentions:' line, '- **Company Identification:** ...') is capitalised
    for layout, not because it names anything. A label prefix is only
    stripped from a list item or a bold lead, and only when it is short
    (:func:`_is_label`), or when it is a label word ('Answer:'), so a
    sentence with a colon in it keeps its names.
    """
    kept: list[str] = []
    for line in answer.splitlines():
        marker = _LIST_MARKER.match(line)
        core = line[marker.end() :] if marker else line.strip()
        core = core.strip()
        if core.startswith("#") or re.fullmatch(r"\*\*[^*]+\*\*:?", core):
            continue
        if core.endswith(":") and len(core.split()) <= 6:
            continue
        label = _LABEL_PREFIX.match(core)
        if label and _is_label(label.group(1), listed=marker is not None):
            core = core[label.end() :]
        kept.append(core)
    return "\n".join(kept)


def _word_forms(word: str) -> list[str]:
    """The word, and the word less a possessive or plural suffix.

    'kahn's' -> 'kahn', 'imps' -> 'imp', 'engineers'' -> 'engineer'; a stem
    shorter than three letters is not offered.
    """
    forms = [word]
    forms.extend(
        word[: -len(suffix)]
        for suffix in ("'s", "s'", "s", "'")
        if word.endswith(suffix) and len(word) - len(suffix) >= 3
    )
    return forms


def _word_supported(word: str, support: str) -> bool:
    """The word, or the word less a possessive/plural suffix, is a whole word of the support text.

    Word-bounded (scorer v6): until then a substring sufficed, so 'Ivan' was
    supported by 'Ivánovna' and 'Other' by 'mother' (H15 of the 2026-10-06
    pipeline audit). The support's own plural is allowed, so 'Rostóv' is
    supported by 'the Rostóvs'.
    """
    return any(
        re.search(r"(?<![a-z0-9])" + re.escape(f) + r"s?(?![a-z0-9])", support)
        for f in _word_forms(word)
    )


def _transliteration_fold(text: str) -> str:
    """Fold i/y and w/v, the letter pairs that transliterate one Cyrillic letter each.

    'Vasily' and 'Vasíli' (once accents are folded), 'Radziwílov' and
    'Radzivílov' then read alike, and so do the word endings -iy and -ii
    with -i ('Vasiliy', 'Vasilii'; audit wave 5). The one fold both the names check
    (:func:`_iy_variant_supported`) and the answer terms (:func:`_term_matches`)
    apply, so a spelling one of them accepts the other does too; until scorer
    v6 the answer terms folded only w/v.
    """
    return _DOUBLE_I_ENDING.sub(_fold_double_i, text.translate(_TRANSLITERATION))


_TRANSLITERATION = str.maketrans({"y": "i", "Y": "I", "w": "v", "W": "V"})

_DOUBLE_I_ENDING = re.compile(r"(?<![a-z0-9])([a-z]+)ii(?![a-z0-9])")
"""A word ending -ii (or -iy, once y is i): one ending, -i ('Vasiliy', 'Vasilii', 'Vasíli';
audit wave 5). Lower case after a letter only, so the numeral in 'Alexander II' stays."""

_ROMAN_NUMERAL = re.compile(r"^[ivxlcdm]+$")


def _fold_double_i(match: re.Match[str]) -> str:
    """The -ii ending folded to -i, except on a Roman numeral ('XVII' is not 'XVI'; audit wave 6)."""
    word = match.group(0)
    return word if _ROMAN_NUMERAL.match(word) else match.group(1) + "i"


def _word_bounded(term: str, text: str) -> bool:
    """``term`` occurs in ``text`` with no letter or digit on either side."""
    return bool(re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", text))


_ODD_SPACES = re.compile(r"[\u00a0\u2007\u2009\u200a\u202f\u205f\u3000]")
"""Non-breaking, figure, thin, hair, narrow and ideographic spaces: a space, as written."""
_SPACED_UNIT = re.compile(
    r"(?<=\d)[ \t\u00a0\u2007\u2009\u202f]+(?=(?:[kKMG%]|\+(?!\s*\d))(?![^\W\d_]))"
)
"""The space between a number and a unit sign ('8\u202fK', '75 %', '70 +'): one quantity, as
the docs write it (GPT-OSS and Granite space them; the Ollama docs review, 2026-10-23). A
sum ('2 + 2') and a word after the number ('8 Kbps', '8 Mistral') keep theirs."""
_SPACED_THOUSANDS = re.compile(r"(?<=\d)[\u00a0\u2007\u2009\u202f](?=\d{3}(?!\d))")
"""A narrow or non-breaking space between digit groups ('64\u202f000'); a plain space between
two numbers ('11434 434') is a list, not a separator."""
_EMPHASIS = re.compile(r"[*`]+|(?<![^\W_])_+|_+(?![^\W_])")
"""Markdown emphasis and code marks: ``*``, ``**``, backticks, and ``_`` at a word's edge
(an underscore inside a word, ``snake_case``, stays)."""
_DOUBLE_QUOTES = re.compile(r"[\"\u201c\u201d\u00ab\u00bb]")
"""Double quote marks and guillemets: a quoted name is the name (apostrophes stay)."""


@dataclass(frozen=True)
class NormalisedAnswer:
    """One answer as every check reads it (:func:`normalise_answer`).

    Attributes:
        text: Format characters, citation markup, markdown emphasis and code marks removed;
            curly apostrophes and typographic hyphens straight; odd spaces
            plain, runs of spaces one; line breaks kept (the sentence
            splitter and the label stripper read them). Case kept: the
            names check reads capitals.
        folded: ``text`` lower-cased and accent-folded (:func:`~chaoscypher_core.benchmark.scorers.probes.fold`).
        flat: ``folded`` with double quote marks dropped and every run of
            whitespace, line breaks included, one space: what a phrase is
            matched against.
        transliterated: ``flat`` under the i/y and w/v fold
            (:func:`_transliteration_fold`).
        lines: ``flat``, except that a run of whitespace holding a line
            break is one line break rather than one space: the same
            characters at the same positions, so a phrase matches alike in
            both, while :func:`negated_span` can still read a line break
            as the end of a clause (scorer v7).
        marked: ``text`` before its markdown emphasis and code marks are
            dropped: the layout a check reads the answer's lists and bold
            headings from (``*`` bullets, ``**Label:**``; :func:`no_wrong_members`,
            scorer v8), each piece then normalised as the others are.
    """

    text: str
    folded: str
    flat: str
    transliterated: str
    lines: str
    marked: str = ""


@functools.lru_cache(maxsize=8192)
def normalise_answer(answer: str, *, strip_labels: bool = False) -> NormalisedAnswer:
    """The one answer normaliser every check applies (audit wave 4 of 2026-10-06).

    In order: format characters (a soft hyphen or zero-width space inside a
    name, :func:`~chaoscypher_core.benchmark.scorers.probes.drop_format_characters`;
    audit wave 5), citation markup (:func:`_strip_citations`), curly apostrophes
    and typographic hyphens (:func:`_straight_apostrophes`), the space inside a
    quantity (``_SPACED_UNIT``, ``_SPACED_THOUSANDS``), odd spaces,
    then (``strip_labels``, the names check) headings and label lines
    (:func:`_strip_labels`, which reads the ``**`` of a bold label, so it
    runs before) and last the markdown emphasis and code marks. Until wave
    4 each check normalised on its own: ``must_not_contain`` dropped only
    ``**``, so 'The officer is *Nicholas*' and a backticked or ``_``-wrapped
    name escaped a declared wrong fact, and it had no i/y fold, so 'Prince
    Vasily' escaped 'Prince Vasíli', while the answer terms and the names
    check folded both. :func:`cites_the_answer` alone reads the raw answer:
    citations are what it checks.
    """
    text = _straight_apostrophes(_strip_citations(drop_format_characters(answer)))
    text = _SPACED_UNIT.sub("", _SPACED_THOUSANDS.sub("", text))
    text = re.sub(r"[ \t]+", " ", _ODD_SPACES.sub(" ", text))
    if strip_labels:
        text = _strip_labels(text)
    marked = text
    text = _EMPHASIS.sub("", text)
    folded = fold(text)
    unquoted = _DOUBLE_QUOTES.sub("", folded)
    flat = re.sub(r"\s+", " ", unquoted).strip()
    lines = re.sub(r"[^\S\n]+", " ", re.sub(r"\s*\n\s*", "\n", unquoted)).strip()
    return NormalisedAnswer(
        text=text,
        folded=folded,
        flat=flat,
        transliterated=_transliteration_fold(flat),
        lines=lines,
        marked=marked,
    )


def _answer_of(rec: dict[str, Any], *, strip_labels: bool = False) -> NormalisedAnswer:
    """The record's answer through :func:`normalise_answer`."""
    return normalise_answer(rec.get("answer") or "", strip_labels=strip_labels)


def _phrase_form(phrase: str) -> str:
    """A fixture phrase (a decoy, a role, a person) as :attr:`NormalisedAnswer.transliterated` spells it."""
    return _transliteration_fold(fold(_straight_apostrophes(phrase)))


# ---------------------------------------------------------------------------
# checks: (record, query) -> (passed, why)
# ---------------------------------------------------------------------------


def finish_stop(rec: dict[str, Any], _: LabeledQuery) -> tuple[bool, str]:
    """The answer ran to completion."""
    fr = rec.get("finish_reason", "stop")
    return fr == "stop", f"finish_reason={fr}"


def answers_from_graph(rec: dict[str, Any], q: LabeledQuery) -> tuple[bool, str]:
    """The answer names every curated answer term the question asks for.

    ``answer_terms`` lists exactly what a correct, concise answer must
    contain (one term per thing asked). A plain string term is matched
    loosely by ``_mentions`` (any significant word or the initialism
    counts). A tuple term is an any-of alias group: one member suffices,
    and each member must appear as a whole phrase (``_mentions_phrase``:
    word-bounded, plural 's' allowed), never by a single word of it, so
    the alias ``little princess`` is not satisfied by 'the princess'. A
    missing group is reported as ``'Mary|Andrew's sister'``. Curly
    apostrophes (U+2019) are straightened on both sides first, so the
    alias ``Andrew's`` matches either spelling in the answer.

    The retrieval benchmark's ``gold_entities`` list every entity *relevant* to the question, so requiring all of them rewarded
    verbosity; they are only the fallback when a query has no answer terms.
    In that fallback a gold entity the question already names (its
    subject: 'the ARPANET switching equipment') is not required.
    """
    # a term inside a citation tag ('[[cite:KG|Borís member_of Semënov Guards]]') is not said
    normalised = _answer_of(rec)
    answer, text, flat = normalised.text, normalised.folded, normalised.flat
    if q.answer_terms:
        support = _support_text(rec.get("context_text") or "", q)
        missing_terms = [
            "|".join(t) if isinstance(t, tuple) else t
            for t in q.answer_terms
            if not _term_matches(flat, t, answer) and not _spelling_variant_named(flat, t, support)
        ]
        if missing_terms:
            return False, f"answer terms missing={missing_terms}"
        absent = [
            "|".join(t) if isinstance(t, tuple) else t
            for t in q.answer_terms
            if not _term_in_support(_used_aliases(flat, t, answer) or t, support, q.alias_evidence)
        ]
        if absent:
            return False, f"answer terms not in the retrieved context={absent}"
        if not _evidence_in_support(q, support):
            return False, f"the asked fact is not in the retrieved context (evidence={q.evidence})"
        declined = _declining_conclusion(text, q)
        if declined:
            return False, f"declines in its conclusion: {declined!r}"
        return True, "answer terms missing=[]"
    question = fold(q.question)
    wanted = [g for g in q.gold_entities if not _mentions(question, g, q.question)] or list(
        q.gold_entities
    )
    missing = [g for g in wanted if not _mentions(flat, g, answer)]
    return not missing, f"gold entities missing={missing}"


@functools.lru_cache(maxsize=256)
def _folded_support(context_text: str, question: str) -> str:
    """The retrieved context plus the question, straight apostrophes, folded: once per question.

    Every check of one question reads it (the answer terms, the names, the
    numbers), and each folded it again until audit wave 5: folding the
    support was 60% of a board rescore. Cached on the two strings.
    """
    return fold(_straight_apostrophes(context_text + " " + question))


@functools.lru_cache(maxsize=256)
def _transliterated_support(support: str) -> str:
    """:func:`_transliteration_fold` of a folded support text, once per question (not per word)."""
    return _transliteration_fold(support)


@functools.lru_cache(maxsize=256)
def _collapsed_support(context_text: str, question: str) -> str:
    """:func:`_folded_support` with every run of whitespace one space (:func:`_support_text`)."""
    return re.sub(r"\s+", " ", _folded_support(context_text, question))


def _support_text(context_text: str, q: LabeledQuery) -> str:
    """What an answer term is grounded in: the retrieved context plus the question, folded.

    A line break inside a phrase ('Prince Andrew' ending one line, 'Bolkónski'
    opening the next) is a space.
    """
    return _collapsed_support(context_text, q.question)


def _evidence_in_support(q: LabeledQuery, support: str) -> bool:
    """The query declares no ``evidence``, or the support holds one of its phrases."""
    return not q.evidence or any(fold(_straight_apostrophes(e)) in support for e in q.evidence)


def context_holds_the_answer(context_text: str, q: LabeledQuery) -> tuple[bool, str]:
    """Whether a retrieved context holds what a right answer to ``q`` needs.

    Every answer-term group held by the context (:func:`context_holds_terms`,
    the retrieval tools' one reading: a bare member beside specific ones
    counts for nothing, so "aunt" alone does not hold "left the aunt"), and
    one ``evidence`` phrase in the context or the question when the query
    declares some. It is what the fixture's ``answerable`` flag claims about
    a pin file, and ``internal/benchmarks/tools/answerable_check.py``
    compares the two (H13 of the 2026-10-06 pipeline audit). Until the final
    review 2 of 2026-10-12 (M8) the terms were read here by the answer
    grounding's looser test (:func:`_term_in_support`, the question counted),
    a second reading beside the tools'. A query with no answer terms (out of
    scope, or the gold-entity fallback) has nothing to hold: True.
    """
    absent = [
        "|".join(t) if isinstance(t, tuple) else t
        for t in q.answer_terms
        if not context_holds_terms(context_text, [t], alias_evidence=q.alias_evidence)
    ]
    if absent:
        return False, f"answer terms not in the retrieved context={absent}"
    if not _evidence_in_support(q, _support_text(context_text, q)):
        return False, f"no evidence phrase in the retrieved context (evidence={q.evidence})"
    return True, "every answer term and the evidence are in the retrieved context"


def _term_matches(text: str, term: str | tuple[str, ...], raw: str) -> bool:
    """One answer term against the folded answer.

    A single-word plain term matches loosely (``_mentions``: the word, a
    plural, an initialism), and so does a multi-word proper name ('Vinton
    Cerf' by 'Cerf', 'Bolt, Beranek and Newman' by 'BBN'). Any other
    multi-word plain term matches only as a whole phrase (scorer v5: 'forty
    thousand' was met by 'four thousand rubles' under the significant-word
    rule). An alias group matches when one member appears as a whole phrase.
    """
    text = re.sub(r"\s+", " ", text)  # a line break inside a phrase is a space
    if _term_matches_exact(text, term, raw):
        return True
    # i/y and w/v transliterate one Cyrillic letter each: 'Radziwílov' names
    # 'Radzivílov' (2026-10-03), 'Vasily' names 'Vasíli' (scorer v6)
    folded = _transliteration_fold(text)
    return _term_matches_exact(folded, term, raw, transliterate=True)


def _term_matches_exact(
    text: str, term: str | tuple[str, ...], raw: str, *, transliterate: bool = False
) -> bool:
    """``_term_matches`` without the transliteration retry; ``transliterate`` folds the term too."""

    def fw(a: str) -> str:
        a = _straight_apostrophes(a)
        return _transliteration_fold(a) if transliterate else a

    if isinstance(term, str):
        term = fw(term)
        if " " in term.strip() and not _name_like(term):
            return _mentions_phrase(text, term)
        return _mentions(text, term, raw)
    return any(_mentions_alias(text, fw(alias)) for alias in term)


def _name_like(term: str) -> bool:
    """Every word of the term bar the minor ones starts with a capital: a proper name.

    'Vinton Cerf', 'Bolt, Beranek and Newman' and 'Honeywell DDP-516' keep
    the loose rule (a surname or an initialism suffices); 'forty thousand'
    and 'electronic mail' do not. A fixture that wants a capitalised phrase
    matched whole writes it as a one-member alias group (q015, q088).
    """
    words = [w.strip(",.") for w in term.split()]
    return all(w[:1].isupper() for w in words if w and w.lower() not in _MINOR)


def _used_aliases(text: str, term: str | tuple[str, ...], raw: str) -> tuple[str, ...]:
    """The members of an alias group the answer actually names (empty for a plain term).

    Grounding reads these, not the whole group: "Prince Andrew" is not grounded
    by a context that only says "young Bolkónski" (Qwen3 30B, naive, q029).
    """
    if isinstance(term, str):
        return ()
    # names only: a relation word ('cousin') may be a reasoned inference from the
    # context ('Nicholas and Sónya, the niece'), and is grounded as the whole group
    return tuple(
        a for a in term if any(ch.isupper() for ch in a) and _term_matches(text, (a,), raw)
    )


def _term_in_support(
    term: str | tuple[str, ...], support: str, alias_evidence: dict[str, list[str]] | None = None
) -> bool:
    """Whether the term, or any alias of it, is in the folded context plus question.

    A grounded answer can only be right from the sources it was given (scorer
    v5): a model that names the regiment from memory when no retrieved chunk
    holds it has not answered from the context. The test is loose on purpose,
    a word of the term or its stem, so it only catches the clearly absent.
    """
    aliases = [term] if isinstance(term, str) else list(term)
    for alias in aliases:
        needs = (alias_evidence or {}).get(alias)
        if needs and not any(fold(_straight_apostrophes(n)) in support for n in needs):
            continue  # the context never ties this name to the asked person
        digits = alias.replace(",", "").strip()
        if digits.isdigit():  # '40,000' is grounded by 'forty thousand'
            if _number_supported(digits, re.sub(r"(?<=\d),(?=\d{3})", "", support)):
                return True
            continue
        folded = fold(_straight_apostrophes(alias))
        if folded in support:
            return True
        long_words = re.findall(r"[a-z]{8,}", folded)
        if (
            any(c.isupper() for c in alias)
            and long_words
            and all(_transliteration_of(w, support) for w in long_words)
        ):
            return True  # 'Ilyínichna' for the context's 'Ilyníchna' (Sonnet 5.5, q090)
        words = [w for w in re.split(r"[\s'-]+", folded) if len(w) >= 4 and w not in _MINOR]
        for w in words or [folded]:
            stem = re.sub(r"(?:'s|s'|ed|es|s|d)$", "", w)
            # at the start of a word, so 'vers' is not found inside 'conversation'
            if len(stem) >= 4 and re.search(r"(?<![a-z0-9])" + re.escape(stem), support):
                return True
    return False


def _declining_conclusion(text: str, q: LabeledQuery) -> str | None:
    """The sentence in which an in-scope answer declines, or None (scorer v5).

    ``declines`` on the whole answer is too broad for in-scope questions:
    narrative negation ("the count had never given such a dinner") and an
    honest caveat after a stated answer are not declines. The rules, each
    fixed against the 5,358 real answers of 2026-10-03:

    - A labelled final answer ("Answer: not in the sources") that declines fails.
    - An opening sentence that names an answer term answers, unless it
      declines after its last term without a contrast ("does not say who she
      is, though Lise is mentioned"); a next sentence that opens with "but" or
      "however" and names a term rescues it.
    - An opening sentence that declines fails, unless the next sentence opens
      with a contrast and names a term, or a labelled answer later does.
    - Otherwise the closing sentence decides: a decline there fails only when
      the answer terms were not already given earlier (a quoted answer and a
      closing "the text does not show the younger ones shouting it" is an
      answer with a caveat).

    A decline counts only when it refers to the sources or is a bare "cannot
    determine", and never when it names one of the question's ``caveats``
    (a premise the text does not state). Long quotes, parentheticals and
    citations are removed before reading for declines; a bullet or line
    break ends a sentence.
    """
    if not _matches_decline(text):
        return None
    body = _PARENTHESISED.sub(" ", _QUOTED.sub(" ", _strip_citations(text)))
    for m in _ANSWER_LABEL.finditer(body):
        if _is_decline(m.group(1), q):
            return m.group(1)[:100]
    sentences = _sentences(body)
    if not sentences:
        return None
    lead, last = sentences[0], sentences[-1]
    nxt = sentences[1] if len(sentences) > 1 else ""
    rescued = bool(
        nxt
        and _OPENS_WITH_CONTRAST.match(nxt)
        and _first_term_position(nxt, q) is not None
        and not _is_decline(nxt, q)
        # "However, it does mention a character named X" and "However, it seems to be X"
        # mention or guess; they do not answer
        and _MENTION_ONLY.search(nxt) is None
        and _HEDGED.search(nxt) is None
    ) or any(
        _first_term_position(m.group(1), q) is not None and not _is_decline(m.group(1), q)
        for m in _ANSWER_LABEL.finditer(body)
    )
    found: str | None = None
    term_at = _first_term_position(lead, q)
    if term_at is not None:
        if _is_decline(lead, q):
            at = _decline_position(lead)
            last_term = _last_term_position(lead, q) or term_at
            # "Natásha is the girl who ran in, but the context does not say she is
            # thirteen" answers first and caveats after; "the little princess is
            # mentioned, but the sources do not say who she is" only mentions
            caveat_after_answer = (
                at > last_term
                and _CONTRAST.search(lead[last_term:at]) is not None
                and _MENTION_ONLY.search(lead[:at]) is None
            )
            declines_after = at > last_term and not caveat_after_answer
            unanswered = at < last_term and not _CONTRAST.search(lead[at:last_term])
            if (declines_after or unanswered) and not rescued:
                found = lead
    elif _is_decline(lead, q):
        if not rescued:
            found = lead
    elif _is_decline(last, q) and _first_term_position(last, q) is None:
        # terms given before the closing sentence, quotes included: a caveat
        before = _strip_citations(text)
        cut = before.rfind(last[:30]) if last[:30] else -1
        prior = before[:cut] if cut > 0 else ""
        terms = list(q.answer_terms) or list(q.gold_entities)
        if not (prior and all(_term_matches(prior, t, prior) for t in terms)):
            found = last
    if (
        found is None
        and len(sentences) > 1
        and _is_decline(last, q)
        and _disowns_its_term(last, q, text)
    ):
        # whatever the opening answered, a term the closing sentence takes back is not answered
        found = last
    if (
        found is None
        and len(sentences) > 1
        and _is_decline(last, q)
        and _DECLINES_THE_QUESTION.search(last)
    ):
        # "Consequently, the combined answer to the question is not provided in the
        # sources": whatever came before, the answer says it has not answered
        found = last
    return found[:100] if found is not None else None


_DISOWNS = re.compile(
    r"\b(?:say|says|state|states|confirm|confirms|support|supports|show|shows|establish|"
    r"establishes)\s+(?:so|this|that|it)\b|\bnot\s+(?:confirmed|stated|established|supported)\b"
)
"""A decline that points back at the claim just made ("but the sources do not say so")."""


def _disowns_its_term(last: str, q: LabeledQuery, text: str) -> bool:
    """The closing sentence names an answer term only to hedge or disown it, and nothing earlier asserts it.

    "... so she is probably this Annette, but the sources do not say so directly"
    and "The knowledge graph lists Borís as a member of the Semënov Guards, but the
    retrieved passages do not confirm this" (Opus 5.5, 2026-10-04) name the term
    and take it back: the term is not answered. A caveat about another detail
    ("..., but the context does not say she is thirteen") neither hedges the term
    nor points back at it, and stays a caveat.
    """
    last_term = _last_term_position(last, q)
    if last_term is None:
        return False
    at = _decline_position(last)
    if at <= last_term or not (_DISOWNS.search(last[at:]) or _HEDGED.search(last[:at])):
        return False
    before = _strip_citations(text)
    cut = before.rfind(last[:30]) if last[:30] else -1
    prior = before[:cut] if cut > 0 else ""
    terms = list(q.answer_terms) or list(q.gold_entities)
    return any(
        _term_matches(last, t, last) and not (prior and _term_matches(prior, t, prior))
        for t in terms
    )


_HEDGED = re.compile(
    r"\b(?:seems?|seemingly|likely|probably|presumably|possibly|perhaps|might|may be|could be|"
    r"appears to)\b"
)
_DECLINES_THE_QUESTION = re.compile(
    r"\b(?:the answer|the question(?!'s)|your question|what you ask|you ask for|asked for|combined answer|"
    r"the information requested)\b"
)


def _is_decline(sentence: str, q: LabeledQuery) -> bool:
    """A source decline that is not about one of the question's caveats."""
    if not _source_decline(sentence):
        return False
    return not any(fold(_straight_apostrophes(c)) in sentence for c in q.caveats)


_OPENS_WITH_CONTRAST = re.compile(r"^(?:but|however|yet|still|nevertheless|nonetheless)\b")
# A clause that only says a name occurs, rather than asserting what the question asks.
_MENTION_ONLY = re.compile(
    r"\b(?:mentioned|mentions|mention|named|referred to|refers to|refer to|referenced|appears|"
    r"appear|cited|discussed|described as present|spoken of|talked about)\b"
)


# A contrast between a decline and the term that follows it: the term answers.
_CONTRAST = re.compile(
    r"\b(?:but|however|yet|nevertheless|nonetheless|still|that said|only that|except that)\b"
)
# Quoted spans of some length in a model's answer are evidence it cites; a short
# quoted phrase ("Madame de Genlis") is the answer itself and stays.
_QUOTED = re.compile(r"\u201c[^\u201d\n]{40,400}\u201d|\"[^\"\n]{40,400}\"")
_SOURCE_RE = re.compile(r"\b" + _SOURCE_WORDS + r"\b")
# Declines that need no source word: a bare inability, not a negated fact.
_BARE_DECLINES = (
    "cannot determine",
    "can't determine",
    "cannot be determined",
    "cannot answer",
    "can't answer",
    "don't know",
    "do not know",
    "no information",
    "insufficient",
    "unable to",
    "i don't have",
    "i do not have",
    "unanswerable",
    "not possible to determine",
)


def _source_decline(sentence: str) -> bool:
    """A decline about the sources ("not in the context", "the text does not say"), or a bare one."""
    if not _matches_decline(sentence):
        return False
    return bool(_SOURCE_RE.search(sentence)) or any(p in sentence for p in _BARE_DECLINES)


def _decline_position(text: str) -> int:
    """Where the first decline phrase or pattern starts in the folded text (-1: none)."""
    hits = [text.find(p) for p in DECLINE_PHRASES if p in text]
    hits += [m.start() for m in (p.search(text) for p in _DECLINE_PATTERNS) if m]
    return min(hits) if hits else -1


def _last_term_position(text: str, q: LabeledQuery) -> int | None:
    """Where the last answer term (or alias) starts in the folded text, or None."""
    terms: list[str | tuple[str, ...]] = list(q.answer_terms) or list(q.gold_entities)
    hits = []
    for term in terms:
        for alias in [term] if isinstance(term, str) else term:
            at = text.rfind(fold(_straight_apostrophes(alias)))
            if at >= 0:
                hits.append(at)
    return max(hits) if hits else None


def _first_term_position(text: str, q: LabeledQuery) -> int | None:
    """Where the first answer term (or alias) starts in the folded text, or None."""
    terms: list[str | tuple[str, ...]] = list(q.answer_terms) or list(q.gold_entities)
    hits = []
    for term in terms:
        for alias in [term] if isinstance(term, str) else term:
            at = text.find(fold(_straight_apostrophes(alias)))
            if at >= 0:
                hits.append(at)
    return min(hits) if hits else None


def _typo_of_supported(word: str, answer_words: set[str], support: str) -> bool:
    """A word of six letters or more one letter off a word the same answer spells as the context does.

    Qwen3.6 27B wrote 'Karágin' and later 'Kurágin' (2026-10-03). The same-answer
    condition is deliberate: Karágina is a different family in this text, and a
    real confusion (only 'Karágin') still fails.
    """
    if len(word) < 6:
        return False
    limit = 2 if len(word) >= 9 else 1  # 'Drubekkáya' beside 'Drubetskáya' in the same answer
    return any(
        other != word and _within_edits(word, other, limit) and _word_supported(other, support)
        for other in answer_words
    )


def _within_edits(a: str, b: str, limit: int) -> bool:
    """Levenshtein distance of ``a`` and ``b`` is at most ``limit`` (small limits only)."""
    if abs(len(a) - len(b)) > limit:
        return False
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        if min(cur) > limit:
            return False
        prev = cur
    return prev[-1] <= limit


def _iy_variant_supported(word: str, support: str) -> bool:
    """'Vasily' for 'Vasíli' and 'Radziwílov' for 'Radzivílov' name the same person or place.

    i/y and w/v each transliterate one Cyrillic letter, so a name spelled the
    other way is the same name (2026-10-03). Both sides go through
    :func:`_transliteration_fold`, the fold the answer terms use, and the
    match is :func:`_word_supported`'s (word-bounded, possessive and plural
    allowed). Until scorer v6 the possessive was stripped with
    ``rstrip("'s")``, which also ate a name's own final s ('boris' -> 'bori').
    """
    if len(word) < 5 or not set(word) & set("iywv"):
        return False
    return _word_supported(_transliteration_fold(word), _transliterated_support(support))


def _one_letter_off(word: str, other: str) -> bool:
    """Two folded words that spell one name one letter apart, by the length rules below.

    One inserted or dropped letter in a word of eight letters or more
    ('Petersburg' for 'Pétersbourg', Phi-4; 'Ilynínchna' for 'Ilyníchna', Opus
    5.5); one substituted letter only from ten letters ('Radziválov' for
    'Radzivílov', Opus 5.5, 2026-10-04). A short name one substitution off is
    another name in this text ('Karágin' and 'Kurágin' are two families), so it
    stays unmatched. The one definition both the names check and the answer
    terms use.
    """
    if word == other:
        return False
    if len(word) == len(other):
        return len(word) >= 10 and sum(a != b for a, b in zip(word, other, strict=True)) == 1
    if abs(len(word) - len(other)) != 1 or len(word) < 8:
        return False
    short, long_ = (word, other) if len(word) < len(other) else (other, word)
    return any(long_[:i] + long_[i + 1 :] == short for i in range(len(long_)))


def _transliteration_of(word: str, support: str) -> bool:
    """A word one letter off a context word (:func:`_one_letter_off`)."""
    if len(word) < 8:
        return False
    lo, hi = len(word) - 1, len(word) + 1
    return any(
        _one_letter_off(word, other) for other in set(re.findall(rf"[a-z]{{{lo},{hi}}}", support))
    )


def _spelling_variant_named(text: str, term: str | tuple[str, ...], support: str) -> bool:
    """The answer names a one-word term (or alias) with one letter off (:func:`_one_letter_off`).

    Only a spelling the context does not use itself: the text names the minister
    'Rumyántsev' and the family 'Rumyántsovs', so 'Rumyántsev' is another name, not
    a typo of 'Rumyántsov'; 'Radziválov' appears nowhere, so it is 'Radzivílov' misspelt.
    """
    aliases = [term] if isinstance(term, str) else list(term)
    words = set(re.findall(r"[a-z]+", text)) - set(re.findall(r"[a-z]+", support))
    for alias in aliases:
        folded = fold(_straight_apostrophes(alias)).strip()
        if " " in folded or len(folded) < 8:
            continue
        if any(_one_letter_off(w, folded) for w in words if abs(len(w) - len(folded)) <= 1):
            return True
    return False


def _name_parts(word: str) -> str:
    """A hyphenated word less its lower-case parts: the parts that may name something.

    'OpenAI-compatible' is 'OpenAI', 'Windows-specific' 'Windows', 'GPU-discovery'
    'GPU': a lower-case part is an English word, so the compound is supported when
    its capitalised parts are (the Ollama docs review, 2026-10-23; until then the
    whole compound had to be in the context). A compound of capitalised parts
    ('Honeywell-Raytheon') is tested whole, as before.
    """
    parts = word.split("-")
    kept = [p for p in parts if not re.fullmatch(r"[a-z]+", p)]
    return "-".join(kept) if len(parts) > 1 and kept else word


def no_unsupported_names(rec: dict[str, Any], q: LabeledQuery) -> tuple[bool, str]:
    """Every proper name in the answer appears in the retrieved context or the question.

    The chat twin of the extraction probes' ``no_invention``: a model that
    pads a correct answer with facts it remembers from training fails here.

    Tolerated: possessives and plurals ("Kahn's", "IMPs"), headings and
    label lines, an expansion of an acronym the context uses
    ('Massachusetts Institute of Technology' when the context says MIT), and
    an acronym the answer coins for a supported phrase it spells out
    ('University of California, Santa Barbara (UCSB)').
    """
    raw_support = _straight_apostrophes((rec.get("context_text") or "") + " " + q.question)
    support = _folded_support(rec.get("context_text") or "", q.question)
    normalised = _answer_of(rec, strip_labels=True)
    answer = normalised.text

    def significant(cand: str) -> list[str]:
        """The candidate's folded words worth testing (three letters or more).

        A hyphenated word is tested less its lower-case parts (:func:`_name_parts`).
        """
        words = [fold(_name_parts(w)) for w in re.split(r"[\s,]+", cand)]
        return [w for w in words if len(w) >= 3 and w not in _NOT_A_NAME]

    expanded: set[str] = set()  # words of a phrase whose acronym the context uses
    coined: set[str] = set()  # acronyms of phrases the answer spells out, supported
    for phrase in _candidate_names(answer):
        ini = _initialism(phrase)
        if len(ini) < 3:
            continue
        words = significant(phrase)
        # the acronym itself, in capitals, in the context: an expansion of it
        if re.search(
            r"(?<![A-Za-z0-9])" + re.escape(ini.upper()) + r"(?![A-Za-z0-9])", raw_support
        ):
            expanded.update(words)
        elif words and all(_word_supported(w, support) for w in words):
            coined.add(ini)

    bad = []
    answer_words = set(re.findall(r"[a-z]{6,}", normalised.folded))
    for cand in _candidate_names(answer, join=False):
        words = significant(cand)
        named = [w for w in words if w not in _TITLES]
        if not words or not named:
            continue  # only titles: 'the Countess', 'Prince'
        if any(_word_supported(w, support) or w in expanded for w in named):
            continue
        if all(
            _typo_of_supported(w, answer_words, support)
            or _transliteration_of(w, support)
            or _iy_variant_supported(w, support)
            for w in named
        ):
            continue  # Karágin where the same answer also writes Kurágin
        if cand.isupper() and cand.lower() in coined:
            continue
        bad.append(cand)
    return not bad, f"names not in the retrieved context={bad[:4]}"


# A number token: '1969', '114', '3.5', '1,000'; a decade's 's' ('1960s') is
# allowed after it. Not part of a word ('S1', 'IPv6', 'C3' are skipped).
_NUMBER = re.compile(r"(?<![\w.])\d[\d,.]*(?=s?\b)")
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
_NUMBER_WORDS = [
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
    "twenty",
]
_NUMBER_WORD_SET = frozenset({*_NUMBER_WORDS, *filter(None, _TENS), "hundred", "thousand"})
"""Every English number word :func:`_number_in_words` writes: never a bare member (:func:`_is_bare_term`)."""


def _number_in_words(n: int) -> str | None:
    """English words for 0..999,999 ('twenty-five', 'five hundred', 'forty thousand')."""
    if n < 0 or n >= 1_000_000:
        return None

    def below_thousand(k: int) -> str:
        parts: list[str] = []
        if k >= 100:
            parts.append(f"{_NUMBER_WORDS[k // 100]} hundred")
            k %= 100
        if k > 0:
            parts.append(
                _NUMBER_WORDS[k]
                if k < len(_NUMBER_WORDS)
                else _TENS[k // 10] + (f"-{_NUMBER_WORDS[k % 10]}" if k % 10 else "")
            )
        return " ".join(parts)

    if n == 0:
        return "zero"
    words = []
    if n >= 1000:
        words.append(f"{below_thousand(n // 1000)} thousand")
        n %= 1000
    if n:
        words.append(below_thousand(n))
    return " ".join(words)


_SIZE_IN_K = re.compile(r"(?<![a-z0-9.])(\d+(?:\.\d+)?)k(?![a-z0-9])")
"""A size the docs write in thousands ('64k', '4K', '1.5k'), folded; '8kb' is another unit."""


def _size_in_k_supported(token: str, support: str) -> bool:
    """An integer that a size in k of the support writes out: m times 1024, or m times 1000.

    The docs say '64k' and the answer sets ``OLLAMA_CONTEXT_LENGTH=65536``
    or ``64000``: the conversion, not an invented number (twenty-one answers
    in the Ollama docs review, 2026-10-23, most on q081, read it in binary
    thousands, seven in decimal ones).
    """
    if not token.isdigit():
        return False
    n = int(token)
    return any(
        n in (round(float(m.group(1)) * 1000), round(float(m.group(1)) * 1024))
        for m in _SIZE_IN_K.finditer(support)
    )


def _number_supported(token: str, support: str) -> bool:
    """The number occurs in the support text as a number, or in words (0 to 999,999).

    Digit-bounded, so '114' is found in 'rfc 114' and '516' in 'ddp-516', but
    '1' is not found in '[s1]' or '1969'. Words are matched with the hyphen
    or a space ('twenty-five', 'twenty five') and 'one hundred' also as 'a
    hundred' (scorer v5; before it only 0-20 had words). A version prefix
    'v' is no part of the number ('v0.40.5' holds 0.40.5, 'v14' holds 14,
    but 'llama3v14' does not), and a size in k holds the number it stands
    for (:func:`_size_in_k_supported`; both from the Ollama docs review,
    2026-10-23).
    """
    if re.search(r"(?<![a-z0-9])v?" + re.escape(token) + r"(?![0-9]|[.,][0-9])", support):
        return True
    if _size_in_k_supported(token, support):
        return True
    if token.isdigit():
        words = _number_in_words(int(token))
        if words is None:
            return False
        forms = {words, words.replace("-", " ")}
        if words.startswith(("one hundred", "one thousand")):
            forms.add(words.replace("one ", "a ", 1))
        return any(_word_bounded(f, support) for f in forms)
    return False


def no_unsupported_numbers(rec: dict[str, Any], q: LabeledQuery) -> tuple[bool, str]:
    """Every number in the answer appears in the retrieved context or the question.

    The names check passes 'Kahn and Cerf shared the 2005 award'; this one
    does not. A number inside an identifier the context holds ('RFC 114',
    'DDP-516') is found there; list numbering ('1. UCLA') and citation
    markers ('[S1]') are not numbers. Thousands separators are ignored.
    """
    # the names check's folded support (straight apostrophes do not touch a number)
    raw_support = _folded_support(rec.get("context_text") or "", q.question)
    support = re.sub(r"(?<=\d),(?=\d{3})", "", raw_support)
    answer = "\n".join(_LIST_MARKER.sub("", line) for line in _answer_of(rec).text.splitlines())
    bad: list[str] = []
    for m in _NUMBER.finditer(answer):
        token = m.group(0).rstrip(".,")
        token = re.sub(r"(?<=\d),(?=\d{3})", "", token)
        if token and not _number_supported(token, support) and token not in bad:
            bad.append(token)
    return not bad, f"numbers not in the retrieved context={bad[:6]}"


def binds_roles(rec: dict[str, Any], q: LabeledQuery) -> tuple[bool, str]:
    """Which-one-is-which questions: each role follows a person who holds it, and every person gets one.

    Term matching cannot tell "Andrew is the aide-de-camp, Nicholas the hussar"
    from the swap; this reads each role term against the nearest name before
    it (citations and long quotes removed). A term may belong to more than one
    person ('officer' for Andrew and Borís); it is wrong only after a person
    who does not hold it ('Nicholas joins as an officer': he goes as a cadet).
    """
    if not q.roles:
        return True, "no roles declared"
    text = _transliteration_fold(re.sub(r"\s+", " ", _QUOTED.sub(" ", _answer_of(rec).folded)))
    holds = [{fold(_straight_apostrophes(t)) for t in r["roles"]} for r in q.roles]
    marks: list[tuple[int, int]] = []
    for i, r in enumerate(q.roles):
        for alias in r["person"]:
            pat = r"(?<![a-z])" + re.escape(_phrase_form(alias)) + r"(?![a-z])"
            marks.extend((m.start(), i) for m in re.finditer(pat, text))
    marks.sort()
    wrong: list[str] = []
    bound: set[int] = set()
    for term in sorted(set().union(*holds)):
        spelled = _transliteration_fold(term)
        for m in re.finditer(r"(?<![a-z])" + re.escape(spelled) + r"s?(?![a-z])", text):
            before = [who for pos, who in marks if pos < m.start()]
            if not before:
                continue
            who = before[-1]
            if term in holds[who]:
                bound.add(who)
            else:
                wrong.append(f"{term} -> {q.roles[who]['person'][0]}")
    missing = [r["person"][0] for i, r in enumerate(q.roles) if i not in bound]
    return not wrong and not missing, f"roles misbound={wrong[:4]}, without a role={missing}"


# Where a list answer's set may stand (``no_wrong_members``): a clause ends at a semicolon,
# a spaced dash, 'while', 'whereas' and 'but' ('OpenClaw supports ..., while Hermes
# Agent supports ...'), or a comma before a conclusion ('..., so both connect to ...',
# ', and therefore'); a heading is a '#' line, a bold line, a line ending in a colon with
# no sentence end, or a line of at most six words without one ('OpenClaw', 'Hermes
# Agent / Hermes'); a list item's label is up to five words before its colon.
_MEMBER_CLAUSE_BREAK = re.compile(
    r";\s|\s[\u2013\u2014-]+\s|,?\s+(?:while|whereas|but)\s"
    r"|,\s+(?:and\s+)?(?:so|therefore|thus|hence)\s"
)
_MEMBER_HEADING = re.compile(r"^\s*(?:#+\s.*|\*\*[^*]+\*\*:?|\*\*[^*]+:\*\*|[^.!?]{1,80}:)\s*$")
_ITEM_LABEL = re.compile(r"([^:]{1,60}):\s")
# Words that make a clause about all the holders at once: 'both connect to', 'the overlap'.
_AGGREGATE_WORD = re.compile(
    r"(?<![a-z])(?:both|each|common|shared|overlap\w*|intersection)(?![a-z])"
)
# A clause that goes on about the holder of the one before: 'It also bridges ...'.
_CONTINUES = re.compile(r"(?:it|its)\s")


def _member_spans(text: str, member: str) -> list[tuple[int, int]]:
    """Where a fixture member occurs in a folded unit: word-bounded, a plural 's' allowed."""
    folded = fold(_straight_apostrophes(member))
    pattern = r"(?<![a-z0-9])" + re.escape(folded) + r"s?(?![a-z0-9])"
    return [m.span() for m in re.finditer(pattern, text)]


class _SetReader:
    """Reads an answer's lines into the units that can state its set (:func:`_set_units`)."""

    def __init__(self, holders: Sequence[tuple[str, ...]]) -> None:
        """Start with no units, no list, no heading."""
        self.holders = holders
        self.units: list[str] = []
        self.section = False  # under a heading that names one holder
        self.lead: str | None = None  # the line before the current list
        self.block: list[str] | None = None
        self.block_skipped = False
        self.after_one = False  # the last clause was about one holder

    @staticmethod
    def norm(line: str) -> str:
        """One line as the other checks read it, its long quotations dropped."""
        return normalise_answer(_QUOTED.sub(" ", line)).flat

    def names_a_holder(self, text: str) -> bool:
        """Whether the folded text names any holder."""
        return any(_member_spans(text, a) for h in self.holders for a in h)

    def about_one(self, text: str) -> bool:
        """The text names exactly one holder and no word for all of them."""
        named = [h for h in self.holders if any(_member_spans(text, a) for a in h)]
        return len(named) == 1 and _AGGREGATE_WORD.search(text) is None

    def item(self, raw: str, marker: re.Match[str]) -> None:
        """A list item: its label and its text join the current list unless about one holder."""
        if self.block is None:
            self.block = []
            self.block_skipped = self.section or (
                self.lead is not None and self.about_one(self.lead)
            )
        item = self.norm(raw[marker.end() :])
        label = _ITEM_LABEL.match(item)
        if label and len(label.group(1).split()) <= 5:
            if self.about_one(label.group(1)):
                return
            self.block.append(label.group(1))
            item = item[label.end() :]
        if not self.about_one(item):
            self.block.append(item)

    def flush(self) -> None:
        """End the current list: it is one unit, unless its lead or heading is about one holder."""
        if self.block and not self.block_skipped:
            self.units.append("\n".join(self.block))
        self.block = None

    def prose(self, raw: str) -> None:
        """A line that is no list item: a heading sets the section, a sentence gives its clauses."""
        self.flush()
        text = self.lead = self.norm(raw)
        words = len(text.split())
        if _MEMBER_HEADING.match(raw) or (words <= 6 and not re.search(r"[.!?]\s*$", text)):
            self.section = self.about_one(text)
            if words <= 6 or not text.rstrip().endswith(":"):
                return
        if self.section:
            return
        for sentence in _sentences(text):
            for clause in _MEMBER_CLAUSE_BREAK.split(sentence):
                if clause.strip():
                    self.clause(clause)

    def clause(self, clause: str) -> None:
        """A clause is a unit unless it is about one holder or goes on about the last one."""
        one = self.about_one(clause) or (
            self.after_one
            and _CONTINUES.match(clause.strip()) is not None
            and not self.names_a_holder(clause)
        )
        self.after_one = one
        if not one:
            self.units.append(clause)


def _set_units(answer: str, holders: Sequence[tuple[str, ...]]) -> list[str]:
    """The pieces of an answer that can state its set, less those about one holder only.

    Read from the answer's marked lines (:attr:`NormalisedAnswer.marked`; the
    other forms have lost their ``*`` bullets): each
    clause of a prose line is a unit, and so is each list (its items joined,
    each item's label with them). A unit, item, heading or list lead that names
    exactly one holder, and no word for all of them (:data:`_AGGREGATE_WORD`),
    is that holder's own list and is left out; so is a clause opening 'It' or
    'Its' after one, and every line under a one-holder heading until the next
    heading. A list item's label stays in its list even when the item's text is
    about one holder ('5. **Signal**: Hermes Agent connects to it' still lists
    Signal under 'Both connect to:'). Long quotations are dropped first: they
    say what the source says, not what the answer's set is.
    """
    reader = _SetReader(holders)
    for raw in answer.splitlines():
        if not raw.strip():
            continue
        marker = _LIST_MARKER.match(raw)
        if marker:
            reader.item(raw, marker)
        else:
            reader.prose(raw)
    reader.flush()
    return reader.units


def no_wrong_members(rec: dict[str, Any], q: LabeledQuery) -> tuple[bool, str]:
    """A list answer's set holds no member the gold leaves out (scorer v8).

    For a list question the fixture names the members a model adds that the
    gold excludes (``wrong_members``: the guide that confirms an 'API Host',
    not a 'Host URL'; the platform only one of the two tools has). One fails
    the answer when it stands in the answer's set: in a clause or a list
    (:func:`_set_units`) that also names a gold member (any member of an
    answer-term group), outside a negated clause (:func:`is_negated_hit`).
    A wrong member named apart from the gold members ('Other tools, such as
    Goose, say API Host'), or in a clause or list about one of the question's
    ``holders`` ('OpenClaw also has iMessage'), states nothing about the set.
    Term matching passes 'Both connect to WhatsApp, Telegram, Slack, Discord
    and Signal': it names every gold term (the Ollama docs review,
    2026-10-23: 34 answers on q103 and q110).
    """
    if not q.wrong_members:
        return True, "no wrong members declared"
    golds = [m for t in q.answer_terms for m in ([t] if isinstance(t, str) else t)]
    stated: list[str] = []
    for unit in _set_units(_answer_of(rec).marked, q.holders):
        if not any(_member_spans(unit, g) for g in golds):
            continue
        for member in q.wrong_members:
            if member not in stated and any(
                not is_negated_hit(unit, *span) for span in _member_spans(unit, member)
            ):
                stated.append(member)
    return not stated, f"wrong members listed={stated}"


NO_POSSESSIVE_MARKER = "|"
"""Ends a ``must_not_contain`` phrase that is not stated by its last word's possessive.

``"officer is Nicholas|"`` is stated by 'the officer is Nicholas', '... is
Nicholas [[cite:C3:S2]]', '... is Nicholas.' at the end of the answer, before
a line break, a dash or a colon; not by 'the officer is Nicholas's friend
Borís' nor 'Nicholas' friend'.

A documented limit: for a phrase whose last word ends in s, an apostrophe,
a space and a word after that word read as its possessive ('Borís' friend'),
so a single-quoted s-final name followed by more words ("the officer is
'Borís' and ...") is not matched; the closing quote cannot be told from the
possessive apostrophe. Write such a phrase without the marker when the
possessive does not change the referent.
"""

STATED_WHEN_NEGATED_MARKER = "!"
"""Starts a ``must_not_contain`` phrase that a negation around it does not exempt (scorer v7).

For a wrong fact the answer states by denying something: q126's
``"!information identifying which Prince Bolkónski is going to the war"``
is the wrong decline 'the context does not contain information identifying
which ...', so the negation that would exempt any other phrase
(:func:`is_negated_hit`) is the wrong fact itself. A phrase that carries
its own polarity needs no marker (:func:`carries_its_own_polarity`).
"""

_EDGE = re.compile(r"^[^\w]+|[^\w]+$")
"""Punctuation (or space) at either end of a phrase: never part of what it matches."""


def wrong_fact_words(phrase: str) -> tuple[tuple[str, ...], bool]:
    """A ``must_not_contain`` phrase as the scorer reads it: its folded words, and the marker.

    The words are the phrase's whitespace-separated tokens, folded, with any
    punctuation at the phrase's two ends dropped: punctuation *inside* a
    phrase is matched as written ('his son, Borís' needs the comma), but a
    trailing ',' or '.' bounds nothing (audit wave 3 of 2026-10-06: 'Old
    Prince Bolkónski.' let '... Bolkónski [[cite:...]]', a line break, a dash
    and the end of the answer through). The one marker is
    :data:`NO_POSSESSIVE_MARKER` at the end. Returns ``(words, no_possessive)``.
    """
    text = fold(_straight_apostrophes(phrase)).strip()
    no_possessive = text.endswith(NO_POSSESSIVE_MARKER)
    if no_possessive:
        text = text[: -len(NO_POSSESSIVE_MARKER)]
    return tuple(_EDGE.sub("", text).split()), no_possessive


def phrase_problems(phrase: str) -> list[str]:
    """Why a fixture's ``must_not_contain`` phrase is not written as the scorer reads it; empty when it is.

    A phrase starts and ends with a word, its words separated by single
    spaces, optionally preceded by :data:`STATED_WHEN_NEGATED_MARKER` and
    followed by :data:`NO_POSSESSIVE_MARKER`; punctuation may sit inside it
    ('his son, Borís', 'bet) that led to') and is matched as written.
    Punctuation at an end was the wave-2 way to bound a phrase ('niece of
    Nicholas,') and bounds nothing now, so it is refused. The fixture guard
    test runs this on every phrase of every shipped fixture.
    """
    body = phrase.removesuffix(NO_POSSESSIVE_MARKER).removeprefix(STATED_WHEN_NEGATED_MARKER)
    problems: list[str] = []
    if not body.strip():
        return ["no words"]
    if _EDGE.search(_straight_apostrophes(body).replace("'", "a")):
        problems.append("punctuation or space at an end (it bounds nothing: use the marker)")
    if "  " in body or "\n" in body or "\t" in body:
        problems.append("words not separated by single spaces")
    if NO_POSSESSIVE_MARKER in body:
        problems.append(f"{NO_POSSESSIVE_MARKER!r} anywhere but at the end")
    if body.startswith(STATED_WHEN_NEGATED_MARKER):
        problems.append(f"{STATED_WHEN_NEGATED_MARKER!r} more than once")
    return problems


def must_not_contain(rec: dict[str, Any], q: LabeledQuery) -> tuple[bool, str]:
    """The answer states none of the query's declared wrong facts.

    ``must_not_contain`` holds the famous wrong answer (a year off by one,
    'Network Control Protocol', the other node's lab). Each term is matched
    by :func:`_phrase_hits`: its words in order, word-bounded, with no
    plural added and its possessive matched unless the phrase ends with
    :data:`NO_POSSESSIVE_MARKER`; the loose significant-word rule is not
    used, or 'Network Control Protocol' would match any answer that says
    'network'. An occurrence inside a negated clause states nothing
    (:func:`is_negated_hit`, scorer v7): 'he came on inspection business,
    not for the name day' does not state 'for the name day'. Such phrases
    are reported as ``negated``, never as a failure, unless the phrase
    carries its own polarity (:func:`carries_its_own_polarity`), when the
    negation is part of the wrong fact.
    """
    if not q.must_not_contain:
        return True, "no wrong facts declared"
    # markdown emphasis, odd spaces, line breaks and quote marks do not hide a stated wrong
    # fact: 'lists her as "Princess Mary Bolkónskaya"' states it (Sonnet 5.5, 2026-10-04), and
    # so do 'The officer is *Nicholas*' and 'Prince Vasily' for 'Prince Vasíli' (audit wave 4).
    # ``lines`` is ``flat`` with its line breaks kept (same matches), so a line break ends a
    # clause for the negation rule; its transliteration is ``transliterated``, likewise.
    answer = _answer_of(rec)
    lines = answer.lines
    spellings = ((lines, False), (_transliteration_fold(lines), True))
    hits: list[str] = []
    negated: list[str] = []
    for t in q.must_not_contain:
        found = [(text, tr, span) for text, tr in spellings for span in _phrase_hits(text, t, tr)]
        if not found:
            continue
        if carries_its_own_polarity(t) or any(
            not is_negated_hit(text, *span, transliterate=tr) for text, tr, span in found
        ):
            hits.append(t)
        else:
            negated.append(t)
    why = f"wrong facts stated={hits}"
    return not hits, why + (f", negated={negated}" if negated else "")


@functools.cache
def _wrong_fact_pattern(phrase: str, *, transliterate: bool = False) -> re.Pattern[str] | None:
    """The compiled match for one phrase (:func:`_phrase_hits`); None for a phrase of no words.

    ``transliterate`` spells the words under the i/y and w/v fold, to match
    :attr:`NormalisedAnswer.transliterated`.
    """
    words, no_possessive = wrong_fact_words(phrase)
    if transliterate:
        words = tuple(_transliteration_fold(w) for w in words)
    if not words:
        return None
    head = r"(?<![^\W_])" if words[0][:1].isalnum() else ""
    tail = r"(?![^\W_])" if words[-1][-1:].isalnum() else ""
    if no_possessive:
        # not 'Nicholas's father', nor 'Nicholas' father' (an s-final word's possessive)
        tail += r"(?!'s(?![^\W_]))"
        if words[-1].endswith("s"):
            tail += r"(?!'\s+[^\W\d_])"
    return re.compile(head + r"\s+".join(re.escape(w) for w in words) + tail)


def _phrase_hits(text: str, phrase: str, transliterate: bool = False) -> list[tuple[int, int]]:
    """Each ``(start, end)`` where a declared wrong fact occurs: its words in order, word-bounded.

    ``text`` is the folded answer (:attr:`NormalisedAnswer.lines`). The
    phrase's words (:func:`wrong_fact_words`) follow one another as written,
    any run of whitespace between two of them.
    Unlike :func:`_mentions_phrase` no plural 's' is added. A possessive is
    the phrase: 'the fete at the French ambassador's' states the wrong host
    'French ambassador', and "Denísov's regiment" names 'Denísov'. Where the
    possessive changes the referent ('Sónya is the niece of Nicholas's father'
    is the right relation; 'niece of Nicholas' the wrong one) the phrase ends
    with :data:`NO_POSSESSIVE_MARKER` ('niece of Nicholas|'): then whatever
    follows the last word states it (a citation, a full stop, a line break, a
    dash, the end of the answer) except its possessive. Until wave 3 of the
    2026-10-06 audit the fixture spelled that out with trailing punctuation
    ('niece of Nicholas,', 'niece of Nicholas.'), and every other continuation
    escaped. Punctuation inside a phrase stays literal: tolerating it (a
    comma as good as a space) made 'Sónya and Borís' match the right 'Nicholas
    and Sónya, and Borís and Natásha' in the archive. With ``transliterate``
    the phrase is spelled under the i/y and w/v fold and ``text`` is
    expected so too. Whether an occurrence *states* the fact is
    :func:`must_not_contain`'s call: one inside a negated clause does not
    (:func:`is_negated_hit`).
    """
    pattern = _wrong_fact_pattern(phrase, transliterate=transliterate)
    return [] if pattern is None else [m.span() for m in pattern.finditer(text)]


NEGATION_CUES: tuple[str, ...] = (
    "not",
    "n't",
    "never",
    "no",
    "nor",
    "neither",
    "cannot",
    "rather than",
    "instead of",
)
"""Words that negate the rest of their clause (:func:`negated_span`; scorer v7).

``n't`` is any contraction ('isn't', 'wasn't', 'didn't', 'doesn't'); 'cannot'
is 'can not' written as one word. A cue followed by no word ('No, he came
to see Pierre', 'whether or not.') negates nothing, nor do the idioms in
:data:`NOT_NEGATING` ('no doubt' asserts).
"""

NOT_NEGATING: tuple[str, ...] = ("no doubt", "no other than")
"""Idioms that open with a cue and assert: 'no doubt he came to see Pierre'."""

ADDITIVE_AFTER_NOT: tuple[str, ...] = ("only", "just", "merely", "simply")
"""Words after which 'not' adds rather than negates: 'not only X (but also Y)' asserts X.

So 'he came not only to see Pierre' states 'to see Pierre'. The one exception
is a phrase that itself begins with the word: 'there is not only one Pétya'
negates 'only one Pétya', the very claim the phrase forbids, so it passes.
"""

CLAUSE_OPENERS: tuple[str, ...] = (
    "but",
    "however",
    "although",
    "though",
    "whereas",
    "because",
    "since",
    "instead",
    "rather",
    "except",
    "besides",
    "beyond",
    "other than",
    "apart from",
)
"""Words that end a negated clause: they open a clause of their own ('not on business but
to see Pierre') or except from the negation ('he wanted nothing except to see Pierre').
'instead' and 'rather' only when they are not the cues 'instead of' and 'rather than'."""

NEW_SUBJECTS: tuple[str, ...] = (
    "he",
    "she",
    "it",
    "they",
    "we",
    "i",
    "you",
    "there",
    "this",
    "that",
    "these",
    "those",
    "the",
    "a",
    "an",
    "his",
    "her",
    "its",
    "their",
    "my",
    "our",
    "your",
    "so",
    "yet",
    "then",
    "which",
    "who",
    "only",
)
"""Words that start a new clause after a comma, 'and' or 'or': 'not for the name day,
he came to see Pierre' asserts its second clause, and 'the context does not state what
happened, only that his father was ordered out' its 'only' clause (q028, Gemma 4 26B)."""


def _alternation(words: tuple[str, ...], *, transliterate: bool) -> str:
    """A regex alternation of ``words`` as the (optionally i/y-folded) answer spells them."""
    spelled = (_transliteration_fold(w) if transliterate else w for w in words)
    return "|".join(re.escape(w).replace(r"\ ", r"\s+") for w in spelled)


_WORD_START = r"(?<![^\W_])"
_WORD_END = r"(?![^\W_])"


@functools.cache
def _negation_patterns(*, transliterate: bool) -> tuple[re.Pattern[str], re.Pattern[str]]:
    """The cue and the clause-boundary patterns of :func:`negated_span`, compiled once per spelling."""
    words = functools.partial(_alternation, transliterate=transliterate)
    plain_cues = tuple(c for c in NEGATION_CUES if c != "n't")
    cue = re.compile(
        f"{_WORD_START}(?:{words(plain_cues)}){_WORD_END}"
        # a contraction: 'isn't', 'didn't', 'doesn't'
        rf"|(?<=[^\W\d_])n't{_WORD_END}"
    )
    boundary = re.compile(
        # sentence and clause punctuation, a line break, a dash between words (en, em)
        r"[.!?;:\n]|\s-+\s|[\u2013\u2014]"
        # a word that opens a clause of its own ('but'; not the cues 'instead of', 'rather than')
        f"|{_WORD_START}(?:{words(CLAUSE_OPENERS)}){_WORD_END}"
        rf"(?!\s+(?:{words(('of', 'than'))}){_WORD_END})"
        # a comma, 'and' or 'or' before a new subject
        f"|(?:,|{_WORD_START}(?:{words(('and', 'or'))}){_WORD_END})"
        rf"\s+(?=(?:{words(NEW_SUBJECTS)}){_WORD_END})"
    )
    return cue, boundary


def _governs(text: str, cue: re.Match[str], start: int, *, transliterate: bool) -> bool:
    """Whether a negation cue negates a phrase that begins at ``start`` (:data:`NEGATION_CUES`)."""
    after = re.compile(r"\s+([^\W_][\w']*)").match(text, cue.end())
    if after is None:
        return False  # 'No, he came ...': the cue negates no word
    idioms = _alternation(NOT_NEGATING, transliterate=transliterate)
    if re.compile(f"(?:{idioms}){_WORD_END}").match(text, cue.start()):
        return False
    additive = _alternation(ADDITIVE_AFTER_NOT, transliterate=transliterate)
    if cue.group(0) == "not" and re.fullmatch(additive, after.group(1)):
        # 'not only to see Pierre' asserts it; 'not only one Pétya' negates 'only one Pétya'
        return after.start(1) == start
    return True


def negated_span(
    text: str, start: int, end: int, *, transliterate: bool = False
) -> tuple[int, int] | None:
    """The negated clause a phrase found at ``text[start:end]`` lies in, or None (scorer v7).

    The clause runs from the nearest negation cue before the phrase
    (:data:`NEGATION_CUES`) to the next clause boundary after it: ``.``,
    ``!``, ``?``, ``;``, ``:``, a line break, a dash between words, a word
    that opens a clause of its own or excepts from the negation
    (:data:`CLAUSE_OPENERS`: 'not on business but to see Pierre' asserts its
    second half), a comma or 'and'/'or' before a new subject or 'only'
    (:data:`NEW_SUBJECTS`), or the end of the answer.
    The cue and the phrase must share one clause: a negation in the sentence
    before, or before any boundary, negates nothing here. The phrase's own
    words are the fixture's and are never read as a boundary. 'not only' (and
    'not just', 'not merely', 'not simply') adds rather than negates
    (:data:`ADDITIVE_AFTER_NOT`), except of a phrase that begins with that
    word. A cue after the phrase ('to see Pierre? No.') negates nothing.

    ``text`` is :attr:`NormalisedAnswer.lines` or, with ``transliterate``,
    its i/y and w/v fold; the cue and boundary words are spelled to match.

    Documented limits: a comma alone does not end the clause, so a negated
    list stays negated ('not Borís, Nicholas or Dólokhov') and so does a
    comma splice with no new subject ('not for the name day, to see
    Pierre'); a cue that does not govern the phrase is read as if it did
    ('it was not long before he came to see Pierre'). Where the boundary
    lists are in doubt they end the clause, which keeps the hit.
    """
    cue_pattern, boundary = _negation_patterns(transliterate=transliterate)
    clause_start = 0
    for m in boundary.finditer(text):
        if m.end() > start:
            break
        clause_start = m.end()
    cues = [
        m
        for m in cue_pattern.finditer(text, clause_start, start)
        if _governs(text, m, start, transliterate=transliterate)
    ]
    if not cues:
        return None
    after = boundary.search(text, end)
    return cues[-1].start(), after.start() if after else len(text)


_PUNCTUATION_INSIDE = re.compile(r"[^\w\s'-]")


@functools.cache
def carries_its_own_polarity(phrase: str) -> bool:
    """Whether a ``must_not_contain`` phrase is stated even inside a negated clause (scorer v7).

    Such a phrase is matched as before the negation rule, wherever it stands.
    It carries its own polarity when, among its words (:func:`wrong_fact_words`),
    it has a negation cue ('not made by Anna Pávlovna', 'age is not stated':
    the wrong fact is a denial, and a cue before it is a double negation or
    an unrelated one, such as a quoted "I don't like him"), a clause
    boundary or an exceptive ('squadron commander beyond Schubert': 'no
    squadron commander beyond Schubert' names Schubert as the commander), or
    punctuation (an apposition or a relative clause, 'eldest son (Borís',
    'Nicholas, called Pétya': a negation does not reach into what an
    apposition identifies); or when it starts with
    :data:`STATED_WHEN_NEGATED_MARKER`, the fixture's word for a wrong
    decline with its negation outside the phrase. A first word in
    :data:`ADDITIVE_AFTER_NOT` is no polarity of its own: 'only one Pétya'
    is denied by 'not only one Pétya'.
    """
    if phrase.lstrip().startswith(STATED_WHEN_NEGATED_MARKER):
        return True
    words = wrong_fact_words(phrase)[0]
    text = " ".join(words)
    cue, boundary = _negation_patterns(transliterate=False)
    return bool(cue.search(text) or boundary.search(text) or _PUNCTUATION_INSIDE.search(text))


def is_negated_hit(text: str, start: int, end: int, *, transliterate: bool = False) -> bool:
    """Whether a wrong fact found at ``text[start:end]`` sits in a negated clause (:func:`negated_span`).

    Such an occurrence states nothing: 'he came on inspection business, not
    for the name day' does not state 'for the name day', and 'there is not
    only one Pétya' does not state 'only one Pétya' (Denis, 2026-10-08,
    after the holdout's h008 and h018 collisions).
    """
    return negated_span(text, start, end, transliterate=transliterate) is not None


def leads_with_gold(rec: dict[str, Any], q: LabeledQuery) -> tuple[bool, str]:
    """Fine-grained band: the gold entity is named before the confusable one.

    Explaining the contrast ('Kahn, not Cerf') is fine; answering with the
    wrong one and mentioning the right one in passing is not.
    """
    if not q.wrong_entities:
        return True, "no confusable entity declared"
    # quoted evidence is not the answer's voice (Muse Glimmer quotes the text, which names
    # the confusable first; the 2026-10-03 full pass)
    text = _transliteration_fold(_QUOTED.sub(" ", _answer_of(rec).folded))
    # an answer-term alias names the gold too ('the youthful little princess' for Lise)
    gold_names = [
        _transliteration_fold(_straight_apostrophes(n))
        for n in [
            *q.gold_entities,
            *(a for t in q.answer_terms if not isinstance(t, str) for a in t if " " in a),
        ]
    ]
    wrong_names = [_transliteration_fold(_straight_apostrophes(n)) for n in q.wrong_entities]
    gold = _first_position(text, gold_names)
    wrong = _first_position(text, wrong_names)
    if wrong is None:
        return True, "confusable entity absent"
    if gold is None:
        return False, f"leads with {q.wrong_entities} and never names the gold entity"
    if gold < wrong:
        return True, f"gold at {gold}, confusable at {wrong}"
    # a contrast first ("Borís is tall and fair; the short one is Nicholas"): the
    # conclusion decides, the last sentence that names either of them
    for sentence in reversed(_sentences(text)):
        g, w = (
            _first_position(sentence, gold_names),
            _first_position(sentence, wrong_names),
        )
        if g is None and w is None:
            continue
        concluded = g is not None and (w is None or g < w)
        return (
            concluded,
            f"leads with the confusable; the conclusion {'names' if concluded else 'does not name'} the gold first",
        )
    return False, f"gold at {gold}, confusable at {wrong}"


# A chunk label as the context headers and the answers spell it: C0, C12.
_CHUNK_LABEL = re.compile(r"\bC(\d{1,3})\b")
# A bracketed or parenthesised group, the only place a bare 'C3' is a citation.
_CITE_GROUP = re.compile(r"\[([^\[\]\n]{1,80})\]|\(([^()\n]{1,80})\)")
# 'chunk C3', 'Chunks C1, C2', 'CHUNK C3': a citation outside any bracket.
_CHUNK_WORD = re.compile(r"\bchunks?\s+C(\d{1,3})\b", re.IGNORECASE)
# 'C0-C9' (hyphen or dash), 'C3-C5': a range, inclusive.
_CHUNK_RANGE = re.compile(r"\bC(\d{1,3})\s*[-\u2013\u2014]\s*C(\d{1,3})\b")
# The context's chunk headers: the text up to the next header is the chunk.
_CHUNK_HEADER = re.compile(r"\[CHUNK (C\d{1,3}) \|[^\]]*\]")


def cited_chunks(answer: str) -> list[str]:
    """The chunk labels an answer cites, in order of first mention.

    Models cite the context's ``[CHUNK Cn | file]`` labels in many shapes
    (the 2026-10-01 runs): ``[C0][C1]``, ``(chunk C1)``, ``(Chunk C4)``,
    ``(from CHUNK C3)``, ``[CHUNK C1 | war_and_peace_book1.txt] [S2]``,
    ``(e.g., in C0, C4, and C9)``, ``(chunks C0-C9)`` (hyphen or dash). A bare ``Cn`` counts
    only inside brackets or parentheses or after the word chunk, so ``vitamin
    C3`` in prose is not a citation; ``[S2]`` sentence labels never are.
    """
    seen: set[str] = set()
    hits: list[tuple[int, int, str]] = []  # (position in the answer, order found, label)

    def add(pos: int, label: str) -> None:
        if label not in seen:
            seen.add(label)
            hits.append((pos, len(hits), label))

    for m in _CITE_GROUP.finditer(answer):
        inner, base = m.group(1) or m.group(2) or "", m.start()
        for r in _CHUNK_RANGE.finditer(inner):
            for n in range(int(r.group(1)), int(r.group(2)) + 1):
                add(base + r.start(), f"C{n}")
        for lm in _CHUNK_LABEL.finditer(inner):
            add(base + lm.start(), f"C{int(lm.group(1))}")
    for m in _CHUNK_WORD.finditer(answer):
        add(m.start(), f"C{int(m.group(1))}")
    return [label for _, _, label in sorted(hits)]


def context_chunks(context_text: str) -> dict[str, str]:
    """The rendered context's chunks by label: the text from each header to the next."""
    headers = list(_CHUNK_HEADER.finditer(context_text))
    chunks: dict[str, str] = {}
    for i, m in enumerate(headers):
        end = headers[i + 1].start() if i + 1 < len(headers) else len(context_text)
        chunks[m.group(1)] = context_text[m.end() : end]
    return chunks


def holds_an_answer_term(text: str, q: LabeledQuery) -> bool:
    """Whether a chunk holds any answer term, matched as ``answers_from_graph`` matches them."""
    folded = fold(_straight_apostrophes(text))
    terms: list[str | tuple[str, ...]] = list(q.answer_terms) or list(q.gold_entities)
    return any(_term_matches(folded, term, text) for term in terms)


def cites_the_answer(rec: dict[str, Any], q: LabeledQuery) -> tuple[bool | None, str]:
    """Reported, not gating: a cited chunk holds an answer term.

    ``True`` when at least one cited chunk's text holds an answer term,
    ``False`` when the answer cites chunks and none does (a label the
    context never rendered is a wrong citation too), ``None`` when the
    answer cites nothing or the question is out of scope (no chunk is
    needed, so the check does not apply).
    """
    if q.band == "out_of_scope":
        return None, "out of scope: no chunk is needed"
    cited = cited_chunks(rec.get("answer") or "")
    if not cited:
        return None, "uncited"
    chunks = context_chunks(rec.get("context_text") or "")
    hits = [label for label in cited if holds_an_answer_term(chunks.get(label, ""), q)]
    return bool(hits), f"cited={cited}, holding an answer term={hits}"


def declines_when_unsupported(rec: dict[str, Any], q: LabeledQuery) -> tuple[bool, str]:
    """Out-of-scope band: no decoy is asserted *and* the answer declines."""
    # Curly apostrophes too, so "don\u2019t say" matches "don't say" (normalise_answer).
    answer = _answer_of(rec)
    text = answer.folded
    # word-bounded, possessive allowed: 'Anna' is no decoy inside 'Annette' (2026-10-06);
    # the i/y fold the other checks apply: 'Iakov' asserts the decoy 'Yákov' (wave 4)
    decoys = [d for d in q.decoy_entities if _word_bounded(_phrase_form(d), answer.transliterated)]
    declined = declines(text)
    # A decline that then guesses ("the sources do not say; it was presumably
    # Prince Vasíli") asserts an answer it has no source for (scorer v5).
    guess = _SPECULATION.search(_PARENTHESISED.sub(" ", text))
    ok = not decoys and declined and guess is None
    why = f"decoys asserted={decoys}, decline phrase={'yes' if declined else 'no'}"
    if guess:
        why += f", speculates: {guess.group(0)!r}"
    return ok, why


# Only the forms that assert a guessed answer; 'if I assume' in leaked reasoning
# and a parenthetical '(likely X)' aside are not guesses (real answers, 2026-10-03).
_SPECULATION = re.compile(r"\b(?:presumably|probably|most likely|would have been|must have been)\b")
_PARENTHESISED = re.compile(r"\([^()\n]{0,300}\)")


class ChatProbeScorer:
    """Evaluate every query's checks; headline = tier-weighted pass rate.

    Args:
        ignore_answerable: Score questions flagged ``answerable: false`` too
            (the retrieval comparison); by default they are excluded.
    """

    version: int = CHAT_PROBE_SCORER_VERSION

    def __init__(self, *, ignore_answerable: bool = False) -> None:
        """Bind the scoring options."""
        self.ignore_answerable = ignore_answerable

    _IN_SCOPE: ClassVar[tuple[str, ...]] = (
        "finish_stop",
        "answers_from_graph",
        "no_unsupported_names",
        "no_unsupported_numbers",
        "must_not_contain",
        "binds_roles",
        "no_wrong_members",
    )
    CHECKS_BY_BAND: ClassVar[dict[str, tuple[str, ...]]] = {
        "factual_single_hop": _IN_SCOPE,
        "paraphrase": _IN_SCOPE,
        "multi_hop": _IN_SCOPE,
        "fine_grained_discrimination": (*_IN_SCOPE, "leads_with_gold"),
        "out_of_scope": ("finish_stop", "declines_when_unsupported", "must_not_contain"),
        "graph_advantage": _IN_SCOPE,
    }
    CHECKS: ClassVar[dict[str, Any]] = {
        "finish_stop": finish_stop,
        "answers_from_graph": answers_from_graph,
        "no_unsupported_names": no_unsupported_names,
        "no_unsupported_numbers": no_unsupported_numbers,
        "must_not_contain": must_not_contain,
        "binds_roles": binds_roles,
        "no_wrong_members": no_wrong_members,
        "leads_with_gold": leads_with_gold,
        "declines_when_unsupported": declines_when_unsupported,
    }

    def score(self, output: RawOutput, fixture: LabeledQuerySet) -> ScoreResult:
        """Score every query record; the shape matches the extraction probes.

        ``metrics["scorer_recipe"]`` names the behaviour that produced the
        verdicts (:func:`~chaoscypher_core.benchmark.scorers.chat_probe_recipe.scorer_recipe`);
        a result row copies it next to ``scorer_version``.
        """
        from chaoscypher_core.benchmark.scorers.chat_probe_recipe import scorer_recipe

        result = self.score_without_recipe(output, fixture)
        return ScoreResult(
            headline_score=result.headline_score,
            metrics={**result.metrics, "scorer_recipe": scorer_recipe()},
        )

    def score_without_recipe(self, output: RawOutput, fixture: LabeledQuerySet) -> ScoreResult:
        """:meth:`score` without the recipe stamp: what the recipe itself is computed from."""
        records = {r.get("query_id"): r for r in (output.extras or {}).get("per_query", [])}
        verdicts: list[dict[str, Any]] = []
        by_section: dict[str, dict[str, list[bool]]] = {}
        weighted_pass = weighted_total = 0.0
        excluded: list[str] = []
        for q in fixture.queries:
            if not q.answerable and not self.ignore_answerable:
                # The retrieved context cannot answer it (fixture audit): not
                # a verdict on the model, so neither a pass nor a fail.
                excluded.append(q.id)
                continue
            rec = records.get(q.id)
            if rec is not None and "</think>" in (rec.get("answer") or ""):
                # leaked reasoning before the final answer is not the answer (Qwen3.5 27B naive)
                rec = {**rec, "answer": rec["answer"].rsplit("</think>", 1)[1]}
            tier = BAND_TIER.get(q.band, "easy")
            details: list[str] = []
            citation: str | None = None
            if rec is None or rec.get("error"):
                passed = False
                details.append(f"no result: {rec.get('error') if rec else 'missing'}")
            else:
                passed = True
                for name in self.CHECKS_BY_BAND.get(q.band, ("finish_stop",)):
                    ok, why = self.CHECKS[name](rec, q)
                    details.append(f"{'PASS' if ok else 'FAIL'} {name}: {why}")
                    passed = passed and ok
                cited, why = cites_the_answer(rec, q)
                details.append(f"CITE cites_the_answer: {why}")
                if cited is not None:
                    citation = "correct" if cited else "wrong"
                elif q.band != "out_of_scope":
                    citation = "uncited"
            w = TIER_WEIGHTS[tier]
            weighted_total += w
            weighted_pass += w if passed else 0
            by_section.setdefault(q.band, {}).setdefault(tier, []).append(passed)
            verdicts.append(
                {
                    "id": q.id,
                    "probe": q.id,
                    "section": q.band,
                    "tier": tier,
                    "passed": passed,
                    "details": details,
                    "citation": citation,
                }
            )
        headline = (weighted_pass / weighted_total * 100.0) if weighted_total else 0.0
        section_rates = {
            sec: {tier: (sum(v) / len(v) * 100.0 if v else None) for tier, v in tiers.items()}
            for sec, tiers in by_section.items()
        }
        cited_n = sum(1 for v in verdicts if v["citation"] in ("correct", "wrong"))
        correct_n = sum(1 for v in verdicts if v["citation"] == "correct")
        return ScoreResult(
            headline_score=headline,
            metrics={
                "chat_probe_scorer_version": self.version,
                "probes_total": len(verdicts),
                "probes_passed": sum(1 for v in verdicts if v["passed"]),
                "section_rates": section_rates,
                "verdicts": verdicts,
                "excluded": excluded,
                # Reported, never gating: cited-correctly over cited.
                "citations_cited": cited_n,
                "citations_correct": correct_n,
                "citation_accuracy": round(100.0 * correct_n / cited_n, 1) if cited_n else None,
            },
        )


__all__ = [
    "ADDITIVE_AFTER_NOT",
    "BAND_TIER",
    "CHAT_PROBE_SCORER_VERSION",
    "CLAUSE_OPENERS",
    "DECLINE_PHRASES",
    "NEGATION_CUES",
    "NEW_SUBJECTS",
    "NOT_NEGATING",
    "NO_POSSESSIVE_MARKER",
    "STATED_WHEN_NEGATED_MARKER",
    "ChatProbeScorer",
    "NormalisedAnswer",
    "carries_its_own_polarity",
    "cited_chunks",
    "cites_the_answer",
    "context_chunks",
    "context_holds_terms",
    "context_holds_the_answer",
    "declines",
    "holds_an_answer_term",
    "is_negated_hit",
    "negated_span",
    "normalise_answer",
    "phrase_problems",
    "wrong_fact_words",
]
