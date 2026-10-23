# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The chat probe scorer's recipe: a hash of its own verdicts on fixed samples.

``CHAT_PROBE_SCORER_VERSION`` is bumped by hand, and v5 changed its verdicts
without a bump (H3 of the 2026-10-06 benchmark pipeline audit: stored v5
verdicts differed from a v5 rescore by up to five points). The recipe is
computed, never declared, like :func:`~chaoscypher_core.benchmark.reindex.reindex_recipe`:
:class:`~chaoscypher_core.benchmark.scorers.chat_probes.ChatProbeScorer`
scores :data:`RECIPE_SAMPLES` and the verdicts are hashed, so any change that
moves a verdict on them moves the recipe. Every result row carries it next
to ``scorer_version``; two rows with one version and two recipes were scored
by different code. The golden test pins each sample's verdict and the
verdict recipe of the current version, so a change fails there until the
version is bumped and the new recipe recorded.

Belt and braces (wave 2 of the audit): the samples cannot exercise every
rule, so the recipe also hashes the scorer's module sources
(:data:`SCORER_MODULES`: the checks, their helpers, the recipe samples and
the fixture loader). Any code change moves the recipe, a change the samples
miss included; only a change that moves a *verdict* on the samples needs the
version bump (:func:`verdict_recipe` is what the golden test pins).

The samples are invented (a network history and a made-up estate ledger),
from no corpus the benchmark uses, and exercise every check: answer terms
and alias groups, the i/y and w/v fold, grounding in the context, names and
numbers, ``must_not_contain`` with its possessive and plural rules and the
no-possessive marker,
declines in and out of scope, speculation, a decoy inside a longer name,
a common word opening a sentence, the confusable entity, role
binding, citations, a truncated answer, a missing and an errored record,
the ``answerable: false`` exclusion, and (audit wave 4) one sample per form
the shared answer normaliser reads through: single emphasis, backticks,
underscores, a hard space and a line break, the i/y fold, a citation inside
a phrase, an answer term in bold and a decoy spelled with i; (wave 5) one
per form the fold reads through: a soft hyphen and a word joiner inside a
name, an en dash inside a word, and the -iy and -ii endings; (wave 6) a
Roman numeral that is not an -ii spelling, a zero-width space between two
words and a spaced en dash; (scorer v7) one per part of the negation rule:
a wrong fact inside a negated clause, after a line break that ends the
negated one, after 'not only' (additive), inside 'not only' when the phrase
begins with 'only', after 'but', a phrase with its own exceptive, one
marked stated-when-negated, and an apposition.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from chaoscypher_core.benchmark.queries import LabeledQuery, LabeledQuerySet
from chaoscypher_core.benchmark.types import RawOutput, ScoreResult


_NET = (
    "[CHUNK C0 | net.txt]\n"
    "[S1] Bolt, Beranek and Newman built the IMP in 1969. [S2] Robert Kahn was a BBN engineer.\n"
    "[CHUNK C1 | net.txt]\n"
    "[S1] Vinton Cerf studied at UCLA. [S2] The first message went from UCLA to the "
    "Stanford Research Institute; the log ran to forty thousand words.\n"
)
_LEDGER = (
    "[CHUNK C0 | ledger.txt]\n"
    "[S1] Prince Grigóri Sávin lent Márfa Ivánovna three hundred rubles at Zavídovo.\n"
    "[CHUNK C1 | ledger.txt]\n"
    "[S1] Her cousin Tíkhon, the steward, kept the accounts; the clerk Yákov copied them. "
    "[S2] Márfa is the niece of the old miller.\n"
)


@dataclass(frozen=True)
class RecipeSample:
    """One fixed (query, answer, context) the recipe scores.

    Attributes:
        id: The sample's query id; verdicts are reported under it.
        query: :class:`LabeledQuery` fields other than ``id``.
        answer: The model's answer; None means no record was produced.
        context: The retrieved context the answer was given.
        finish_reason: How the answer ended (``stop`` unless truncated).
        error: A per-query error the record carries, if any.
    """

    id: str
    query: dict[str, Any]
    answer: str | None
    context: str = ""
    finish_reason: str = "stop"
    error: str | None = None


