# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The grounded-chat prompt every chat row is asked with (local runs and the MCP bridge)."""

from __future__ import annotations

from chaoscypher_core.benchmark.chat_prompt import format_retrieved_context, grounded_chat_prompt


def test_the_prompt_text_is_pinned() -> None:
    """Changing it changes every chat score: it must be a deliberate edit."""
    assert grounded_chat_prompt("Who?", "- Kutuzov") == (
        "Answer the question using ONLY the retrieved context below. If the "
        "context does not contain the answer, say that it is not in the sources "
        "instead of guessing.\n\n"
        "Retrieved context:\n- Kutuzov\n\nQuestion: Who?\n\nAnswer:"
    )


def test_context_lists_entities_relationships_then_chunks() -> None:
    """Entities with descriptions, triples (dict or tuple), then chunk text; empty is marked."""
    retrieved = {
        "graph_context": {
            "seed_entities": [{"label": "Kutuzov", "description": "a general"}],
            "related_entities": [{"name": "Bagration"}],
            "relationships": [
                {"source": "Bagration", "label": "serves under", "target": "Kutuzov"},
                ("Kutuzov", "commands", "army"),
            ],
        },
        "chunks": [{"text": "  Kutuzov commanded.  "}, {"content": ""}, "raw chunk"],
    }
    assert format_retrieved_context(retrieved).splitlines() == [
        "- Kutuzov: a general",
        "- Bagration",
        "- Bagration serves under Kutuzov",
        "- Kutuzov commands army",
        "Kutuzov commanded.",
        "raw chunk",
    ]
    assert format_retrieved_context({}) == "(empty)"


def test_a_result_that_carries_its_rendering_is_prompted_with_exactly_that_text() -> None:
    """The handler's ``context_text`` is what the app's chat loop hands the model: the benchmark
    prompts with the same text, so the leaderboard measures what the product shows.
    """
    retrieved = {"context_text": "- rendered by the handler", "chunks": [{"text": "ignored"}]}
    assert format_retrieved_context(retrieved) == "- rendered by the handler"


def test_the_citation_rule_is_the_apps_marker_and_only_when_asked() -> None:
    from chaoscypher_core.benchmark.chat_prompt import CITATION_RULE

    asked = grounded_chat_prompt("Who?", "- Kutuzov", ask_for_citations=True)
    assert "[[cite:Cn:Sm|filename]]" in CITATION_RULE
    assert CITATION_RULE in asked and asked.endswith("Question: Who?\n\nAnswer:")
    assert CITATION_RULE not in grounded_chat_prompt("Who?", "- Kutuzov")


def test_the_answer_policy_is_the_app_prompts_own_text_and_off_by_default() -> None:
    """One policy text for the app's system prompt and the benchmark (2026-10-05)."""
    from chaoscypher_core.services.chat.engine.constants import GROUNDED_ANSWER_POLICY

    plain = grounded_chat_prompt("Who?", "- Kutuzov")
    assert GROUNDED_ANSWER_POLICY not in plain
    with_policy = grounded_chat_prompt("Who?", "- Kutuzov", answer_policy=True)
    assert GROUNDED_ANSWER_POLICY in with_policy
    assert with_policy.endswith("Retrieved context:\n- Kutuzov\n\nQuestion: Who?\n\nAnswer:")
    assert "relationship lines" in GROUNDED_ANSWER_POLICY
    assert "every part" in GROUNDED_ANSWER_POLICY
    assert "Names vary" in GROUNDED_ANSWER_POLICY  # the passage-only rungs' pattern (v2)
    assert "Before you say a part" in GROUNDED_ANSWER_POLICY  # v5e declines (2026-10-12)
    assert (
        "say the line disagrees" not in GROUNDED_ANSWER_POLICY
    )  # lever B: measured -3.0, removed 2026-10-12
