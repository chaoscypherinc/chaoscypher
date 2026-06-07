# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Regression: SearchRepository.check_fts5_integrity hook is wired and quiet on healthy DBs.

Pre-launch review F3. SearchRepository now runs FTS5's
``'integrity-check'`` command at construction time and logs a
structured WARNING if the check fails. These tests pin the **healthy**
side of the contract: a fresh DB and a content-seeded DB both pass
integrity-check, and the constructor stays quiet (no false-positive
warning at boot).

Inducing drift that FTS5's integrity-check actually catches is
non-trivial without manipulating FTS5's internal segment tables
(``fulltext_index_data``, ``fulltext_index_idx``, ``fulltext_index_docsize``);
that test coverage gap is filed under DEFERRED.md ("Pending FTS5 drift
test coverage"). In production the integrity-check fires against real
on-disk corruption (partial writes, disk failures), which is the
operator-visible failure mode the F3 hook is meant to surface.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from sqlalchemy import text

from chaoscypher_core.adapters.sqlite.engine import evict_engine, get_engine
from chaoscypher_core.adapters.sqlite.repos import SearchRepository


def _construct(db_path: Path) -> SearchRepository:
    engine = get_engine(db_path)
    return SearchRepository(engine=engine, vector_dim=4, embedding_model="test-model")


def _release(db_path: Path, repo: SearchRepository) -> None:
    """Release engine connections so the temp DB can be cleaned up."""
    repo._engine.dispose()
    evict_engine(db_path)


def test_check_fts5_integrity_passes_on_fresh_db(tmp_path: Path) -> None:
    """A fresh DB with no content passes integrity-check."""
    db_path = tmp_path / "app.db"
    repo = _construct(db_path)
    try:
        is_consistent, error = repo.check_fts5_integrity()
        assert is_consistent, f"unexpected drift on fresh DB: {error}"
        assert error is None
    finally:
        _release(db_path, repo)


def test_check_fts5_integrity_passes_after_seeded_content(tmp_path: Path) -> None:
    """Content inserted with triggers intact stays consistent."""
    db_path = tmp_path / "app.db"
    repo = _construct(db_path)
    try:
        with repo._engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO fulltext_content "
                    "(node_id, label, properties, searchable_text) "
                    "VALUES ('n1', 'foo', '{}', 'foo bar')"
                )
            )
        is_consistent, error = repo.check_fts5_integrity()
        assert is_consistent, f"unexpected drift after seeded content: {error}"
    finally:
        _release(db_path, repo)


def test_warn_on_search_index_drift_quiet_on_healthy_init(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Healthy boot emits no ``search_index_drift_detected`` warning."""
    db_path = tmp_path / "app.db"
    with caplog.at_level(logging.WARNING):
        repo = _construct(db_path)
    try:
        warnings = [r for r in caplog.records if "search_index_drift_detected" in r.getMessage()]
        assert not warnings, (
            "did not expect search_index_drift_detected WARNING on healthy boot; "
            f"saw {[r.getMessage() for r in warnings]!r}"
        )
    finally:
        _release(db_path, repo)
