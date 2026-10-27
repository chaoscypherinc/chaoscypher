# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The memory tools' plain text: the provenance notation, entity cards, grouped lists, relation chains, notes and the budget cut.

``recall`` reads a shaped retrieval result (the dict ``graphrag_search``
returns, without ``context_text``) and writes it for a small model: a
lookup is an entity card per seed entity, an aggregate the grouped lists
of ``graph_context["aggregates"]``, a path the chains of
``graph_context["walks"]`` (and a line per gap), the global shape the
digest text its caller passes in; then the memory chunks no rendered fact
cited, as Notes. Everything is cut to a character budget on whole lines
(:func:`cut_to_budget`).

:func:`fact_line` is the one notation (spec decision 5): every fact line of
every memory tool goes through it, ``SUBJECT LABEL OBJECT [TAG] "sentence"``,
the tag from :func:`tag`. A renderer never writes a tag or a quoted
sentence itself. Output is ASCII apart from the stored names and sentences:
``..`` for a window, ``...`` for a cut, ``"`` for a quote.

Where a fact comes from is a :class:`ProvenanceLookup` (``for_edge``,
``for_chunk``); ``mcp.provenance.GraphProvenance`` reads it from the graph's
memberships, and a test passes a dict.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Protocol

from chaoscypher_core.services.workflows.tools.engine.handlers.graphrag_blocks import (
    cited_chunk_ids,
)


if TYPE_CHECKING:
    from datetime import date


SENTENCE_CHARS = 160
"""The longest quoted sentence (a fact's evidence, a note's text), cut on a word (:func:`cut_on_word`)."""

TITLE_CHARS = 40
"""The longest document title in a ``[doc TITLE]`` tag, cut on a word."""

PASSAGE_CHARS = 320
"""The most characters of one document passage in ``recall``: a manual's step or two, cut on a word."""

PASSAGE_LIMIT = 3
"""The most document passages one ``recall`` lists (``more=true`` doubles the budget, not this)."""

ELLIPSIS = "..."
"""What a cut text ends with: three ASCII dots (small tokenizers split the one-character ellipsis badly)."""

EMPTY_RECALL = "Nothing remembered about that yet."
"""``recall``'s answer when the result renders nothing."""

INDENT = "  "
"""The indent of a fact line under its card, group or chain head, and of a note."""


def cut_on_word(text: str, limit: int) -> str:
    """``text`` cut to at most ``limit`` characters on a word boundary, with :data:`ELLIPSIS`.

    The text unchanged when it fits; otherwise the longest prefix that ends
    where a word ends (the next character is whitespace) and, with the
    ellipsis appended, is at most ``limit`` characters. A first word longer
    than that is hard-cut to ``limit - 3`` characters plus the ellipsis.

    Args:
        text: The text to cut.
        limit: The most characters the result may have.
    """
    if len(text) <= limit:
        return text
    room = max(limit - len(ELLIPSIS), 0)
    for end in range(min(room, len(text) - 1), 0, -1):
        if text[end].isspace() and not text[end - 1].isspace():
            return text[:end] + ELLIPSIS
    return text[:room] + ELLIPSIS


def _one_line(text: str) -> str:
    """``text`` with every run of whitespace (newlines too) folded to one space, ends stripped."""
    return " ".join(text.split())


@dataclass(frozen=True)
class Provenance:
    """Where a fact or a chunk comes from, as the notation shows it.

    Attributes:
        kind: ``memory`` (a remembered note), ``doc`` (an ordinary document)
            or ``pack`` (an imported package).
        by: Who stated a memory (the source's ``memory_by``); None otherwise.
        name: A document's title or a pack's name; None for a memory.
        valid_from: From when the fact holds; None: timeless.
        valid_to: When a later memory closed it; None: open.
        sentence: The first stored evidence sentence, whole; None when there is none.
        chunk_id: The chunk that sentence (or this chunk) is in, so the notes
            leave out what a rendered fact already cites; never rendered.
    """

    kind: Literal["memory", "doc", "pack"]
    by: str | None
    name: str | None
    valid_from: date | None
    valid_to: date | None
    sentence: str | None
    chunk_id: str | None = None


