# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The graph blocks a question shape renders: an entity's edges grouped by relation (aggregate) and the chains between two entities (walk), each row with the stored evidence sentence; and the chunk ids those rows cite.

The aggregate block (:func:`aggregate_blocks`) answers a cast question
("which of the Rostóv children are named?") with the list itself: the named
entity's edges grouped by label, each row the full triple and the first
sentence the extraction stored as its evidence (Task 1's relationship
citations), so the model reads a list instead of rebuilding it from the
chunks. Keep, flag, never delete: a row the block leaves out (a synthetic
inverse twin, an unevidenced edge, a label the question does not ask about,
a second copy of a fact, a row past the per-label cap, the edges past a
capped listing) is counted, never dropped silently.

The walk block (:func:`walk_blocks`) answers a connection question ("how
is Dólokhov connected to Pierre?") with the chain itself: the shortest
relation chains between two named entities over the whole loaded graph
(:func:`shortest_walks`: the shortest length and one longer, never beyond),
at most ``max_walks`` for the whole question (:func:`walk_chains`), each hop
the stored fact and its first evidence sentence, and a gap entry for a pair
no chain of up to ``max_hops`` relations joins. What the walk leaves out (a
folded inverse twin, an unevidenced edge, the edges of a hub past the degree
cap, a capped graph listing, the pairs past :data:`WALK_MAX_PAIRS`, the
chains past ``max_walks``) is counted by :func:`walk_chains` and the handler,
never dropped silently; a gap says whether anything was left out
(``left_out``), so it never claims more than the walk read.

:func:`annotate_markers` points each row and hop at its sentence in the
final chunks (``C3:S2``) once the chunk aliases are settled; the parser of
a formatted chunk's ``[S#]`` lines (:func:`labelled_sentences`) is the one
the relationship evidence markers read too.
"""

from __future__ import annotations

import itertools
import re
from collections import deque
from collections.abc import Collection, Iterable, Iterator, Mapping, Sequence
from typing import Any

import structlog

from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
    INVERSE_OF_KEY,
    PIPELINE_EDGE_FLAG_KEYS,
    fold,
    is_unevidenced_record,
    pipeline_flag,
)
from chaoscypher_core.services.sources.engine.extraction.utils.relation_vocabulary import (
    canonical_edge_label,
)


logger = structlog.get_logger(__name__)


EVIDENCE_QUOTE_CHARS = 140
"""The longest evidence sentence a graph block quotes, in characters (cut at a word, with ``…``)."""

_SKIP_REASONS: tuple[str, ...] = (
    "inverse",
    "unevidenced",
    "other_labels",
    "duplicate",
    "past_per_label",
    "listing_capped",
)
"""Every reason an aggregate block leaves a row out, as its ``skipped`` keys (:func:`aggregate_blocks`)."""

_WALK_SKIP_REASONS: tuple[str, ...] = (
    "outside_graph",
    "inverse",
    "unevidenced",
    "parallel",
    "degree_capped",
    "listing_capped",
    "pairs",
    "past_max_walks",
)
"""Every reason a walk leaves something out, as its ``skipped`` keys (:func:`walk_chains`).

The edges first; ``listing_capped`` is 1 when the whole-graph edge listing
returned its limit (the edges past it were never read) and ``pairs`` the
pairs past :data:`WALK_MAX_PAIRS`, both set by the handler; then the chains
past the question's ``max_walks`` (``past_max_walks``). Those that can cut a
connection make a gap ``left_out`` (:data:`_GAP_HEDGE_REASONS`).
"""

_GAP_HEDGE_REASONS: tuple[str, ...] = (
    "outside_graph",
    "unevidenced",
    "degree_capped",
    "listing_capped",
)
"""The ``skipped`` counts that can cut a connection: any of them non-zero makes a gap ``left_out`` (:func:`walk_blocks`).

A folded inverse (its forward twin carries the step), a parallel edge (a
sibling joins the same two nodes), the pairs past :data:`WALK_MAX_PAIRS` and
the chains past ``max_walks`` never remove a route between a gap's pair, so
they leave the plain wording (ruling of 2026-10-09: with ``inverse`` counted,
every gap on a real graph was hedged).
"""

WALK_MAX_PAIRS = 3
"""The most pairs of named entities a walk block joins (:func:`walk_pairs`)."""

WALK_DEGREE_FLOOR = 500
"""The fewest edges a walk expands one node through, whatever ``max_hops`` (:func:`walk_degree_cap`)."""

WALK_DEGREE_PER_HOP = 50
"""The edges per allowed hop a walk expands one node through, past :data:`WALK_DEGREE_FLOOR`."""

_CHUNK_LABEL = re.compile(r"\[CHUNK (C\d+)")
_LABELLED_SENTENCE = re.compile(
    r"\[S(\d+)\]\s*(.*?)(?=\[S\d+\]|</untrusted_document>|\Z)", flags=re.S
)


def _edge_in_context(edge: Any, *, include_unevidenced: bool) -> bool:
    """Whether ``edge`` enters the graph context: PageRank, the paths, the relationship lines and the blocks.

    The one decision (keep, flag, never delete, 2026-10-07): a relationship
    no cited sentence states is committed flagged ``unevidenced``
    (``name_identity.is_unevidenced_record``) and stays out of the chat's
    context unless ``graphrag.include_unevidenced_edges`` lets it in, read
    from the edge's properties: a full listing's, or the minimal listing's
    PageRank walks, which projects every pipeline edge flag
    (``name_identity.PIPELINE_EDGE_FLAG_KEYS``, ``list_edges_minimal``).
    """
    return include_unevidenced or not is_unevidenced_record(getattr(edge, "properties", None))


def labelled_sentences(chunk: Any) -> tuple[str, list[tuple[int, str]]] | None:
    """A formatted chunk's label (``C3``) and its ``[S#]`` sentences as ``(number, text)``; None without a header.

    The one parser of :func:`~chaoscypher_core.services.workflows.tools.engine.chunk_hydration.format_chunk_content`'s
    block: the numbers are its 1-based ``[S{n}]`` labels and the label is
    the ``[CHUNK C3 | …]`` header the model reads (kept in step with the
    final alias by ``assign_chunk_aliases``).
    """
    text = (chunk.get("content") or "") if isinstance(chunk, dict) else str(chunk)
    label = _CHUNK_LABEL.search(text)
    if not label:
        return None
    return label.group(1), [(int(num), s) for num, s in _LABELLED_SENTENCE.findall(text)]


def quote(sentence: str) -> str:
    """An evidence sentence as a block quotes it: whitespace folded, cut at a word to :data:`EVIDENCE_QUOTE_CHARS` with ``…``."""
    text = " ".join(sentence.split())
    if len(text) <= EVIDENCE_QUOTE_CHARS:
        return text
    cut = text[: EVIDENCE_QUOTE_CHARS - 1]
    if text[EVIDENCE_QUOTE_CHARS - 1] != " " and " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(" ,;:") + "…"


def first_sentence(rows: Sequence[Mapping[str, Any]]) -> tuple[str, str, int] | None:
    """The first stored evidence sentence of one edge's citation rows, as ``(text, chunk_id, local_index)``.

    The rows in the storage's order (edge id, chunk id, row id:
    ``get_relationship_citations_batch``), so the same sentence every time;
    a sentence without text or without an integer ``local_index`` is
    passed over. None when no row has one.
    """
    for row in rows:
        for sentence in row.get("sentences") or []:
            if not isinstance(sentence, Mapping):
                continue
            text = str(sentence.get("text") or "").strip()
            index = sentence.get("local_index")
            if text and isinstance(index, int) and not isinstance(index, bool):
                return text, str(sentence.get("chunk_id") or row.get("chunk_id") or ""), index
    return None


def _edge_flags(edge: Any) -> list[str]:
    """The pipeline flags set on ``edge`` (:data:`PIPELINE_EDGE_FLAG_KEYS`, in that order)."""
    props = getattr(edge, "properties", None)
    return [key for key in PIPELINE_EDGE_FLAG_KEYS if pipeline_flag(props, key)]


def _inverse_of(edge: Any) -> str:
    """The forward relation a synthetic inverse edge mirrors, or ``""`` for a forward edge."""
    props = getattr(edge, "properties", None)
    value = props.get(INVERSE_OF_KEY) if isinstance(props, Mapping) else None
    return str(value) if value else ""


def _fact_key(edge: Any) -> tuple[str, str, str]:
    """A forward edge's fact: ``(source id, target id, canonical label)``."""
    label = str(edge.label)
    return edge.source_node_id, edge.target_node_id, canonical_edge_label(label) or label


