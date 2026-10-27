# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""The seed of a memory's KNOWN ENTITIES list: the graph's nodes it should reuse, then the speaker.

A memory is one chunk, so the running list the document path builds group by group would
be empty for it. :func:`build_seed` reads the graph instead (spec 2026-10-20 decision 2,
spec 2026-10-24 § 4.3), in this order and within ``extraction.known_entities_limit``:

1. the top hit of each ``about`` name;
2. the nodes the keyword index returns for the text;
3. for a scoped memory, the nodes the scope's other memories attest, most attested first;
4. for a scoped memory, the nodes ``mcp.memory.scope_nodes`` names for its scope;

then the speaker, so "I", a bare first name and "the journal" land on the nodes the graph
already has. The text's own names always come before the scope's; the scope's share of
the limit keeps room for its named nodes, so a scope with many nodes never crowds them out.
The seed also carries what the scope offers beyond names (:class:`MemorySeed`): the
templates its scope nodes use (D26), and the scope nodes a person made, which the commit
attaches the memory to (``services/graph/memory/offered_nodes.py``).

The memory reads every memory tool shares (``all_sources``, ``memory_rows``,
``node_by_label``) live here, so the seed reads them without importing ``memory.py``.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.constants import SYSTEM_TEMPLATE_IDS, is_system_template_id
from chaoscypher_core.models import MEMORY_SOURCE_TYPE
from chaoscypher_core.services.graph.memory.offered_nodes import attachable_node_ids
from chaoscypher_core.services.sources.engine.deduplication.name_identity import fold
from chaoscypher_core.services.sources.engine.extraction.domains.core_types import CORE_TYPES
from chaoscypher_core.services.sources.engine.extraction.utils.offered_types import OfferedType


if TYPE_CHECKING:
    from chaoscypher_core.bootstrap import Engine
    from chaoscypher_core.models import Node, Template

logger = structlog.get_logger(__name__)

SPEAKER_ROLE = "the speaker (I, me)"
"""The role phrase the speaker's line carries, so the extractor reads "I" as that person."""
SOURCE_PAGE = 1000
"""The most sources a memory tool reads in one page (a scope's memories, every document)."""

_LOGGED_MISSING: set[tuple[str, str, str]] = set()
"""The ``(event, scope, name)`` misses logged so far: each is logged once per process."""


def all_sources(engine: Engine, source_type: str | None = None) -> list[dict[str, Any]]:
    """Every source row (of ``source_type`` when given), paged through: no memory is ever out of reach."""
    rows: list[dict[str, Any]] = []
    page = 1
    while True:
        batch, total = engine.storage_adapter.list_sources(
            page=page, page_size=SOURCE_PAGE, source_type=source_type
        )
        rows.extend(batch)
        if not batch or len(rows) >= total:
            return rows
        page += 1


def memory_rows(engine: Engine, scope: str | None = None) -> list[dict[str, Any]]:
    """The memory source rows (every memory tool's and the pack export's read), newest first by ``valid_from`` then ``created_at``; ``scope`` filters."""
    rows = all_sources(engine, MEMORY_SOURCE_TYPE)
    if scope is not None:
        rows = [r for r in rows if r.get("memory_scope") == scope]
    return sorted(
        rows,
        key=lambda r: (str(r.get("valid_from") or ""), str(r.get("created_at") or "")),
        reverse=True,
    )


def node_by_label(engine: Engine, name: str) -> Node | None:
    """The node whose label is ``name`` (case and accents folded), or None.

    A node with no type (one a person made) is preferred to a typed one of the same
    name: a memory's own twin of a person's node never hides it.
    """
    hits = engine.search_repository.keyword_search(name, limit=10, item_type="node")
    ids = [h for h, _ in hits]
    if not ids:
        return None
    wanted = fold(name)
    nodes = engine.graph_repository.get_nodes_batch(ids, include_embedding=False)
    matches = [n for n in nodes if fold(n.label) == wanted]
    return next((n for n in matches if not n.entity_type), matches[0] if matches else None)


class ScopeSetting(StrEnum):
    """The settings that name a scope's nodes and templates: what a miss is logged under.

    An enum value is a fixed name, so a log inside ``private_text`` keeps it, while the
    scope and the names (a person's words) are withheld above DEBUG.
    """

    NODES = "mcp.memory.scope_nodes"
    TEMPLATES = "mcp.memory.scope_templates"


def log_missing_once(event: str, setting: ScopeSetting, scope: str, names: Sequence[str]) -> None:
    """Log the names a scope's setting lists that match nothing: each once per process.

    One warning per call for the names not logged before: the setting and their count
    (kept inside ``private_text``), the scope and the names (withheld there above DEBUG).
    """
    fresh = [n for n in dict.fromkeys(names) if (event, scope, n) not in _LOGGED_MISSING]
    if not fresh:
        return
    _LOGGED_MISSING.update((event, scope, n) for n in fresh)
    logger.warning(event, setting=setting, missing_count=len(fresh), scope=scope, labels=fresh)


