# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The pins under audit wave 6: a memo that sees every class, a digest hashed once per file state.

- The recipe memo held the attributes of eight classes named by hand, so a
  patched method of any other class the recipe reads was served the recipe
  computed before the patch.
- ``pin_retrieval --if-missing`` hashed the indexed copy once per rung and
  lever variant (about six times a run, 2.9 to 3.9 s each at 179,000 chunks).
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from chaoscypher_core.benchmark import pins


def test_a_patched_method_of_an_unnamed_class_recomputes_the_recipe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``NameConventions.forms`` replaced: the memo sees it (no class is named by hand any more)."""
    from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
        NameConventions,
    )

    computed: list[str] = []
    original = pins._compute_retrieval_recipe

    def counting(tables: str) -> str:
        computed.append(tables)
        return original(tables)

    monkeypatch.setattr(pins, "_compute_retrieval_recipe", counting)
    before = pins.retrieval_recipe()
    assert pins.retrieval_recipe() == before
    assert len(computed) == 1  # memoised while nothing moved
    monkeypatch.setattr(NameConventions, "forms", property(lambda self: ()))
    patched = pins.retrieval_recipe()
    assert len(computed) == 2
    assert patched != before  # the sample reads surname forms


def test_every_class_of_the_recipes_modules_is_in_its_state() -> None:
    """Each class a listed module defines has its attributes in the memo's state."""
    from chaoscypher_core.services.sources.engine.deduplication import name_identity
    from chaoscypher_core.services.sources.engine.extraction.utils import relation_vocabulary
    from chaoscypher_core.services.workflows.tools.engine.handlers import (
        graphrag_handlers,
        question_shape,
    )

    state = {id(v) for v in pins._recipe_code_state()}
    for module in (name_identity, graphrag_handlers, question_shape, relation_vocabulary):
        classes = pins._defined_classes(module)
        assert classes
        for cls in classes:
            assert all(id(v) in state for v in vars(cls).values()), cls.__name__


def test_every_module_of_the_question_shapes_is_in_the_recipes_state() -> None:
    """The router, the shape names, the shape levers, the blocks and the relation words: a patched value recomputes the recipe."""
    from chaoscypher_core import graphrag_profiles, question_shapes
    from chaoscypher_core.services.sources.engine.extraction.utils import relation_vocabulary
    from chaoscypher_core.services.workflows.tools.engine.handlers import (
        graphrag_blocks,
        question_shape,
    )

    state = {id(v) for v in pins._recipe_code_state()}
    for module in (
        question_shape,
        question_shapes,
        graphrag_profiles,
        graphrag_blocks,
        relation_vocabulary,
    ):
        assert all(id(v) in state for v in vars(module).values()), module.__name__


def _index_db(path: Path) -> None:
    con = sqlite3.connect(path)
    for table in ("vec_search_chunks", "vec_search_nodes"):
        con.execute(f'CREATE TABLE "{table}_rowids" (rowid INTEGER PRIMARY KEY, id TEXT)')
        con.execute(
            f'CREATE TABLE "{table}_vector_chunks00" (rowid INTEGER PRIMARY KEY, vectors BLOB)'
        )
    con.execute("CREATE TABLE graph_edges (id TEXT PRIMARY KEY, label TEXT)")
    con.execute("INSERT INTO graph_edges VALUES ('e1', 'parent_of')")
    con.commit()
    con.close()


def _counting_hashlib(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Count the digests :mod:`pins` starts."""
    started: list[int] = []

    def sha256(*args: Any) -> Any:
        started.append(1)
        return hashlib.sha256(*args)

    monkeypatch.setattr(pins, "hashlib", SimpleNamespace(sha256=sha256))
    return started


def test_an_unchanged_file_is_hashed_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = tmp_path / "app.db"
    _index_db(db)
    started = _counting_hashlib(monkeypatch)
    first = pins.index_digest(db)
    assert started
    started.clear()
    assert pins.index_digest(db) == first
    assert started == []  # no hashing at all
    with sqlite3.connect(db) as con:
        con.execute("UPDATE graph_edges SET label = 'spouse_of'")
    assert pins.index_digest(db) != first
    assert started


def test_a_write_still_in_the_wal_is_a_changed_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A WAL write leaves the main file as it was: the WAL's stamp is part of the key."""
    db = tmp_path / "app.db"
    _index_db(db)
    writer = sqlite3.connect(db)
    try:
        writer.execute("PRAGMA journal_mode=WAL")
        first = pins.index_digest(db)
        main = db.stat()
        writer.execute("UPDATE graph_edges SET label = 'spouse_of'")
        writer.commit()  # in the WAL; the writer stays open, so nothing is checkpointed
        assert (db.stat().st_mtime_ns, db.stat().st_size) == (main.st_mtime_ns, main.st_size)
        started = _counting_hashlib(monkeypatch)
        assert pins.index_digest(db) != first
        assert started
    finally:
        writer.close()