class ProvenanceLookup(Protocol):
    """Reads the provenance of a fact (by edge id) and of a chunk (by chunk id)."""

    def for_edge(self, edge_id: str) -> Provenance:
        """The provenance of the fact ``edge_id`` states."""
        ...

    def for_chunk(self, chunk_id: str) -> Provenance:
        """The provenance of the chunk: its source's (no sentence)."""
        ...


def tag(p: Provenance) -> str:
    """The bracketed provenance of a fact or a note.

    A memory is ``[by YYYY-MM-DD]`` (``[by]`` without a date), a closed one
    ``[by YYYY-MM-DD..YYYY-MM-DD, replaced]``; a document ``[doc TITLE]``,
    the title cut at :data:`TITLE_CHARS` on a word; a pack ``[pack NAME]``.
    A part that is missing is left out (``[doc]``; a memory with no ``by``
    shows its date alone, one with neither is ``[memory]``).

    Args:
        p: The provenance.
    """
    if p.kind == "pack":
        return f"[pack {p.name}]" if p.name else "[pack]"
    if p.kind == "doc":
        title = _one_line(p.name or "")
        return f"[doc {cut_on_word(title, TITLE_CHARS)}]" if title else "[doc]"
    when = p.valid_from.isoformat() if p.valid_from else ""
    if p.valid_to is not None:
        when = f"{when}..{p.valid_to.isoformat()}, replaced"
    parts = " ".join(part for part in (p.by, when) if part)
    return f"[{parts}]" if parts else "[memory]"


def fact_line(subject: str, label: str, obj: str, p: Provenance) -> str:
    """One fact in the notation: ``SUBJECT LABEL OBJECT [TAG] "sentence"`` (spec decision 5).

    The one notation function: every fact line of every memory tool is
    written here. The sentence, when there is one, is folded to one line and
    cut at :data:`SENTENCE_CHARS` on a word.

    Args:
        subject: The fact's subject (the edge's source label).
        label: The relation label.
        obj: The fact's object (the edge's target label).
        p: The fact's provenance (:func:`tag`).
    """
    line = f"{subject} {label} {obj} {tag(p)}"
    sentence = _one_line(p.sentence or "")
    if sentence:
        line += f' "{cut_on_word(sentence, SENTENCE_CHARS)}"'
    return line


def _visible(p: Provenance, as_of: date | None) -> bool:
    """Whether a fact shows: open now (no ``as_of``), or open on ``as_of``."""
    if as_of is None:
        return p.valid_to is None
    started = p.valid_from is None or p.valid_from <= as_of
    return started and (p.valid_to is None or as_of < p.valid_to)


def _with_id(line: str, item_id: Any, ids: bool) -> str:
    """``line`` with `` #ID`` appended when ``ids`` is on and there is an id."""
    return f"{line} #{item_id}" if ids and item_id else line


@dataclass
class _Rendered:
    """A renderer's lines and the chunk ids its facts cite (the notes leave those out)."""

    lines: list[str]
    cited: set[str]


def _cites(p: Provenance, *chunk_ids: Any) -> set[str]:
    """The chunk ids a rendered fact cites: its provenance's and its row's."""
    return {str(c) for c in (p.chunk_id, *chunk_ids) if c}


def _date_desc(p: Provenance) -> tuple[bool, int]:
    """Sort key: dated facts first, newest first; timeless facts after."""
    return p.valid_from is None, -(p.valid_from.toordinal() if p.valid_from else 0)


def _graph_context(result: Mapping[str, Any]) -> Mapping[str, Any]:
    """The retrieval result's ``graph_context`` mapping, else an empty one."""
    gc = result.get("graph_context")
    return gc if isinstance(gc, Mapping) else {}


