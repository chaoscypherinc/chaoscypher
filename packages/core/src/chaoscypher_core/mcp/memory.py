# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""The memory tools' handlers: ``remember``, ``recall``, ``list_memories``, ``digest``.

A memory is the import pipeline run on one text: a source of type ``memory`` carrying who
said it, in which scope and from when, chunked, indexed and extracted with the KNOWN
ENTITIES list seeded from the graph, so its "Pierre" lands on the Pierre node. The answer
is plain text sized for a small model: what was remembered, what it linked, the facts.
"""

from __future__ import annotations

import asyncio
from collections import Counter, defaultdict
from collections.abc import Callable, Collection, Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any

import structlog
from mcp.types import CallToolResult, TextContent

from chaoscypher_core.adapters.sqlite.repos import memory_audit
from chaoscypher_core.adapters.sqlite.repos.memory_audit import count_forgotten
from chaoscypher_core.mcp.memory_link import (
    LinkOutcome,
    LinkView,
    MemoryLinker,
    link_repository,
    link_view,
    new_claim_token,
)
from chaoscypher_core.mcp.memory_seed import all_sources, memory_rows, node_by_label
from chaoscypher_core.mcp.provenance import GraphProvenance
from chaoscypher_core.mcp.secrets import looks_like_secret
from chaoscypher_core.models import MEMORY_SOURCE_TYPE, MemoryLinkState, is_memory_source
from chaoscypher_core.services.graph.memory.supersession import (
    recompute_single_valued,
    single_valued_labels,
)
from chaoscypher_core.services.sources.engine.extraction.utils.relation_vocabulary import (
    EMPTY_RELATION_VOCABULARY,
)
from chaoscypher_core.services.workflows.tools.engine.memory_render import (
    EMPTY_RECALL,
    INDENT,
    SENTENCE_CHARS,
    Provenance,
    cut_on_word,
    cut_to_budget,
    fact_line,
    render_recall,
    tag,
)
from chaoscypher_core.utils.id import generate_hex_id
from chaoscypher_core.utils.logging.privacy import log_failure, private_text


if TYPE_CHECKING:
    from chaoscypher_core.bootstrap import Engine
    from chaoscypher_core.models import SourceDeletion
    from chaoscypher_core.ports.graph import GraphRepositoryProtocol
    from chaoscypher_core.services.workflows.tools.engine.executor import ToolExecutorService
    from chaoscypher_core.settings import EngineSettings

logger = structlog.get_logger(__name__)

# The status copy (spec 2026-10-24 § 4.3, D19): this module owns it, verbatim.
NO_FACTS = "Linked: none (no facts found; the text is kept as a note)"
TIMED_OUT_LINE = "Not linked yet: the model did not answer in time. Kept as a note; it links later."
FAILED_LINE = "Not linked yet: the model failed. Kept as a note; it links later."
NO_MODEL_LINE = (
    "Not linked yet: no extraction model is set up. Kept as a note; it links once one is."
)
NOT_STORED = "Not stored: the database is busy. Try again."
GONE_LINE = "Forgotten before it was linked."


def _session(engine: Engine) -> Any:
    """The engine's open storage session (the audit rows live beside the sources)."""
    session = engine.storage_adapter.session
    if session is None:
        msg = "the engine's storage is not connected"
        raise RuntimeError(msg)
    return session


def _answer(text: str, *, error: bool = False) -> CallToolResult:
    """One text block as the tool result, flagged as an error when ``error``."""
    return CallToolResult(content=[TextContent(type="text", text=text)], isError=error)


def _parse_date(value: str | None) -> date | None:
    """A ``YYYY-MM-DD`` date; None for empty; ValueError for anything else."""
    if not value:
        return None
    return date.fromisoformat(value)


def remembered_line(by: str, valid_from: date, text: str) -> str:
    """The receipt's first line: ``Remembered [by DATE] "TEXT"``."""
    return f'Remembered [{by} {valid_from.isoformat()}] "{cut_on_word(text, SENTENCE_CHARS)}"'


BOTH_KEPT = "Both kept."


