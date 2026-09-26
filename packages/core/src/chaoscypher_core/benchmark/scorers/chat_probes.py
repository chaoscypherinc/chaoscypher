# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""ChatProbeScorer - pass/fail grounded-chat probes, no judge.

Every labelled query is a probe. A query passes only if every check passes;
the headline is the tier-weighted pass rate, the same formula and the same
verdict shape as the extraction probes so both tables read alike.

Bands map to tiers: single-hop and paraphrase are easy (weight 1), multi-hop
and fine-grained discrimination medium (2), out-of-scope hard (3).

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
- ``leads_with_gold`` - fine-grained band: the gold entity comes first.
- ``declines_when_unsupported`` - out-of-scope band: declines, asserts no decoy.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, ClassVar

from chaoscypher_core.benchmark.scorers.probes import TIER_WEIGHTS, _fold
from chaoscypher_core.benchmark.types import ScoreResult


if TYPE_CHECKING:
    from chaoscypher_core.benchmark.queries import LabeledQuery, LabeledQuerySet
    from chaoscypher_core.benchmark.types import RawOutput

CHAT_PROBE_SCORER_VERSION = 2

BAND_TIER: dict[str, str] = {
    "factual_single_hop": "easy",
    "paraphrase": "easy",
    "multi_hop": "medium",
    "fine_grained_discrimination": "medium",
    "out_of_scope": "hard",
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
}


def _first_position(text: str, names: list[str]) -> int | None:
    """Earliest position of any of the names in the folded text, or None.

    A name counts from its whole form or from any of its significant words
    (word-bounded), matching how ``answers_from_graph`` accepts a surname
    alone: 'Cerf was on the BBN team; Kahn joined later.' names 'Vint Cerf'
    at position 0.
    """
    hits: list[int] = []
    for name in names:
        whole = text.find(_fold(name))
        if whole >= 0:
            hits.append(whole)
        for word in _significant_words(name):
            match = re.search(r"(?<![a-z0-9])" + re.escape(_fold(word)) + r"(?![a-z0-9])", text)
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
    folded = _fold(name)
    return bool(re.search(r"(?<![a-z0-9])" + re.escape(folded) + r"s?(?![a-z0-9])", text))


def _straight_apostrophes(s: str) -> str:
    """Map curly single quotes (U+2018, U+2019) to ``'`` so both spellings compare equal."""
    return s.replace("\u2019", "'").replace("\u2018", "'")


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


# A sentence ends at '.', '!' or '?' followed by whitespace and a capital -
# unless the period closes a single-capital initial ('J. C. R. Licklider').
_SENTENCE_BREAK = re.compile(r"(?<=[.!?])(?<!\b[A-Z]\.)\s+(?=[A-Z])")


def _sentences(text: str) -> list[str]:
    """Split text into sentences so a capitalised run never spans two of them."""
    return [s for s in _SENTENCE_BREAK.split(text) if s.strip()]


def _candidate_names(answer: str, *, join: bool = True) -> list[str]:
    """Capitalised words that are not sentence-initial function words.

    A heuristic name extractor is enough here: a name is only flagged when
    *none* of its significant words appear in the context, so a stray
    sentence-start word costs nothing unless it is genuinely absent. The
    answer is split into sentences first, so '...at ARPA. The text' yields
    'ARPA', never 'ARPA. The'.
    """
    out: list[str] = []
    for sentence in _sentences(answer):
        out.extend(_sentence_candidates(sentence, join=join))
    return out


def _sentence_candidates(sentence: str, *, join: bool) -> list[str]:
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
    for m in re.finditer(
        r"\b([A-Z][A-Za-z0-9.'-]*(?:\s+" + connectors + r"\s*[A-Z][A-Za-z0-9.'-]*)*)",
        sentence,
    ):
        cand = m.group(1).strip().rstrip(".,;:!?'\"")
        if not cand or cand.lower() in _NOT_A_NAME:
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


