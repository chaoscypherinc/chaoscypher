# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Migration up/down/up roundtrip tests.

For every reversible migration N (i.e. ``down_revision`` is not None and
``N`` is not listed in :data:`KNOWN_IRREVERSIBLE`), assert that

    upgrade to N → snapshot → downgrade to N-1 → upgrade to N → snapshot

produces identical ``sqlite_master`` snapshots. Catches subtle
reversibility bugs: column-type drift after re-upgrade, indexes the
downgrade forgets to drop, default-value rendering changes, etc.

Forward-only path is covered by ``test_runner.py``. This file is the only
place the downgrade path is exercised under test.

After the 2026-06-02 squash the script directory holds a single revision
(the 0001 baseline, which has no ``down_revision``), so the parametrized
roundtrip collects nothing today. It auto-reparametrizes — and starts
exercising real down→up roundtrips again — as soon as new migrations land
on top of the baseline.
"""

from __future__ import annotations

import re
import sqlite3
from importlib import resources
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from chaoscypher_core.adapters.sqlite.engine import evict_engine
from chaoscypher_core.database.migrations.runner import (
    current_revision,
    downgrade_to,
    upgrade_to,
)


# Migrations whose ``downgrade()`` is intentionally lossy and raises
# ``NotImplementedError``. Roundtripping them is impossible by design —
# production rollback past these requires restoring from backup.
#
# Empty after the 2026-06-02 migration squash: the only lossy downgrade
# (revision 0013's template reconciliation) was collapsed into the single
# 0001 baseline, whose ``downgrade()`` is a guarded no-op rather than a
# ``NotImplementedError`` (baseline-floor semantics; see test_runner.py).
# Add an entry back here when a future migration ships a downgrade that
# raises ``NotImplementedError``.
KNOWN_IRREVERSIBLE: frozenset[str] = frozenset()


# Reversible-in-principle migrations whose roundtrip currently fails for
# pre-existing reasons. Each entry's value is the reason surfaced in
# pytest xfail output. Marked ``strict=False`` so a future fix that makes
# an entry pass surfaces as XPASS rather than a hard failure.
#
# This set was empty after the migration-reversibility audit landed —
# every previously-flagged drift was either a real downgrade bug (fixed
# in the migration file) or a cosmetic-only difference now neutralized
# by :func:`_normalize_sql`. Add a revision back here only if a future
# migration introduces a roundtrip failure that genuinely can't be fixed
# at the migration site.
KNOWN_DRIFT: dict[str, str] = {}


def _script_directory() -> ScriptDirectory:
    pkg = resources.files("chaoscypher_core.database.migrations")
    cfg = Config(str(Path(str(pkg / "alembic.ini"))))
    return ScriptDirectory.from_config(cfg)


def _reversible_revisions_oldest_first() -> list[pytest.param]:
    """Revisions to parametrize the roundtrip test over, oldest first.

    Excludes the baseline (no ``down_revision`` to test against) and
    every entry in :data:`KNOWN_IRREVERSIBLE`. Migrations listed in
    :data:`KNOWN_DRIFT` are emitted with an ``xfail(strict=False)``
    marker so the test still runs and surfaces the failure reason.
    """
    script = _script_directory()
    oldest_first = list(reversed([r.revision for r in script.walk_revisions()]))
    params: list[pytest.param] = []
    for rev in oldest_first:
        if script.get_revision(rev).down_revision is None:
            continue
        if rev in KNOWN_IRREVERSIBLE:
            continue
        if rev in KNOWN_DRIFT:
            params.append(
                pytest.param(
                    rev,
                    marks=pytest.mark.xfail(
                        strict=False,
                        reason=KNOWN_DRIFT[rev],
                    ),
                )
            )
        else:
            params.append(pytest.param(rev))
    return params


def _down_revision_of(revision: str) -> str:
    script = _script_directory()
    down = script.get_revision(revision).down_revision
    assert down is not None, f"{revision!r} has no down_revision (baseline?)"
    if isinstance(down, tuple):
        msg = f"Multi-parent migration {revision!r} not supported by roundtrip test"
        raise AssertionError(msg)
    return down


_WHITESPACE_RUN = re.compile(r"\s+")
# Matches ``create table foo`` or ``create table "foo"`` — the table name
# capture excludes whitespace, the opening paren of the body, and the
# optional surrounding double quotes that Alembic's batch rebuild adds.
_CREATE_TABLE_HEAD = re.compile(r'(create table)\s+"?([^\s"(]+)"?')
# Matches the column list of a ``FOREIGN KEY (...)`` clause and the
# referenced table name following ``REFERENCES``. The key (cols, target)
# is used to dedupe an anonymous FK against a named one on the same columns.
_FK_KEY = re.compile(r"foreign key\s*\(([^)]+)\)\s+references\s+(\S+)")

# Top-level CREATE TABLE clauses that are table constraints rather than
# column definitions. Used to split ``columns`` from ``constraints`` so
# constraints can be sorted/deduped independently.
_CONSTRAINT_PREFIXES: tuple[str, ...] = (
    "primary key",
    "unique ",
    "constraint ",
    "foreign key",
    "check ",
)


def _split_top_level(body: str) -> list[str]:
    """Split a CREATE TABLE body on top-level commas only.

    A naive ``body.split(",")`` would slice inside ``REFERENCES table(a, b)``
    — paren-depth tracking is needed to keep multi-column refs intact.
    """
    parts: list[str] = []
    depth = 0
    buf: list[str] = []
    for ch in body:
        if ch == "(":
            depth += 1
            buf.append(ch)
        elif ch == ")":
            depth -= 1
            buf.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    if buf:
        parts.append("".join(buf).strip())
    return parts


def _normalize_create_table(sql: str) -> str:
    """Canonicalize a CREATE TABLE statement so cosmetic drift compares equal.

    Three classes of cosmetic difference are neutralized:

    1. **Table-name quoting**: ``create table foo`` ≡ ``create table "foo"``.
       Alembic's batch rebuild always emits the name quoted; the baseline
       does not.
    2. **Constraint ordering**: Alembic's batch rebuild reorders FK
       declarations relative to the source. Sorting the constraint list
       alphabetically makes the comparison order-independent.
    3. **Duplicate FK declarations**: when the baseline declares an
       anonymous ``FOREIGN KEY(col) REFERENCES tbl`` and a later
       migration adds a named version on the same column → target, the
       SQLite ``CREATE TABLE`` text carries both. Alembic's batch rebuild
       collapses them into one (the named one is preserved, the anonymous
       one is reflected away). Drop unnamed FKs that overlap a named FK
       on the same ``(from_cols, to_table)`` to match.

    Bracketed alongside :data:`KNOWN_DRIFT`, which should remain empty in
    steady state.
    """
    head_match = _CREATE_TABLE_HEAD.match(sql)
    if not head_match:
        return sql
    prefix = f"{head_match.group(1)} {head_match.group(2)}"

    open_paren = sql.find("(")
    close_paren = sql.rfind(")")
    if open_paren == -1 or close_paren == -1:
        return sql
    body = sql[open_paren + 1 : close_paren]
    suffix = sql[close_paren + 1 :].strip()  # WITHOUT ROWID, AUTOINCREMENT, etc.

    clauses = _split_top_level(body)
    columns: list[str] = []
    constraints: list[str] = []
    for clause in clauses:
        lower = clause.lstrip().lower()
        if any(lower.startswith(prefix_kw) for prefix_kw in _CONSTRAINT_PREFIXES):
            constraints.append(clause)
        else:
            columns.append(clause)

    # Build the set of (from_cols, to_table) tuples covered by a NAMED FK.
    # Anonymous FKs matching any of these are duplicates and removed.
    named_fk_keys: set[tuple[str, str]] = set()
    for clause in constraints:
        lower = clause.lower()
        if lower.startswith("constraint ") and "foreign key" in lower:
            match = _FK_KEY.search(lower)
            if match:
                named_fk_keys.add((match.group(1).strip(), match.group(2).strip()))

    deduped: list[str] = []
    for clause in constraints:
        lower = clause.lower().strip()
        if lower.startswith("foreign key"):
            match = _FK_KEY.search(lower)
            if match and (match.group(1).strip(), match.group(2).strip()) in named_fk_keys:
                continue
        deduped.append(clause)

    constraints_sorted = sorted(deduped, key=str.lower)
    rebuilt = f"{prefix} ( {', '.join(columns + constraints_sorted)} )"
    if suffix:
        rebuilt = f"{rebuilt} {suffix}"
    return rebuilt


def _normalize_sql(sql: str) -> str:
    """Lowercase, collapse whitespace, strip trailing semicolons.

    Schema equality after roundtrip is checked against this normalized
    form so superficial rendering differences (e.g. an extra newline
    Alembic introduces during ``op.batch_alter_table``) don't trip a
    real-drift assertion. ``CREATE TABLE`` statements get additional
    structural canonicalization via :func:`_normalize_create_table` to
    neutralize Alembic-vs-baseline table-name quoting, FK ordering, and
    legacy anonymous/named FK duplication.
    """
    collapsed = _WHITESPACE_RUN.sub(" ", sql.lower()).rstrip(";").strip()
    if collapsed.startswith("create table"):
        return _normalize_create_table(collapsed)
    return collapsed


SchemaRow = tuple[str, str, str, str]


def _schema_snapshot(db_path: Path) -> list[SchemaRow]:
    """Sorted ``(type, name, tbl_name, normalized_sql)`` rows from sqlite_master.

    Excludes ``alembic_version`` (the version stamp itself, which moves
    during the test) and SQLite-internal rows (``sqlite_%``).
    """
    rows: list[SchemaRow] = []
    with sqlite3.connect(str(db_path)) as conn:
        cur = conn.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master "
            "WHERE name != 'alembic_version' AND name NOT LIKE 'sqlite_%'"
        )
        for type_, name, tbl_name, sql in cur:
            rows.append((type_, name, tbl_name, _normalize_sql(sql or "")))
    return sorted(rows)


@pytest.mark.parametrize("revision", _reversible_revisions_oldest_first())
def test_migration_roundtrip(tmp_path: Path, revision: str) -> None:
    """Each reversible migration: up → snapshot → down → up → snapshot ≡."""
    db_path = tmp_path / "test.db"
    sqlite3.connect(str(db_path)).close()  # Create empty file.

    # Engine cache is module-global; tmp_path makes the key unique per test
    # but stale Engine objects accumulate across the parametrized run and
    # have been observed to introduce order-dependent flakiness in earlier
    # iterations. Evict at the end so each test starts from a clean cache.
    try:
        upgrade_to(db_path, revision)
        before = _schema_snapshot(db_path)

        parent = _down_revision_of(revision)
        downgrade_to(db_path, parent)
        assert current_revision(db_path) == parent

        upgrade_to(db_path, revision)
        after = _schema_snapshot(db_path)

        assert before == after, (
            f"Schema drift after roundtrip of {revision!r}.\n"
            f"Parent: {parent}\n"
            f"Diff (only rows that changed):\n"
            f"  Before-only: {sorted(set(before) - set(after))}\n"
            f"  After-only:  {sorted(set(after) - set(before))}"
        )
    finally:
        evict_engine(db_path)


def test_known_irreversible_actually_raise(tmp_path: Path) -> None:
    """Each entry in KNOWN_IRREVERSIBLE must actually raise on downgrade.

    Guards against a future maintainer adding a revision to the skip set
    by mistake. If a migration becomes reversible, this test fails and
    the maintainer must remove it from the skip set.

    The set is empty after the 2026-06-02 squash; this loops zero times
    until a future irreversible migration is added.
    """
    for revision in sorted(KNOWN_IRREVERSIBLE):
        parent = _down_revision_of(revision)
        db_path = tmp_path / f"{revision}.db"
        sqlite3.connect(str(db_path)).close()
        upgrade_to(db_path, revision)
        with pytest.raises(NotImplementedError):
            downgrade_to(db_path, parent)
