# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Release a source from the graph and rebuild what it leaves behind (#795, spec 2026-10-17 D3/D5).

Every path that takes a source out of the graph (single delete, re-commit
cleanup, force re-extract) calls :func:`release_source_graph` inside its own
transaction, then :func:`refresh_released_graph` (or its adapter forms
:func:`refresh_after_release` / :func:`refresh_after_release_sync`) once that
transaction has committed. The reset paths release every source at once
(``release_all_sources``) and leave no survivor to rebuild.

The release (``GraphRepositoryProtocol.release_source``) takes the source's
memberships and deletes what no source attests any more. A survivor, a node or
edge another source still attests, is then rebuilt from the readings its
remaining members wrote (:func:`~chaoscypher_core.services.graph.resolution.apply.member_readings`):
the survivor ranking (``plan._keep``) picks the reading whose label it keeps,
the others fold into it through the merge's own fold
(:func:`~chaoscypher_core.services.graph.resolution.apply.fold_members`), and
its home ``source_id`` becomes that reading's source. An edge folds its readings
with ``merge_edge_properties`` and is homed at its lowest remaining member
(:func:`rebuild_from_members`, which a commit also runs on the reused rows its
source attests again). The release and the rebuild join the caller's
transaction: they are the integrity guarantee.

Everything else runs after the commit, in :func:`refresh_released_graph`:
``resolve_graph`` scoped to the rebuilt nodes and the ends of the surviving
edges, so alias drops, flags and merges apply to them again (it loads and
plans the whole graph, so it may not hold the writer lock; each merge takes
it for its own read and write), the search-index cleanup and the
re-embedding (an awaited provider call). They are best-effort and logged, as
the post-commit identity pass is, and cover only the rows ``ReleasedRows``
names (``deleted_node_ids``, ``stale_node_ids``, ``resolve_node_ids``).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.models import EdgeUpdate, NodeUpdate
from chaoscypher_core.services.graph.resolution.apply import (
    ResolutionReport,
    distinct_readings,
    fold_members,
    member_readings,
    resolve_graph,
)
from chaoscypher_core.services.graph.resolution.plan import _keep
from chaoscypher_core.services.sources.engine.deduplication.embedding_generator import (
    node_embedding_text,
)
from chaoscypher_core.services.sources.engine.deduplication.service import (
    merge_edge_properties,
)


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
    from chaoscypher_core.models import ReleasedRows
    from chaoscypher_core.ports.embedding import EmbeddingProviderProtocol
    from chaoscypher_core.ports.graph import GraphRepositoryProtocol
    from chaoscypher_core.ports.search import SearchRepositoryProtocol
    from chaoscypher_core.ports.storage_sources import SourceStorageProtocol
    from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
        NameConventions,
    )
    from chaoscypher_core.settings import EngineSettings


logger = structlog.get_logger(__name__)


# ---- The graph's naming conventions (shared with the post-commit identity pass) ----


def graph_source_ids(repo: GraphRepositoryProtocol) -> set[str]:
    """Every source that attests a node of the graph (``member_source_ids``).

    Read off the membership table's ``source_id`` index in one ``SELECT
    DISTINCT``. Until #795 this paged every node for its home
    ``source_id``: inside a release's transaction it held the writer lock
    across a whole-graph read, and it missed a source that attests merged
    nodes only.
    """
    return repo.member_source_ids()


def graph_packs(
    repo: GraphRepositoryProtocol,
    sources: SourceStorageProtocol,
    database_name: str,
    *,
    include: Iterable[str] = (),
    exclude: Iterable[str] = (),
) -> tuple[str, ...]:
    """The union of the packs the graph's sources extracted with, sorted.

    ``domain_resolver.sources_packs`` over the source row of every source with
    a node in the graph, plus ``include`` and less ``exclude``: what the
    identity pass reads names by (``domain_resolver.conventions_of_packs``).
    """
    from chaoscypher_core.services.sources.engine.extraction.domain_resolver import (
        sources_packs,
    )

    ids = (graph_source_ids(repo) | set(include)) - set(exclude)
    return sources_packs(sources.get_source(sid, database_name) for sid in sorted(ids))