def changed_lines(engine: Engine, source_id: str) -> list[str]:
    """``Changed:`` per fact the memory closed, ``Noted:`` per fact of its own it closed as older.

    Spec decision 6's exact copy: ``->`` is the one arrow in a memory tool's output.
    """
    graph = engine.graph_repository
    prov = GraphProvenance(engine)
    labels: dict[str, str] = {}

    def label_of(node_id: str) -> str:
        if node_id not in labels:
            labels[node_id] = _label(graph, node_id)
        return labels[node_id]

    def open_fact(subject: str, label: str, member_ids: list[str]) -> Any | None:
        """The open fact of the same subject and label among ``member_ids``' edges."""
        for eid in member_ids:
            e = graph.get_edge(eid)
            if (
                e is not None
                and e.source_node_id == subject
                and e.label == label
                and e.valid_to is None
            ):
                return e
        return None

    members = graph.member_edge_ids(source_id)
    own = graph.memory_source_fields([source_id]).get(source_id, {})
    # this memory's own statement, whatever the fact's earliest date (a restated fact)
    new_tag = tag(memory_provenance(by=own.get("memory_by"), valid_from=own.get("valid_from")))
    lines: list[str] = []
    for closed in graph.edges_closed_by(source_id):
        new = open_fact(closed.source_node_id, closed.label, members)
        if new is None:
            continue
        old_tag = tag(
            memory_provenance(by=prov.for_edge(closed.id).by, valid_from=closed.valid_from)
        )
        lines.append(
            f"Changed: {label_of(closed.source_node_id)} {closed.label} "
            f"{label_of(closed.target_node_id)} {old_tag} -> {label_of(new.target_node_id)} "
            f"{new_tag}. {BOTH_KEPT}"
        )
    for eid in members:
        edge = graph.get_edge(eid)
        if edge is None or edge.valid_to is None or edge.closed_by_source_id == source_id:
            continue
        newer = next(
            (e for e in graph.open_edges(edge.source_node_id, edge.label) if e.id != edge.id), None
        )
        if newer is None:
            continue
        mine = tag(memory_provenance(by=prov.for_edge(edge.id).by, valid_from=edge.valid_from))
        lines.append(
            f"Noted: {label_of(edge.source_node_id)} {edge.label} {label_of(edge.target_node_id)} "
            f"{mine} is older than {label_of(newer.target_node_id)} {tag(prov.for_edge(newer.id))}; "
            "kept as history."
        )
    return lines


def linked_lines(engine: Engine, source_id: str) -> list[str]:
    """``Linked:`` (the nodes the memory attests) and ``Facts:`` (its edges), or :data:`NO_FACTS`."""
    graph = engine.graph_repository
    nodes = graph.get_nodes_batch(graph.member_node_ids(source_id), include_embedding=False)
    if not nodes:
        return [NO_FACTS]
    labels = {n.id: n.label for n in nodes}
    lines = ["Linked: " + ", ".join(f"{n.label} ({n.entity_type or 'Entity'})" for n in nodes)]
    for edge_id in graph.member_edge_ids(source_id):
        edge = graph.get_edge(edge_id)
        if edge is None:
            continue
        subject = labels.get(edge.source_node_id) or _label(graph, edge.source_node_id)
        obj = labels.get(edge.target_node_id) or _label(graph, edge.target_node_id)
        lines.append(f"Facts: {subject} {edge.label} {obj}")
    return lines


def _label(graph: Any, node_id: str) -> str:
    """A node's label, else its id when the node is gone."""
    node = graph.get_node(node_id, include_embedding=False)
    return node.label if node is not None else node_id


@dataclass(frozen=True)
class MemoryArgs:
    """``remember``'s arguments, parsed and defaulted."""

    text: str
    by: str
    scope: str | None
    valid_from: date
    about: tuple[str, ...]


def parse_memory_args(args: dict[str, Any], settings: EngineSettings) -> MemoryArgs | str:
    """The parsed arguments, or the refusal text (a secret, a bad date, no text)."""
    text = str(args.get("text") or "").strip()
    if not text:
        return "text is required"
    kind = looks_like_secret(text)
    if kind is not None:
        return f"Not stored: this looks like a {kind}. Memories never hold keys or passwords."
    try:
        valid_from = _parse_date(args.get("valid_from")) or datetime.now(UTC).date()
    except ValueError:
        return "valid_from must be YYYY-MM-DD"
    return MemoryArgs(
        text=text,
        by=str(args.get("by") or settings.mcp.memory.default_by),
        scope=args.get("scope") or None,
        valid_from=valid_from,
        about=tuple(a.strip() for a in str(args.get("about") or "").split(",") if a.strip()),
    )


