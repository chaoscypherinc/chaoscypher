# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Tag names follow the tag rule and are unique per database; sources gain a summary.

Revision ID: 0026
Revises: 0025
Create Date: 2026-11-03 12:00:00.000000+00:00

Document tags now follow one rule (``services.sources.tags``): trimmed, lower-case,
``^[a-z0-9][a-z0-9:_-]{0,63}$``. Before 0026 a tag name was stored as given, so a
database may hold ``"Work Docs"`` and ``"work docs"`` side by side.

The upgrade rewrites every name with :func:`rewrite_tag_name` (non-ASCII becomes a space
before lower-casing, so the Kelvin sign never turns into ``k``; lower-case; any run of
other characters becomes one ``-``; leading ``:_-`` and trailing ``-`` dropped; cut to 64;
``"tag"`` when nothing is left). Tags whose rewritten names meet in one database merge
into the oldest (``created_at``, then ``id``), which keeps its own colour and
description; the others' assignments move to it (a document that has both keeps one).
Assignments whose source or tag row is gone are deleted: the runner applies with foreign
keys off, and the rebuilt foreign key must hold. Then ``(database_name, name)`` becomes
unique, ``source_tag_assignments.source_id`` turns ``ON DELETE CASCADE`` (SQLite cannot
alter a foreign key, so the table is rebuilt as 0019 rebuilds its homed tables), and
``sources.summary`` (a one-line, model-written summary) is added.

When any name would change, the migration holds for the owner's confirmation and
:func:`cc_hold_preview` lists each rename and merge; on a database whose names already
follow the rule it applies as usual. It writes row values, so ``needs_confirmation``.
The ``tag_id`` index the spec asks for is already in the baseline
(``ix_source_tag_assignments_tag_id``).

The rule is frozen here on purpose (``TAG_PATTERN``, ``TAG_MAX_LENGTH``, the trim and
lower-case, the replacement class), not imported from ``services.sources.tags``: a
migration is a record of what it did, and a later change to the live rule must not
change it. ``test_0026_source_tag_names`` pins every rewrite against the live rule.

The downgrade drops ``summary``, puts the foreign key back without ``ON DELETE`` and
drops the unique index; renames and merges are not reversed.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import NamedTuple

import sqlalchemy as sa
from alembic import op


# Revision identifiers, used by Alembic.
revision: str = "0026"
down_revision: str | Sequence[str] | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# ChaosCypher migration metadata — consumed by the runner's tier classifier.
# Tier values: "safe_auto" | "needs_confirmation" | "manual"
CC_TIER: str = "needs_confirmation"
CC_DESCRIPTION: str = (
    "Tag names become lower-case and follow the tag rule (renaming or merging any that "
    "do not); a tag name is unique within its database; a document's tag assignments "
    "are deleted with it; documents gain a one-line summary"
)

_UNIQUE_NAME = "uq_source_tags_database_name_name"
_ASSIGNMENTS = "source_tag_assignments"
_SOURCE_FK = f"fk_{_ASSIGNMENTS}_source_id_sources"
#: Names the unnamed baseline foreign keys so batch mode can drop one (SQLite reflects them nameless).
_FK_NAMES = {"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"}

# The tag rule as 0026 applies it, frozen (see the module docstring).
#: A valid tag name, after trimming and lower-casing.
TAG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9:_-]{0,63}$")
#: The longest valid tag name.
TAG_MAX_LENGTH = 64
#: A run of characters a tag name may not hold; each run becomes ``-``.
_NOT_ALLOWED = re.compile(r"[^a-z0-9:_-]+")
#: A run of non-ASCII characters; becomes a space before lower-casing, so it joins the
#: ``_NOT_ALLOWED`` run around it and folds into one ``-``.
_NON_ASCII = re.compile(r"[^\x00-\x7f]+")
_EMPTY_NAME = "tag"


class TagRow(NamedTuple):
    """One ``source_tags`` row as the plan reads it."""

    id: str
    database_name: str
    name: str
    created_at: str


class TagNamePlan(NamedTuple):
    """What the upgrade does to tag names, and the owner's lines for it.

    A ``NamedTuple``, not a dataclass: Alembic loads this module without registering it
    in ``sys.modules``, which ``@dataclass`` needs to resolve its annotations.
    """

    renames: dict[str, str]
    """Keeper tag id → its new name (only keepers whose name changes)."""
    merges: dict[str, str]
    """Merged tag id → the keeper tag id it merges into."""
    lines: list[str]
    """One plain line per rename and merge, for the hold preview."""