_BUILT = {
    "band": "factual_single_hop",
    "question": "Who built the IMP?",
    "gold_entities": ["Bolt, Beranek and Newman"],
    "gold_answer": "BBN.",
    "answer_terms": [("Bolt, Beranek and Newman", "BBN")],
}
_OUT_OF_SCOPE = {
    "band": "out_of_scope",
    "question": "Who bought BBN?",
    "expect_refusal": True,
    "decoy_entities": ["Raytheon"],
}

_KEPT_BY_THE_CLERK = {
    "band": "factual_single_hop",
    "question": "Who kept the accounts?",
    "gold_entities": ["Tíkhon"],
    "gold_answer": "Tíkhon.",
    "answer_terms": ["Tíkhon"],
    "must_not_contain": ["kept by Yákov", "Grigóri kept"],
}
"""A wrong fact the normalised-answer samples state in each markup form (audit wave 4)."""

RECIPE_SAMPLES: tuple[RecipeSample, ...] = (
    RecipeSample(
        "s01_terms_cited", _BUILT, "Bolt, Beranek and Newman built it [[cite:C0:S1|net.txt]].", _NET
    ),
    RecipeSample("s02_unsupported_name", _BUILT, "BBN built it, with Leonard Kleinrock.", _NET),
    RecipeSample(
        "s03_wrong_year",
        {**_BUILT, "question": "When was the IMP built?", "answer_terms": ["1969"]},
        "BBN built it in 1969 and shipped it in 1968.",
        _NET,
    ),
    RecipeSample(
        "s04_number_in_words",
        {**_BUILT, "question": "How long was the log?", "answer_terms": ["forty thousand"]},
        "Forty thousand words (40,000).",
        _NET,
    ),
    RecipeSample(
        "s05_must_not_contain_hit",
        {
            **_BUILT,
            "question": "Where did Cerf study?",
            "answer_terms": ["UCLA"],
            "must_not_contain": ["studied at Stanford"],
        },
        "He studied at UCLA; he studied at Stanford as well.",
        _NET,
    ),
    RecipeSample(
        "s06_must_not_contain_possessive",
        {
            "band": "multi_hop",
            "question": "What relation is Márfa to the miller?",
            "gold_entities": ["Márfa Ivánovna"],
            "gold_answer": "His niece.",
            "answer_terms": ["niece"],
            "must_not_contain": ["niece of Tíkhon"],
        },
        "Márfa is the niece of Tíkhon's master, the old miller [[cite:C1:S2|ledger.txt]].",
        _LEDGER,
    ),
    RecipeSample(
        "s26_no_possessive_phrase_cited",
        {
            "band": "multi_hop",
            "question": "Whose niece is Márfa?",
            "gold_entities": ["Márfa Ivánovna"],
            "gold_answer": "The old miller's.",
            "answer_terms": ["niece"],
            "must_not_contain": ["niece of Tíkhon|"],
        },
        "Márfa is the niece of Tíkhon [[cite:C1:S2|ledger.txt]]",
        _LEDGER,
    ),
    RecipeSample(
        "s27_no_possessive_phrase_spares_the_possessive",
        {
            "band": "multi_hop",
            "question": "Whose niece is Márfa?",
            "gold_entities": ["Márfa Ivánovna"],
            "gold_answer": "The old miller's.",
            "answer_terms": ["niece"],
            "must_not_contain": ["niece of Tíkhon|"],
        },
        "Márfa is the niece of Tíkhon's master, the old miller [[cite:C1:S2|ledger.txt]].",
        _LEDGER,
    ),
    RecipeSample(
        "s07_must_not_contain_plural",
        {
            "band": "factual_single_hop",
            "question": "Who kept the accounts?",
            "gold_entities": ["Tíkhon"],
            "gold_answer": "Tíkhon.",
            "answer_terms": ["Tíkhon"],
            "must_not_contain": ["the clerk kept"],
        },
        "Tíkhon kept them; the clerks kept nothing but copies.",
        _LEDGER,
    ),
    RecipeSample(
        "s08_declines_in_scope", _BUILT, "The sources do not say who built the IMP.", _NET
    ),
    RecipeSample(
        "s09_declines_after_its_term",
        _BUILT,
        "BBN is mentioned, but the context does not say who built the IMP.",
        _NET,
    ),
    RecipeSample(
        "s10_out_of_scope_declines", _OUT_OF_SCOPE, "The context does not say who bought BBN.", _NET
    ),
    RecipeSample("s11_out_of_scope_decoy", _OUT_OF_SCOPE, "Raytheon bought BBN.", _NET),
    RecipeSample(
        "s12_out_of_scope_speculates",
        _OUT_OF_SCOPE,
        "The sources do not say; it was most likely a larger firm.",
        _NET,
    ),
    RecipeSample(
        "s13_leads_with_the_confusable",
        {
            "band": "fine_grained_discrimination",
            "question": "Who was the BBN engineer?",
            "gold_entities": ["Robert Kahn"],
            "gold_answer": "Kahn.",
            "wrong_entities": ["Vinton Cerf"],
            "answer_terms": ["Kahn"],
        },
        "Cerf, and also Kahn.",
        _NET,
    ),
    RecipeSample(
        "s14_transliterated_spelling",
        {
            "band": "factual_single_hop",
            "question": "Who lent Márfa the money?",
            "gold_entities": ["Grigóri Sávin"],
            "gold_answer": "Prince Grigóri Sávin.",
            "answer_terms": ["Grigóri"],
        },
        "Prince Grigory Savin lent it at Zawidowo.",
        _LEDGER,
    ),
    RecipeSample(
        "s15_patronymic_is_not_a_name",
        {
            "band": "factual_single_hop",
            "question": "What is Márfa's patronymic?",
            "gold_entities": ["Márfa Ivánovna"],
            "gold_answer": "Ivánovna.",
            "answer_terms": ["Ivánovna"],
        },
        "Ivánovna; her father was Ivan.",
        _LEDGER,
    ),
    RecipeSample("s16_cites_the_wrong_chunk", _BUILT, "BBN built it [C1].", _NET),
    RecipeSample("s17_truncated", _BUILT, "BBN built", _NET, finish_reason="length"),
    RecipeSample(
        "s18_roles_swapped",
        {
            "band": "multi_hop",
            "question": "Who kept the accounts and who copied them?",
            "gold_entities": ["Tíkhon", "Yákov"],
            "gold_answer": "Tíkhon the steward kept them; Yákov the clerk copied them.",
            "answer_terms": ["Tíkhon", "Yákov"],
            "roles": [
                {"person": ["Tíkhon"], "roles": ["steward"]},
                {"person": ["Yákov"], "roles": ["clerk"]},
            ],
        },
        "Tíkhon is the clerk and Yákov the steward.",
        _LEDGER,
    ),
    RecipeSample(
        "s19_from_memory",
        {**_BUILT, "question": "Where did Cerf work later?", "answer_terms": ["Google"]},
        "At Google.",
        _NET,
    ),
    RecipeSample(
        "s20_excluded",
        {**_BUILT, "answerable": False},
        "BBN.",
        _NET,
    ),
    RecipeSample(
        "s21_of_alias_with_a_gap",
        {
            "band": "multi_hop",
            "question": "Who is the old miller to Márfa?",
            "gold_entities": ["Márfa Ivánovna"],
            "gold_answer": "Her uncle.",
            "answer_terms": [("uncle of Márfa",)],
        },
        "He is the uncle of the young Márfa.",
        _LEDGER,
    ),
    RecipeSample("s22_no_record", _BUILT, None, _NET),
    RecipeSample("s23_errored_record", _BUILT, "", _NET, error="TimeoutError: no answer"),
    RecipeSample(
        "s24_decoy_inside_a_longer_name",
        {
            "band": "out_of_scope",
            "question": "Who bought the ledger?",
            "expect_refusal": True,
            "decoy_entities": ["Yákov"],
        },
        "The ledger does not say who bought it; only Yákovlev's copies are mentioned.",
        _LEDGER,
    ),
    RecipeSample(
        "s25_common_word_opens_the_sentence",
        _BUILT,
        "Even then, Bolt, Beranek and Newman built it. State it plainly: BBN.",
        _NET,
    ),
    # audit wave 4: one answer normaliser for every check; a sample per form it reads through
    RecipeSample(
        "s28_wrong_fact_in_single_emphasis",
        _KEPT_BY_THE_CLERK,
        "Not Tíkhon: they were kept by *Yákov*.",
        _LEDGER,
    ),
    RecipeSample(
        "s29_wrong_fact_in_backticks",
        _KEPT_BY_THE_CLERK,
        "Not Tíkhon: they were kept by `Yákov`.",
        _LEDGER,
    ),
    RecipeSample(
        "s30_wrong_fact_in_underscores",
        _KEPT_BY_THE_CLERK,
        "Not Tíkhon: they were kept by _Yákov_.",
        _LEDGER,
    ),
    RecipeSample(
        "s31_wrong_fact_across_a_hard_space_and_a_line_break",
        _KEPT_BY_THE_CLERK,
        "Not Tíkhon: they were kept by\u00a0\nYákov.",
        _LEDGER,
    ),
    RecipeSample(
        "s32_wrong_fact_spelled_with_y",
        _KEPT_BY_THE_CLERK,
        "Tíkhon, though Prince Grigory kept a ledger too.",
        _LEDGER,
    ),
    RecipeSample(
        "s33_wrong_fact_around_a_citation",
        _KEPT_BY_THE_CLERK,
        "Not Tíkhon: they were kept by [[cite:C1:S1|ledger.txt]] Yákov.",
        _LEDGER,
    ),
    RecipeSample(
        "s34_answer_term_in_emphasis",
        {
            "band": "factual_single_hop",
            "question": "Was the steward the clerk?",
            "gold_entities": ["Tíkhon"],
            "gold_answer": "No: Tíkhon is not the clerk.",
            "answer_terms": [("not the clerk",)],
        },
        "No: Tíkhon is **not** the clerk.",
        _LEDGER,
    ),
    RecipeSample(
        "s35_decoy_spelled_with_i",
        {
            "band": "out_of_scope",
            "question": "Who bought the ledger?",
            "expect_refusal": True,
            "decoy_entities": ["Yákov"],
        },
        "The ledger does not say who bought it; Iakov copied it.",
        _LEDGER,
    ),
    # audit wave 5: the fold reads through format characters, an en dash inside a word, and
    # the -iy/-ii endings; a sample per form
    RecipeSample(
        "s36_soft_hyphen_inside_a_name",
        _KEPT_BY_THE_CLERK,
        "Tík\u00adhon kept the accounts.",
        _LEDGER,
    ),
    RecipeSample(
        "s37_word_joiner_inside_a_name",
        _KEPT_BY_THE_CLERK,
        "Tí\u2060khon kept the accounts.",
        _LEDGER,
    ),
    RecipeSample(
        "s38_en_dash_inside_a_word",
        {
            "band": "factual_single_hop",
            "question": "Where was the loan signed?",
            "gold_entities": ["Zavídovo-Pólye"],
            "gold_answer": "At Zavídovo-Pólye.",
            "answer_terms": [("Zavídovo-Pólye",)],
        },
        "At Zavídovo\u2013Pólye.",
        _LEDGER + "[CHUNK C2 | ledger.txt]\n[S1] The loan was signed at Zavídovo-Pólye.\n",
    ),
    RecipeSample(
        "s39_wrong_fact_spelled_with_iy",
        _KEPT_BY_THE_CLERK,
        "Tíkhon, though Prince Grigoriy kept a ledger too.",
        _LEDGER,
    ),
    RecipeSample(
        "s40_wrong_fact_spelled_with_ii",
        _KEPT_BY_THE_CLERK,
        "Tíkhon, though Prince Grigorii kept a ledger too.",
        _LEDGER,
    ),
    RecipeSample(
        "s41_roman_numeral_is_not_an_ii_spelling",
        {**_KEPT_BY_THE_CLERK, "must_not_contain": ["kept by Yákov", "Grigóri XVI kept"]},
        "Tíkhon kept them; Prince Grigóri XVII kept nothing.",
        _LEDGER + "[CHUNK C2 | ledger.txt]\n[S1] Prince Grigóri XVII lent the money.\n",
    ),
    # audit wave 6: a zero-width space between two letters is a word boundary, not dropped
    # (dropping it joined 'Tíkhon' and 'kept'); a spaced en dash is a spaced hyphen (q110)
    RecipeSample(
        "s42_zero_width_space_between_two_words",
        _KEPT_BY_THE_CLERK,
        "Tíkhon\u200bkept the accounts.",
        _LEDGER,
    ),
    RecipeSample(
        "s43_spaced_en_dash_is_a_spaced_hyphen",
        {**_KEPT_BY_THE_CLERK, "answer_terms": [("Tíkhon - the steward",)]},
        "Tíkhon \u2013 the steward.",
        _LEDGER,
    ),
    # scorer v7 (Denis, 2026-10-08): a wrong fact inside a negated clause states nothing
    RecipeSample(
        "s44_wrong_fact_inside_a_negated_clause",
        _KEPT_BY_THE_CLERK,
        "Tíkhon kept them; they were never kept by Yákov.",
        _LEDGER,
    ),
    RecipeSample(
        "s45_a_line_break_ends_the_negated_clause",
        _KEPT_BY_THE_CLERK,
        "Tíkhon did not copy them\nThey were kept by Yákov",
        _LEDGER,
    ),
    RecipeSample(
        "s46_not_only_adds",
        _KEPT_BY_THE_CLERK,
        "Tíkhon, though they were not only kept by Yákov.",
        _LEDGER,
    ),
    RecipeSample(
        "s47_not_only_denies_the_phrase_s_own_only",
        {**_KEPT_BY_THE_CLERK, "must_not_contain": ["only one clerk"]},
        "Tíkhon kept them; there was not only one clerk.",
        _LEDGER,
    ),
    RecipeSample(
        "s48_but_ends_the_negated_clause",
        _KEPT_BY_THE_CLERK,
        "Tíkhon. They were not lost but kept by Yákov.",
        _LEDGER,
    ),
    RecipeSample(
        "s49_a_phrase_with_its_own_exceptive",
        {**_KEPT_BY_THE_CLERK, "must_not_contain": ["steward beyond Tíkhon"]},
        "Tíkhon kept them; there was no steward beyond Tíkhon.",
        _LEDGER,
    ),
    RecipeSample(
        "s50_a_phrase_marked_stated_when_negated",
        {**_KEPT_BY_THE_CLERK, "must_not_contain": ["!kept by the miller"]},
        "Tíkhon kept them, never kept by the miller.",
        _LEDGER,
    ),
    RecipeSample(
        "s51_an_apposition_is_not_negated",
        {**_KEPT_BY_THE_CLERK, "must_not_contain": ["cousin, Yákov"]},
        "Tíkhon kept them, not her cousin, Yákov.",
        _LEDGER,
    ),
)
"""The fixed samples :func:`scorer_recipe` scores; the golden test pins each verdict."""