def link_status_lines(engine: Engine, source_id: str, outcome: LinkOutcome) -> list[str]:
    """The lines after ``Remembered``: what the memory linked, or why it is not linked yet."""
    if outcome in (LinkOutcome.LINKED, LinkOutcome.ALREADY_LINKED):
        # linked_lines answers NO_FACTS for a memory that attests nothing
        return [*linked_lines(engine, source_id), *changed_lines(engine, source_id)]
    by_outcome = {
        LinkOutcome.LINKED_EMPTY: NO_FACTS,
        LinkOutcome.TIMED_OUT: TIMED_OUT_LINE,
        LinkOutcome.ELSEWHERE: TIMED_OUT_LINE,
        LinkOutcome.FAILED: FAILED_LINE,
        LinkOutcome.NO_MODEL: NO_MODEL_LINE,
        LinkOutcome.GONE: GONE_LINE,
    }
    return [by_outcome[outcome]]


def link_suffix(view: LinkView, attempts: int) -> str:
    """The end of a memory's ``list_memories`` line: its link state, nothing when linked."""
    if view is LinkView.FAILED:
        tries = "1 try" if attempts == 1 else f"{attempts} tries"
        return f" (not linked yet: the model failed, {tries})"
    return {
        LinkView.PENDING: " (not linked yet)",
        LinkView.LINKING: " (linking)",
        LinkView.INTERRUPTED: " (not linked yet: interrupted)",
        LinkView.NO_MODEL: " (not linked yet: no extraction model)",
        LinkView.LINKED_EMPTY: " (no facts found)",
        LinkView.LINKED: "",
    }[view]


def not_linked_line(views: Iterable[LinkView]) -> str | None:
    """``digest``'s count of the memories not linked yet, or None when there are none."""
    counts = Counter(views)
    waiting = sum(n for v, n in counts.items() if v not in (LinkView.LINKED, LinkView.LINKED_EMPTY))
    if not waiting:
        return None
    return (
        f"Not linked yet: {waiting} ({counts[LinkView.NO_MODEL]} no model, "
        f"{counts[LinkView.FAILED]} failed, {counts[LinkView.INTERRUPTED]} interrupted)"
    )


def _link_views(
    engine: Engine,
    *,
    source_ids: Collection[str] | None = None,
    scope: str | None = None,
) -> dict[str, tuple[LinkView, int]]:
    """Each memory's link view and failed-attempt count, read on a fresh session."""
    now = datetime.now(UTC)
    rows = link_repository(engine).link_rows(
        engine.database_name, source_ids=source_ids, scope=scope
    )
    return {row.id: (link_view(row, now), row.recovery_attempts) for row in rows}


async def _store(engine: Engine, parsed: MemoryArgs, source_id: str, token: str) -> bool:
    """Store the memory, its row written already claimed; False when no row was written.

    The row carries the text (``full_text``), so a store that fails after the row is
    written is not lost: the link chunks it again from the kept text.
    """
    fields = {
        "memory_by": parsed.by,
        "memory_scope": parsed.scope,
        "valid_from": parsed.valid_from.isoformat(),
        "full_text": parsed.text,
        "memory_link_state": MemoryLinkState.LINKING.value,
        "memory_link_claim": token,
        "last_activity_at": datetime.now(UTC),
    }
    try:
        await engine.process_document(
            parsed.text,
            source_id=source_id,
            filename=f"memory-{source_id[:8]}.txt",
            auto_confirm=True,
            forced_domain="generic",
            source_type=MEMORY_SOURCE_TYPE,
            source_fields=fields,
            extract=False,
        )
    except Exception as error:
        if engine.storage_adapter.get_source(source_id, engine.database_name) is None:
            log_failure(logger, "memory_not_stored", error, source_id=source_id)
            return False
        log_failure(logger, "memory_store_incomplete", error, source_id=source_id)
    return True