def rewrite_tag_name(name: str) -> str:
    """``name`` as the tag rule accepts it: trimmed and lower-cased, rewritten only when it must be.

    Non-ASCII becomes a space before lower-casing (``"\u212a".lower()`` is ``"k"``). A
    space never matches the rule, so a name that held any always goes through the
    rewrite, where the space folds into the run of other characters around it.
    """
    new = _NON_ASCII.sub(" ", name.strip()).lower()
    if TAG_PATTERN.fullmatch(new):
        return new
    new = _NOT_ALLOWED.sub("-", new).lstrip(":_-").rstrip("-")
    new = new[:TAG_MAX_LENGTH].rstrip("-")
    return new or _EMPTY_NAME


def plan_tag_names(rows: Sequence[TagRow]) -> TagNamePlan:
    """Group ``rows`` by database and rewritten name; the oldest of each group keeps it.

    The keeper (oldest by ``created_at``, then ``id``) is renamed when its name changes;
    every other row of the group merges into it. Lines are sorted by database, new name,
    ``created_at`` and ``id``, and name the database when the rows span several.
    """
    groups: dict[tuple[str, str], list[TagRow]] = {}
    for row in rows:
        groups.setdefault((row.database_name, rewrite_tag_name(row.name)), []).append(row)
    several = len({row.database_name for row in rows}) > 1
    plan = TagNamePlan(renames={}, merges={}, lines=[])
    for (database_name, new), members in sorted(groups.items()):
        keeper, *merged = sorted(members, key=lambda r: (r.created_at, r.id))
        suffix = f' in database "{database_name}"' if several else ""
        if keeper.name != new:
            plan.renames[keeper.id] = new
            plan.lines.append(f'"{keeper.name}" → "{new}"{suffix}')
        for row in merged:
            plan.merges[row.id] = keeper.id
            plan.lines.append(f'"{row.name}" → "{new}" (merged){suffix}')
    return plan


def _tag_rows(conn: sa.Connection) -> list[TagRow]:
    result = conn.execute(sa.text("SELECT id, database_name, name, created_at FROM source_tags"))
    return [TagRow(r.id, r.database_name, r.name, str(r.created_at)) for r in result]


def cc_hold_preview(conn: sa.Connection) -> list[str]:
    """Each tag rename and merge the upgrade would make (``source_tags`` is a baseline table)."""
    return plan_tag_names(_tag_rows(conn)).lines


def _apply(conn: sa.Connection, plan: TagNamePlan) -> None:
    """Merge, then rename, then delete the assignments whose source or tag is gone."""
    for merged, keeper in plan.merges.items():
        params = {"keeper": keeper, "merged": merged}
        conn.execute(
            sa.text(f"UPDATE OR IGNORE {_ASSIGNMENTS} SET tag_id = :keeper WHERE tag_id = :merged"),
            params,
        )
        conn.execute(sa.text(f"DELETE FROM {_ASSIGNMENTS} WHERE tag_id = :merged"), params)
        conn.execute(sa.text("DELETE FROM source_tags WHERE id = :merged"), params)
    for tag_id, new in plan.renames.items():
        conn.execute(
            sa.text("UPDATE source_tags SET name = :name WHERE id = :id"),
            {"name": new, "id": tag_id},
        )
    conn.execute(
        sa.text(f"DELETE FROM {_ASSIGNMENTS} WHERE source_id NOT IN (SELECT id FROM sources)")
    )
    conn.execute(
        sa.text(f"DELETE FROM {_ASSIGNMENTS} WHERE tag_id NOT IN (SELECT id FROM source_tags)")
    )


def _source_fk(ondelete: str | None) -> None:
    """Rebuild ``source_tag_assignments`` with its ``source_id`` foreign key on ``ondelete``."""
    with op.batch_alter_table(_ASSIGNMENTS, naming_convention=_FK_NAMES) as batch_op:
        batch_op.drop_constraint(_SOURCE_FK, type_="foreignkey")
        batch_op.create_foreign_key(_SOURCE_FK, "sources", ["source_id"], ["id"], ondelete=ondelete)


def upgrade() -> None:
    """Fix the names, make them unique per database, cascade on source delete, add summary."""
    conn = op.get_bind()
    _apply(conn, plan_tag_names(_tag_rows(conn)))
    op.create_index(_UNIQUE_NAME, "source_tags", ["database_name", "name"], unique=True)
    _source_fk("CASCADE")
    op.add_column("sources", sa.Column("summary", sa.Text(), nullable=True))


def downgrade() -> None:
    """Drop the summary, the cascade and the unique index; names stay as renamed."""
    with op.batch_alter_table("sources") as batch_op:
        batch_op.drop_column("summary")
    _source_fk(None)
    op.drop_index(_UNIQUE_NAME, table_name="source_tags")
