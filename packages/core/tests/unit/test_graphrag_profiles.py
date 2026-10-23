# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""GraphRAG lever profiles: resolved per model id, size band and domain, first rule wins."""

from __future__ import annotations

from typing import Any

import pytest

from chaoscypher_core.graphrag_profiles import (
    parameter_size_b,
    profile_for,
    resolve_graphrag_profile,
)
from chaoscypher_core.question_shapes import PRE_SHAPE_FIELDS
from chaoscypher_core.settings import RESOLUTION_FIELDS, GraphRAGProfileRule, GraphRAGSettings


def _settings() -> GraphRAGSettings:
    return GraphRAGSettings(
        profiles={
            "lean": {"entity_aliases": False, "chunk_limit": 10},
            "rich": {"entity_aliases": True, "relationship_evidence": True},
            "novel": {"seed_paths": True},
        },
        profile_rules=[
            GraphRAGProfileRule(profile="lean", model="qwen3:30b-instruct"),
            GraphRAGProfileRule(profile="rich", max_params_b=27),
            GraphRAGProfileRule(profile="novel", domain="literary"),
            GraphRAGProfileRule(profile="lean", min_params_b=27),
        ],
    )


def test_parameter_size_reads_ollama_strings_and_model_tags() -> None:
    assert parameter_size_b("30.5B") == 30.5
    assert parameter_size_b("gemma4:31b") == 31
    assert parameter_size_b("800M") == 0.8
    assert parameter_size_b("qwen3.5:9b") == 9
    assert parameter_size_b(None) is None
    assert parameter_size_b("latest") is None


def test_the_first_matching_rule_wins_exact_model_before_size_before_domain() -> None:
    s = _settings()
    assert profile_for(s, model="qwen3:30b-instruct") == "lean"  # exact, though 30B > 27
    assert profile_for(s, model="qwen3.5:9b") == "rich"  # size band from the tag
    assert profile_for(s, model="gemma4:31b") == "lean"  # the min_params rule
    assert profile_for(s, model="gemma4:31b", domain="literary") == "novel"  # domain before min
    assert profile_for(s, model="mystery:latest") is None  # no size, no rule
    assert profile_for(s, model="mystery:latest", size_b=14) == "rich"


@pytest.mark.parametrize(
    ("rule_domain", "label", "matches"),
    [
        ("literary", "literary", True),
        ("Literary", "literary", True),  # case is ignored
        ("literary", "historical+literary", True),  # one of the label's packs
        ("historical+literary", "historical+literary", True),  # the label exactly
        ("historical", "literary", False),
        ("generic", "generic", True),  # the floor alone
        (
            "generic",
            "literary",
            False,
        ),  # every corpus has the floor; only a pack-less one is named so
        ("literary", None, False),
    ],
)
def test_a_rules_domain_matches_the_label_or_one_of_its_packs(
    rule_domain: str, label: str | None, matches: bool
) -> None:
    from chaoscypher_core.graphrag_profiles import domain_matches, rule_matches

    assert domain_matches(rule_domain, label) is matches
    rule = GraphRAGProfileRule(profile="p", domain=rule_domain)
    assert rule_matches(rule, model=None, size_b=None, domain=label) is matches


def test_resolving_applies_the_levers_and_returns_settled_settings() -> None:
    s = _settings()
    rich = resolve_graphrag_profile(s, model="qwen3.5:9b")
    assert rich.entity_aliases is True
    assert rich.relationship_evidence is True
    assert rich.chunk_limit == s.chunk_limit  # untouched levers keep the base values
    assert rich.profiles == {}
    assert rich.profile_rules == []
    assert resolve_graphrag_profile(rich, model="qwen3.5:9b") is rich  # settled
    assert resolve_graphrag_profile(s, model="mystery:latest") is s  # no match: the base


def test_a_profile_cannot_set_the_profile_fields_or_an_unknown_lever() -> None:
    s = GraphRAGSettings(
        profiles={"x": {"profiles": {}}, "y": {"not_a_field": 1}},
        profile_rules=[
            GraphRAGProfileRule(profile="x", model="a"),
            GraphRAGProfileRule(profile="y", model="b"),
        ],
    )
    with pytest.raises(ValueError, match="may not set"):
        resolve_graphrag_profile(s, model="a")
    with pytest.raises(ValueError):
        resolve_graphrag_profile(s, model="b")
    with pytest.raises(ValueError, match="not defined"):
        resolve_graphrag_profile(
            GraphRAGSettings(profile_rules=[GraphRAGProfileRule(profile="ghost")]), model="a"
        )


