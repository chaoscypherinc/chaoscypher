# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The grounded-chat prompt: retrieved context rendered as text, and the question around it.

One implementation for every producer of grounded-chat rows - the CLI's
local chat stage and the MCP benchmark bridge - so a model answering
through an MCP client reads byte-for-byte what a local model reads.
"""

from __future__ import annotations

from typing import Any

from chaoscypher_core.services.workflows.tools.engine.context_render import render_retrieved_context


def format_retrieved_context(retrieved: dict[str, Any]) -> str:
    """Render what GraphRAG search returned as the context a chat model reads.

    Entities with their descriptions, the relationship triples, and the
    retrieved chunk text - so a grounded answer can be checked against
    exactly this text.

    Args:
        retrieved: The ``graphrag_search`` result (``graph_context`` and ``chunks``).

    Returns:
        One line per entity and relationship, then each chunk's text;
        ``"(empty)"`` when nothing was retrieved.
    """
    # The handler renders its own result (``context_text``): the app's chat
    # loop hands the model that text, so the benchmark prompts with the same.
    text = retrieved.get("context_text")
    if isinstance(text, str) and text:
        return text
    return render_retrieved_context(retrieved)


CITATION_RULE = (
    "Cite every claim drawn from a chunk with the chunk's label right after it, as "
    "[[cite:Cn:Sm|filename]] where Cn is the chunk label and Sm the sentence label "
    "(the app's citation marker). When you are unsure which sentence, cite that chunk "
    "with S1 rather than omitting the marker; never go back to re-check the text for it."
)


def grounded_chat_prompt(
    question: str,
    context_text: str,
    *,
    ask_for_citations: bool = False,
    answer_policy: bool = False,
) -> str:
    """Build the single user message a grounded-chat answer is asked with.

    Args:
        question: The fixture question.
        context_text: The retrieved context, as :func:`format_retrieved_context` renders it.
        ask_for_citations: Add the app's citation rule (``GraphRAGSettings.ask_for_citations``).
        answer_policy: Add the app's grounded answer policy
            (``GraphRAGSettings.answer_policy``; the same text the app's system prompt adds).

    Returns:
        The prompt: answer only from the context, or say it is not in the sources.
    """
    from chaoscypher_core.services.chat.engine.constants import GROUNDED_ANSWER_POLICY

    rule = f" {CITATION_RULE}" if ask_for_citations else ""
    policy = f"\n\n{GROUNDED_ANSWER_POLICY}" if answer_policy else ""
    return (
        "Answer the question using ONLY the retrieved context below. If the "
        "context does not contain the answer, say that it is not in the sources "
        f"instead of guessing.{rule}{policy}\n\n"
        f"Retrieved context:\n{context_text}\n\nQuestion: {question}\n\nAnswer:"
    )


__all__ = ["CITATION_RULE", "format_retrieved_context", "grounded_chat_prompt"]