def _forward_keys(edges: Iterable[Any]) -> set[tuple[str, str, str]]:
    """The facts (:func:`_fact_key`) of the forward edges among ``edges`` (no ``inverse_of``)."""
    return {_fact_key(e) for e in edges if not _inverse_of(e)}


def _folds_onto_twin(edge: Any, forward_keys: Collection[tuple[str, str, str]]) -> bool:
    """Whether ``edge`` is a synthetic inverse whose forward twin is among ``forward_keys``.

    The one inverse fold of the blocks: the twin states the fact the inverse
    mirrors, ``(target, source, inverse_of's label)`` read as ``(source,
    target, label)``, labels canonical. An inverse whose twin is missing (no
    forward edge, or one of another relation between the same two nodes) is
    not folded: the block keeps it, written as the forward fact.
    """
    forward = _inverse_of(edge)
    if not forward:
        return False
    key = (edge.target_node_id, edge.source_node_id, canonical_edge_label(forward) or forward)
    return key in forward_keys


def _row(
    edge: Any,
    label_map: Mapping[str, str],
    citations: Mapping[str, Sequence[Mapping[str, Any]]],
) -> tuple[str, tuple[str, str, str], dict[str, Any]]:
    """One edge as an aggregate row: ``(canonical label, fact key, row)``; an inverse is written as its forward fact.

    The fact key is ``(source id, canonical label, target id)``: two edges
    with one key state one fact (committed once per source, say).
    """
    src, tgt, label = edge.source_node_id, edge.target_node_id, str(edge.label)
    forward = _inverse_of(edge)
    edge_id = str(getattr(edge, "id", "") or "")
    rows = [] if forward else list(citations.get(edge_id) or [])
    if forward:  # no forward twin among the entity's edges: the fact, read the way it was stated
        src, tgt, label = tgt, src, forward
    label = canonical_edge_label(label) or label
    found = first_sentence(rows)
    chunk_id = (
        found[1] if found else next((str(r["chunk_id"]) for r in rows if r.get("chunk_id")), None)
    )
    return (
        label,
        (src, label, tgt),
        {
            "edge_id": edge_id,
            "source": label_map.get(src, src),
            "target": label_map.get(tgt, tgt),
            "sentence": found[0] if found else None,
            "chunk_id": chunk_id or None,
            "local_index": found[2] if found else None,
            "flags": _edge_flags(edge),
        },
    )


