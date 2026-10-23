# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""One resolution of a chat row's levers, and what the row records about them (audit H5, H11, R10)."""

from __future__ import annotations

import pytest

from chaoscypher_core.benchmark.chat_run import (
    SHAPE_ROW_KEYS,
    chat_context_window,
    lever_signature,
    parse_sweep_set,
    prompt_template_sha256,
    resolve_run_graphrag,
    resolved_levers,
    retrieval_shape,
    retrieved_entity_ids,
    run_provenance,
)
from chaoscypher_core.settings import GraphRAGProfileRule, GraphRAGSettings, LLMSettings


def _with_profile() -> GraphRAGSettings:
    settings = GraphRAGSettings()
    settings.profiles = {"small": {"max_triples": 12, "entity_aliases": True}}
    settings.profile_rules = [GraphRAGProfileRule(profile="small", max_params_b=27)]
    return settings


def test_the_sweep_variant_is_applied_after_the_profile() -> None:
    """A profile overwrote a SWEEP_SET lever on the models it matched until 2026-10-06."""
    out = resolve_run_graphrag(
        _with_profile(), model="qwen3.5:9b", sweep_set="graphrag.max_triples=33"
    )
    assert out.max_triples == 33 and out.entity_aliases is True
    # a model the profile does not match gets the variant on the plain levers
    big = resolve_run_graphrag(
        _with_profile(), model="gemma4:31b", sweep_set="graphrag.max_triples=33"
    )
    assert big.max_triples == 33 and big.entity_aliases is GraphRAGSettings().entity_aliases


def test_no_profile_and_no_variant_return_the_settings_themselves() -> None:
    settings = GraphRAGSettings()
    assert resolve_run_graphrag(settings, model="m") is settings


def test_a_variant_value_the_field_rejects_is_an_error_not_a_new_fingerprint() -> None:
    """'conected' ran all_edges under its own pins (audit R9)."""
    with pytest.raises(ValueError, match="triple_selection"):
        resolve_run_graphrag(
            GraphRAGSettings(), model="m", sweep_set="graphrag.triple_selection=conected"
        )
    with pytest.raises(ValueError, match="SWEEP_SET: no GraphRAG setting 'nope'"):
        parse_sweep_set("graphrag.nope=1")


def test_the_row_records_every_lever_and_a_hash_of_the_prompt() -> None:
    settings = GraphRAGSettings()
    run = run_provenance(settings, sweep_set="graphrag.max_triples=33", num_ctx=32768)
    assert run["levers"] == resolved_levers(settings)
    assert "profiles" not in run["levers"] and "profile_rules" not in run["levers"]
    assert run["lever_signature"] == lever_signature(run["levers"])
    assert run["sweep_set"] == "graphrag.max_triples=33" and run["num_ctx"] == 32768
    flipped = settings.model_copy(update={"answer_policy": not settings.answer_policy})
    assert prompt_template_sha256(flipped) != run["prompt_sha256"]
    assert lever_signature(None) == "unrecorded"


def test_the_prompt_hash_moves_with_the_policy_text(monkeypatch: pytest.MonkeyPatch) -> None:
    from chaoscypher_core.services.chat.engine import constants

    settings = GraphRAGSettings(answer_policy=True)
    before = prompt_template_sha256(settings)
    monkeypatch.setattr(constants, "GROUNDED_ANSWER_POLICY", "Another policy.")
    assert prompt_template_sha256(settings) != before


def test_retrieved_entity_ids_come_from_the_graph_context() -> None:
    """The top-level 'entities' key no rung fills left every row's ids empty (R10)."""
    retrieved = {
        "graph_context": {
            "seed_entities": [{"id": "n1"}, {"id": "n2"}],
            "related_entities": [{"id": "n3"}, {"id": "n1"}, {"label": "no id"}],
        },
        "entities": [{"id": "dead"}],
    }
    assert retrieved_entity_ids(retrieved) == ["n1", "n2", "n3"]
    assert retrieved_entity_ids({"chunks": []}) == []


def test_the_context_window_is_ollamas_num_ctx() -> None:
    assert chat_context_window(LLMSettings(chat_provider="ollama", ollama_num_ctx=8192)) == 8192
    assert chat_context_window(LLMSettings(chat_provider="anthropic")) is None
    assert chat_context_window(None) is None


def test_a_dict_lever_is_set_from_the_sweep_as_json() -> None:
    got = parse_sweep_set(
        'graphrag.shape_levers={"path": {"vector_reserve": 2}};graphrag.max_hops=2'
    )
    assert got == {"shape_levers": {"path": {"vector_reserve": 2}}, "max_hops": 2}


def test_a_dict_lever_that_is_not_json_is_an_error() -> None:
    with pytest.raises(ValueError, match="JSON object"):
        parse_sweep_set("graphrag.shape_levers=path:2")
    with pytest.raises(ValueError, match="JSON object"):
        parse_sweep_set("graphrag.shape_levers=[1, 2]")


@pytest.mark.parametrize("name", ["profiles", "profile_rules"])
def test_a_sweep_naming_a_profile_field_is_refused(name: str) -> None:
    """The profile is resolved before the sweep applies, so such a lever would do nothing (Task 2 review)."""
    with pytest.raises(ValueError, match=rf"^SWEEP_SET: {name} .*resolved before the sweep"):
        parse_sweep_set(f"graphrag.{name}={{}}")


def test_the_router_and_the_shape_bundles_stay_sweepable() -> None:
    """Both are read per question, after the sweep: the phase A variants set them."""
    got = parse_sweep_set(
        'graphrag.router=rules;graphrag.shape_levers={"path": {"vector_reserve": 4}}'
    )
    assert got == {"router": "rules", "shape_levers": {"path": {"vector_reserve": 4}}}


def test_a_row_records_the_shape_its_retrieval_ran_with() -> None:
    """``shape``, ``shape_reason``, ``shape_source`` from ``retrieval_stats``; None where none ran."""
    retrieved = {
        "chunks": [],
        "retrieval_stats": {
            "shape": "path",
            "shape_reason": "two named entities and between",
            "shape_source": "rules",
            "chunk_limit": 10,
        },
    }
    assert SHAPE_ROW_KEYS == ("shape", "shape_reason", "shape_source")
    assert retrieval_shape(retrieved) == {
        "shape": "path",
        "shape_reason": "two named entities and between",
        "shape_source": "rules",
    }
    hybrid = {"chunks": [], "retrieval_stats": {"mode": "hybrid", "faults": []}}
    assert retrieval_shape(hybrid) == dict.fromkeys(SHAPE_ROW_KEYS)
    assert retrieval_shape({"chunks": []}) == dict.fromkeys(SHAPE_ROW_KEYS)  # naive: no stats