def graph_conventions(
    repo: GraphRepositoryProtocol,
    sources: SourceStorageProtocol,
    database_name: str,
    settings: EngineSettings | None = None,
    *,
    include: Iterable[str] = (),
    exclude: Iterable[str] = (),
) -> NameConventions:
    """The naming conventions the graph's sources read by: :func:`graph_packs`' tables over the floor's."""
    from chaoscypher_core.services.sources.engine.extraction.domain_resolver import (
        conventions_of_packs,
    )

    packs = graph_packs(repo, sources, database_name, include=include, exclude=exclude)
    return conventions_of_packs(packs, settings)


# ---- The rebuild ----


@dataclasses.dataclass(frozen=True)
class _NodeRebuild:
    """One node's rebuild: whether it changed, and the home source and type it now has."""

    changed: bool
    home: str | None
    entity_type: str | None


def _rebuild_node(
    repo: GraphRepositoryProtocol, node_id: str, conventions: NameConventions
) -> _NodeRebuild:
    """Rebuild one survivor from its remaining readings.

    The readings are taken once per content (``distinct_readings``): sources
    that wrote the same thing are one reading, so nothing folds into itself,
    and the row's current home keeps it when it holds that content. One
    reading left is the node as that source wrote it, with no fold. With
    several, the survivor ranking picks the reading whose label the node keeps
    and the others fold into it. A reading's support is the number of sources
    that wrote it, so the name more remaining readings write ranks higher; a
    reading's own edges are not kept apart once merged, so degree plays no part.
    """
    node = repo.get_node(node_id, include_embedding=False)
    if node is None:
        return _NodeRebuild(changed=False, home=None, entity_type=None)
    readings = distinct_readings(member_readings(repo.node_members(node_id)), prefer=node.source_id)
    if not readings:
        return _NodeRebuild(changed=False, home=None, entity_type=None)
    keys = [str(i) for i in range(len(readings))]
    by_key = {k: record for k, (_, record, _n) in zip(keys, readings, strict=True)}
    support = {k: n for k, (_, _r, n) in zip(keys, readings, strict=True)}
    chosen = keys[0] if len(keys) == 1 else _keep(keys, by_key, support, conventions)
    home, survivor, _support = readings[int(chosen)]
    others = [by_key[k] for k in keys if k != chosen]
    props = (
        fold_members(survivor, others, set()) if others else dict(survivor.get("properties") or {})
    )
    label = str(survivor.get("label") or node.label)
    entity_type = str(survivor.get("type") or "") or node.entity_type
    changed = (label, props, entity_type) != (
        node.label,
        dict(node.properties or {}),
        node.entity_type,
    )
    if (label, props) != (node.label, dict(node.properties or {})):
        repo.update_node(node_id, NodeUpdate(label=label, properties=props))
    return _NodeRebuild(changed=changed, home=home, entity_type=entity_type)


def _rebuild_edge(repo: GraphRepositoryProtocol, edge_id: str) -> tuple[str, str, str] | None:
    """Rebuild one surviving edge from its remaining readings: its new home and its endpoints."""
    edge = repo.get_edge(edge_id)
    if edge is None:
        return None
    readings = distinct_readings(member_readings(repo.edge_members(edge_id)), prefer=edge.source_id)
    if not readings:
        return None
    props: dict[str, Any] = dict(readings[0][1].get("properties") or {})
    for _, record, _n in readings[1:]:
        props = merge_edge_properties(props, dict(record.get("properties") or {}))
    if props != dict(edge.properties or {}):
        repo.update_edge(edge_id, EdgeUpdate(properties=props))
    # the validity window's start is its members' earliest date (a memory's valid_from);
    # a timeless member (a document) changes nothing, no dated member leaves it unset
    dates = [d for d in repo.source_valid_from(repo.edge_members(edge_id)).values() if d]
    valid_from = min(dates) if dates else None
    if valid_from != edge.valid_from:
        repo.set_edge_validity(
            edge_id,
            valid_from=valid_from,
            valid_to=edge.valid_to,
            closed_by_source_id=edge.closed_by_source_id,
        )
    # sources come in id order: the first reading's is the lowest remaining member
    # holding it, or the current home when the home wrote the same thing
    return readings[0][0], edge.source_node_id, edge.target_node_id