def _lookup(
    result: Mapping[str, Any], prov: ProvenanceLookup, ids: bool, as_of: date | None
) -> _Rendered:
    """Cards for the seed entities (see :func:`render_lookup`)."""
    gc = _graph_context(result)
    rels = [r for r in gc.get("relationships") or [] if r.get("edge_id")]
    out = _Rendered([], set())
    for entity in gc.get("seed_entities") or []:
        nid = entity.get("id")
        name = entity.get("label") or nid or ""
        kind = entity.get("type")
        out.lines.append(_with_id(f"{name} ({kind})" if kind else str(name), nid, ids))
        aliases = [str(a) for a in entity.get("aliases") or [] if a]
        if aliases:
            out.lines.append(f"{INDENT}also: {', '.join(aliases)}")
        facts: list[tuple[int, Provenance, Mapping[str, Any]]] = []
        for rel in rels:
            side = 0 if rel.get("source_id") == nid else 1 if rel.get("target_id") == nid else -1
            if side < 0:
                continue
            p = prov.for_edge(str(rel["edge_id"]))
            if _visible(p, as_of):
                facts.append((side, p, rel))
            else:
                out.cited |= _cites(p)  # a hidden fact's memory is no note either
        facts.sort(
            key=lambda f: (
                f[0],
                *_date_desc(f[1]),
                str(f[2].get("label")),
                str(f[2].get("target" if f[0] == 0 else "source")),
            )
        )
        for _, p, rel in facts:
            line = fact_line(str(rel["source"]), str(rel["label"]), str(rel["target"]), p)
            out.lines.append(INDENT + _with_id(line, rel["edge_id"], ids))
            out.cited |= _cites(p)
    return out


def render_lookup(
    result: Mapping[str, Any],
    prov: ProvenanceLookup,
    *,
    ids: bool = False,
    as_of: date | None = None,
) -> list[str]:
    """An entity card per seed entity of ``graph_context["seed_entities"]``.

    A card is ``NAME (TYPE)`` (the entry's ``type``; ``NAME`` alone without
    one), ``  also: ALIASES`` when the entry lists any, then one indented
    fact line per relationship of ``graph_context["relationships"]`` the
    entity is an end of: the ones it is the subject of first, then the ones
    it is the object of, each run newest ``valid_from`` first (timeless
    facts last), then by label. A relationship without an ``edge_id`` has
    no provenance and is not listed; related entities get no card.

    Args:
        result: The shaped retrieval result.
        prov: Where each fact comes from.
        ids: Append `` #<node id>`` to each card head and `` #<edge id>`` to each fact.
        as_of: Show the facts open on that date instead of the open ones.
    """
    return _lookup(result, prov, ids, as_of).lines


def _aggregate(
    result: Mapping[str, Any], prov: ProvenanceLookup, ids: bool, as_of: date | None
) -> _Rendered:
    """Grouped lists (see :func:`render_aggregate`)."""
    out = _Rendered([], set())
    for block in _graph_context(result).get("aggregates") or []:
        for group in block.get("groups") or []:
            label = str(group.get("label") or "")
            rows = [(row, prov.for_edge(str(row["edge_id"]))) for row in group.get("rows") or []]
            shown = [(row, p) for row, p in rows if _visible(p, as_of)]
            for row, p in rows:
                if (row, p) not in shown:
                    out.cited |= _cites(p, row.get("chunk_id"))  # hidden: no note either
            if not shown:
                continue
            count = int(group.get("count") or len(rows)) - (len(rows) - len(shown))
            plus = "+" if group.get("count_capped") else ""
            out.lines.append(
                _with_id(
                    f"{block.get('entity')}: {label} ({count}{plus})", block.get("entity_id"), ids
                )
            )
            for row, p in shown:
                line = fact_line(str(row["source"]), label, str(row["target"]), p)
                out.lines.append(INDENT + _with_id(line, row["edge_id"], ids))
                out.cited |= _cites(p, row.get("chunk_id"))
    return out


def render_aggregate(
    result: Mapping[str, Any],
    prov: ProvenanceLookup,
    *,
    ids: bool = False,
    as_of: date | None = None,
) -> list[str]:
    """The grouped lists of ``graph_context["aggregates"]`` (``graphrag_blocks.aggregate_blocks``' shape).

    Per group, ``NAME: LABEL (N)`` and one indented fact line per row. ``N``
    is the group's ``count`` less the rows hidden as closed, with ``+`` when
    the entity's edge listing was capped (the count is a floor); a group
    with no row left is not listed.

    Args:
        result: The shaped retrieval result.
        prov: Where each fact comes from.
        ids: Append `` #<node id>`` to each group head and `` #<edge id>`` to each fact.
        as_of: Show the facts open on that date instead of the open ones.
    """
    return _aggregate(result, prov, ids, as_of).lines