async def handle_remember(
    engine: Engine, args: dict[str, Any], settings: EngineSettings, linker: MemoryLinker
) -> CallToolResult:
    """Store one memory, link it while the wait lasts, and answer with what it linked or why not."""
    with private_text():  # a failure's log may quote the memory
        return await _remember(engine, args, settings, linker)


async def _remember(
    engine: Engine, args: dict[str, Any], settings: EngineSettings, linker: MemoryLinker
) -> CallToolResult:
    """:func:`handle_remember`'s body, run inside ``private_text()``."""
    parsed = parse_memory_args(args, settings)
    if isinstance(parsed, str):
        return _answer(parsed, error=True)
    source_id = generate_hex_id()
    token = new_claim_token()
    if not await _store(engine, parsed, source_id, token):
        return _answer(NOT_STORED, error=True)
    first = remembered_line(parsed.by, parsed.valid_from, parsed.text)
    task = linker.spawn(source_id, token=token, about=parsed.about)
    try:
        outcome = await asyncio.wait_for(
            asyncio.shield(task), timeout=settings.mcp.memory.wait_seconds
        )
    except TimeoutError:
        logger.info("memory_still_linking", source_id=source_id)
        outcome = LinkOutcome.TIMED_OUT
    except Exception as error:
        # the row exists and keeps the text: never an error, or the agent stores it twice
        log_failure(logger, "memory_link_raised", error, source_id=source_id)
        outcome = LinkOutcome.FAILED
    lines = [first, *link_status_lines(engine, source_id, outcome)]
    return _answer(cut_to_budget(lines, settings.mcp.memory.result_chars))


# ----------------------------------------------------------------------------
# recall, list_memories, digest
# ----------------------------------------------------------------------------

NOTHING_YET = "Nothing remembered yet."
DIGEST_DAYS = 7


def _other_sources(engine: Engine) -> list[str]:
    """Every source that is no memory (documents, packs): always in scope (spec § 8b)."""
    return [r["id"] for r in all_sources(engine) if not is_memory_source(r)]


def _memory_text(engine: Engine, source_id: str) -> str:
    """A memory's text, its first chunk's (else its kept text), cut at :data:`SENTENCE_CHARS` on a word."""
    chunks, _ = engine.storage_adapter.get_chunks_by_source(source_id, page_size=1)
    if chunks:
        text = str(chunks[0].get("content") or "")
    else:  # its store never finished: the row keeps the text
        text = engine.storage_adapter.get_source_full_text(source_id, engine.database_name) or ""
    return cut_on_word(" ".join(text.split()), SENTENCE_CHARS)


def _memory_tag(row: dict[str, Any]) -> str:
    """The ``[by DATE]`` tag of a memory source row."""
    return tag(
        memory_provenance(
            by=row.get("memory_by"),
            valid_from=_parse_date(row.get("valid_from")),
        )
    )


def memory_provenance(*, by: str | None, valid_from: date | None) -> Provenance:
    """A memory's provenance from its source row alone (no sentence: the list and digest tags)."""
    return Provenance("memory", by, None, valid_from, None, None)


async def handle_list_memories(
    engine: Engine, args: dict[str, Any], settings: EngineSettings
) -> CallToolResult:
    """What was remembered, newest first: ``[by DATE] "text"`` per memory."""
    scope = args.get("scope") or None
    about = str(args.get("about") or "").strip()
    limit = max(1, min(50, int(args.get("limit") or 10)))
    rows = memory_rows(engine, scope)
    if about:
        node = node_by_label(engine, about)
        members = set(engine.graph_repository.node_members(node.id)) if node is not None else set()
        rows = [r for r in rows if r["id"] in members]
        if not rows:
            return _answer(f"Nothing remembered about {about}.")
    if not rows:
        return _answer(NOTHING_YET)
    listed = rows[:limit]
    views = await asyncio.to_thread(_link_views, engine, source_ids=[r["id"] for r in listed])
    lines = [  # a memory another process forgot between the two reads is left out
        f'{_memory_tag(r)} "{_memory_text(engine, r["id"])}"' + link_suffix(*views[r["id"]])
        for r in listed
        if r["id"] in views
    ]
    return _answer(cut_to_budget(lines, settings.mcp.memory.result_chars))