@dataclasses.dataclass(frozen=True)
class Rebuilt:
    """What :func:`rebuild_from_members` changed: the nodes whose text changed, and edge ends."""

    changed_node_ids: frozenset[str]
    endpoint_ids: frozenset[str]

    @property
    def resolve_node_ids(self) -> frozenset[str]:
        """The scope the identity resolution runs over after: the changed nodes and edge ends."""
        return self.changed_node_ids | self.endpoint_ids


def rebuild_from_members(
    repo: GraphRepositoryProtocol,
    node_ids: Iterable[str],
    edge_ids: Iterable[str],
    conventions: NameConventions,
) -> Rebuilt:
    """Rebuild each node and edge from the readings its members wrote, and re-home it.

    The one rebuild both a release (the survivors that lost a member) and a
    commit (the reused rows the committing source attests again,
    ``upsert_nodes_batch(reattested=)``) run, so a row's content is always the
    fold of its members' readings, whichever way the membership changed. A
    node is re-homed to the chosen reading's source with that reading's
    entity type (``rehome``, which also moves it to that source's template of
    the same name when there is one); an edge to its lowest member. Joins the
    caller's transaction: nothing here commits.
    """
    changed: set[str] = set()
    node_homes: dict[str, str] = {}
    entity_types: dict[str, str] = {}
    for node_id in sorted(set(node_ids)):
        rebuilt = _rebuild_node(repo, node_id, conventions)
        if rebuilt.changed:
            changed.add(node_id)
        if rebuilt.home is not None:
            node_homes[node_id] = rebuilt.home
        if rebuilt.entity_type:
            entity_types[node_id] = rebuilt.entity_type
    edge_homes: dict[str, str] = {}
    endpoints: set[str] = set()
    for edge_id in sorted(set(edge_ids)):
        rebuilt_edge = _rebuild_edge(repo, edge_id)
        if rebuilt_edge is not None:
            edge_homes[edge_id] = rebuilt_edge[0]
            endpoints.update(rebuilt_edge[1:])
    repo.rehome(node_homes, edge_homes, entity_types=entity_types)
    return Rebuilt(changed_node_ids=frozenset(changed), endpoint_ids=frozenset(endpoints))


def release_source_graph(
    repo: GraphRepositoryProtocol,
    source_id: str,
    *,
    sources: SourceStorageProtocol,
    database_name: str,
    settings: EngineSettings | None = None,
) -> ReleasedRows:
    """Release ``source_id`` from the graph and rebuild every survivor from its remaining members.

    See the module docstring. ``repo`` must be the caller's transaction
    repository (``get_transaction_graph_repository``): nothing here commits.
    The naming conventions are the graph's remaining sources' (:func:`graph_conventions`
    less ``source_id``, under ``settings``), read only when something survived.
    Survivors are rebuilt and re-homed by :func:`rebuild_from_members`.

    The scoped identity resolution over the rebuilt nodes and both ends of
    every surviving edge is not run here: it loads and plans the whole graph,
    and the release runs inside the caller's write transaction. The returned
    rows carry its scope and conventions (``resolve_node_ids``,
    ``conventions``) for :func:`refresh_released_graph`, which runs it after
    the commit as the post-commit identity pass does (#795). The fold and
    the re-home, the integrity guarantee, stay in the transaction.

    Returns:
        The release's rows. ``stale_node_ids`` are the nodes whose text changed.
    """
    released = repo.release_source(source_id)
    if not (released.touched_node_ids or released.touched_edge_ids):
        return released
    conventions = graph_conventions(repo, sources, database_name, settings, exclude=[source_id])
    rebuilt = rebuild_from_members(
        repo, released.touched_node_ids, released.touched_edge_ids, conventions
    )
    logger.info(
        "source_graph_released",
        source_id=source_id,
        nodes_deleted=len(released.deleted_node_ids),
        nodes_rebuilt=len(rebuilt.changed_node_ids),
        edges_rebuilt=len(released.touched_edge_ids),
    )
    return dataclasses.replace(
        released,
        stale_node_ids=rebuilt.changed_node_ids,
        resolve_node_ids=rebuilt.resolve_node_ids,
        conventions=conventions,
    )


