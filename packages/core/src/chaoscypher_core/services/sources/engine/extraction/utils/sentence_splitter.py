# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Sentence-level splitting for evidence-gated extraction.

Splits text into numbered sentences for LLM prompts and parses
sentence references (e.g., ``S3``, ``S2-S4``) from extraction output.

Reuses quote-protection patterns from the chunking service to
prevent splitting inside quoted dialogue.
"""

import re

from chaoscypher_core.utils.chunk import (
    _EXCLAMATION_PLACEHOLDER,
    _PERIOD_PLACEHOLDER,
    _QUESTION_PLACEHOLDER,
    _QUOTE_PATTERN,
)


# Known abbreviations that end with a period but are NOT sentence-ending.
_ABBREVIATIONS = frozenset(
    {
        "Mr",
        "Mrs",
        "Ms",
        "Dr",
        "Prof",
        "Sr",
        "Jr",
        "St",
        "Gen",
        "Gov",
        "Rep",
        "Sen",
        "Sgt",
        "Cpl",
        "Pvt",
        "Capt",
        "Lt",
        "Col",
        "Maj",
        "Cmdr",
        "Adm",
        "Rev",
        "Hon",
        "Pres",
        "U.S",
        "U.K",
        "E.U",
        "U.N",
        "a.m",
        "p.m",
        "vs",
        "etc",
        "i.e",
        "e.g",
        "al",
        "fig",
        "no",
        "vol",
        "dept",
        "approx",
        "inc",
        "ltd",
        "corp",
        "assn",
    }
)

# Build pattern matching abbreviation + period at word boundary.
# Matches e.g. "Mr." "U.S." "etc." — captures the abbreviation.
_ABBREV_PERIOD_RE = re.compile(
    r"\b("
    + "|".join(re.escape(a) for a in sorted(_ABBREVIATIONS, key=len, reverse=True))
    + r")\.\s",
)
_ABBREV_PLACEHOLDER = "\u2025"  # TWO DOT LEADER — replaces ". " in abbreviations

# Sentence-ending pattern: period/exclamation/question followed by space+uppercase
# or end-of-string. This runs AFTER abbreviation and quote protection.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z\u201c\"(])")


def split_into_sentences(text: str) -> list[str]:
    """Split text into sentences using regex heuristics.

    Handles abbreviations, quoted dialogue, and common edge cases.
    If the text produces zero sentences, returns the full text as S1.

    Args:
        text: Source text to split.

    Returns:
        List of sentence strings (at least one element).

    """
    if not text or not text.strip():
        return [text] if text else [""]

    working = text.strip()

    # Step 1: Protect abbreviation periods
    def _protect_abbrev(m: re.Match[str]) -> str:
        return m.group(1) + f"{_ABBREV_PLACEHOLDER} "

    working = _ABBREV_PERIOD_RE.sub(_protect_abbrev, working)

    # Step 2: Protect quoted text (periods inside quotes)
    def _protect_quote(m: re.Match[str]) -> str:
        q = m.group(0)
        q = q.replace(". ", f"{_PERIOD_PLACEHOLDER} ")
        q = q.replace("! ", f"{_EXCLAMATION_PLACEHOLDER} ")
        return q.replace("? ", f"{_QUESTION_PLACEHOLDER} ")

    working = _QUOTE_PATTERN.sub(_protect_quote, working)

    # Step 3: Split on sentence boundaries
    raw_sentences = _SENTENCE_SPLIT_RE.split(working)

    # Step 4: Restore placeholders and clean up
    sentences: list[str] = []
    for s in raw_sentences:
        restored = s.replace(_ABBREV_PLACEHOLDER, ".")
        restored = restored.replace(_PERIOD_PLACEHOLDER, ".")
        restored = restored.replace(_EXCLAMATION_PLACEHOLDER, "!")
        restored = restored.replace(_QUESTION_PLACEHOLDER, "?")
        cleaned = restored.strip()
        if cleaned:
            sentences.append(cleaned)

    return sentences if sentences else [text.strip()]


def split_into_sentences_with_offsets(text: str) -> list[dict[str, int]]:
    """Split text into sentences and return character offsets.

    Uses ``split_into_sentences`` for splitting, then locates each sentence
    in the original text to compute ``start`` and ``end`` character positions.

    Args:
        text: Source text to split.

    Returns:
        List of ``{"start": int, "end": int}`` dicts.
        S1 = index 0, S2 = index 1, etc.

    """
    sentences = split_into_sentences(text)
    offsets: list[dict[str, int]] = []
    search_start = 0

    for sentence in sentences:
        pos = text.find(sentence, search_start)
        if pos == -1:
            # Fallback: stripped text may not match exactly, use current position
            pos = search_start
        offsets.append({"start": pos, "end": pos + len(sentence)})
        search_start = pos + len(sentence)

    return offsets


def format_numbered_sentences(sentences: list[str]) -> str:
    """Format sentences as ``S1: ... S2: ...`` block for prompt injection.

    Args:
        sentences: List of sentence strings.

    Returns:
        Newline-delimited numbered sentence block.

    """
    return "\n".join(f"S{i + 1}: {s}" for i, s in enumerate(sentences))


_SENT_REF_TOKEN_RE = re.compile(r"^\s*S(\d+)(?:-S?(\d+))?\s*$")


def parse_sent_ref(ref: str) -> list[int] | None:
    """Parse a sentence reference string into 1-indexed sentence numbers.

    Accepts comma-separated lists where each token is either a single
    sentence (``S3``) or a range (``S2-S4`` or ``S3-5``). The returned
    list is sorted and deduplicated; gaps between cited tokens are NOT
    filled in (``S3,S5`` → ``[3, 5]``, not ``[3, 4, 5]``).

    Examples:
        ``"S3"`` → ``[3]``
        ``"S2-S4"`` → ``[2, 3, 4]``
        ``"S3,S5"`` → ``[3, 5]``
        ``"S1-S4, S9, S11"`` → ``[1, 2, 3, 4, 9, 11]``

    Args:
        ref: Sentence reference string.

    Returns:
        Sorted, deduplicated list of 1-indexed sentence numbers, or
        ``None`` if any token is unparseable or out of range.

    """
    if not ref:
        return None
    indices: set[int] = set()
    # Split on both ``,`` and ``;`` — see line_parser._SENT_REF_PATTERN for
    # the rationale. The prompt's alias convention leaks into sent_ref
    # output as ``;``; treating both as separators is unambiguous since
    # neither has another valid meaning inside a sent_ref.
    for token in re.split(r"[,;]", ref):
        m = _SENT_REF_TOKEN_RE.match(token)
        if not m:
            return None
        start = int(m.group(1))
        end = int(m.group(2)) if m.group(2) else start
        if start < 1 or end < 1 or start > end:
            return None
        indices.update(range(start, end + 1))
    if not indices:
        return None
    return sorted(indices)


def get_referenced_sentences(sent_ref_str: str, sentences: list[str]) -> list[str] | None:
    """Get the cited sentences for a reference.

    Returns the union of sentences cited by ``sent_ref_str`` — exactly
    those sentences, no implicit fill-in.

    Args:
        sent_ref_str: Sentence reference (e.g. ``"S2-S4"`` or ``"S1, S5"``).
        sentences: Full sentence list from ``split_into_sentences``.

    Returns:
        List of cited sentence strings, or ``None`` if the ref is
        unparseable or any cited index is out of bounds.

    """
    parsed = parse_sent_ref(sent_ref_str)
    if not parsed:
        return None
    if parsed[-1] > len(sentences):
        return None
    return [sentences[i - 1] for i in parsed]


__all__: list[str] = [
    "format_numbered_sentences",
    "get_referenced_sentences",
    "parse_sent_ref",
    "split_into_sentences",
    "split_into_sentences_with_offsets",
]
