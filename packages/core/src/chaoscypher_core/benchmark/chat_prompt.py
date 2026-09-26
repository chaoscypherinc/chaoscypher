# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The grounded-chat prompt: retrieved context rendered as text, and the question around it.

One implementation for every producer of grounded-chat rows - the CLI's
local chat stage and the MCP benchmark bridge - so a model answering
through an MCP client reads byte-for-byte what a local model reads.
"""

from __future__ import annotations

from typing import Any


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
    gc = retrieved.get("graph_context", {}) or {}
    lines: list[str] = []
    for e in list(gc.get("seed_entities", []) or []) + list(gc.get("related_entities", []) or []):
        name = e.get("label") or e.get("name") or ""
        desc = e.get("description") or ""
        lines.append(f"- {name}: {desc}" if desc else f"- {name}")
    for r in gc.get("relationships", []) or []:
        if isinstance(r, dict):
            lines.append(
                f"- {r.get('source', r.get('from', ''))} {r.get('label', r.get('type', ''))} "
                f"{r.get('target', r.get('to', ''))}"
            )
        else:
            lines.append(f"- {' '.join(str(x) for x in r)}")
    for c in retrieved.get("chunks", []) or []:
        text = (c.get("text") or c.get("content") or "") if isinstance(c, dict) else str(c)
        if text:
            lines.append(text.strip())
    return "\n".join(lines) or "(empty)"


def grounded_chat_prompt(question: str, context_text: str) -> str:
    """Build the single user message a grounded-chat answer is asked with.

    Args:
        question: The fixture question.
        context_text: The retrieved context, as :func:`format_retrieved_context` renders it.

    Returns:
        The prompt: answer only from the context, or say it is not in the sources.
    """
    return (
        "Answer the question using ONLY the retrieved context below. If the "
        "context does not contain the answer, say that it is not in the sources "
        "instead of guessing.\n\n"
        f"Retrieved context:\n{context_text}\n\nQuestion: {question}\n\nAnswer:"
    )


__all__ = ["format_retrieved_context", "grounded_chat_prompt"]