# ---- After the commit ----


async def reembed_nodes(
    repo: GraphRepositoryProtocol,
    node_ids: Iterable[str],
    provider: EmbeddingProviderProtocol,
    *,
    search: SearchRepositoryProtocol | None = None,
) -> int:
    """Re-embed each node as it reads now (:func:`node_embedding_text`); the number embedded.

    The one re-embed the post-commit identity pass and :func:`refresh_released_graph`
    share. Nodes are read again by id, so a caller holding an older copy never
    embeds its old text; an id no longer in the graph is skipped. The vectors
    land on ``graph_nodes.embedding`` and, with ``search``, in the search
    index's node rows (full-text and vector, ``index_nodes_batch``).
    """
    ids = list(dict.fromkeys(node_ids))
    nodes = [n for n in (repo.get_node(i, include_embedding=False) for i in ids) if n]
    if not nodes:
        return 0
    result = await provider.batch_embed([node_embedding_text(n) for n in nodes])
    repo.update_node_embeddings_batch(
        dict(zip([n.id for n in nodes], result.embeddings, strict=True))
    )
    if search is not None:
        search.index_nodes_batch(
            [n for n in (repo.get_node(i, include_embedding=True) for i in ids) if n]
        )
    return len(nodes)


@dataclasses.dataclass(frozen=True)
class LazyEmbedder:
    """An embedding provider the post-release refresh builds only when something went stale.

    ``build`` makes (or hands over) the provider; a failure there (a cloud
    provider with no API key) is the refresh's logged, non-fatal failure,
    never the caller's. ``owned`` says the provider was built for this
    refresh alone: it is closed (``aclose``) once the refresh is done. A
    caller lending its long-lived provider (the CLI's) passes ``owned=False``.
    """

    build: Callable[[], EmbeddingProviderProtocol]
    owned: bool


type Embedder = EmbeddingProviderProtocol | LazyEmbedder | None
"""What a refresh re-embeds with: a provider, one built on demand, or none."""


async def _close(provider: Any) -> None:
    """Close an embedding provider built for the refresh; a failure is logged, never raised."""
    close = getattr(provider, "aclose", None)
    if close is None:
        return
    try:
        await close()
    except Exception:
        logger.exception("released_graph_embedder_close_failed_non_fatal")


def _resolve_released(
    repo: GraphRepositoryProtocol,
    released: ReleasedRows,
    search: SearchRepositoryProtocol | None,
) -> tuple[set[str], set[str]]:
    """The release's scoped identity resolution: (nodes merged away, nodes whose text it changed).

    ``resolve_graph`` over ``released.resolve_node_ids`` under
    ``released.conventions``, outside any transaction: each merge group takes
    the writer lock only for its own read and write (``write_locked``, in
    ``_apply_merges``) and commits on its own. Best-effort, logged: the
    release stands. On a failure partway the sets name the units that
    committed before it (``resolve_graph(report=)``), so the merged-away
    nodes of those groups still leave the index and their kept nodes are
    still re-embedded; until the #795 follow-up a failure returned nothing.
    """
    if not released.resolve_node_ids or released.conventions is None:
        return set(), set()
    report = ResolutionReport()
    try:
        resolve_graph(
            repo,
            conventions=released.conventions,
            search=search,
            scope=released.resolve_node_ids,
            report=report,
        )
    except Exception:
        logger.exception(
            "released_graph_resolution_failed_non_fatal",
            nodes=len(released.resolve_node_ids),
            committed_merges=len(report.removed_node_ids),
        )
    else:
        logger.info(
            "released_graph_resolved",
            nodes_merged=len(report.removed_node_ids),
            aliases_dropped=len(report.aliases_dropped),
            flagged=report.flagged,
        )
    return set(report.removed_node_ids), set(report.changed_node_ids)