def _strip_labels(answer: str) -> str:
    """Drop headings and label lines, and the label prefix of a list item.

    A model's formatting ('## Answer', '**Step-by-Step Explanation:**', a
    'Mentions:' line, '- **Company Identification:** ...') is capitalised
    for layout, not because it names anything. A label prefix is only
    stripped from a list item or a bold lead, and only when it is at most
    five words, so a sentence with a colon in it keeps its names.
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
        if label and (marker or core.startswith("**")) and len(label.group(1).split()) <= 5:
            core = core[label.end() :]
        kept.append(core)
    return "\n".join(kept)


def _word_supported(word: str, support: str) -> bool:
    """The word, or the word less a possessive/plural suffix, is in the support text.

    'kahn's' -> 'kahn', 'imps' -> 'imp', 'engineers'' -> 'engineer'.
    """
    forms = [word]
    forms.extend(
        word[: -len(suffix)]
        for suffix in ("'s", "s'", "s")
        if word.endswith(suffix) and len(word) - len(suffix) >= 3
    )
    return any(f in support for f in forms)


def _word_bounded(term: str, text: str) -> bool:
    """``term`` occurs in ``text`` with no letter or digit on either side."""
    return bool(re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", text))


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
    answer = _straight_apostrophes(rec.get("answer") or "")
    text = _fold(answer)
    if q.answer_terms:
        missing_terms = [
            "|".join(t) if isinstance(t, tuple) else t
            for t in q.answer_terms
            if not (
                _mentions(text, _straight_apostrophes(t), answer)
                if isinstance(t, str)
                else any(_mentions_phrase(text, _straight_apostrophes(alias)) for alias in t)
            )
        ]
        return not missing_terms, f"answer terms missing={missing_terms}"
    question = _fold(q.question)
    wanted = [g for g in q.gold_entities if not _mentions(question, g, q.question)] or list(
        q.gold_entities
    )
    missing = [g for g in wanted if not _mentions(text, g, answer)]
    return not missing, f"gold entities missing={missing}"


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
    raw_support = (rec.get("context_text") or "") + " " + q.question
    support = _fold(raw_support)
    answer = _strip_labels(rec.get("answer") or "")

    def significant(cand: str) -> list[str]:
        """The candidate's folded words worth testing (three letters or more)."""
        words = re.split(r"[\s,]+", _fold(cand))
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
    for cand in _candidate_names(answer, join=False):
        words = significant(cand)
        if not words or any(_word_supported(w, support) or w in expanded for w in words):
            continue
        if cand.isupper() and cand.lower() in coined:
            continue
        bad.append(cand)
    return not bad, f"names not in the retrieved context={bad[:4]}"


# A number token: '1969', '114', '3.5', '1,000'; a decade's 's' ('1960s') is
# allowed after it. Not part of a word ('S1', 'IPv6', 'C3' are skipped).
_NUMBER = re.compile(r"(?<![\w.])\d[\d,.]*(?=s?\b)")
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


def _number_supported(token: str, support: str) -> bool:
    """The number occurs in the support text as a number, or as its word (0-20).

    Digit-bounded, so '114' is found in 'rfc 114' and '516' in 'ddp-516', but
    '1' is not found in '[s1]' or '1969'.
    """
    if re.search(r"(?<![a-z0-9])" + re.escape(token) + r"(?![0-9]|[.,][0-9])", support):
        return True
    if token.isdigit() and int(token) < len(_NUMBER_WORDS):
        return _word_bounded(_NUMBER_WORDS[int(token)], support)
    return False


def no_unsupported_numbers(rec: dict[str, Any], q: LabeledQuery) -> tuple[bool, str]:
    """Every number in the answer appears in the retrieved context or the question.

    The names check passes 'Kahn and Cerf shared the 2005 award'; this one
    does not. A number inside an identifier the context holds ('RFC 114',
    'DDP-516') is found there; list numbering ('1. UCLA') and citation
    markers ('[S1]') are not numbers. Thousands separators are ignored.
    """
    raw_support = _fold((rec.get("context_text") or "") + " " + q.question)
    support = re.sub(r"(?<=\d),(?=\d{3})", "", raw_support)
    answer = "\n".join(
        _LIST_MARKER.sub("", line) for line in (rec.get("answer") or "").splitlines()
    )
    bad: list[str] = []
    for m in _NUMBER.finditer(answer):
        token = m.group(0).rstrip(".,")
        token = re.sub(r"(?<=\d),(?=\d{3})", "", token)
        if token and not _number_supported(token, support) and token not in bad:
            bad.append(token)
    return not bad, f"numbers not in the retrieved context={bad[:6]}"


def must_not_contain(rec: dict[str, Any], q: LabeledQuery) -> tuple[bool, str]:
    """The answer states none of the query's declared wrong facts.

    ``must_not_contain`` holds the famous wrong answer (a year off by one,
    'Network Control Protocol', the other node's lab). Each term is matched
    as a whole phrase, word-bounded, like ``_mentions``'s first rule; the
    loose significant-word rule is not used, or 'Network Control Protocol'
    would match any answer that says 'network'.
    """
    if not q.must_not_contain:
        return True, "no wrong facts declared"
    text = _fold(rec.get("answer") or "")
    hits = [t for t in q.must_not_contain if _mentions_phrase(text, t)]
    return not hits, f"wrong facts stated={hits}"