def _row_order(row: Mapping[str, Any]) -> tuple[bool, str, str]:
    """A group's row order: rows with a sentence first, then by target, then by source (folded)."""
    return row["sentence"] is None, fold(row["target"]), fold(row["source"])


def _group(
    label: str,
    keyed: Sequence[tuple[tuple[str, str, str], dict[str, Any]]],
    per_label: int,
    *,
    capped: bool,
    skipped: dict[str, int],
) -> dict[str, Any]:
    """One label's group: its rows in :func:`_row_order`, one per fact, the first ``per_label`` listed.

    A copy of a fact already listed (the same fact key) is counted under
    ``skipped["duplicate"]``, the first in row order kept (a row with a
    sentence wins); the rows past ``per_label`` under ``skipped["past_per_label"]``.
    """
    seen: set[tuple[str, str, str]] = set()
    rows: list[dict[str, Any]] = []
    for key, row in sorted(keyed, key=lambda kr: _row_order(kr[1])):
        if key in seen:
            skipped["duplicate"] += 1
            continue
        seen.add(key)
        rows.append(row)
    skipped["past_per_label"] += max(len(rows) - per_label, 0)
    return {"label": label, "count": len(rows), "count_capped": capped, "rows": rows[:per_label]}


def aggregate_blocks(
    *,
    entity_ids: Sequence[str],
    edges_by_node: Mapping[str, Sequence[Any]],
    nodes_map: Mapping[str, Any],
    label_map: Mapping[str, str],
    families: Mapping[str, frozenset[str]],
    citations: Mapping[str, Sequence[Mapping[str, Any]]],
    per_label: int,
    include_unevidenced: bool,
    capped_ids: Collection[str] = frozenset(),
) -> list[dict[str, Any]]:
    """The aggregate blocks of ``entity_ids``: each entity's edges grouped by label, one evidence sentence per row.

    One block per entity (in the given order) that keeps at least one row:
    ``{"entity_id", "entity", "groups", "skipped"}``. A group is
    ``{"label", "count", "count_capped", "rows"}``, keyed by the canonical
    label: ``count`` is the number of distinct facts and ``rows`` the first
    ``per_label`` of them, those with a sentence first; ``count_capped`` says
    the entity's edge listing hit its limit (``capped_ids``), so the count
    is a floor. A row is the full triple (``source``, ``target``, the entity
    on either side), its first stored sentence whole (:func:`first_sentence`;
    the renderer quotes it, :func:`quote`) with its ``chunk_id`` and
    ``local_index`` (a row cited without a sentence keeps its first
    citation's chunk), and the edge's pipeline ``flags``. Groups run largest
    first, then by label.

    What a block leaves out is counted in ``skipped``, in this order:
    ``inverse``, a synthetic inverse edge (``properties.inverse_of``) whose
    forward twin is among the entity's edges (:func:`_folds_onto_twin`; an
    inverse with no such twin is kept as the forward fact, without a sentence);
    ``unevidenced``, an edge :func:`_edge_in_context` keeps out (unless
    ``include_unevidenced``); ``other_labels``, a label outside the union of
    ``families``' labels (the question's relations; empty keeps every label);
    ``duplicate``, a second edge stating a listed fact (same source, label
    and target); ``past_per_label``, the rows past each group's first
    ``per_label`` (its ``count`` still holds them); and ``listing_capped``,
    1 when the entity's listing hit its limit (the edges past it were never read).

    Args:
        entity_ids: The entities to list, in order.
        edges_by_node: Each node's listed edges, both directions.
        nodes_map: The nodes by id (the entity's own label falls back to it).
        label_map: Node labels by id, for the rows' endpoints.
        families: The question's relations and their edge labels.
        citations: Each edge's citation rows by edge id, in the storage's order.
        per_label: The rows a group lists.
        include_unevidenced: Let unevidenced edges in, flagged.
        capped_ids: The nodes whose edge listing returned its limit in a direction.
    """
    allowed = frozenset(canonical_edge_label(lb) for labels in families.values() for lb in labels)
    blocks: list[dict[str, Any]] = []
    for nid in entity_ids:
        edges = list(
            {getattr(e, "id", None) or id(e): e for e in edges_by_node.get(nid) or []}.values()
        )
        forward_keys = _forward_keys(edges)
        capped = nid in capped_ids
        skipped = dict.fromkeys(_SKIP_REASONS, 0)
        skipped["listing_capped"] = int(capped)
        by_label: dict[str, list[tuple[tuple[str, str, str], dict[str, Any]]]] = {}
        for edge in edges:
            if _folds_onto_twin(edge, forward_keys):
                skipped["inverse"] += 1
                continue
            if not _edge_in_context(edge, include_unevidenced=include_unevidenced):
                skipped["unevidenced"] += 1
                continue
            label, key, row = _row(edge, label_map, citations)
            if allowed and label not in allowed:
                skipped["other_labels"] += 1
                continue
            by_label.setdefault(label, []).append((key, row))
        if not by_label:
            logger.debug("graphrag_aggregate_block_empty", entity_id=nid, skipped=skipped)
            continue
        groups = [
            _group(label, keyed, per_label, capped=capped, skipped=skipped)
            for label, keyed in by_label.items()
        ]
        node = nodes_map.get(nid)
        blocks.append(
            {
                "entity_id": nid,
                "entity": label_map.get(nid) or getattr(node, "label", None) or nid,
                "groups": sorted(groups, key=lambda g: (-g["count"], g["label"])),
                "skipped": skipped,
            }
        )
    return blocks