def recipe_inputs() -> tuple[RawOutput, LabeledQuerySet]:
    """The samples as the scorer reads them: per-query records and their fixture."""
    queries = [LabeledQuery(id=s.id, **s.query) for s in RECIPE_SAMPLES]
    records = [
        {
            "query_id": s.id,
            "band": s.query["band"],
            "answer": s.answer,
            "finish_reason": s.finish_reason,
            "context_text": s.context,
            **({"error": s.error} if s.error else {}),
        }
        for s in RECIPE_SAMPLES
        if s.answer is not None
    ]
    raw = RawOutput(
        entities=[],
        relationships=[],
        latency_ms=0,
        input_tokens=0,
        output_tokens=0,
        error=None,
        extras={"per_query": records},
    )
    return raw, LabeledQuerySet(version="recipe", queries=queries)


def recipe_verdicts() -> ScoreResult:
    """The scorer's verdicts on :data:`RECIPE_SAMPLES` (no recipe stamp, so no recursion)."""
    from chaoscypher_core.benchmark.scorers.chat_probes import ChatProbeScorer

    raw, fixture = recipe_inputs()
    return ChatProbeScorer().score_without_recipe(raw, fixture)


SCORER_MODULES: tuple[str, ...] = (
    "chaoscypher_core.benchmark.scorers.chat_probes",
    "chaoscypher_core.benchmark.scorers.probes",
    "chaoscypher_core.benchmark.scorers.chat_probe_recipe",
    "chaoscypher_core.benchmark.queries",
)
"""The modules whose source the recipe hashes: the checks, the shared fold and
tier weights, these samples, and the loader that turns the fixture's YAML into
the terms and alias groups the checks read."""