def test_shape_levers_apply_on_top_of_the_model_profile_and_only_known_levers() -> None:
    from chaoscypher_core.graphrag_profiles import apply_shape_levers

    base = GraphRAGSettings(
        profiles={"small": {"vector_reserve": 8}},
        profile_rules=[GraphRAGProfileRule(profile="small", model="m")],
        shape_levers={"path": {"vector_reserve": 2, "evidence_chunks": 5}},
    )
    resolved = resolve_graphrag_profile(base, model="m")
    assert resolved.vector_reserve == 8
    path = apply_shape_levers(resolved, "path")
    assert (path.vector_reserve, path.evidence_chunks) == (2, 5)
    assert apply_shape_levers(resolved, "lookup").vector_reserve == 8  # no bundle: unchanged
    assert resolved.vector_reserve == 8  # a copy, never in place


def test_a_shape_bundle_may_not_set_the_resolution_fields() -> None:
    from chaoscypher_core.graphrag_profiles import apply_shape_levers

    bundles: list[dict[str, Any]] = [
        {"profiles": {}},
        {"shape_levers": {}},
        {"router": "off"},
        {"no_such_lever": 1},
    ]
    for bad in bundles:
        # past the settings' own check (it refuses these when configured): apply refuses them too
        unchecked = GraphRAGSettings.model_construct(shape_levers={"path": bad})
        with pytest.raises(ValueError):
            apply_shape_levers(unchecked, "path")


def test_the_shipped_shape_bundles_and_description_defaults() -> None:
    """The shipped shape is the one the panel measured (2026-10-19, roadmap step 2.1).

    Four builds (v5d to main1015) read ``router=rules`` with question-aware descriptions ahead
    of the LLM-routed base, +6 median on the panel and level on the holdout, and the rules
    router costs no model call; the board measures the product's defaults, never a SWEEP_SET.
    """
    s = GraphRAGSettings()
    assert s.router == "rules"
    assert s.shape_levers == {
        "aggregate": {"vector_reserve": 4, "evidence_chunks": 6},
        "path": {"vector_reserve": 4, "evidence_chunks": 6},
        "global": {"vector_reserve": 4, "evidence_chunks": 2},
    }
    assert (s.evidence_chunks, s.max_hops, s.aggregate_per_label) == (0, 3, 12)
    assert (s.entity_description_chars, s.clean_descriptions, s.question_aware_descriptions) == (
        400,
        True,
        True,
    )
    assert s.question_parts is False


def test_a_shape_bundle_keyed_by_an_unknown_shape_is_refused() -> None:
    """A misspelt shape (``paht``) is an error naming it and the shapes, never a bundle that never runs."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match=r"'paht'.*lookup, aggregate, path, global"):
        GraphRAGSettings(shape_levers={"paht": {"vector_reserve": 2}})
    settings = GraphRAGSettings()
    with pytest.raises(ValidationError, match="'paht'"):  # a sweep override is checked the same
        settings.shape_levers = {"paht": {}}
    assert GraphRAGSettings(shape_levers={"lookup": {"vector_reserve": 2}}).shape_levers


@pytest.mark.parametrize(
    "field", ["no_such_lever", *sorted(RESOLUTION_FIELDS | set(PRE_SHAPE_FIELDS))]
)
def test_a_shape_bundle_naming_no_post_shape_lever_is_refused_when_configured(field: str) -> None:
    """Unknown, resolution and pre-shape fields are refused by the settings, never mid-retrieval.

    A pre-shape field (the seeds', PageRank's, the router's) is read before the shape is known,
    so a bundle setting it would do nothing or split the pipeline.
    """
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match=field):
        GraphRAGSettings(shape_levers={"path": {field: GraphRAGSettings().model_dump().get(field)}})


def test_the_resolution_fields_are_one_set_the_profiles_read() -> None:
    import chaoscypher_core.graphrag_profiles as profiles

    assert profiles.RESOLUTION_FIELDS is RESOLUTION_FIELDS
    assert {"profiles", "profile_rules", "shape_levers", "router"} == RESOLUTION_FIELDS
    assert set(PRE_SHAPE_FIELDS) <= set(GraphRAGSettings.model_fields)