def walk_degree_cap(max_hops: int) -> int:
    """The most edges a walk expands one node through: ``max(500, 50 * max_hops)``, the first ones by id."""
    return max(WALK_DEGREE_FLOOR, WALK_DEGREE_PER_HOP * max_hops)


def _edge_key(edge: Any) -> str:
    """An edge's id as the walk orders neighbours by (``""`` for an edge without one)."""
    return str(getattr(edge, "id", "") or "")


def _far_end(edge: Any, nid: str) -> str:
    """The endpoint of ``edge`` that is not ``nid`` (``nid`` itself for a self-loop)."""
    return str(edge.target_node_id if edge.source_node_id == nid else edge.source_node_id)


def _hops_to(b: str, adjacency: Mapping[str, Sequence[Any]], limit: int) -> dict[str, int]:
    """Each node's fewest hops to ``b`` over every edge of ``adjacency``, up to ``limit`` hops.

    A lower bound on the walk's own distance (which follows only the edges
    under the degree cap), so pruning by it never loses a chain.
    """
    hops = {b: 0}
    queue = deque([b])
    while queue:
        nid = queue.popleft()
        if hops[nid] >= limit:
            continue
        for edge in adjacency.get(nid) or ():
            other = _far_end(edge, nid)
            if other not in hops:
                hops[other] = hops[nid] + 1
                queue.append(other)
    return hops


def _parallel_order(edge: Any) -> tuple[bool, str]:
    """Which of several edges joining two nodes a walk steps through: an unflagged one first, then by id."""
    return bool(_edge_flags(edge)), _edge_key(edge)


