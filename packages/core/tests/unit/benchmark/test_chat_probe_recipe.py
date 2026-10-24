# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The chat probe scorer's recipe and its golden samples (H3 of the 2026-10-06 audit).

Scorer v5 changed its verdicts without a version bump. The recipe is a hash of
the scorer's own verdicts on fixed samples; these tests pin every sample's
verdict and the recipe of the current version, so a change that moves a
verdict fails here until ``CHAT_PROBE_SCORER_VERSION`` is bumped, the change
is named in the scorer's docstring, and the new recipe is recorded below.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chaoscypher_core.benchmark.scorers import chat_probe_recipe as recipe
from chaoscypher_core.benchmark.scorers import chat_probes as cp


RECIPE_BY_VERSION: dict[int, str] = {
    # v6 moved within the version on 2026-10-06 (audit wave 2, before any v6 row was
    # archived: possessive must_not_contain, word-bounded decoys, common openers; the
    # first v6 verdict recipe was 73c94ffea469), and again in wave 3 (still none archived:
    # phrases bounded by words, the no-possessive marker; the wave-2 recipe was 15a0b0c1998c),
    # and in wave 4 (still none archived: one answer normaliser for every check and a
    # sample per form it reads through; the wave-3 recipe was 7c21a0046cbd), and in wave 5
    # (still none archived: the fold reads through format characters, an en dash inside a
    # word and the -iy/-ii endings; the wave-4 recipe was cfc147ab0828), and in wave 6
    # (still none archived: a Roman numeral is not an -ii spelling, a zero-width space
    # between two letters is a word boundary, a spaced en dash a spaced hyphen; the
    # wave-5 recipe was 4d4cf85f5777)
    6: "d1fa5a485717",
    # v7 (2026-10-08): a must_not_contain phrase inside a negated clause states nothing,
    # unless it carries its own polarity; samples s44-s51
    7: "0ede66d6097f",
    # v8 (2026-10-23): the Ollama docs answer review's rules (units, versions, sizes in k,
    # 'No.', acronyms and keys, English openers, hyphen compounds) and no_wrong_members;
    # samples s52-s61
    8: "17fd2b9d6f27",
}
"""The verdict recipe each scorer version produced; a new version adds its line, old ones stay."""