def _since(args: dict[str, Any]) -> date | str:
    """The window's first day, or the refusal text."""
    try:
        return _parse_date(args.get("since")) or (
            datetime.now(UTC).date() - timedelta(days=DIGEST_DAYS)
        )
    except ValueError:
        return "since must be YYYY-MM-DD"


def digest_lines(engine: Engine, scope: str | None, since: date) -> list[str]:
    """The digest: the window's memories and facts, what was replaced, what was forgotten."""
    prov = GraphProvenance(engine)
    graph = engine.graph_repository
    rows = [
        r
        for r in memory_rows(engine, scope)
        if str(r.get("created_at") or "")[:10] >= since.isoformat()
    ]
    added: dict[str, list[str]] = defaultdict(list)
    facts = 0
    for row in rows:
        for edge_id in graph.member_edge_ids(row["id"]):
            edge = graph.get_edge(edge_id)
            if edge is None:
                continue
            nodes = {
                n.id: n.label
                for n in graph.get_nodes_batch(
                    [edge.source_node_id, edge.target_node_id], include_embedding=False
                )
            }
            subject = nodes.get(edge.source_node_id, edge.source_node_id)
            obj = nodes.get(edge.target_node_id, edge.target_node_id)
            line = INDENT + fact_line(subject, edge.label, obj, prov.for_edge(edge_id))
            if line not in added[subject]:
                added[subject].append(line)
                facts += 1
    lines = [f"Since {since.isoformat()}: {len(rows)} memories, {facts} facts added."]
    if added:
        lines.append("Added:")
        for subject, fact_lines in added.items():
            lines.append(f"{subject}:")
            lines.extend(fact_lines)
    replaced: list[str] = []
    for row in rows:
        for closed in graph.edges_closed_by(row["id"]):
            nodes = {
                n.id: n.label
                for n in graph.get_nodes_batch(
                    [closed.source_node_id, closed.target_node_id], include_embedding=False
                )
            }
            replaced.append(
                INDENT
                + fact_line(
                    nodes.get(closed.source_node_id, closed.source_node_id),
                    closed.label,
                    nodes.get(closed.target_node_id, closed.target_node_id),
                    prov.for_edge(closed.id),
                )
            )
    if replaced:
        lines.append("Replaced:")
        lines.extend(replaced)
    else:
        lines.append("Replaced: none")
    forgotten = count_forgotten(_session(engine), engine.database_name, since.isoformat())
    lines.append(f"Forgotten: {forgotten}")
    # every memory of the scope, not only the window's: the line reports a present state
    waiting = not_linked_line(view for view, _ in _link_views(engine, scope=scope).values())
    if waiting is not None:
        lines.append(waiting)
    return lines


async def handle_digest(
    engine: Engine, args: dict[str, Any], settings: EngineSettings
) -> CallToolResult:
    """What a window added: ``Since DATE: N memories, M facts added.`` and the facts."""
    since = _since(args)
    if isinstance(since, str):
        return _answer(since, error=True)
    lines = digest_lines(engine, args.get("scope") or None, since)
    return _answer(cut_to_budget(lines, settings.mcp.memory.result_chars))


async def handle_recall(
    engine: Engine,
    args: dict[str, Any],
    settings: EngineSettings,
    executor: ToolExecutorService,
) -> CallToolResult:
    """The graph's answer in its shape (spec decision 4): cards, grouped lists, chains, notes."""
    query = str(args.get("query") or "").strip()
    if not query:
        return _answer("query is required", error=True)
    scope = args.get("scope") or None
    try:
        as_of = _parse_date(args.get("as_of"))
    except ValueError:
        return _answer("as_of must be YYYY-MM-DD", error=True)
    if scope is None:
        if not memory_rows(engine) and not _other_sources(engine):
            return _answer(EMPTY_RECALL)
        source_ids = None  # the whole graph: every memory and every document
    else:
        source_ids = [r["id"] for r in memory_rows(engine, scope)] + _other_sources(engine)
        if not source_ids:
            return _answer(EMPTY_RECALL)
    handlers = executor.graphrag_handlers
    with handlers.lever_scope():
        result = await handlers.retrieve_shaped(query, source_ids=source_ids, router="rules")
    global_text = None
    if result.get("retrieval_stats", {}).get("shape") == "global":
        global_text = "\n".join(
            digest_lines(engine, scope, datetime.now(UTC).date() - timedelta(days=DIGEST_DAYS))
        )
    text = render_recall(
        result,
        GraphProvenance(engine),
        budget=settings.mcp.memory.result_chars,
        more=bool(args.get("more")),
        ids=bool(args.get("ids")),
        global_text=global_text,
        as_of=as_of,
    )
    return _answer(text)


