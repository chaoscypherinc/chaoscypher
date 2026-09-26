# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""ProbeScorer checks: each is a pure function of one probe record.

These are the unit tests for the checkers themselves. They deliberately do
NOT prove the probe pipeline end to end — that requires a real model run,
and this week showed twice that unit tests pass while the real path is
broken. The end-to-end check is `scratchpad`-driven and reported separately.
"""

from __future__ import annotations

from chaoscypher_core.benchmark.probes import Probe
from chaoscypher_core.benchmark.scorers import probes as probe_checks
from chaoscypher_core.benchmark.types import RawOutput


def _rec(**over):
    base = {
        "id": "X",
        "entities": [],
        "relationships": [],
        "input_tokens": 100,
        "output_tokens": 100,
        "finish_reason": "stop",
        "aborted_by_loop": False,
        "parser_lines_dropped": 0,
        "invalid_relationship_count": 0,
        "raw_llm_response": "=== PASS 1 (Entities) ===\nE|A|Person|a|1.0|S1|desc\n\n=== PASS 2 (Relationships) ===\n",
        "sentences": ["one.", "two.", "three."],
    }
    base.update(over)
    return base


def _ent(name, aliases=(), props=None, ref="S1"):
    e = {"name": name, "aliases": list(aliases), "sent_ref": ref, "type": "Person"}
    if props is not None:
        e["properties"] = props
    return e


# --- individual checks --------------------------------------------------


def test_finish_stop_fails_on_length() -> None:
    ok, _ = probe_checks.finish_stop(_rec(finish_reason="length"), {})
    assert not ok
    assert probe_checks.finish_stop(_rec(), {})[0]


def test_entity_count_matching_merges_aliases() -> None:
    """One entity carrying all name variants counts once; two rows count twice."""
    merged = _rec(entities=[_ent("Prince Andrei Bolkonsky", ["Andrew", "Andryusha"])])
    split = _rec(entities=[_ent("Prince Andrei"), _ent("Andrew Bolkonsky")])
    args = {"names": ["Andrei", "Andrew", "Andryusha"], "max": 1}
    assert probe_checks.entity_count_matching(merged, args)[0]
    assert not probe_checks.entity_count_matching(split, args)[0]


def test_no_entity_matching_catches_alias_leak() -> None:
    rec = _rec(entities=[_ent("Some Officer", aliases=["Dolokhov"])])
    ok, why = probe_checks.no_entity_matching(rec, {"names": ["Dolokhov"]})
    assert not ok and "Some Officer" in why


def test_property_key_unique_flags_repeat_regardless_of_case() -> None:
    rec = _rec(entities=[_ent("Pierre", props={"Title": "Count"})])
    assert probe_checks.property_key_unique_per_entity(rec, {})[0]
    rec = _rec(
        entities=[
            _ent(
                "Pierre",
                props=[{"key": "title", "value": "Count"}, {"key": "Title", "value": "Count"}],
            )
        ]
    )
    assert not probe_checks.property_key_unique_per_entity(rec, {})[0]


def test_relationship_count_pair_is_direction_agnostic() -> None:
    rels = [
        {"source": 0, "target": 1},
        {"source": 1, "target": 0},
        {"source": 0, "target": 1},
        {"source": 0, "target": 1},
    ]
    ok, why = probe_checks.relationship_count_pair(_rec(relationships=rels), {"max": 3})
    assert not ok and "=4" in why


def test_output_tokens_ratio_infinite_when_no_input() -> None:
    assert not probe_checks.output_tokens_ratio(
        _rec(input_tokens=0, output_tokens=5), {"max_ratio": 3}
    )[0]
    assert probe_checks.output_tokens_ratio(
        _rec(input_tokens=100, output_tokens=250), {"max_ratio": 3}
    )[0]


def test_only_format_lines_ignores_pass_headers_and_blank_lines() -> None:
    assert probe_checks.only_format_lines(_rec(), {})[0]
    bad = _rec(raw_llm_response="Sure! Here are the entities:\nE|A|Person|a|1.0|S1|d")
    ok, why = probe_checks.only_format_lines(bad, {})
    assert not ok and "Sure!" in why


def test_refs_in_bounds_rejects_out_of_range_and_unparseable() -> None:
    good = _rec(entities=[_ent("A", ref="S1-S2")])
    assert probe_checks.refs_in_bounds(good, {})[0]
    assert not probe_checks.refs_in_bounds(_rec(entities=[_ent("A", ref="S9")]), {})[0]
    assert not probe_checks.refs_in_bounds(_rec(entities=[_ent("A", ref="sentence one")]), {})[0]


# --- scorer aggregation -------------------------------------------------


def _probe(pid, tier, checks, section="E"):
    return Probe(
        id=pid,
        probe=pid.split("-")[0],
        section=section,
        tier=tier,
        instruction="i",
        passage="p.",
        checks=tuple(checks),
    )


def test_probe_passes_only_if_every_check_passes() -> None:
    """One failing check fails the probe; the gate runs first on incomplete runs."""
    probe = _probe("E6-easy", "easy", [{"type": "finish_stop"}, {"type": "entity_count", "max": 0}])
    # Complete run, one check fails -> per-check details are produced.
    out = RawOutput(
        entities=[],
        relationships=[],
        latency_ms=0,
        input_tokens=0,
        output_tokens=0,
        error=None,
        extras={"probes": [_rec(id="E6-easy", entities=[_ent("A")])]},
    )
    res = probe_checks.ProbeScorer().score(out, [probe])
    assert res.metrics["probes_passed"] == 0 and res.headline_score == 0.0
    v = res.metrics["verdicts"][0]
    assert any(d.startswith("PASS finish_stop") for d in v["details"])
    assert any(d.startswith("FAIL entity_count") for d in v["details"])
    # Incomplete run -> the completion gate fails it before any check runs.
    out.extras["probes"][0]["aborted_by_loop"] = True
    v = probe_checks.ProbeScorer().score(out, [probe]).metrics["verdicts"][0]
    assert v["details"] == ["did not complete: finish_reason=stop aborted_by_loop=True"]


def test_headline_is_tier_weighted() -> None:
    """One easy pass + one hard fail = 1/(1+3) = 25%, not 50%."""
    probes = [
        _probe("A-easy", "easy", [{"type": "finish_stop"}]),
        _probe("A-hard", "hard", [{"type": "finish_stop"}]),
    ]
    out = RawOutput(
        entities=[],
        relationships=[],
        latency_ms=0,
        input_tokens=0,
        output_tokens=0,
        error=None,
        extras={"probes": [_rec(id="A-easy"), _rec(id="A-hard", finish_reason="length")]},
    )
    res = probe_checks.ProbeScorer().score(out, probes)
    assert res.headline_score == 25.0
    assert res.metrics["section_rates"]["E"] == {"easy": 100.0, "hard": 0.0}


def test_missing_record_is_a_fail_not_a_crash() -> None:
    probe = _probe("Z-easy", "easy", [{"type": "finish_stop"}])
    out = RawOutput(
        entities=[],
        relationships=[],
        latency_ms=0,
        input_tokens=0,
        output_tokens=0,
        error=None,
        extras={"probes": []},
    )
    res = probe_checks.ProbeScorer().score(out, [probe])
    assert res.metrics["probes_passed"] == 0
    assert "missing" in res.metrics["verdicts"][0]["details"][0]


def test_no_duplicate_relationships_allows_variety_but_not_repeats() -> None:
    """Eight distinct types on one pair is correct; the same triple twice is spam."""
    variety = [
        {"source": 0, "target": 1, "type": t}
        for t in ("loved", "wrote_to", "danced_with", "proposed_to")
    ]
    assert probe_checks.no_duplicate_relationships(_rec(relationships=variety), {"max": 1})[0]
    spam = [*variety, {"source": 0, "target": 1, "type": "LOVED"}]
    ok, why = probe_checks.no_duplicate_relationships(_rec(relationships=spam), {"max": 1})
    assert not ok and "=2" in why


# --- section B-D checks --------------------------------------------------


def _rel(
    s, t, typ, just="Prince Andrei fought at Austerlitz against Napoleon's forces there.", ref="S1"
):
    return {"source": s, "target": t, "type": typ, "justification": just, "sent_ref": ref}


def test_refs_name_entity_requires_the_cited_sentence_to_mention_it() -> None:
    rec = _rec(
        sentences=["Kutuzov commanded the army.", "The rain fell."],
        entities=[_ent("Kutuzov", ref="S1"), _ent("Bagration", ref="S2")],
    )
    ok, why = probe_checks.refs_name_entity(rec, {})
    assert not ok and "Bagration" in why


def test_rel_refs_contain_both_endpoints() -> None:
    rec = _rec(
        sentences=["Kutuzov ordered Bagration to hold.", "Kutuzov slept."],
        entities=[_ent("Kutuzov"), _ent("Bagration")],
        relationships=[_rel(0, 1, "commands", ref="S1"), _rel(0, 1, "commands", ref="S2")],
    )
    ok, why = probe_checks.rel_refs_contain_both(rec, {})
    assert not ok and "Kutuzov->Bagration" in why
    rec["relationships"] = [_rel(0, 1, "commands", ref="S1")]
    assert probe_checks.rel_refs_contain_both(rec, {})[0]


def test_relationship_type_absent_and_present_are_substring_matches() -> None:
    rec = _rec(relationships=[_rel(0, 1, "grandparent_of")])
    assert not probe_checks.relationship_type_absent(rec, {"types": ["grandparent"]})[0]
    assert probe_checks.relationship_type_present(rec, {"types": ["grandparent"]})[0]
    assert probe_checks.relationship_type_absent(rec, {"types": ["spouse"]})[0]


def test_every_entity_has_relationship_names_the_lonely_one() -> None:
    rec = _rec(entities=[_ent("A"), _ent("B"), _ent("C")], relationships=[_rel(0, 1, "knows")])
    ok, why = probe_checks.every_entity_has_relationship(rec, {})
    assert not ok and "C" in why


def test_justification_checks() -> None:
    short = _rec(relationships=[_rel(0, 1, "x", just="too short")])
    assert not probe_checks.justification_length(short, {"min": 50, "max": 300})[0]
    thinking = _rec(
        relationships=[
            _rel(
                0,
                1,
                "x",
                just="I think they were married, but alternatively they might be siblings; unclear.",
            )
        ]
    )
    assert not probe_checks.justification_evidence_only(thinking, {})[0]
    assert probe_checks.justification_evidence_only(_rec(relationships=[_rel(0, 1, "x")]), {})[0]


def test_confidence_band_and_alias_checks() -> None:
    e = _ent("Prince Andrei Bolkonsky", aliases=["Andrei", "Prince Andrew", "the soldiers"])
    e["confidence"] = 0.8
    rec = _rec(entities=[e])
    assert probe_checks.confidence_in_band(rec, {"name": "Andrei", "min": 0.7, "max": 0.9})[0]
    assert not probe_checks.confidence_in_band(rec, {"name": "Andrei", "min": 1.0, "max": 1.0})[0]
    assert probe_checks.aliases_include(rec, {"name": "Andrei", "aliases": ["Andrew"]})[0]
    ok, why = probe_checks.aliases_exclude(rec, {"aliases": ["soldiers", "n/a"]})
    assert not ok and "soldiers" in why


def test_types_within_and_property_values_from_text() -> None:
    e = _ent("Pierre", props={"title": "Count", "nationality": "Martian"})
    e["type"] = "Character"
    rec = _rec(entities=[e], sentences=["Pierre Bezukhov was a count.", "He was Russian."])
    assert probe_checks.types_within(rec, {"types": ["Character", "Place"]})[0]
    assert not probe_checks.types_within(rec, {"types": ["Place"]})[0]
    ok, why = probe_checks.property_values_from_text(rec, {})
    assert not ok and "nationality=martian" in why


def test_property_values_accept_faithful_paraphrase_but_not_world_knowledge() -> None:
    """Calibration 2026-09-23: 'sixteen'->16, 'sang'->singing, 'commanded'->commander
    must pass; title=emperor for a passage that never says it must fail.
    """
    e = _ent(
        "Natasha",
        props={"age": "16", "talent": "singing", "occupation": "commander", "title": "Emperor"},
    )
    rec = _rec(
        entities=[e],
        sentences=["Natasha was sixteen.", "Her singing was admired.", "She commanded the choir."],
    )
    ok, why = probe_checks.property_values_from_text(rec, {})
    assert not ok
    assert (
        "emperor" in why and "age=16" not in why and "singing" not in why and "commander" not in why
    )


def test_relationship_type_absent_ignores_negated_types() -> None:
    """'not_spouse_of' is the model correctly declining the relation."""
    rec = _rec(relationships=[_rel(0, 1, "not_spouse_of")])
    assert probe_checks.relationship_type_absent(rec, {"types": ["spouse"]})[0]
    rec = _rec(relationships=[_rel(0, 1, "spouse_of")])
    assert not probe_checks.relationship_type_absent(rec, {"types": ["spouse"]})[0]


def test_rescore_reapplies_current_checkers_to_saved_records() -> None:
    """A saved row carrying raw records can be re-scored without an LLM."""
    from types import SimpleNamespace

    probe = _probe("Z-easy", "easy", [{"type": "finish_stop"}])
    row = SimpleNamespace(extras={"probes": [_rec(id="Z-easy", finish_reason="length")]})
    assert probe_checks.rescore(row, [probe]).metrics["probes_passed"] == 0
    row.extras["probes"][0]["finish_reason"] = "stop"
    assert probe_checks.rescore(row, [probe]).metrics["probes_passed"] == 1


def test_pad_with_filler_reaches_target_and_keeps_passage_first() -> None:
    """Padding appends entity-free prose AFTER the passage, so sent_refs stay valid."""
    from chaoscypher_core.benchmark.probes import pad_with_filler
    from chaoscypher_core.services.sources.engine.extraction.utils.sentence_splitter import (
        split_into_sentences,
    )

    passage = "Kutuzov slept. Napoleon dined."
    padded = pad_with_filler(passage, 3600)
    assert padded.startswith(passage)
    assert len(padded) >= 3600
    sents = split_into_sentences(padded)
    assert sents[0].startswith("Kutuzov") and sents[1].startswith("Napoleon")
    # No proper nouns in the filler: only sentence-initial words may be capitalised.
    for sentence in sents[2:]:
        inner = sentence.split()[1:]
        assert all(not w[:1].isupper() for w in inner), sentence


def test_incomplete_run_fails_every_probe_even_if_its_checks_would_pass() -> None:
    """A truncated or loop-aborted run cannot pass vacuously.

    Calibration 2026-09-24: a model that emits nothing before hitting the
    token budget "passes" no_entity_matching and entity_count max 0.
    """
    probe = _probe("Z-easy", "easy", [{"type": "entity_count", "max": 0}])
    out = RawOutput(
        entities=[],
        relationships=[],
        latency_ms=0,
        input_tokens=0,
        output_tokens=0,
        error=None,
        extras={"probes": [_rec(id="Z-easy", finish_reason="length")]},
    )
    res = probe_checks.ProbeScorer().score(out, [probe])
    assert res.metrics["probes_passed"] == 0
    assert "did not complete" in res.metrics["verdicts"][0]["details"][0]
    out.extras["probes"][0]["finish_reason"] = "stop"
    assert probe_checks.ProbeScorer().score(out, [probe]).metrics["probes_passed"] == 1


def test_splice_into_carrier_keeps_probe_contiguous_and_is_seeded() -> None:
    from chaoscypher_core.benchmark.probes import splice_into_carrier

    carrier = "One. Two. Three. Four. Five."
    a, off_a = splice_into_carrier("Kutuzov slept. Napoleon dined.", carrier, seed=42)
    b, off_b = splice_into_carrier("Kutuzov slept. Napoleon dined.", carrier, seed=42)
    assert a == b and off_a == off_b  # reproducible
    assert "Kutuzov slept. Napoleon dined." in a  # contiguous
    assert not a.startswith("Kutuzov")  # never before the first carrier sentence
    assert off_a >= 2


def test_carrier_checks() -> None:
    rec = _rec(
        entities=[_ent("Kutuzov"), _ent("Anna Pavlovna", aliases=["Annette"]), _ent("Genoa")],
        carrier_cast=["Anna Pávlovna|Annette|Anna", "Prince Vasíli|Vasili", "Genoa", "Lucca"],
        sentences=[
            "Kutuzov read the order.",
            "Anna Pávlovna received her guests.",
            "Genoa was mentioned.",
        ],
    )
    ok, why = probe_checks.carrier_coverage(rec, {"min_fraction": 0.5})
    assert ok and "2/4" in why  # accent-folded: Pávlovna matches Pavlovna
    assert not probe_checks.carrier_coverage(rec, {"min_fraction": 0.75})[0]
    assert probe_checks.no_invention(rec, {})[0]  # all three are named in the input
    rec["entities"].append(_ent("Napoleon Bonaparte"))
    ok, why = probe_checks.no_invention(rec, {})
    assert not ok and "Napoleon" in why


def test_scorer_scores_only_the_probes_that_ran_when_only_is_set() -> None:
    """An unattempted probe is not a failure; the headline covers what ran."""
    probes = [
        _probe("A-easy", "easy", [{"type": "finish_stop"}]),
        _probe("B-easy", "easy", [{"type": "finish_stop"}]),
    ]
    out = RawOutput(
        entities=[],
        relationships=[],
        latency_ms=0,
        input_tokens=0,
        output_tokens=0,
        error=None,
        extras={"probes": [_rec(id="B-easy")], "selected_ids": ["B-easy"]},
    )
    res = probe_checks.ProbeScorer().score(out, probes)
    assert res.metrics["probes_total"] == 1 and res.metrics["probes_passed"] == 1
    assert res.headline_score == 100.0


def test_completion_counters_count_length_and_loop_records() -> None:
    """Probe rows carry the integrity counters the leaderboard warning reads."""
    from chaoscypher_core.benchmark.probes import completion_counters

    records = [
        _rec(finish_reason="stop"),
        _rec(finish_reason="length"),
        _rec(finish_reason="length", aborted_by_loop=True),
        _rec(finish_reason="unknown", aborted_by_loop=True),
    ]
    assert completion_counters(records) == (2, 2)
    assert completion_counters([]) == (0, 0)