def _path(
    result: Mapping[str, Any], prov: ProvenanceLookup, ids: bool, as_of: date | None
) -> _Rendered:
    """Chains and gaps (see :func:`render_path`)."""
    gc = _graph_context(result)
    out = _Rendered([], set())
    shown_pairs: set[tuple[str, str]] = set()
    hidden_pairs: list[tuple[str, str]] = []
    for walk in gc.get("walks") or []:
        pair = (str(walk.get("from")), str(walk.get("to")))
        hops = [(hop, prov.for_edge(str(hop["edge_id"]))) for hop in walk.get("hops") or []]
        if not hops or not all(_visible(p, as_of) for _, p in hops):
            for hop, p in hops:
                out.cited |= _cites(p, hop.get("chunk_id"))  # hidden: no note either
            if pair not in hidden_pairs:
                hidden_pairs.append(pair)
            continue
        shown_pairs.add(pair)
        out.lines.append(f"{pair[0]} to {pair[1]}:")
        for hop, p in hops:
            line = fact_line(str(hop["source"]), str(hop["label"]), str(hop["target"]), p)
            out.lines.append(INDENT + _with_id(line, hop["edge_id"], ids))
            out.cited |= _cites(p, hop.get("chunk_id"))
    gaps = [pair for pair in hidden_pairs if pair not in shown_pairs]
    gaps += [(str(g.get("from")), str(g.get("to"))) for g in gc.get("walk_gaps") or []]
    out.lines.extend(f"No path found between {a} and {b}." for a, b in gaps)
    return out


def render_path(
    result: Mapping[str, Any],
    prov: ProvenanceLookup,
    *,
    ids: bool = False,
    as_of: date | None = None,
) -> list[str]:
    """The chains of ``graph_context["walks"]``: ``A to B:`` then one indented fact line per hop.

    A chain through a fact that is not shown (closed, or not open on
    ``as_of``) is not a chain: it is left out, and a pair left with no chain
    reads as a gap. Each gap (those, then ``graph_context["walk_gaps"]``)
    is ``No path found between A and B.``

    Args:
        result: The shaped retrieval result.
        prov: Where each fact comes from.
        ids: Append `` #<edge id>`` to each fact.
        as_of: Show the facts open on that date instead of the open ones.
    """
    return _path(result, prov, ids, as_of).lines


def render_notes(
    result: Mapping[str, Any], covered_chunk_ids: Collection[str], prov: ProvenanceLookup
) -> list[str]:
    """``Notes:`` and one indented ``[TAG] "text"`` per memory chunk no rendered fact cited.

    The chunks of ``result["chunks"]`` in their order, each once; a chunk is
    a memory's when its provenance (:meth:`ProvenanceLookup.for_chunk`, its
    source's) is. The text is the chunk's stored text, folded to one line and
    cut at :data:`SENTENCE_CHARS` on a word. Nothing to list is no lines (no
    header).

    Args:
        result: The shaped retrieval result.
        covered_chunk_ids: The chunk ids the rendered facts cite.
        prov: Where each chunk comes from.
    """
    lines: list[str] = []
    seen = set(covered_chunk_ids)
    for chunk in result.get("chunks") or []:
        cid = str(chunk.get("chunk_id") or "")
        text = _one_line(str(chunk.get("original_content") or ""))
        if not cid or cid in seen or not text:
            continue
        seen.add(cid)
        p = prov.for_chunk(cid)
        if p.kind == "memory":
            lines.append(f'{INDENT}{tag(p)} "{cut_on_word(text, SENTENCE_CHARS)}"')
    return ["Notes:", *lines] if lines else []


def render_passages(result: Mapping[str, Any], prov: ProvenanceLookup) -> list[str]:
    """``Passages:`` and one indented ``[TAG] "text"`` per document or pack chunk, at most a few.

    A recall over a manual needs the manual's words, not only the facts drawn
    from it: a procedure ("hold the button, then...") rarely becomes a fact,
    and a fact line quotes one sentence. So the chunks of ``result["chunks"]``
    whose source is a document or a pack (:meth:`ProvenanceLookup.for_chunk`)
    are listed in retrieval order, each once, up to :data:`PASSAGE_LIMIT`,
    folded to one line and cut at :data:`PASSAGE_CHARS` on a word, whether or
    not a rendered fact cites them. Memory chunks are notes
    (:func:`render_notes`), never passages. Nothing to list is no lines.

    Args:
        result: The shaped retrieval result.
        prov: Where each chunk comes from.
    """
    lines: list[str] = []
    seen: set[str] = set()
    for chunk in result.get("chunks") or []:
        if len(lines) == PASSAGE_LIMIT:
            break
        cid = str(chunk.get("chunk_id") or "")
        text = _one_line(str(chunk.get("original_content") or ""))
        if not cid or cid in seen or not text:
            continue
        seen.add(cid)
        p = prov.for_chunk(cid)
        if p.kind in ("doc", "pack"):
            lines.append(f'{INDENT}{tag(p)} "{cut_on_word(text, PASSAGE_CHARS)}"')
    return ["Passages:", *lines] if lines else []