def _steps(
    nid: str,
    edges: Sequence[Any],
    cap: int,
    degree_capped: dict[str, int] | None,
    parallel: set[str] | None,
    reach: Collection[str] | None = None,
) -> dict[str, Any]:
    """``nid``'s neighbours and the one edge a walk steps to each through, from its first ``cap`` edges by id.

    Only the edges whose far end is in ``reach`` (when given: the nodes
    within the walk's remaining hops of its target, :func:`_hops_to`) are
    candidates, and the cap applies to them, so a connector is never cut by
    the edges of a hub that lead nowhere (until 2026-10-09 the cap took the
    first ``cap`` edges by id before the pruning, and a connector past them
    was lost while the gap said no chain exists). The candidates past
    ``cap`` are counted on ``degree_capped`` (when given) under ``nid``.
    Several edges to one neighbour are one step, through the
    edge :func:`_parallel_order` puts first (one without pipeline flags, then
    the lowest id: the search runs before the citations are read, so whether
    an edge has a stored sentence is not known here); the others' ids go
    into ``parallel`` (when given). A self-loop goes nowhere and is left out.
    """
    ordered = sorted(
        (e for e in edges if reach is None or _far_end(e, nid) in reach), key=_edge_key
    )
    if len(ordered) > cap:
        if degree_capped is not None:
            degree_capped[nid] = len(ordered) - cap
        ordered = ordered[:cap]
    by_end: dict[str, list[Any]] = {}
    for edge in ordered:
        by_end.setdefault(_far_end(edge, nid), []).append(edge)
    by_end.pop(nid, None)
    first: dict[str, Any] = {}
    for end, joined in by_end.items():
        joined.sort(key=_parallel_order)
        first[end] = joined[0]
        if parallel is not None:
            parallel.update(_edge_key(e) or f"#{id(e)}" for e in joined[1:])
    return first


def shortest_walks(
    a: str,
    b: str,
    adjacency: Mapping[str, Sequence[Any]],
    *,
    max_hops: int,
    max_walks: int,
    degree_capped: dict[str, int] | None = None,
    parallel: set[str] | None = None,
) -> list[list[Any]]:
    """The shortest simple chains of edges from ``a`` to ``b``, then those one hop longer, at most ``max_walks``.

    Iterative deepening over an undirected adjacency (an edge is usable from
    either endpoint): every chain of the shortest length found, in the
    order a depth-first search finds them, then the chains one hop longer
    while fewer than ``max_walks`` are found, and never a chain longer than
    that (the shortest length plus one) or than ``max_hops``. Until
    2026-10-09 the search kept deepening to ``max_hops`` until it had
    ``max_walks`` chains: a path question averaged 14 walks on the real
    pins, some 30 walks and 80 hops. A node is visited once per chain, and
    ``b`` only at its end. Deterministic: a node's neighbours are taken in the
    edge-id order of their first edge, and two nodes joined by several edges
    are one step (a walk is a route through the graph; a second edge between
    the same two nodes would repeat it), through the edge :func:`_steps`
    picks; the ids of the others are recorded in ``parallel`` when given.

    A node is expanded only through the edges whose far end can still
    reach ``b`` within ``max_hops`` (:func:`_steps`); one with more such
    edges than :func:`walk_degree_cap` is expanded through its first that
    many by id, and each such node and the number of its edges not followed
    is recorded in ``degree_capped`` when given (keep, flag, never delete).
    """
    if a == b or max_walks <= 0 or max_hops <= 0:
        return []
    reach = _hops_to(b, adjacency, max_hops - 1)
    if not any(_far_end(e, a) in reach for e in adjacency.get(a) or ()):
        return []
    cap = walk_degree_cap(max_hops)
    steps: dict[str, dict[str, Any]] = {}

    def neighbours(nid: str) -> dict[str, Any]:
        """``nid``'s steps (:func:`_steps`), computed once per search."""
        if nid not in steps:
            steps[nid] = _steps(nid, adjacency.get(nid) or (), cap, degree_capped, parallel, reach)
        return steps[nid]

    found: list[list[Any]] = []

    def extend(nid: str, chain: list[Any], visited: set[str], left: int) -> None:
        """Every chain of exactly ``left`` more hops from ``nid`` to ``b``, appended to ``found``."""
        if left == 1:
            edge = neighbours(nid).get(b)
            if edge is not None:
                found.append([*chain, edge])
            return
        for other, edge in neighbours(nid).items():
            if len(found) >= max_walks:
                return
            if other in visited or other == b or reach.get(other, max_hops) > left - 1:
                continue
            visited.add(other)
            extend(other, [*chain, edge], visited, left - 1)
            visited.discard(other)

    shortest = 0
    for length in range(1, max_hops + 1):
        extend(a, [], {a}, length)
        shortest = shortest or (length if found else 0)
        if len(found) >= max_walks or (shortest and length > shortest):
            break
    return found[:max_walks]


