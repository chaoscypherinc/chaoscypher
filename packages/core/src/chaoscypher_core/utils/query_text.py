# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

r"""The one normalisation a query's text passes through before anything tokenizes it.

The stored text a retrieval searches (chunks, labels, aliases) is NFC: the
ingestion cleaners compose it (``text_preparation``, ``text_cleaner``). A
question typed or pasted in decomposed form (NFD: ``u`` followed by a
combining acute) was tokenized as it came: the retrieval handlers' word
pattern (``[^\W_]+``) and the keyword arm's (``\w+``) treat a combining
mark as a separator, so "Bezúkhov" in NFD was the two words ``bezu`` and
``khov``; FTS5's ``unicode61`` tokenizer splits the same way. 87 of 120
benchmark contexts changed when the questions were given in NFD (audit wave
5). Every query entry point (the GraphRAG handlers, the search repository's
keyword, vector and hybrid searches, the MCP chat suite) and every query
tokenizer runs the text through :func:`normalize_query` first, so NFC and
NFD spellings of one question retrieve the same context.
"""

from __future__ import annotations

import unicodedata
from typing import Final, Literal


QUERY_NORMAL_FORM: Final[Literal["NFC"]] = "NFC"
"""The Unicode normal form every query is put in: the stored text's (composed)."""


def normalize_query(text: str) -> str:
    """``text`` in :data:`QUERY_NORMAL_FORM` (composed): idempotent, and cheap on composed text."""
    if unicodedata.is_normalized(QUERY_NORMAL_FORM, text):
        return text
    return unicodedata.normalize(QUERY_NORMAL_FORM, text)


__all__ = ["QUERY_NORMAL_FORM", "normalize_query"]