def _more_tail(left: int) -> str:
    """The line that says how many more results a ``more=true`` call would show."""
    return f"({left} more, ask with more=true)"


def cut_to_budget(lines: Sequence[str], budget: int) -> str:
    """``lines`` joined by newlines, whole lines only, at most ``budget`` characters.

    When lines are left over, the last kept line gives its place to
    ``(N more, ask with more=true)`` (``N`` counts it), dropping more lines
    while the tail would not fit.

    Args:
        lines: The lines, in order.
        budget: The most characters the result may have.
    """
    total, fit = 0, 0
    for line in lines:
        added = len(line) + (1 if fit else 0)
        if total + added > budget:
            break
        total, fit = total + added, fit + 1
    if fit == len(lines):
        return "\n".join(lines)
    for keep in range(fit - 1, -1, -1):
        text = "\n".join([*lines[:keep], _more_tail(len(lines) - keep)])
        if len(text) <= budget:
            return text
    return _more_tail(len(lines))


def _shape_lines(
    result: Mapping[str, Any],
    prov: ProvenanceLookup,
    *,
    ids: bool,
    as_of: date | None,
    global_text: str | None,
) -> _Rendered:
    """The shape's lines: its own block, or the cards when the block rendered nothing."""
    shape = (result.get("retrieval_stats") or {}).get("shape")
    if shape == "global" and global_text:
        return _Rendered(global_text.splitlines(), set())
    block = {"aggregate": _aggregate, "path": _path}.get(str(shape))
    if block is not None:
        rendered = block(result, prov, ids, as_of)
        if rendered.lines:
            return rendered
    return _lookup(result, prov, ids, as_of)


def render_recall(
    result: Mapping[str, Any],
    prov: ProvenanceLookup,
    *,
    budget: int,
    more: bool,
    ids: bool,
    global_text: str | None = None,
    as_of: date | None = None,
) -> str:
    """``recall``'s answer: the result in its shape, then the notes, cut to the budget.

    By ``retrieval_stats["shape"]``: ``lookup`` the entity cards
    (:func:`render_lookup`), ``aggregate`` the grouped lists
    (:func:`render_aggregate`), ``path`` the chains (:func:`render_path`),
    ``global`` ``global_text`` (the digest's text, which the caller builds);
    a shape whose block is empty falls back to the cards. Then the memory
    chunks no rendered fact cited (:func:`render_notes`), then the document
    and pack passages (:func:`render_passages`). Closed facts are
    left out unless ``as_of`` names a date they were open on. A failed or
    empty result is :data:`EMPTY_RECALL`.

    Args:
        result: The shaped retrieval result (``retrieve_shaped``'s dict).
        prov: Where each fact and chunk comes from.
        budget: The most characters of the answer (``mcp.memory.result_chars``).
        more: Double the budget.
        ids: Append ids to card heads and fact lines.
        global_text: The digest text the global shape renders.
        as_of: Show the facts open on that date.
    """
    if not result or result.get("success") is False:
        return EMPTY_RECALL
    rendered = _shape_lines(result, prov, ids=ids, as_of=as_of, global_text=global_text)
    covered = rendered.cited | set(cited_chunk_ids(_graph_context(result)))
    lines = [
        *rendered.lines,
        *render_notes(result, covered, prov),
        *render_passages(result, prov),
    ]
    if not lines:
        return EMPTY_RECALL
    return cut_to_budget(lines, budget * 2 if more else budget)


__all__ = [
    "ELLIPSIS",
    "EMPTY_RECALL",
    "PASSAGE_CHARS",
    "PASSAGE_LIMIT",
    "SENTENCE_CHARS",
    "TITLE_CHARS",
    "Provenance",
    "ProvenanceLookup",
    "cut_on_word",
    "cut_to_budget",
    "fact_line",
    "render_aggregate",
    "render_lookup",
    "render_notes",
    "render_passages",
    "render_path",
    "render_recall",
    "tag",
]