def walk_pairs(named: Sequence[str]) -> list[tuple[str, str]]:
    """The pairs a walk joins: the named entities two at a time, in their order, the first :data:`WALK_MAX_PAIRS`."""
    return list(itertools.islice(itertools.combinations(dict.fromkeys(named), 2), WALK_MAX_PAIRS))


def walk_pairs_left_out(named: Sequence[str]) -> int:
    """How many pairs of the named entities :func:`walk_pairs` leaves out (past :data:`WALK_MAX_PAIRS`)."""
    n = len(dict.fromkeys(named))
    return max(n * (n - 1) // 2 - WALK_MAX_PAIRS, 0)


def _take_walks(
    found: Mapping[tuple[str, str], Sequence[list[Any]]], max_walks: int
) -> tuple[dict[tuple[str, str], list[list[Any]]], int]:
    """The chains a question lists, at most ``max_walks`` across its pairs, and how many it left out.

    Each pair's shortest chain first (shortest first, then in pair order),
    so a connected pair keeps a walk while the cap allows one per pair; then
    the other chains, shortest first, then in pair order. A pair's listed
    chains keep their search order. A pair with no chain maps to ``[]`` (its
    gap); a pair whose chains the cap all left out is absent (it is
    connected: no gap, its chains counted in the returned number).
    """
    ranked = sorted(
        (i > 0, len(chain), p, i)
        for p, chains in enumerate(found.values())
        for i, chain in enumerate(chains)
    )
    keep = {(p, i) for _, _, p, i in ranked[:max_walks]}
    taken: dict[tuple[str, str], list[list[Any]]] = {}
    for p, (pair, chains) in enumerate(found.items()):
        listed = [chain for i, chain in enumerate(chains) if (p, i) in keep]
        if listed or not chains:
            taken[pair] = listed
    return taken, max(len(ranked) - max_walks, 0)


def walk_chains(
    *,
    pairs: Sequence[tuple[str, str]],
    edges: Sequence[Any],
    max_hops: int,
    max_walks: int,
    include_unevidenced: bool,
    node_ids: Collection[str] | None = None,
) -> tuple[dict[tuple[str, str], list[list[Any]]], dict[str, int]]:
    """The walk's chains for each pair (:func:`shortest_walks`) over ``edges``, at most ``max_walks`` in all, and what it left out.

    ``max_walks`` caps the question's walks across its pairs
    (``GraphRAGSettings.max_walks``, :func:`_take_walks`): each pair is
    searched for up to that many, and the ones listed are each pair's
    shortest first, then the rest shortest first.

    An edge with an end outside ``node_ids`` (the loaded graph's nodes, when
    given) is left out, so a walk never passes through or ends on a node
    the graph did not load (a disabled source's, one past the node limit).
    One adjacency entry per undirected fact: a synthetic inverse edge whose
    forward twin is among ``edges`` (:func:`_folds_onto_twin`) is left out,
    so a walk never lists one fact twice; an inverse without its twin is
    kept (its hop reads as the forward fact, :func:`walk_blocks`). Edges
    :func:`_edge_in_context` keeps out are left out unless
    ``include_unevidenced``.

    Returns ``(chains, skipped)``: ``skipped`` counts, in this order,
    ``outside_graph``, ``inverse`` (the folded twins), ``unevidenced``,
    ``parallel`` (the edges a search did not step through because another
    edge joins the same two nodes, each counted once), ``degree_capped``
    (the edges past :func:`walk_degree_cap` of each node the search
    expanded, each node counted once) and ``past_max_walks`` (the chains
    found past the question's cap); ``listing_capped`` and ``pairs`` are 0
    here, for the caller that knows them (:data:`_WALK_SKIP_REASONS`).
    """
    skipped = dict.fromkeys(_WALK_SKIP_REASONS, 0)
    forward_keys = _forward_keys(edges)
    adjacency: dict[str, list[Any]] = {}
    for edge in edges:
        if node_ids is not None and (
            edge.source_node_id not in node_ids or edge.target_node_id not in node_ids
        ):
            skipped["outside_graph"] += 1
            continue
        if _folds_onto_twin(edge, forward_keys):
            skipped["inverse"] += 1
            continue
        if not _edge_in_context(edge, include_unevidenced=include_unevidenced):
            skipped["unevidenced"] += 1
            continue
        adjacency.setdefault(edge.source_node_id, []).append(edge)
        adjacency.setdefault(edge.target_node_id, []).append(edge)
    capped: dict[str, int] = {}
    parallel: set[str] = set()
    found = {
        (a, b): shortest_walks(
            a,
            b,
            adjacency,
            max_hops=max_hops,
            max_walks=max_walks,
            degree_capped=capped,
            parallel=parallel,
        )
        for a, b in pairs
    }
    chains, skipped["past_max_walks"] = _take_walks(found, max_walks)
    skipped["parallel"] = len(parallel)
    skipped["degree_capped"] = sum(capped.values())
    if any(skipped.values()):
        logger.debug("graphrag_walk_edges_skipped", skipped=skipped)
    return chains, skipped


def chain_node_ids(chains: Mapping[tuple[str, str], Sequence[Sequence[Any]]]) -> list[str]:
    """Every node id the chains' edges touch, each once, in chain order (the labels a walk needs)."""
    return list(
        dict.fromkeys(
            end
            for walks in chains.values()
            for chain in walks
            for edge in chain
            for end in (edge.source_node_id, edge.target_node_id)
        )
    )


def walk_blocks(
    *,
    pairs: Sequence[tuple[str, str]],
    edges: Sequence[Any],
    nodes_map: Mapping[str, Any],
    citations: Mapping[str, Sequence[Mapping[str, Any]]],
    max_hops: int,
    max_walks: int,
    include_unevidenced: bool,
    chains: Mapping[tuple[str, str], list[list[Any]]] | None = None,
    skipped: Mapping[str, int] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The walk block of ``pairs``: ``(walks, gaps)``, the chains between each pair with one evidence sentence per hop.

    ``chains`` are the pairs' chains when the caller has found them already
    (:func:`walk_chains`, so it could fetch the nodes the chains pass
    through first), with ``skipped``, what the walk left out (the caller's
    counts, ``listing_capped`` among them); without ``chains`` the block
    finds them over ``edges`` and reads ``skipped`` from that search.
    ``nodes_map`` holds every node a chain touches (an id without a node
    renders as itself).

    A walk is ``{"from", "to", "hops"}``, ``from``/``to`` the pair's labels,
    one per chain, pairs in order and each pair's chains shortest first. A
    hop is ``{"edge_id", "source", "target", "label", "sentence",
    "chunk_id", "local_index", "flags"}``, written as the aggregate rows
    are: the stored fact in its stored direction (``source label target``
    as committed, since the sentence states it that way, so a hop the walk
    travels against its edge still reads ``Prince Vasíli parent_of
    Anatole``; the walk's own order is the order of its hops), the
    canonical label, the edge's first stored sentence whole with its chunk
    and ``local_index`` (the renderer quotes it), and its pipeline flags. A
    synthetic inverse without its forward twin is written as the forward
    fact (endpoints swapped back, the forward label) without a sentence.

    A pair no chain of up to ``max_hops`` relations joins is a gap,
    ``{"from", "to", "max_hops", "left_out"}``: never silence. ``left_out``
    says the walk left out something that could cut a route (a
    :data:`_GAP_HEDGE_REASONS` count non-zero, or ``chains`` given without
    ``skipped``), so the gap claims only that no evidenced chain joins the
    pair among the relations it read. A
    pair absent from ``chains`` (connected, its chains past the question's
    ``max_walks``: :func:`_take_walks`) renders nothing.
    """
    if chains is None:
        chains, skipped = walk_chains(
            pairs=pairs,
            edges=edges,
            max_hops=max_hops,
            max_walks=max_walks,
            include_unevidenced=include_unevidenced,
        )
    label_map = {nid: str(getattr(node, "label", "") or nid) for nid, node in nodes_map.items()}
    left_out = skipped is None or any(skipped.get(key) for key in _GAP_HEDGE_REASONS)
    walks: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    for a, b in pairs:
        if (a, b) not in chains:
            continue
        ends = {"from": label_map.get(a, a), "to": label_map.get(b, b)}
        found = chains[(a, b)]
        if not found:
            gaps.append({**ends, "max_hops": max_hops, "left_out": left_out})
            continue
        for chain in found:
            hops = []
            for edge in chain:
                label, _, row = _row(edge, label_map, citations)
                hops.append(
                    {
                        "edge_id": row["edge_id"],
                        "source": row["source"],
                        "target": row["target"],
                        "label": label,
                        "sentence": row["sentence"],
                        "chunk_id": row["chunk_id"],
                        "local_index": row["local_index"],
                        "flags": row["flags"],
                    }
                )
            walks.append({**ends, "hops": hops})
    return walks, gaps


def walk_edge_ids(chains: Mapping[tuple[str, str], Sequence[Sequence[Any]]]) -> list[str]:
    """The ids of the chains' edges that can carry a citation (no synthetic inverse), each once, in order."""
    return list(
        dict.fromkeys(
            _edge_key(edge)
            for walks in chains.values()
            for chain in walks
            for edge in chain
            if _edge_key(edge) and not _inverse_of(edge)
        )
    )


def _block_rows(blocks: Sequence[Mapping[str, Any]]) -> Iterator[dict[str, Any]]:
    """Every row of the aggregate blocks, in block, group and row order."""
    for block in blocks:
        for group in block.get("groups") or []:
            yield from group.get("rows") or []


def block_edge_ids(blocks: Sequence[Mapping[str, Any]]) -> list[str]:
    """The edge ids the blocks' rows list, each once, in row order."""
    return list(dict.fromkeys(r["edge_id"] for r in _block_rows(blocks) if r.get("edge_id")))


def block_chunk_ids(blocks: Sequence[Mapping[str, Any]]) -> list[str]:
    """The chunk ids the blocks' rows cite, each once, in row order (rows with a chunk only)."""
    return list(dict.fromkeys(r["chunk_id"] for r in _block_rows(blocks) if r.get("chunk_id")))


def _marker(row: Mapping[str, Any], label: str, sentences: Sequence[tuple[int, str]]) -> str | None:
    """``C3:S4`` for the chunk sentence that states the row's stored sentence, or None.

    The stored ``local_index`` is chunk-local and 0-based, the ``[S{n}]``
    labels 1-based: the sentence at ``local_index + 1`` when its text folds
    to the row's, else the first one that does (the splitter and the
    extraction counted differently), else none.
    """
    wanted = fold(str(row.get("sentence") or ""))
    if not wanted:
        return None
    texts = dict(sentences)
    index = row.get("local_index")
    if isinstance(index, int) and fold(texts.get(index + 1, "")) == wanted:
        return f"{label}:S{index + 1}"
    for num, text in sentences:
        if fold(text) == wanted:
            return f"{label}:S{num}"
    return None


def _walk_hops(walks: Sequence[Mapping[str, Any]]) -> Iterator[dict[str, Any]]:
    """Every hop of the walks, in walk and hop order."""
    for walk in walks:
        yield from walk.get("hops") or []


def _cited_rows(graph_context: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Every row that quotes stored evidence: the aggregate rows, then the walk hops, in order."""
    return [
        *_block_rows(graph_context.get("aggregates") or []),
        *_walk_hops(graph_context.get("walks") or []),
    ]


def cited_chunk_ids(graph_context: Mapping[str, Any]) -> list[str]:
    """The chunk ids a graph context's blocks cite, each once: the aggregate rows', then the walk hops'.

    The order the evidence arm takes them in (block order), and the rows
    whose markers :func:`annotate_markers` sets.
    """
    return list(
        dict.fromkeys(str(r["chunk_id"]) for r in _cited_rows(graph_context) if r.get("chunk_id"))
    )


def annotate_markers(result: dict[str, Any]) -> None:
    """Set ``marker`` (``C3:S4``) on each aggregate row and walk hop whose sentence is in a final chunk.

    Run after the merge, once the chunk aliases are final; a row or hop
    whose chunk is not among ``result["chunks"]``, or whose sentence no
    ``[S#]`` line of the chunk states, keeps no marker (its sentence still
    renders quoted).
    """
    by_chunk: dict[str, tuple[str, list[tuple[int, str]]]] = {}
    for chunk in result.get("chunks") or []:
        parsed = labelled_sentences(chunk)
        if parsed is not None and isinstance(chunk, dict) and chunk.get("chunk_id"):
            by_chunk.setdefault(str(chunk["chunk_id"]), parsed)
    if not by_chunk:
        return
    for row in _cited_rows(result.get("graph_context") or {}):
        found = by_chunk.get(str(row.get("chunk_id") or ""))
        marker = _marker(row, *found) if found else None
        if marker:
            row["marker"] = marker


__all__ = [
    "EVIDENCE_QUOTE_CHARS",
    "WALK_DEGREE_FLOOR",
    "WALK_DEGREE_PER_HOP",
    "WALK_MAX_PAIRS",
    "aggregate_blocks",
    "annotate_markers",
    "block_chunk_ids",
    "block_edge_ids",
    "chain_node_ids",
    "cited_chunk_ids",
    "first_sentence",
    "labelled_sentences",
    "quote",
    "shortest_walks",
    "walk_blocks",
    "walk_chains",
    "walk_degree_cap",
    "walk_edge_ids",
    "walk_pairs",
    "walk_pairs_left_out",
]
