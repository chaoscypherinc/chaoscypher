# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Sentence-level splitting for evidence-gated extraction.

Splits text into numbered sentences for LLM prompts and parses
sentence references (e.g., ``S3``, ``S2-S4``) from extraction output.

Reuses quote-protection patterns from the chunking service to
prevent splitting inside quoted dialogue.
"""

import re
from collections.abc import Mapping, Sequence
from typing import Any

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
        "Mlle",
        "Mme",
        "Messrs",
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

_CLOSING_QUOTES = "\u201d\"\u2019'\u00bb"
"""Marks that can close a quotation after its sentence's last punctuation (U+201D, ", U+2019, ', U+00BB)."""

_OPENING_QUOTES = "\u201c\"\u2018'\u00ab"
"""Marks that can open a quotation (U+201C, ", U+2018, ', U+00AB)."""

# Sentence boundaries; this runs AFTER abbreviation and quote protection.
#
# 1. ``.``/``!``/``?`` then whitespace then an uppercase letter, an opening
#    quote or a parenthesis ("He left. She stayed.").
# 2. ``.``/``!``/``?`` then one or two closing quotes (a quotation inside a
#    quotation closes with both: U+2019 U+201D), then either a line break (the next
#    paragraph: "“Yes.”\n\n“No,” said he.") or whitespace before another
#    quotation ("“Yes.” “No.”"). A closing quote followed on the same line by
#    a word is left joined: "“Is it?” Pierre asked." is one sentence (the
#    attribution belongs to the speech). A quote that closes without
#    sentence punctuation ("“No,” said he.") never ends a sentence.
_SENTENCE_SPLIT_RE = re.compile(
    r"(?<=[.!?])\s+(?=[A-Z\u201c\"(])"
    rf"|(?:(?<=[.!?][{_CLOSING_QUOTES}])|(?<=[.!?][{_CLOSING_QUOTES}][{_CLOSING_QUOTES}]))"
    rf"(?:[^\S\n]*\n\s*|\s+(?=[{_OPENING_QUOTES}]))"
    rf"(?=[A-Z(\u00ab{_OPENING_QUOTES}])"
)


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
        """Replace an abbreviation's period with a placeholder."""
        return m.group(1) + f"{_ABBREV_PLACEHOLDER} "

    working = _ABBREV_PERIOD_RE.sub(_protect_abbrev, working)

    # Step 2: Protect quoted text (periods inside quotes)
    def _protect_quote(m: re.Match[str]) -> str:
        """Replace sentence punctuation inside a quotation with placeholders."""
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


def sentences_at(indices: Sequence[int], sentences: Sequence[str]) -> list[str] | None:
    """The sentences at sorted 1-indexed ``indices`` (``parse_sent_ref``'s), or None when one is out of bounds.

    The one bounds check of a citation, read by the evidence validator's
    ``CitedSentences.of`` (which keeps the parsed indices for its look-back):
    exactly the cited sentences, no implicit fill-in.
    """
    if not indices or indices[-1] > len(sentences):
        return None
    return [sentences[i - 1] for i in indices]


# Separator between small chunks inside an extraction group's text. One
# definition: ``build_group_text`` joins with it and ``resolve_evidence``
# recomputes the same join to place each numbered sentence in its chunk.
GROUP_TEXT_SEPARATOR = "\n\n"


def local_sentence_index(chunk: Mapping[str, Any], local_start: int) -> int | None:
    """Return the chunk-local index of the sentence starting at ``local_start``.

    The one reading of a citation's ``local_index``: evidence resolution at
    extraction and the migrations that recompute stored citations both call it.

    Uses the chunk's stored ``chunk_metadata["sentence_offsets"]`` when present,
    else recomputes them from the chunk's content with the same splitter the
    indexer uses. An exact start match wins; otherwise the sentence whose span
    contains the position is used.

    Args:
        chunk: Small-chunk dict with ``content`` and optional ``chunk_metadata``.
        local_start: Character position of the cited sentence in the chunk.

    Returns:
        0-based sentence index inside the chunk, or ``None`` when no stored
        sentence covers the position.

    """
    metadata = chunk.get("chunk_metadata")
    offsets = metadata.get("sentence_offsets") if isinstance(metadata, Mapping) else None
    if not offsets:
        offsets = split_into_sentences_with_offsets(chunk.get("content") or "")
    containing: int | None = None
    for idx, span in enumerate(offsets):
        start, end = span.get("start"), span.get("end")
        if not isinstance(start, int) or not isinstance(end, int):
            continue
        if start == local_start:
            return idx
        if containing is None and start <= local_start < end:
            containing = idx
    return containing


def resolve_evidence(
    sent_ref: str | None,
    group_sentences: Sequence[str] | None,
    group_small_chunks: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Resolve an extraction-group ``sent_ref`` to the cited sentences and chunks.

    ``sent_ref`` numbers the sentences of the *extraction group* the model was
    shown (``group_sentences``), not of any one stored chunk. Each cited
    sentence is placed in the small chunk it falls in:

    1. When the group's small chunks, joined with ``GROUP_TEXT_SEPARATOR``,
       split into exactly ``group_sentences``, the sentence is placed by its
       character offset in that join (exact, even for repeated sentences).
    2. Otherwise (content was stripped or cleaned before numbering, or a
       chunk is missing) the first chunk whose content contains the sentence
       wins, which is also how overlap duplicates resolve.
    3. A sentence no chunk contains keeps its text with ``chunk_id=None``.

    Out-of-range references are skipped; an unparseable ``sent_ref`` yields
    no evidence.

    Args:
        sent_ref: Sentence reference from the model (``"S2, S10"``).
        group_sentences: The numbered sentences of the group text.
        group_small_chunks: The group's small chunks in group order, each with
            ``id``, ``content`` and optional ``chunk_metadata``.

    Returns:
        JSON-serialisable list of ``{"text", "chunk_id", "local_index"}``
        dicts, one per cited in-range sentence, in reference order.

    """
    if not sent_ref or not group_sentences:
        return []
    parsed = parse_sent_ref(sent_ref)
    if not parsed:
        return []
    cited = [n for n in parsed if n <= len(group_sentences)]
    if not cited:
        return []

    chunks = [c for c in group_small_chunks if c.get("content")]
    # (chunk, start offset of the chunk inside the joined text)
    placements: list[tuple[Mapping[str, Any], int]] = []
    cursor = 0
    for chunk in chunks:
        placements.append((chunk, cursor))
        cursor += len(chunk["content"]) + len(GROUP_TEXT_SEPARATOR)
    joined = GROUP_TEXT_SEPARATOR.join(c["content"] for c in chunks)
    offsets: list[dict[str, int]] | None = None
    if chunks and split_into_sentences(joined) == list(group_sentences):
        offsets = split_into_sentences_with_offsets(joined)

    evidence: list[dict[str, Any]] = []
    for number in cited:
        text = group_sentences[number - 1]
        chunk_id: str | None = None
        local_index: int | None = None
        located: tuple[Mapping[str, Any], int] | None = None
        if offsets is not None:
            start = offsets[number - 1]["start"]
            for chunk, chunk_start in placements:
                if chunk_start <= start < chunk_start + len(chunk["content"]):
                    located = (chunk, start - chunk_start)
                    break
        else:
            for chunk, _chunk_start in placements:
                pos = chunk["content"].find(text)
                if pos >= 0:
                    located = (chunk, pos)
                    break
        if located is not None:
            chunk, local_start = located
            chunk_id = chunk.get("id")
            local_index = local_sentence_index(chunk, local_start)
        evidence.append({"text": text, "chunk_id": chunk_id, "local_index": local_index})
    return evidence


def evidence_snippet(
    evidence: Sequence[Mapping[str, Any]] | None,
    chunk_id: str | None = None,
) -> str | None:
    """Join the cited sentences of an evidence list into one citation snippet.

    A citation row names one chunk, so with *chunk_id* only the sentences
    placed in that chunk are used; when none are, the sentences that could not
    be placed in any chunk (text-only evidence) are used instead.

    Args:
        evidence: Evidence list from :func:`resolve_evidence`.
        chunk_id: The citation's chunk, or ``None`` for every sentence.

    Returns:
        The cited sentence texts joined with a space, or ``None`` when empty.

    """
    items = [e for e in evidence or () if isinstance(e, Mapping) and e.get("text")]
    if chunk_id is not None:
        items = [e for e in items if e.get("chunk_id") == chunk_id] or [
            e for e in items if not e.get("chunk_id")
        ]
    return " ".join(e["text"] for e in items) if items else None


def merge_evidence(
    first: Sequence[Any] | None,
    second: Sequence[Any] | None,
) -> list[dict[str, Any]]:
    """Return the order-preserving union of two evidence lists.

    Used wherever two extracted items merge (entity dedup, within-task
    relationship merge) so a merged item keeps every sentence it was cited by.

    Args:
        first: Evidence of the kept item (JSON-loaded; non-dict entries are skipped).
        second: Evidence of the merged-in item.

    Returns:
        A new list with each distinct ``(text, chunk_id, local_index)`` once.

    """
    merged: list[dict[str, Any]] = []
    seen: set[tuple[Any, Any, Any]] = set()
    for item in [*(first or ()), *(second or ())]:
        if not isinstance(item, Mapping):
            continue
        key = (item.get("text"), item.get("chunk_id"), item.get("local_index"))
        if key in seen:
            continue
        seen.add(key)
        merged.append(dict(item))
    return merged


__all__: list[str] = [
    "GROUP_TEXT_SEPARATOR",
    "evidence_snippet",
    "format_numbered_sentences",
    "local_sentence_index",
    "merge_evidence",
    "parse_sent_ref",
    "resolve_evidence",
    "sentences_at",
    "split_into_sentences",
    "split_into_sentences_with_offsets",
]
