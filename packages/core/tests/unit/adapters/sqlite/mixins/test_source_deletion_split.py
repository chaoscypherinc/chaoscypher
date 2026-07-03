# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Phase 3 Task F: split ``SourcesMixin.delete_source_db`` cascade.

The cascade DELETE that removes a source plus its citations, chunks,
tags, embeddings, and extraction jobs+tasks used to live as one big
method on ``SourcesMixin``. That method reached into six tables owned
by five sibling mixins, breaking the mixin encapsulation rule.

After this task each owning mixin exposes a ``delete_*_for_source``
method, and a new ``SourceDeletionMixin`` orchestrates them. This test
locks in:

1. ``sources.py`` no longer imports cross-family SQLModel entity classes.
2. Each sibling mixin exposes the expected delegation surface.
3. ``SourceDeletionMixin`` exists and the composed ``SqliteAdapter``
   inherits from it.
4. The end-to-end cascade still removes every related row when run
   against a real SQLite database.
5. Partial-failure inside the orchestrator rolls back ALL sibling
   deletes when wrapped in ``adapter.transaction()``.
"""

from __future__ import annotations

import ast
import tempfile
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlmodel import SQLModel, select

from chaoscypher_core.adapters.sqlite import SqliteAdapter
from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter as SqliteAdapterImpl
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.adapters.sqlite.mixins._chunk_tasks_crud import ChunkTasksCRUDMixin
from chaoscypher_core.adapters.sqlite.mixins.source_deletion import SourceDeletionMixin
from chaoscypher_core.adapters.sqlite.mixins.source_files_extraction_jobs import (
    SourceExtractionJobsMixin,
)
from chaoscypher_core.adapters.sqlite.mixins.source_files_indexing import (
    SourceIndexingMixin,
)
from chaoscypher_core.adapters.sqlite.mixins.sources_chunks import SourceChunksMixin
from chaoscypher_core.adapters.sqlite.mixins.sources_citations import SourceCitationsMixin
from chaoscypher_core.adapters.sqlite.mixins.sources_tags import SourceTagsMixin
from chaoscypher_core.adapters.sqlite.models import (
    ChunkExtractionJob,
    ChunkExtractionTask,
    DocumentChunk,
    RelationshipCitation,
    SourceCitation,
    SourceEntityEmbedding,
    SourceRow,
    SourceTag,
    SourceTagAssignment,
)
from chaoscypher_core.models import SourceStatus


SOURCES_FILE = (
    Path(__file__).resolve().parents[5]
    / "src"
    / "chaoscypher_core"
    / "adapters"
    / "sqlite"
    / "mixins"
    / "sources.py"
)


# ---------------------------------------------------------------------------
# 1. Ownership — sources.py must not import cross-family entity classes
# ---------------------------------------------------------------------------


FORBIDDEN_SOURCES_IMPORTS = frozenset(
    {
        "SourceCitation",
        "RelationshipCitation",
        "DocumentChunk",
        "SourceEntityEmbedding",
        "ChunkExtractionJob",
        "ChunkExtractionTask",
    }
)
# SourceTagAssignment stays as a read-only JOIN participant in list_sources;
# the mixin ownership rule targets WRITES. Treat it as allowed.
ALLOWED_SOURCES_IMPORTS = frozenset({"SourceTagAssignment"})


def _imported_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.name)
    return names


def test_sources_py_no_longer_imports_cross_family_entities() -> None:
    """AST scan: sources.py must not import cross-family entity classes."""
    assert SOURCES_FILE.exists(), f"missing {SOURCES_FILE}"
    tree = ast.parse(SOURCES_FILE.read_text(encoding="utf-8"))
    imported = _imported_names(tree)
    forbidden_present = FORBIDDEN_SOURCES_IMPORTS & imported
    assert not forbidden_present, (
        f"sources.py still imports cross-family entity classes: "
        f"{sorted(forbidden_present)}. They must be owned by sibling mixins."
    )
    # sanity check: the allowed-by-exception import is still there so
    # list_sources' tag-join keeps working.
    assert ALLOWED_SOURCES_IMPORTS.issubset(imported)


# ---------------------------------------------------------------------------
# 2. Delegation surface — each owning mixin exposes its delete_*_for_source
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mixin_cls", "method_name"),
    [
        (SourceCitationsMixin, "delete_citations_for_source"),
        (SourceChunksMixin, "delete_chunks_for_source"),
        (SourceTagsMixin, "delete_tags_for_source"),
        (SourceIndexingMixin, "delete_entity_embeddings_for_source"),
        (ChunkTasksCRUDMixin, "delete_tasks_for_source"),
        (SourceExtractionJobsMixin, "delete_extraction_jobs_for_source"),
    ],
)
def test_sibling_mixin_exposes_delete_for_source(mixin_cls: type, method_name: str) -> None:
    """Each sibling mixin must expose its ``delete_*_for_source`` method."""
    assert hasattr(mixin_cls, method_name), (
        f"{mixin_cls.__name__} missing {method_name}; the SourceDeletion "
        f"orchestrator fans out to it."
    )
    assert callable(getattr(mixin_cls, method_name))


# ---------------------------------------------------------------------------
# 3. Orchestrator is present
# ---------------------------------------------------------------------------


def test_source_deletion_mixin_has_delete_source_db() -> None:
    """``SourceDeletionMixin`` must declare the ``delete_source_db`` method."""
    assert hasattr(SourceDeletionMixin, "delete_source_db")
    assert callable(SourceDeletionMixin.delete_source_db)


# ---------------------------------------------------------------------------
# 4. SqliteAdapter composes the new mixin
# ---------------------------------------------------------------------------


def test_sqlite_adapter_includes_source_deletion_mixin() -> None:
    """The composed adapter class must inherit from ``SourceDeletionMixin``."""
    assert issubclass(SqliteAdapter, SourceDeletionMixin)
    # Also reachable via the concrete import path.
    assert issubclass(SqliteAdapterImpl, SourceDeletionMixin)


# ---------------------------------------------------------------------------
# 5/6. End-to-end fixtures
# ---------------------------------------------------------------------------


DB_NAME = "default"


def _insert_full_source_fixture(adapter: SqliteAdapter) -> str:
    """Seed one source plus one row in each of the six related tables."""
    source_id = f"src_{uuid.uuid4().hex[:12]}"
    now = datetime.now(UTC)

    source = SourceRow(
        id=source_id,
        database_name=DB_NAME,
        filename="fixture.txt",
        filepath="/tmp/fixture.txt",
        file_type="text",
        status=SourceStatus.COMMITTED,
        created_at=now,
        updated_at=now,
    )
    adapter.session.add(source)
    # Flush the source row so downstream FK constraints can see it
    # before we add rows that reference it.
    adapter.session.flush()

    # Chunk
    chunk = DocumentChunk(
        id=f"chunk_{uuid.uuid4().hex[:12]}",
        database_name=DB_NAME,
        source_id=source_id,
        chunk_index=0,
        content="fixture chunk",
        created_at=now,
    )
    adapter.session.add(chunk)

    # Tag + tag assignment
    tag_id = f"tag_{uuid.uuid4().hex[:12]}"
    adapter.session.add(
        SourceTag(
            id=tag_id,
            database_name=DB_NAME,
            name="fixture-tag",
        )
    )
    adapter.session.add(
        SourceTagAssignment(
            id=f"assign_{uuid.uuid4().hex[:12]}",
            source_id=source_id,
            tag_id=tag_id,
            database_name=DB_NAME,
            assigned_at=now,
        )
    )

    # Entity citation
    adapter.session.add(
        SourceCitation(
            id=f"cite_{uuid.uuid4().hex[:12]}",
            database_name=DB_NAME,
            entity_uri="chaoscypher:entity_fixture",
            entity_label="Fixture Entity",
            source_id=source_id,
            chunk_id=chunk.id,
            confidence=0.9,
            extraction_method="test",
            created_at=now,
        )
    )

    # Relationship citation
    adapter.session.add(
        RelationshipCitation(
            id=f"rcite_{uuid.uuid4().hex[:12]}",
            database_name=DB_NAME,
            edge_id="edge_fixture",
            edge_label="relates_to",
            source_entity_label="A",
            target_entity_label="B",
            source_id=source_id,
            chunk_id=chunk.id,
            confidence=0.9,
            extraction_method="test",
            created_at=now,
        )
    )

    # Entity embedding
    adapter.session.add(
        SourceEntityEmbedding(
            id=f"emb_{source_id}_0",
            source_id=source_id,
            entity_index=0,
            embedding=b"\x00\x00\x00\x00",
            embedding_dimensions=1,
            created_at=now,
        )
    )

    # Extraction job + task
    job_id = f"job_{uuid.uuid4().hex[:12]}"
    adapter.session.add(
        ChunkExtractionJob(
            id=job_id,
            source_id=source_id,
            database_name=DB_NAME,
            created_at=now,
        )
    )
    adapter.session.add(
        ChunkExtractionTask(
            id=f"task_{uuid.uuid4().hex[:12]}",
            job_id=job_id,
            database_name=DB_NAME,
            chunk_index=0,
            created_at=now,
        )
    )

    adapter.session.commit()
    return source_id


def _count_related_rows(adapter: SqliteAdapter, source_id: str) -> dict[str, int]:
    """Count rows in each of the seven related tables for a source."""
    sess = adapter.session

    def _count(stmt) -> int:
        return len(list(sess.scalars(stmt).all()))

    job_ids_subq = select(ChunkExtractionJob.id).where(ChunkExtractionJob.source_id == source_id)

    return {
        "source": _count(select(SourceRow).where(SourceRow.id == source_id)),
        "chunks": _count(select(DocumentChunk).where(DocumentChunk.source_id == source_id)),
        "citations": _count(select(SourceCitation).where(SourceCitation.source_id == source_id)),
        "relationship_citations": _count(
            select(RelationshipCitation).where(RelationshipCitation.source_id == source_id)
        ),
        "tag_assignments": _count(
            select(SourceTagAssignment).where(SourceTagAssignment.source_id == source_id)
        ),
        "embeddings": _count(
            select(SourceEntityEmbedding).where(SourceEntityEmbedding.source_id == source_id)
        ),
        "jobs": _count(select(ChunkExtractionJob).where(ChunkExtractionJob.source_id == source_id)),
        "tasks": _count(
            select(ChunkExtractionTask).where(ChunkExtractionTask.job_id.in_(job_ids_subq))
        ),
    }


@pytest.fixture
def adapter() -> Iterator[SqliteAdapter]:
    """Fresh SqliteAdapter against a temp directory SQLite file.

    ``ignore_cleanup_errors=True`` is required on Windows because SQLite
    can leave handles briefly after ``adapter.close()``.
    """
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        db_dir = Path(td) / "chaoscypher-test"
        db_dir.mkdir(parents=True, exist_ok=True)
        db_path = db_dir / "app.db"

        engine = get_engine(str(db_path))
        SQLModel.metadata.create_all(engine, checkfirst=True)

        a = SqliteAdapter(str(db_path), database_name=DB_NAME)
        a.connect()
        try:
            yield a
        finally:
            a.disconnect()


# ---------------------------------------------------------------------------
# 5. End-to-end cascade
# ---------------------------------------------------------------------------


def test_delete_source_db_cascade_end_to_end(adapter: SqliteAdapter) -> None:
    """Calling ``delete_source_db`` must remove every related row."""
    source_id = _insert_full_source_fixture(adapter)

    # Pre-condition: every table has exactly one row for this source.
    pre = _count_related_rows(adapter, source_id)
    assert all(count == 1 for count in pre.values()), (
        f"fixture broken — expected one row per table, got {pre}"
    )

    result = adapter.delete_source_db(source_id, database_name=DB_NAME)
    assert result is True

    adapter.session.expire_all()
    post = _count_related_rows(adapter, source_id)
    assert all(count == 0 for count in post.values()), (
        f"rows not fully cascaded after delete_source_db; remaining: {post}"
    )


# ---------------------------------------------------------------------------
# 6. Partial-failure rollback
# ---------------------------------------------------------------------------


def test_delete_source_db_rolls_back_on_partial_failure(
    adapter: SqliteAdapter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If a sibling delete raises mid-cascade, ALL deletes roll back.

    Force ``delete_entity_embeddings_for_source`` (the 4th sibling call)
    to raise. The orchestrator runs inside ``adapter.transaction()`` so
    the citation + chunk + tag deletes that already flushed must NOT
    land in the database.
    """
    source_id = _insert_full_source_fixture(adapter)
    pre = _count_related_rows(adapter, source_id)
    assert all(count == 1 for count in pre.values())

    sentinel = RuntimeError("simulated mid-cascade failure")

    def _boom(self: SqliteAdapter, src_id: str) -> None:
        raise sentinel

    monkeypatch.setattr(
        SqliteAdapter,
        "delete_entity_embeddings_for_source",
        _boom,
        raising=True,
    )

    with pytest.raises(RuntimeError, match="simulated mid-cascade failure"):
        with adapter.transaction():
            adapter.delete_source_db(source_id, database_name=DB_NAME)

    adapter.session.expire_all()
    post = _count_related_rows(adapter, source_id)
    assert post == pre, (
        f"partial-failure rollback did not restore pre-state.\n  before: {pre}\n  after:  {post}"
    )