def _record(node: Node) -> dict[str, Any]:
    """A node as the shape ``KnownEntities.add_group`` reads."""
    aliases = node.properties.get("aliases") if isinstance(node.properties, dict) else None
    return {
        "name": node.label,
        "type": node.entity_type or "",
        "aliases": [str(a) for a in aliases or [] if a],
        "properties": {},
    }


def speaker_record(by: str) -> dict[str, Any]:
    """The speaker's record: a Person carrying :data:`SPEAKER_ROLE`."""
    return {"name": by, "type": "Person", "aliases": [], "properties": {"role": SPEAKER_ROLE}}


@dataclass
class _ScopePart:
    """Steps 3 and 4 of one seed: the nodes they add, and those already in it by name."""

    seen: set[str]
    """Folded names taken so far (the seed's, then each added node's)."""
    present: Collection[str] = ()
    """Ids of the nodes steps 1 and 2 put in the seed."""
    added: list[Node] = field(default_factory=list)
    again: list[Node] = field(default_factory=list)
    """Nodes the scope steps found that steps 1 and 2 had offered already: scope nodes too."""

    def take(self, node: Node) -> bool:
        """Add ``node`` unless its name is taken (then note it when the seed holds it)."""
        if fold(node.label) in self.seen:
            if node.id in self.present and all(n.id != node.id for n in self.again):
                self.again.append(node)
            return False
        self.seen.add(fold(node.label))
        return True


def _named_nodes(engine: Engine, scope: str, limit: int, part: _ScopePart) -> list[Node]:
    """Step 4: the nodes ``mcp.memory.scope_nodes`` names for ``scope``, at most ``limit``."""
    nodes: list[Node] = []
    missing: list[str] = []
    for label in engine.settings.mcp.memory.scope_nodes.get(scope, []):
        if len(nodes) >= limit:
            break
        node = node_by_label(engine, label)
        if node is None:
            missing.append(label)
        elif part.take(node):
            nodes.append(node)
    log_missing_once("memory_scope_node_missing", ScopeSetting.NODES, scope, missing)
    return nodes


def _scope_part(
    engine: Engine,
    scope: str,
    part: _ScopePart,
    *,
    exclude_source: str | None,
    limit: int,
) -> _ScopePart:
    """Fill ``part`` with steps 3 and 4 within ``limit``: named nodes kept first."""
    if limit <= 0:
        return part
    named = _named_nodes(engine, scope, limit, part)
    room = limit - len(named)
    attested: list[Node] = []
    ids = [r["id"] for r in memory_rows(engine, scope) if r["id"] != exclude_source]
    if room > 0 and ids:
        graph = engine.graph_repository
        # room plus the names already taken: the room fills unless several attested
        # nodes share one taken name (then a few fewer are offered; never more than the room)
        attested_ids = graph.nodes_attested_by(ids, room + len(part.seen))
        fetched = (
            graph.get_nodes_batch(attested_ids, include_embedding=False) if attested_ids else []
        )
        by_id = {n.id: n for n in fetched}
        for node_id in attested_ids:
            node = by_id.get(node_id)
            if node is not None and part.take(node):
                attested.append(node)
                if len(attested) >= room:
                    break
    part.added = [*attested, *named]
    return part


def scope_nodes(
    engine: Engine,
    scope: str | None,
    *,
    exclude_source: str | None = None,
    limit: int | None = None,
    skip: Collection[str] = (),
) -> list[Node]:
    """Steps 3 and 4 of the seed: the scope's attested nodes, then its named ones.

    Args:
        engine: The engine of the memory's database.
        scope: The memory's scope; None has no scope nodes.
        exclude_source: The memory being linked, whose own memberships do not count.
        limit: The most nodes returned (default ``extraction.known_entities_limit``);
            the named nodes are kept first, the attested ones fill the rest.
        skip: Folded names already in the seed, left out here.

    Returns:
        The attested nodes, most attested first, then the named nodes; no folded name twice.
    """
    if scope is None:
        return []
    cap = engine.settings.extraction.known_entities_limit if limit is None else limit
    part = _ScopePart(set(skip))
    return _scope_part(engine, scope, part, exclude_source=exclude_source, limit=cap).added


def _template_type(template: Template) -> OfferedType:
    """A node template as an offered type."""
    return OfferedType(template.name, template.description or "", template.id)


_CORE_NAMES = frozenset(fold(t) for t in CORE_TYPES)


def _offerable(template: Template | None) -> bool:
    """Whether a template may be offered to a scoped memory (D26).

    Not a shipped one; not one a source owns (a per-source template, an import's: the
    source's own); not one named like a core type, which the prompt lists already and
    whose entities the commit types itself.
    """
    return (
        template is not None
        and template.template_type == "node"
        and not is_system_template_id(template.id)
        and template.id not in SYSTEM_TEMPLATE_IDS
        and template.source_id is None
        and fold(template.name) not in _CORE_NAMES
    )