GOLDEN: dict[str, tuple[bool, list[str], str | None]] = {
    # sample id: (passed, the checks that fail, citation)
    "s01_terms_cited": (True, [], "correct"),
    "s02_unsupported_name": (False, ["no_unsupported_names"], "uncited"),
    "s03_wrong_year": (False, ["no_unsupported_numbers"], "uncited"),
    "s04_number_in_words": (True, [], "uncited"),
    "s05_must_not_contain_hit": (False, ["must_not_contain"], "uncited"),
    "s06_must_not_contain_possessive": (False, ["must_not_contain"], "correct"),
    "s07_must_not_contain_plural": (True, [], "uncited"),
    "s26_no_possessive_phrase_cited": (False, ["must_not_contain"], "correct"),
    "s27_no_possessive_phrase_spares_the_possessive": (True, [], "correct"),
    "s08_declines_in_scope": (False, ["answers_from_graph"], "uncited"),
    "s09_declines_after_its_term": (False, ["answers_from_graph"], "uncited"),
    "s10_out_of_scope_declines": (True, [], None),
    "s11_out_of_scope_decoy": (False, ["declines_when_unsupported"], None),
    "s12_out_of_scope_speculates": (False, ["declines_when_unsupported"], None),
    "s13_leads_with_the_confusable": (False, ["leads_with_gold"], "uncited"),
    "s14_transliterated_spelling": (True, [], "uncited"),
    "s15_patronymic_is_not_a_name": (False, ["no_unsupported_names"], "uncited"),
    "s16_cites_the_wrong_chunk": (True, [], "wrong"),
    "s17_truncated": (False, ["finish_stop"], "uncited"),
    "s18_roles_swapped": (False, ["binds_roles"], "uncited"),
    "s19_from_memory": (False, ["answers_from_graph", "no_unsupported_names"], "uncited"),
    "s21_of_alias_with_a_gap": (True, [], "uncited"),
    "s22_no_record": (False, [], None),
    "s23_errored_record": (False, [], None),
    "s24_decoy_inside_a_longer_name": (True, [], None),
    "s25_common_word_opens_the_sentence": (True, [], "uncited"),
    "s28_wrong_fact_in_single_emphasis": (False, ["must_not_contain"], "uncited"),
    "s29_wrong_fact_in_backticks": (False, ["must_not_contain"], "uncited"),
    "s30_wrong_fact_in_underscores": (False, ["must_not_contain"], "uncited"),
    "s31_wrong_fact_across_a_hard_space_and_a_line_break": (False, ["must_not_contain"], "uncited"),
    "s32_wrong_fact_spelled_with_y": (False, ["must_not_contain"], "uncited"),
    "s33_wrong_fact_around_a_citation": (False, ["must_not_contain"], "correct"),
    "s34_answer_term_in_emphasis": (True, [], "uncited"),
    "s35_decoy_spelled_with_i": (False, ["declines_when_unsupported"], None),
    "s36_soft_hyphen_inside_a_name": (True, [], "uncited"),
    "s37_word_joiner_inside_a_name": (True, [], "uncited"),
    "s38_en_dash_inside_a_word": (True, [], "uncited"),
    "s39_wrong_fact_spelled_with_iy": (False, ["must_not_contain"], "uncited"),
    "s40_wrong_fact_spelled_with_ii": (False, ["must_not_contain"], "uncited"),
    "s41_roman_numeral_is_not_an_ii_spelling": (True, [], "uncited"),
    "s42_zero_width_space_between_two_words": (True, [], "uncited"),
    "s43_spaced_en_dash_is_a_spaced_hyphen": (True, [], "uncited"),
    "s44_wrong_fact_inside_a_negated_clause": (True, [], "uncited"),
    "s45_a_line_break_ends_the_negated_clause": (False, ["must_not_contain"], "uncited"),
    "s46_not_only_adds": (False, ["must_not_contain"], "uncited"),
    "s47_not_only_denies_the_phrase_s_own_only": (True, [], "uncited"),
    "s48_but_ends_the_negated_clause": (False, ["must_not_contain"], "uncited"),
    "s49_a_phrase_with_its_own_exceptive": (False, ["must_not_contain"], "uncited"),
    "s50_a_phrase_marked_stated_when_negated": (False, ["must_not_contain"], "uncited"),
    "s51_an_apposition_is_not_negated": (False, ["must_not_contain"], "uncited"),
    "s52_a_unit_spaced_from_its_number": (True, [], "uncited"),
    "s53_a_version_without_its_v": (True, [], "uncited"),
    "s54_a_size_in_k_in_binary_thousands": (True, [], "uncited"),
    "s55_a_size_in_k_in_decimal_thousands": (True, [], "uncited"),
    "s56_no_ends_a_sentence": (True, [], "uncited"),
    "s57_acronyms_keys_and_placeholders": (True, [], "uncited"),
    "s58_english_openers": (True, [], "uncited"),
    "s59_a_hyphenated_compound": (True, [], "uncited"),
    "s60_a_wrong_member_beside_the_gold": (False, ["no_wrong_members"], "uncited"),
    "s61_a_holder_s_own_list": (True, [], "uncited"),
}


def _failing(details: list[str]) -> list[str]:
    """The names of the checks a verdict's details report as failing."""
    return sorted(d.split(":", 1)[0].removeprefix("FAIL ") for d in details if d.startswith("FAIL"))


def test_every_sample_reproduces_its_golden_verdict() -> None:
    """Each sample passes or fails exactly the checks recorded for it."""
    result = recipe.recipe_verdicts()
    got = {
        v["id"]: (v["passed"], _failing(v["details"]), v["citation"])
        for v in result.metrics["verdicts"]
    }
    assert got == GOLDEN
    assert result.metrics["excluded"] == ["s20_excluded"]


