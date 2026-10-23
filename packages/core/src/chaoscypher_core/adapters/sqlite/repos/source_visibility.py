# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Which rows a switched-off source hides, and which name no source: one SQL reading for every reader.

A source set ``enabled = 0`` must not answer: none of its chunks or nodes
may reach a search arm, a scope count or a graph listing, scoped or not.
Until 2026-10-06 the graph listings left them out and both chunk arms of the
search did not, so a disabled source's chunks were in 120 of 120 benchmark
contexts (audit wave 6). Every reader builds its condition here:

- :func:`source_visible_sql`: the row may answer (it names no source, or a
  source not switched off). A source id with no ``sources`` row hides
  nothing: what hides a row is a source the user switched off.
- :func:`no_source_sql`: the row names no source, NULL and ``''`` alike.

A graph node or edge is attested by every source in its membership table
(``graph_node_sources`` / ``graph_edge_sources``); its ``source_id`` is only
its home, one member (#795, D7). Readers that mean "this source's rows" read
the membership, through one set of conditions built here:

- :func:`attested_visible_sql`: some member source is not switched off. A
  merged node homed at a disabled source stays visible while another member
  is enabled.
- :func:`attested_by_sql`: some member is in a list of sources.
- :func:`unattested_sql`: the row has no member and names no source (a manual
  or legacy row): what the column-only reading called "no source".
- :func:`in_source_scope_sql`: unattested, or attested by the scope.
- :func:`attestations_sql`: one ``(item_id, source_id)`` row per attestation,
  for the per-source counts (a merged node counts once under each member).
- :func:`template_visible_sql`: a template's home source is not switched off,
  or a visible node or edge uses it (templates have no membership).

A row with no membership row reads its ``source_id`` column exactly as the
column-only conditions do, so a NULL-source row keeps its NULL reading. The
ORM forms (:func:`visible`, :func:`attested`, :func:`attested_by`, :func:`in_source_scope`,
:func:`attestations`, :func:`member_ids_column`) wrap the same SQL text.

The Python forms are :func:`chaoscypher_core.ports.search.source_visible`,
:func:`chaoscypher_core.ports.search.has_no_source`,
:func:`chaoscypher_core.ports.search.item_visible` and
:func:`chaoscypher_core.ports.search.item_in_source_scope` (services read
those); a test pins each SQL form to its Python one.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from sqlalchemy import String, bindparam, column, inspect, literal_column, text

from chaoscypher_core.adapters.sqlite.models import (
    GraphEdge,
    GraphEdgeSource,
    GraphNode,
    GraphNodeSource,
    GraphTemplate,
)


if TYPE_CHECKING:
    from sqlalchemy.sql.elements import TextClause
    from sqlalchemy.sql.selectable import Subquery


SOURCES_TABLE = "sources"
"""The table whose ``enabled`` flag the predicate reads."""


def no_source_sql(source_column: str) -> str:
    """A SQL condition true when ``source_column`` names no source (NULL or ``''``).

    The SQL form of :func:`chaoscypher_core.ports.search.has_no_source`.
    """
    return f"coalesce({source_column}, '') = ''"


def source_hidden_sql(source_column: str) -> str:
    """A SQL condition true when ``source_column`` names a source the user switched off."""
    return (
        f"EXISTS (SELECT 1 FROM {SOURCES_TABLE} hidden_src "
        f"WHERE hidden_src.id = {source_column} AND hidden_src.enabled = 0)"
    )


def source_visible_sql(source_column: str) -> str:
    """A SQL condition true when the row whose source is ``source_column`` may answer.

    The SQL form of :func:`chaoscypher_core.ports.search.source_visible`:
    the row names no source, or one not switched off.
    """
    return f"NOT {source_hidden_sql(source_column)}"


ANY_SOURCE_HIDDEN_SQL = f"SELECT EXISTS (SELECT 1 FROM {SOURCES_TABLE} WHERE enabled = 0)"
"""Whether any source is switched off: a reader may skip the predicate when none is."""


MEMBERSHIP_TABLES: dict[str, tuple[str, str]] = {
    str(GraphNode.__tablename__): (str(GraphNodeSource.__tablename__), "node_id"),
    str(GraphEdge.__tablename__): (str(GraphEdgeSource.__tablename__), "edge_id"),
}
"""Each graph table's membership table and the column naming the row it attests (migration 0019)."""

MEMBER_SEPARATOR = "\x1f"
"""What :func:`member_ids_column` joins member ids with (no source id holds it)."""


def _membership(table: str, row: str, condition: str = "") -> str:
    """``EXISTS`` a membership row of ``row`` (a ``table`` row's name in the query), meeting ``condition``."""
    members, key = MEMBERSHIP_TABLES[table]
    where = f" AND {condition}" if condition else ""
    return f"EXISTS (SELECT 1 FROM {members} att WHERE att.{key} = {row}.id{where})"


def unattested_sql(table: str, row: str | None = None) -> str:
    """A SQL condition true when the ``table`` row has no member and names no source.

    ``row`` is the row's name in the query (an alias), ``table`` by default.
    """
    row = row or table
    return f"(NOT {_membership(table, row)} AND {no_source_sql(f'{row}.source_id')})"


def attested_visible_sql(table: str, row: str | None = None) -> str:
    """A SQL condition true when the ``table`` row may answer: a member source not switched off.

    A row with no member reads its ``source_id`` as :func:`source_visible_sql`
    does. The SQL form of :func:`chaoscypher_core.ports.search.item_visible`.

    Written for the planner, not per row: when no source is switched off
    (:data:`ANY_SOURCE_HIDDEN_SQL`, evaluated once) every row passes without a
    probe; otherwise the hidden rows are one uncorrelated set, built from the
    switched-off sources' attestations (those with no member left on), that
    each row is checked against. A per-row ``EXISTS`` on the membership was
    4x slower on a 200k-node count and 25x on a listing page.
    """
    row = row or table
    members, key = MEMBERSHIP_TABLES[table]
    off = f"(SELECT off_src.id FROM {SOURCES_TABLE} off_src WHERE off_src.enabled = 0)"
    hidden = (
        f"SELECT hid.item_id FROM ({attestations_sql(table)}) hid "
        f"WHERE hid.source_id IN {off} AND NOT EXISTS (SELECT 1 FROM {members} lit "
        f"WHERE lit.{key} = hid.item_id AND lit.source_id NOT IN {off})"
    )
    return f"(NOT ({ANY_SOURCE_HIDDEN_SQL}) OR {row}.id NOT IN ({hidden}))"


def template_visible_sql(row: str | None = None) -> str:
    """A SQL condition true when a template may be listed: its home source is not switched off, or a visible row uses it.

    Templates have no membership table: a merged node keeps the template its
    home source wrote, and a re-homed one keeps it too (#795). So a template
    homed at a switched-off source stays listed while a node or edge any
    enabled source attests still uses it, as that node or edge is listed.
    """
    row = row or str(GraphTemplate.__tablename__)
    used = " OR ".join(
        f"{row}.id IN (SELECT tu.template_id FROM {table} tu "
        f"WHERE {attested_visible_sql(table, 'tu')})"
        for table in MEMBERSHIP_TABLES
    )
    return f"(NOT ({ANY_SOURCE_HIDDEN_SQL}) OR {source_visible_sql(f'{row}.source_id')} OR {used})"


def attested_by_sql(table: str, in_list: str, row: str | None = None) -> str:
    """A SQL condition true when a source of ``in_list`` (``"(:a, :b)"`` or an expanding ``":ids"``) attests the row.

    A row with no member reads its ``source_id`` column (:func:`attestations_sql`).
    An ``IN`` over the attestations, not a per-row ``EXISTS``: SQLite drives it
    from the membership's ``source_id`` index into the primary key, so a
    scoped listing reads the scope's rows, not every row of the database
    (3.5 ms against 36 ms for one source of 100 in 200k nodes).
    """
    row = row or table
    return (
        f"{row}.id IN (SELECT scoped.item_id FROM ({attestations_sql(table)}) scoped "
        f"WHERE scoped.source_id IN {in_list})"
    )


def in_source_scope_sql(table: str, in_list: str, row: str | None = None) -> str:
    """A SQL condition true when the row is in a source scope: unattested, or attested by it.

    The SQL form of :func:`chaoscypher_core.ports.search.item_in_source_scope`.
    """
    return f"({unattested_sql(table, row)} OR {attested_by_sql(table, in_list, row)})"


def attestations_sql(table: str) -> str:
    """``SELECT item_id, source_id``: one row per source attesting a ``table`` row.

    Its membership rows, and the home of each row with none (that is not
    unattested). A row merged from two sources has two.
    """
    members, key = MEMBERSHIP_TABLES[table]
    return (
        f"SELECT att_m.{key} AS item_id, att_m.source_id AS source_id FROM {members} att_m "
        f"UNION ALL SELECT home.id AS item_id, home.source_id AS source_id FROM {table} home "
        f"WHERE NOT {no_source_sql('home.source_id')} AND NOT {_membership(table, 'home')}"
    )


# -- ORM forms ----------------------------------------------------------------


def _table_and_row(entity: Any) -> tuple[str, str]:
    """The table of a mapped class (or an ``aliased`` one) and its name in a query."""
    info = inspect(entity)
    table = info.mapper.local_table.name
    return table, (info.name if info.is_aliased_class else table)


def visible(entity: Any) -> TextClause:
    """``WHERE`` clause: the ``entity`` row may be listed.

    A node or edge by :func:`attested_visible_sql`, a template by
    :func:`template_visible_sql`.
    """
    table, row = _table_and_row(entity)
    if table == GraphTemplate.__tablename__:
        return text(template_visible_sql(row))
    return text(attested_visible_sql(table, row))


def _with_ids(sql_for: Any, entity: Any, source_ids: Iterable[str]) -> TextClause:
    """``sql_for``'s condition with the ids bound as one expanding parameter.

    The parameter is named after the row it reads, so the same clause compiles
    to the same SQL every call (the statement cache reuses it) and two rows of
    one statement (an edge's two endpoints) never share a name.
    """
    table, row = _table_and_row(entity)
    name = f"attested_ids_{row}"
    return text(sql_for(table, f":{name}", row)).bindparams(
        bindparam(name, value=sorted(set(source_ids)), expanding=True)
    )


def attested_by(entity: Any, source_ids: Iterable[str]) -> TextClause:
    """``WHERE`` clause: a source of ``source_ids`` attests the ``entity`` row (:func:`attested_by_sql`)."""
    return _with_ids(attested_by_sql, entity, source_ids)


def in_source_scope(entity: Any, source_ids: Iterable[str]) -> TextClause:
    """``WHERE`` clause: the ``entity`` row is in the scope (:func:`in_source_scope_sql`)."""
    return _with_ids(in_source_scope_sql, entity, source_ids)


def attested(entity: Any) -> TextClause:
    """``WHERE`` clause: some source attests the ``entity`` row (``NOT`` :func:`unattested_sql`)."""
    return text(f"NOT {unattested_sql(*_table_and_row(entity))}")


def attestations(entity: Any, name: str = "attested") -> Subquery:
    """:func:`attestations_sql` as a subquery with ``item_id`` and ``source_id`` columns."""
    table, _ = _table_and_row(entity)
    return (
        text(attestations_sql(table))
        .columns(column("item_id", String), column("source_id", String))
        .subquery(name)
    )


def member_ids_column(entity: Any) -> Any:
    """A select column: the row's member source ids joined by :data:`MEMBER_SEPARATOR` (NULL for none).

    Read back with :func:`split_member_ids`; the loads that hand nodes to the
    in-memory scope filters fill ``member_source_ids`` from it.
    """
    table, row = _table_and_row(entity)
    members, key = MEMBERSHIP_TABLES[table]
    return literal_column(
        f"(SELECT group_concat(mid.source_id, char(31)) FROM {members} mid "
        f"WHERE mid.{key} = {row}.id)",
        String,
    ).label("member_ids")


def split_member_ids(value: str | None) -> list[str]:
    """The member source ids a :func:`member_ids_column` value holds, sorted."""
    return sorted(value.split(MEMBER_SEPARATOR)) if value else []


__all__ = [
    "ANY_SOURCE_HIDDEN_SQL",
    "MEMBERSHIP_TABLES",
    "SOURCES_TABLE",
    "attestations",
    "attestations_sql",
    "attested",
    "attested_by",
    "attested_by_sql",
    "attested_visible_sql",
    "in_source_scope",
    "in_source_scope_sql",
    "member_ids_column",
    "no_source_sql",
    "source_hidden_sql",
    "source_visible_sql",
    "split_member_ids",
    "template_visible_sql",
    "unattested_sql",
    "visible",
]
