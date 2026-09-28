# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Ollama model-tag normalization.

Ollama's ``/api/tags`` always reports fully-tagged names (``llama3:latest``),
while users and the curated registries routinely configure the untagged form
(``llama3``, ``bge-m3``) — which ``ollama pull`` and ``/api/chat`` accept as
``:latest``. Comparing the two literally reports every untagged model as
"not pulled" and trips the extraction-ready gate that blocks chat and
import. Shared by the LLM health snapshot and the Ollama embedding probe so
both sides agree on one rule.
"""

from __future__ import annotations

from collections.abc import Iterable


_DEFAULT_TAG = "latest"


def normalize_ollama_tag(name: str) -> str:
    """Return ``name`` with an explicit ``:latest`` tag when it has none.

    A ``/`` in the name is a namespace (``library/llama3``,
    ``hf.co/org/model``), not a tag separator, so only a colon *after* the
    last slash counts as a tag. Whitespace is stripped; the empty string is
    returned unchanged.
    """
    cleaned = name.strip()
    if not cleaned:
        return cleaned
    tail = cleaned.rsplit("/", 1)[-1]
    if ":" in tail:
        return cleaned
    return f"{cleaned}:{_DEFAULT_TAG}"


def ollama_model_present(name: str, installed: Iterable[str]) -> bool:
    """Whether ``name`` names a model in ``installed`` (tag-normalized on both sides)."""
    wanted = normalize_ollama_tag(name)
    return any(normalize_ollama_tag(candidate) == wanted for candidate in installed)


__all__ = ["normalize_ollama_tag", "ollama_model_present"]
