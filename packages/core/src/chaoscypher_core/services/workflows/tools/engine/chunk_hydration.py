# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Shared chunk hydration utilities for RAG result formatting.

Provides stateless helper functions for formatting raw document chunks
into LLM-readable form with sentence numbering, alias assignment, and
metadata cleaning.  Shared by the node tool handlers and the GraphRAG
handler so the formatting logic lives in exactly one place.
"""

from typing import Any

from chaoscypher_core.services.sources.engine.extraction.utils.sentence_splitter import (
    split_into_sentences,
)


UNTRUSTED_FENCE_OPEN = "<untrusted_document>"
UNTRUSTED_FENCE_CLOSE = "</untrusted_document>"
_DEFANGED_FENCE_CLOSE = "<\\/untrusted_document>"


def neutralize_untrusted_fence(text: str) -> str:
    r"""Defang literal closing fence tags inside document-derived text.

    A document that itself contains the literal ``</untrusted_document>``
    string could otherwise terminate the fence early, letting the rest of
    its text read as out-of-band content. Replaces every occurrence with
    a defanged form (``<\\/untrusted_document>``) so exactly one closing
    fence — the one appended by the wrapper — survives.

    Args:
        text: Document-derived text about to be fenced.

    Returns:
        ``text`` with all literal closing fence tags defanged.

    """
    return text.replace(UNTRUSTED_FENCE_CLOSE, _DEFANGED_FENCE_CLOSE)


def format_chunk_content(content: str, filename: str, alias: str) -> tuple[str, int]:
    """Format raw chunk content into a numbered, headed block for LLM consumption.

    Sentence-splits ``content``, prefixes each sentence with ``[S{n}]``, and
    prepends a chunk header of the form ``[CHUNK {alias} | {filename}]``.
    The full block is wrapped in ``<untrusted_document>`` / ``</untrusted_document>``
    fence tags to signal the LLM that the body is data retrieved from a
    user-supplied document (not instructions). The chat system prompt names
    this fence and instructs the model to treat text inside it as untrusted
    data; any literal closing tag inside the body is defanged first (see
    :func:`neutralize_untrusted_fence`) so the document cannot forge an
    early end of the fence.

    Args:
        content: Raw text content of the chunk.
        filename: Source filename used in the chunk header (may be empty string).
        alias: Short alias for the chunk (e.g. ``"C0"``) used in the header and
            citation references.

    Returns:
        A tuple of ``(formatted_content, sentence_count)`` where
        ``formatted_content`` is the full headed block ready for LLM injection
        and ``sentence_count`` is the number of sentences detected.

    """
    sentences = split_into_sentences(content) if content else []
    sentence_lines = "\n".join(f"[S{i + 1}] {s}" for i, s in enumerate(sentences))
    header = f"[CHUNK {alias} | {filename}]"
    body = f"{header}\n{sentence_lines}" if sentence_lines else header
    body = neutralize_untrusted_fence(body)
    numbered_content = f"{UNTRUSTED_FENCE_OPEN}\n{body}\n{UNTRUSTED_FENCE_CLOSE}"
    return numbered_content, len(sentences)


def assign_chunk_aliases(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Assign sequential C0, C1, C2… aliases to a list of chunk dicts.

    Mutates each chunk dict in-place, setting (or updating) the
    ``chunk_alias`` key.  Also updates the ``[CHUNK …]`` header in the
    ``content`` field to reflect the new alias so citation labels remain
    consistent after reranking.

    Args:
        chunks: List of chunk dicts.  Each dict must have a ``"content"``
            field whose first line is a ``[CHUNK {old_alias} | …]`` header.

    Returns:
        The same list with mutated dicts (identical reference).

    """
    for i, chunk in enumerate(chunks):
        new_alias = f"C{i}"
        old_alias = chunk.get("chunk_alias", "")
        chunk["chunk_alias"] = new_alias
        if old_alias and old_alias != new_alias:
            chunk["content"] = chunk["content"].replace(
                f"[CHUNK {old_alias} |", f"[CHUNK {new_alias} |"
            )
    return chunks


def clean_chunk_metadata(chunk_metadata: dict[str, Any] | None) -> dict[str, Any] | None:
    """Strip ``combined_content`` from ``hierarchical_group`` in chunk metadata.

    Returns a shallow copy of ``chunk_metadata`` with the ``combined_content``
    key removed from the nested ``hierarchical_group`` dict (if present).
    The original dict is never mutated.

    ``combined_content`` contains sibling chunks' full text.  Sending it to
    the LLM causes hallucinations where the model quotes from chunks it did
    not actually retrieve, so it must be removed before injection.

    Args:
        chunk_metadata: Raw ``chunk_metadata`` dict from the storage layer,
            or ``None`` if the chunk has no metadata.

    Returns:
        A cleaned copy of the metadata dict, or ``None`` if the input was
        ``None``.

    """
    if chunk_metadata is None:
        return None
    cleaned = dict(chunk_metadata)
    hg = cleaned.get("hierarchical_group")
    if isinstance(hg, dict):
        cleaned["hierarchical_group"] = {k: v for k, v in hg.items() if k != "combined_content"}
    return cleaned


__all__: list[str] = [
    "UNTRUSTED_FENCE_CLOSE",
    "UNTRUSTED_FENCE_OPEN",
    "assign_chunk_aliases",
    "clean_chunk_metadata",
    "format_chunk_content",
    "neutralize_untrusted_fence",
]