# ----------------------------------------------------------------------------
# forget
# ----------------------------------------------------------------------------

HANDLE_MIN = 6
PREVIEW_MATCHES = 5
NEEDS_HANDLE = "Say which: confirm needs a handle."


def memory_handle(source_id: str, all_ids: Collection[str]) -> str:
    """The shortest prefix of ``source_id``, at least :data:`HANDLE_MIN` long, unique in ``all_ids``."""
    others = [i for i in all_ids if i != source_id]
    for n in range(HANDLE_MIN, len(source_id) + 1):
        prefix = source_id[:n]
        if not any(o.startswith(prefix) for o in others):
            return prefix
    return source_id


def resolve_handle(handle: str, all_ids: Collection[str]) -> str:
    """The one id ``handle`` is a prefix of, else the refusal text (too short, none, or several)."""
    if len(handle) < HANDLE_MIN:
        return f"Handle {handle} is too short; a handle has at least {HANDLE_MIN} characters."
    matches = sorted(i for i in all_ids if i.startswith(handle))
    if not matches:
        return f"No memory has handle {handle}."
    if len(matches) == 1:
        return matches[0]
    needed = max(len(memory_handle(m, matches)) for m in matches)
    return f"Handle {handle} is ambiguous; use {needed} characters."


def _single_valued_pairs(engine: Engine, source_id: str) -> list[tuple[str, str]]:
    """The (subject, label) pairs of the memory's single-valued facts, to recompute after it goes."""
    graph = engine.graph_repository
    labels = single_valued_labels(EMPTY_RELATION_VOCABULARY)
    pairs: list[tuple[str, str]] = []
    for edge_id in graph.member_edge_ids(source_id):
        edge = graph.get_edge(edge_id)
        if (
            edge is not None
            and edge.label in labels
            and (edge.source_node_id, edge.label) not in pairs
        ):
            pairs.append((edge.source_node_id, edge.label))
    return pairs


def _fact_counts(engine: Engine, source_id: str) -> tuple[int, int]:
    """``(go, stays)``: the source's member edges with one member, and the rest."""
    graph = engine.graph_repository
    go = stays = 0
    for edge_id in graph.member_edge_ids(source_id):
        if len(graph.edge_members(edge_id)) <= 1:
            go += 1
        else:
            stays += 1
    return go, stays


