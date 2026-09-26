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