def leads_with_gold(rec: dict[str, Any], q: LabeledQuery) -> tuple[bool, str]:
    """Fine-grained band: the gold entity is named before the confusable one.

    Explaining the contrast ('Kahn, not Cerf') is fine; answering with the
    wrong one and mentioning the right one in passing is not.
    """
    if not q.wrong_entities:
        return True, "no confusable entity declared"
    text = _fold(rec.get("answer") or "")
    gold = _first_position(text, q.gold_entities)
    wrong = _first_position(text, q.wrong_entities)
    if wrong is None:
        return True, "confusable entity absent"
    if gold is None:
        return False, f"leads with {q.wrong_entities} and never names the gold entity"
    return gold < wrong, f"gold at {gold}, confusable at {wrong}"


def declines_when_unsupported(rec: dict[str, Any], q: LabeledQuery) -> tuple[bool, str]:
    """Out-of-scope band: no decoy is asserted *and* the answer declines."""
    text = _fold(rec.get("answer") or "")
    decoys = [d for d in q.decoy_entities if _fold(d) in text]
    declined = any(p in text for p in DECLINE_PHRASES)
    ok = not decoys and declined
    return ok, f"decoys asserted={decoys}, decline phrase={'yes' if declined else 'no'}"


class ChatProbeScorer:
    """Evaluate every query's checks; headline = tier-weighted pass rate."""

    version: int = CHAT_PROBE_SCORER_VERSION

    _IN_SCOPE: ClassVar[tuple[str, ...]] = (
        "finish_stop",
        "answers_from_graph",
        "no_unsupported_names",
        "no_unsupported_numbers",
        "must_not_contain",
    )
    CHECKS_BY_BAND: ClassVar[dict[str, tuple[str, ...]]] = {
        "factual_single_hop": _IN_SCOPE,
        "paraphrase": _IN_SCOPE,
        "multi_hop": _IN_SCOPE,
        "fine_grained_discrimination": (*_IN_SCOPE, "leads_with_gold"),
        "out_of_scope": ("finish_stop", "declines_when_unsupported", "must_not_contain"),
    }
    CHECKS: ClassVar[dict[str, Any]] = {
        "finish_stop": finish_stop,
        "answers_from_graph": answers_from_graph,
        "no_unsupported_names": no_unsupported_names,
        "no_unsupported_numbers": no_unsupported_numbers,
        "must_not_contain": must_not_contain,
        "leads_with_gold": leads_with_gold,
        "declines_when_unsupported": declines_when_unsupported,
    }

    def score(self, output: RawOutput, fixture: LabeledQuerySet) -> ScoreResult:
        """Score every query record; the shape matches the extraction probes."""
        records = {r.get("query_id"): r for r in (output.extras or {}).get("per_query", [])}
        verdicts: list[dict[str, Any]] = []
        by_section: dict[str, dict[str, list[bool]]] = {}
        weighted_pass = weighted_total = 0.0
        for q in fixture.queries:
            rec = records.get(q.id)
            tier = BAND_TIER.get(q.band, "easy")
            details: list[str] = []
            if rec is None or rec.get("error"):
                passed = False
                details.append(f"no result: {rec.get('error') if rec else 'missing'}")
            else:
                passed = True
                for name in self.CHECKS_BY_BAND.get(q.band, ("finish_stop",)):
                    ok, why = self.CHECKS[name](rec, q)
                    details.append(f"{'PASS' if ok else 'FAIL'} {name}: {why}")
                    passed = passed and ok
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
                }
            )
        headline = (weighted_pass / weighted_total * 100.0) if weighted_total else 0.0
        section_rates = {
            sec: {tier: (sum(v) / len(v) * 100.0 if v else None) for tier, v in tiers.items()}
            for sec, tiers in by_section.items()
        }
        return ScoreResult(
            headline_score=headline,
            metrics={
                "chat_probe_scorer_version": self.version,
                "probes_total": len(fixture.queries),
                "probes_passed": sum(1 for v in verdicts if v["passed"]),
                "section_rates": section_rates,
                "verdicts": verdicts,
            },
        )


__all__ = ["BAND_TIER", "CHAT_PROBE_SCORER_VERSION", "DECLINE_PHRASES", "ChatProbeScorer"]