def test_the_recipe_is_the_one_recorded_for_this_version() -> None:
    """A verdict change without a version bump fails here (the v5 drift, H3)."""
    assert recipe.verdict_recipe() == RECIPE_BY_VERSION.get(cp.CHAT_PROBE_SCORER_VERSION), (
        "the chat probe verdicts moved: bump CHAT_PROBE_SCORER_VERSION, name the change in the "
        "chat_probes docstring and record the new recipe in RECIPE_BY_VERSION"
    )
    assert len(set(RECIPE_BY_VERSION.values())) == len(RECIPE_BY_VERSION)  # each version differs


def test_the_samples_exercise_every_check() -> None:
    """Every check of every band fails on at least one sample, so none can move unseen."""
    failed = {name for _, fails, _ in GOLDEN.values() for name in fails}
    every_check = {c for checks in cp.ChatProbeScorer.CHECKS_BY_BAND.values() for c in checks}
    assert every_check <= failed, every_check - failed
    assert {"correct", "wrong", "uncited", None} <= {c for *_, c in GOLDEN.values()}


def test_the_recipe_moves_when_the_scorer_source_does(monkeypatch: pytest.MonkeyPatch) -> None:
    """Belt and braces (audit wave 2): an edit no sample sees still moves the recipe."""
    first = recipe.compute_scorer_recipe()
    assert recipe.compute_verdict_recipe() == recipe.verdict_recipe()
    real = recipe.source_digest()
    monkeypatch.setattr(recipe, "source_digest", lambda: real[::-1])
    assert recipe.compute_scorer_recipe() != first
    assert recipe.compute_verdict_recipe() == recipe.verdict_recipe()  # the samples did not move


def test_the_source_digest_reads_every_scorer_module(tmp_path: Path) -> None:
    """The digest covers the checks, their helpers, the samples and the loader, by content."""
    assert {
        "chaoscypher_core.benchmark.scorers.chat_probes",
        "chaoscypher_core.benchmark.scorers.probes",
        "chaoscypher_core.benchmark.scorers.chat_probe_recipe",
        "chaoscypher_core.benchmark.queries",
    } <= set(recipe.SCORER_MODULES)
    every = recipe.source_digest()
    assert every != recipe.source_digest(recipe.SCORER_MODULES[:-1])
    import sys

    (tmp_path / "scorer_probe_mod.py").write_text("X = 1\n", encoding="utf-8")
    sys.path.insert(0, str(tmp_path))
    try:
        one = recipe.source_digest(("scorer_probe_mod",))
        (tmp_path / "scorer_probe_mod.py").write_text("X = 2\n", encoding="utf-8")
        assert recipe.source_digest(("scorer_probe_mod",)) != one
    finally:
        sys.path.remove(str(tmp_path))
    with pytest.raises(RuntimeError, match="no source file"):
        recipe.source_digest(("chaoscypher_core.no_such_scorer_module",))


def test_the_recipe_moves_when_a_check_does(monkeypatch: pytest.MonkeyPatch) -> None:
    """Computed, never declared: a check that rules differently changes the recipe."""
    first = recipe.compute_scorer_recipe()
    assert first == recipe.compute_scorer_recipe() and len(first) == 12
    checks = dict(cp.ChatProbeScorer.CHECKS)
    checks["must_not_contain"] = lambda _rec, _q: (True, "wrong facts stated=[]")
    monkeypatch.setattr(cp.ChatProbeScorer, "CHECKS", checks)
    assert recipe.compute_scorer_recipe() != first
    monkeypatch.undo()
    monkeypatch.setattr(cp, "_NOT_A_NAME", cp._NOT_A_NAME | {"ivan"})
    assert recipe.compute_scorer_recipe() != first


def test_score_stamps_the_recipe_on_its_metrics() -> None:
    """``score`` carries the recipe the row copies; the verdicts are the recipe-free ones."""
    raw, fixture = recipe.recipe_inputs()
    stamped = cp.ChatProbeScorer().score(raw, fixture)
    plain = cp.ChatProbeScorer().score_without_recipe(raw, fixture)
    assert stamped.metrics["scorer_recipe"] == recipe.scorer_recipe()
    assert {k: v for k, v in stamped.metrics.items() if k != "scorer_recipe"} == plain.metrics
    assert stamped.headline_score == plain.headline_score