def _named_templates(engine: Engine, scope: str) -> list[OfferedType]:
    """The node templates ``mcp.memory.scope_templates`` names for ``scope``, in its order.

    A name matches an offerable template (:func:`_offerable`) whose name folds equal
    (several: the lowest id); a name that matches none is skipped and logged once.
    """
    names = engine.settings.mcp.memory.scope_templates.get(scope, [])
    if not names:
        return []
    by_name: dict[str, Template] = {}
    for template in sorted(
        engine.graph_repository.list_templates(template_type="node"), key=lambda t: t.id
    ):
        if _offerable(template):
            by_name.setdefault(fold(template.name), template)
    offered: list[OfferedType] = []
    missing: list[str] = []
    for name in names:
        match = by_name.get(fold(name))
        if match is None:
            missing.append(name)
            continue
        offered.append(_template_type(match))
    log_missing_once("memory_scope_template_missing", ScopeSetting.TEMPLATES, scope, missing)
    return offered


def scope_templates(engine: Engine, scope: str | None, nodes: Sequence[Node]) -> list[OfferedType]:
    """The templates a scoped memory is offered as known types (D26): memory sources only.

    The templates ``nodes`` use (the scope nodes its seed offers), then the ones
    ``mcp.memory.scope_templates`` names; only offerable ones (:func:`_offerable`); the
    first per folded name wins. Empty for no scope.
    """
    if scope is None:
        return []
    graph = engine.graph_repository
    offered: list[OfferedType] = []
    for template_id in dict.fromkeys(n.template_id for n in nodes if n.template_id):
        template = graph.get_template(template_id)
        if template is not None and _offerable(template):
            offered.append(_template_type(template))
    offered.extend(_named_templates(engine, scope))
    seen: set[str] = set()
    unique: list[OfferedType] = []
    for offered_type in offered:
        if fold(offered_type.name) not in seen:
            seen.add(fold(offered_type.name))
            unique.append(offered_type)
    return unique


@dataclass(frozen=True)
class MemorySeed:
    """What a memory's extraction is offered from the graph, read once per link."""

    nodes: list[Node]
    """Every seed node, in the module's order (the speaker is added by :meth:`records`)."""
    scope_nodes: list[Node] = field(default_factory=list)
    """Those of :attr:`nodes` the scope steps (3 and 4) offer, the ones steps 1 and 2 had
    offered already included."""
    templates: list[OfferedType] = field(default_factory=list)
    """The scope's templates (:func:`scope_templates`)."""

    def records(self, by: str) -> list[dict[str, Any]]:
        """The KNOWN ENTITIES records, the speaker's last."""
        return records_of(self.nodes, by)

    @property
    def attach_ids(self) -> list[str]:
        """The scope nodes a person made: the commit attaches the memory's twin of one to it."""
        return attachable_node_ids(self.scope_nodes)


def build_seed(
    engine: Engine,
    text: str,
    about: Sequence[str],
    by: str,
    *,
    scope: str | None = None,
    exclude_source: str | None = None,
) -> MemorySeed:
    """The seed of a memory ``text`` said by ``by``, ``about`` the named people.

    At most ``extraction.known_entities_limit - 1`` nodes, leaving room for the speaker;
    none whose folded name is another's or the speaker's. ``scope`` adds the scope's
    nodes (``exclude_source``: the memory being linked), and its templates.
    """
    limit = engine.settings.extraction.known_entities_limit
    room = max(0, limit - 1)
    search = engine.search_repository
    hits: list[str] = []
    for name in about:
        hits.extend(h for h, _ in search.keyword_search(name, limit=1, item_type="node"))
    if text.strip():
        hits.extend(h for h, _ in search.keyword_search(text, limit=limit, item_type="node"))
    ids = list(dict.fromkeys(hits))
    by_id = {
        n.id: n
        for n in (
            engine.graph_repository.get_nodes_batch(ids, include_embedding=False) if ids else []
        )
    }
    nodes: list[Node] = []
    seen = {fold(by)}
    for node_id in ids:
        node = by_id.get(node_id)
        if node is None or fold(node.label) in seen:
            continue
        seen.add(fold(node.label))
        nodes.append(node)
    nodes = nodes[:room]
    if scope is None:
        return MemorySeed(nodes)
    part = _scope_part(
        engine,
        scope,
        _ScopePart(seen, present={n.id for n in nodes}),
        exclude_source=exclude_source,
        limit=room - len(nodes),
    )
    offered = [*part.again, *part.added]
    return MemorySeed([*nodes, *part.added], offered, scope_templates(engine, scope, offered))


def records_of(nodes: Sequence[Node], by: str) -> list[dict[str, Any]]:
    """The seed records of ``nodes`` (:attr:`MemorySeed.nodes`), the speaker's last."""
    return [*(_record(n) for n in nodes), speaker_record(by)]