async def refresh_released_graph(
    repo: GraphRepositoryProtocol,
    released: ReleasedRows,
    *,
    embedder: Embedder,
    search: SearchRepositoryProtocol | None,
) -> None:
    """After the release's transaction commits: resolution, search index and embeddings follow the graph.

    The one post-release step every release path runs (a guard in
    ``test_ci_config_guards.py`` holds each caller of a release to it):

    * the scoped identity resolution the release left for after the commit
      (:func:`_resolve_released`: alias drops, flags and merges over the
      rebuilt nodes and the ends of the surviving edges);
    * the deleted nodes' search rows go (``delete_nodes_batch``); the
      resolution's own index step (``apply_resolution(search=)``) takes the
      rows of the nodes it merged away and rewrites the full-text rows of the
      nodes it changed, and no other row;
    * every stale node is re-embedded (:func:`reembed_nodes`) and its search
      rows rewritten from the node as it reads now, or, with no embedding
      provider (none given, or a :class:`LazyEmbedder` that fails to build),
      its full-text row rewritten and the ids logged
      (``released_graph_not_reembedded``) for a later re-embed.

    A :class:`LazyEmbedder` is built only when a node went stale, and closed
    after when it is ``owned``. Only the rows named in ``released`` (and the
    resolution's) are touched, never the whole index. Best-effort: a failure
    is logged, never raised; the release stands.
    """
    merged_away, resolved = _resolve_released(repo, released, search)
    deleted = sorted(released.deleted_node_ids)
    stale = sorted((released.stale_node_ids | resolved) - merged_away)
    if search is not None and deleted:
        try:
            search.delete_nodes_batch(deleted)
        except Exception:
            logger.exception("released_graph_search_cleanup_failed_non_fatal", nodes=len(deleted))
    if not stale:
        return
    provider: EmbeddingProviderProtocol | None = None
    owned = False
    if isinstance(embedder, LazyEmbedder):
        try:
            provider, owned = embedder.build(), embedder.owned
        except Exception:
            logger.exception("released_graph_embedder_unavailable_non_fatal")
    else:
        provider = embedder
    try:
        if provider is not None:
            await reembed_nodes(repo, stale, provider, search=search)
            return
        logger.warning("released_graph_not_reembedded", node_ids=stale)
        if search is not None:
            search.index_nodes_batch(
                [n for n in (repo.get_node(i, include_embedding=True) for i in stale) if n]
            )
    except Exception:
        logger.exception("released_graph_refresh_failed_non_fatal", nodes=len(stale))
    finally:
        if owned:
            await _close(provider)


async def refresh_after_release(
    storage_adapter: SqliteAdapter,
    database_name: str,
    released: ReleasedRows,
    *,
    embedder: Embedder,
    search: SearchRepositoryProtocol | None,
) -> None:
    """:func:`refresh_released_graph` on the graph behind ``storage_adapter``: for the delete and re-extract callers."""
    from chaoscypher_core.repo_factories import get_transaction_graph_repository

    await refresh_released_graph(
        get_transaction_graph_repository(storage_adapter, database_name),
        released,
        embedder=embedder,
        search=search,
    )


def refresh_after_release_sync(
    storage_adapter: SqliteAdapter,
    database_name: str,
    released: ReleasedRows,
    *,
    embedder: Embedder,
    search: SearchRepositoryProtocol | None,
) -> None:
    """:func:`refresh_after_release` for a one-shot sync caller (a worker thread, a CLI command).

    Runs on a fresh event loop, closed after: a caller that keeps an event
    loop of its own for its providers (the CLI's processing service) runs
    :func:`refresh_after_release` on that loop instead, since a cached async
    client used on a closed loop fails. When this thread already runs a loop,
    the refresh runs on a helper thread under a copy of the caller's context,
    so the adapter's task-bound session (``session_scope``) is the caller's.
    """
    import asyncio
    import concurrent.futures
    import contextvars

    def run() -> None:
        asyncio.run(
            refresh_after_release(
                storage_adapter, database_name, released, embedder=embedder, search=search
            )
        )

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        run()
        return
    context = contextvars.copy_context()
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(context.run, run).result()


__all__ = [
    "Embedder",
    "LazyEmbedder",
    "Rebuilt",
    "graph_conventions",
    "graph_packs",
    "graph_source_ids",
    "rebuild_from_members",
    "reembed_nodes",
    "refresh_after_release",
    "refresh_after_release_sync",
    "refresh_released_graph",
    "release_source_graph",
]
