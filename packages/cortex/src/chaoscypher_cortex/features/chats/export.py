# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Markdown rendering for chat export.

Turns a persisted chat (messages + the citation/entity metadata saved with
each assistant message) into a readable Markdown document: role headings,
entity markers reduced to bold labels, and ``[[cite:...]]`` markers replaced
with sequential footnotes carrying the source filename and sentence text
where the persisted ``chunk_citations`` metadata has them.
"""

import re
from typing import Any


# [[cite:C0:S1,S2|label]] — group 1 = "C0:S1,S2" (the persisted citation
# key shape), group 2 = optional display label.
_CITE_RE = re.compile(r"\[\[cite:([^\]|]+?)(?:\|([^\]]+))?\]\]", re.IGNORECASE)

# [[node:...|Label]] / [[edge:...|Label]] — keep only the label, bolded.
_ENTITY_RE = re.compile(r"\[\[(?:node|edge)[^|\]]*\|([^\]]+)\]\]", re.IGNORECASE)

_ROLE_HEADINGS = {"user": "You", "assistant": "Assistant", "system": "System"}


def _resolve_citation(ref: str, citations: dict[str, Any]) -> dict[str, Any] | None:
    """Find the persisted citation entry for a marker reference.

    Persisted keys use the same alias the marker carries
    (``"{chunk_alias}:{sentence_refs}"``), so an exact case-insensitive
    match covers the normal case; a prefix match on the alias part covers
    markers whose sentence list was reformatted.

    Args:
        ref: The marker's reference part (e.g. ``"C0:S1"``).
        citations: The message's persisted ``chunk_citations`` mapping.

    Returns:
        The citation entry dict, or None when unresolvable.

    """
    ref_lower = ref.lower()
    alias = ref_lower.split(":", 1)[0]
    for key, value in citations.items():
        key_lower = str(key).lower()
        if key_lower == ref_lower or key_lower.split(":", 1)[0] == alias:
            return value if isinstance(value, dict) else None
    return None


def _replace_citations(
    content: str,
    message: dict[str, Any],
    footnotes: list[str],
) -> str:
    """Replace ``[[cite:...]]`` markers with sequential Markdown footnotes.

    Args:
        content: Message content potentially carrying citation markers.
        message: The full message dict (persisted metadata is read from
            ``extra_metadata.chunk_citations``).
        footnotes: Document-level footnote collector (mutated in place).

    Returns:
        Content with markers replaced by ``[^n]`` references.

    """
    meta = message.get("extra_metadata") or {}
    citations = meta.get("chunk_citations") or {}

    def _sub(match: re.Match[str]) -> str:
        ref = match.group(1)
        label = match.group(2) or "source"
        info = _resolve_citation(ref, citations)
        number = len(footnotes) + 1
        if info and (info.get("sentence_text") or info.get("filename")):
            filename = info.get("filename") or info.get("label") or label
            sentence = info.get("sentence_text") or ""
            entry = f"[^{number}]: {filename}"
            if sentence:
                entry += f' — "{sentence}"'
        else:
            entry = f"[^{number}]: {label}"
        footnotes.append(entry)
        return f"[^{number}]"

    return _CITE_RE.sub(_sub, content)


def render_chat_markdown(chat: dict[str, Any]) -> str:
    """Render a chat dict (with messages) to a Markdown document.

    Tool messages are omitted (their useful content is already woven into
    the assistant answers); entity markers become bold labels; citation
    markers become footnotes listed at the end of the document.

    Args:
        chat: The full chat dict as returned by ChatService.get_chat.

    Returns:
        The Markdown document text (trailing newline included).

    """
    lines: list[str] = [f"# {chat.get('title') or 'Chat'}", ""]
    footnotes: list[str] = []

    for message in chat.get("messages") or []:
        role = message.get("role")
        if role == "tool":
            continue
        heading = _ROLE_HEADINGS.get(str(role), str(role).title())
        content = str(message.get("content") or "")
        content = _ENTITY_RE.sub(lambda m: f"**{m.group(1)}**", content)
        content = _replace_citations(content, message, footnotes)
        lines.extend([f"### {heading}", "", content, ""])

    if footnotes:
        lines.extend(["---", "", *footnotes])

    return "\n".join(lines).rstrip() + "\n"


__all__ = ["render_chat_markdown"]