def source_digest(modules: tuple[str, ...] = SCORER_MODULES) -> str:
    """The sha256 of the modules' source files, in order, each prefixed by its name."""
    digest = hashlib.sha256()
    for name in modules:
        spec = importlib.util.find_spec(name)
        if spec is None or spec.origin is None:
            msg = f"scorer module {name} has no source file to hash"
            raise RuntimeError(msg)
        digest.update(name.encode("utf-8") + b"\0")
        digest.update(Path(spec.origin).read_bytes())
    return digest.hexdigest()


def compute_verdict_recipe() -> str:
    """Hash the verdicts on the samples: what passed, every check's reason, the citation."""
    result = recipe_verdicts()
    payload = {
        "headline": round(result.headline_score, 6),
        "excluded": result.metrics["excluded"],
        "verdicts": [
            [v["id"], v["passed"], v["details"], v["citation"]] for v in result.metrics["verdicts"]
        ],
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


@cache
def verdict_recipe() -> str:
    """:func:`compute_verdict_recipe`, once per process: what the golden test pins per version."""
    return compute_verdict_recipe()


def compute_scorer_recipe() -> str:
    """The verdict recipe and the source digest, hashed together (12 hex characters)."""
    blob = f"{compute_verdict_recipe()}:{source_digest()}"
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


@cache
def scorer_recipe() -> str:
    """The chat probe scorer's recipe, computed once per process (see the module docstring).

    Computed, never declared: any change to a check, a word list or a
    pattern that moves a verdict on :data:`RECIPE_SAMPLES` changes it, and
    so does any edit of the :data:`SCORER_MODULES` sources. Result rows carry
    it as ``scorer_recipe`` (``metrics["scorer_recipe"]`` and the row field
    of the same name).
    """
    return compute_scorer_recipe()


__all__ = [
    "RECIPE_SAMPLES",
    "SCORER_MODULES",
    "RecipeSample",
    "compute_scorer_recipe",
    "compute_verdict_recipe",
    "recipe_inputs",
    "recipe_verdicts",
    "scorer_recipe",
    "source_digest",
    "verdict_recipe",
]