# ---------------------------------------------------------------------------
# 7. delete_source_files must never rmtree a non-absolute path (CWD-wipe guard)
# ---------------------------------------------------------------------------


def test_delete_source_files_ignores_relative_path(
    adapter: SqliteAdapter,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bare/relative filepath must be a no-op, NOT an rmtree of the CWD.

    Regression: an imported source's ``filepath`` was a display name, not an
    on-disk path. ``Path("war_and_peace.txt").parent`` is ``.`` (the process
    working directory), so the old code ``rmtree``'d the CWD — which wiped the
    served frontend at ``/app/static`` when an imported source was deleted.
    """
    canary = tmp_path / "do_not_delete"
    canary.mkdir()
    (canary / "keep.txt").write_text("keep me")
    monkeypatch.chdir(tmp_path)  # so a bare name's parent "." resolves here

    adapter.delete_source_files("war_and_peace.txt")

    assert canary.exists(), "a relative filepath must not delete the working dir"
    assert (canary / "keep.txt").exists()


def test_delete_source_files_removes_absolute_staged_dir(
    adapter: SqliteAdapter,
    tmp_path: Path,
) -> None:
    """An absolute staged-file path still removes its parent directory."""
    staged_dir = tmp_path / "sources" / "src_abc"
    staged_dir.mkdir(parents=True)
    (staged_dir / "doc.txt").write_text("staged content")

    adapter.delete_source_files(str(staged_dir / "doc.txt"))

    assert not staged_dir.exists(), "an absolute staged dir should be removed"