def _matching_memories(
    engine: Engine, query: str, rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """The memories whose chunks the keyword index returns for ``query``, in hit order, each once."""
    by_id = {r["id"]: r for r in rows}
    if not query.strip() or not by_id:
        return []
    hits = engine.search_repository.keyword_search(
        query, limit=PREVIEW_MATCHES * 4, item_type="chunk", source_ids=list(by_id)
    )
    matched: list[dict[str, Any]] = []
    for item_id, _ in hits:
        chunk = engine.storage_adapter.get_chunk(
            str(item_id).removeprefix("chunk:"), engine.database_name
        )
        source_id = str((chunk or {}).get("source_id") or "")
        if source_id in by_id and by_id[source_id] not in matched:
            matched.append(by_id[source_id])
    return matched


def forget_preview(
    engine: Engine, matched: list[dict[str, Any]], all_ids: Collection[str]
) -> list[str]:
    """The numbered matches with their handles and counts, and how to confirm one."""
    lines: list[str] = []
    for n, row in enumerate(matched[:PREVIEW_MATCHES], start=1):
        handle = memory_handle(row["id"], all_ids)
        go, stays = _fact_counts(engine, row["id"])
        elsewhere = " (also stated elsewhere)" if stays else ""
        lines.append(
            f'{n}. {handle} {_memory_tag(row)} "{_memory_text(engine, row["id"])}" '
            f"- {go} facts go, {stays} stays{elsewhere}"
        )
    if len(matched) > PREVIEW_MATCHES:
        lines.append(f"{len(matched) - PREVIEW_MATCHES} more, narrow the query.")
    if matched:
        first = memory_handle(matched[0]["id"], all_ids)
        lines.append(f'To forget one: forget(handle="{first}", confirm=true)')
    else:
        lines.append("No memory matches.")
    return lines


async def remove_source(
    engine: Engine,
    source_id: str,
    *,
    commit_with: Callable[[GraphRepositoryProtocol], None] | None = None,
) -> SourceDeletion:
    """Delete a source and refresh the graph it released: the one delete path of the server.

    ``commit_with`` runs inside the delete's transaction (``SourceService.delete_source``).
    The refresh after the commit is best-effort: once the delete commits it stands.
    """
    from chaoscypher_core.services.graph.management.release import refresh_after_release
    from chaoscypher_core.services.graph.management.source import SourceService

    database_name = engine.settings.current_database
    sms = SourceService(
        repository=engine.storage_adapter, database_name=database_name, settings=engine.settings
    )
    deleted = sms.delete_source(
        source_id, search_repo=engine.search_repository, commit_with=commit_with
    )
    await refresh_after_release(
        engine.storage_adapter,
        database_name,
        deleted.released,
        embedder=engine.embedding_provider,
        search=engine.search_repository,
    )
    return deleted


async def handle_forget(
    engine: Engine, args: dict[str, Any], settings: EngineSettings
) -> CallToolResult:
    """Preview what a query matches, or forget one memory by its handle with ``confirm``."""
    rows = memory_rows(engine)
    all_ids = [r["id"] for r in rows]
    handle = str(args.get("handle") or "").strip()
    confirm = bool(args.get("confirm"))
    query = str(args.get("query") or "")
    if handle and not confirm:
        resolved = resolve_handle(handle, all_ids)
        if resolved not in all_ids:
            return _answer(resolved, error=True)
        lines = forget_preview(engine, [next(r for r in rows if r["id"] == resolved)], all_ids)
        return _answer(cut_to_budget(lines, settings.mcp.memory.result_chars))
    if not handle:
        matched = _matching_memories(engine, query, rows)
        lines = forget_preview(engine, matched, all_ids)
        if confirm:
            lines.insert(0, NEEDS_HANDLE)
        return _answer(cut_to_budget(lines, settings.mcp.memory.result_chars))
    with private_text():  # the release and the settle log names the memory states
        return await _forget_one(engine, handle, rows, all_ids)


async def _forget_one(
    engine: Engine, handle: str, rows: list[dict[str, Any]], all_ids: list[str]
) -> CallToolResult:
    """:func:`handle_forget`'s confirmed branch, run inside ``private_text()``."""
    resolved = resolve_handle(handle, all_ids)
    if resolved not in all_ids:
        return _answer(resolved, error=True)
    row = next(r for r in rows if r["id"] == resolved)
    go, stays = _fact_counts(engine, resolved)
    pairs = _single_valued_pairs(engine, resolved)

    def settle_and_audit(graph: GraphRepositoryProtocol) -> None:
        """In the delete's transaction: settle what the memory had closed, write the audit row."""
        for subject_id, label in pairs:  # what the memory had closed is open again
            recompute_single_valued(graph, subject_id, label)
        memory_audit.record(
            _session(engine),
            engine.database_name,
            action=memory_audit.FORGET,
            handle=handle,
            by=row.get("memory_by"),
            scope=row.get("memory_scope"),
            facts_removed=go,
            facts_kept=stays,
        )

    deleted = await remove_source(engine, resolved, commit_with=settle_and_audit)
    if not deleted:
        return _answer(f"No memory has handle {handle}.", error=True)
    return _answer(f"Forgotten {handle}: {go} facts removed, {stays} kept.")
