# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for Task 4.1: durable search-index retry queue on commit failure."""

from __future__ import annotations

import contextlib


class _FakeAdapter:
    """Minimal adapter stub implementing SearchRetryQueueProtocol +
    ``transaction()``. Uses INSERT OR IGNORE to match the real adapter's
    dedup behavior on the (kind, item_id) unique key.
    """

    def __init__(self, session):
        self.session = session

    def transaction(self):
        @contextlib.contextmanager
        def _ctx():
            yield
            self.session.commit()

        return _ctx()

    def enqueue_pending_search_index(self, *, rows):
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        from chaoscypher_core.adapters.sqlite.models import PendingSearchIndex

        if not rows:
            return
        for row in rows:
            item_id = row["item_id"]
            kind = row["kind"]
            source_id = row.get("source_id")
            stmt = (
                sqlite_insert(PendingSearchIndex)
                .values(
                    id=f"{kind}:{item_id}",
                    kind=kind,
                    item_id=item_id,
                    source_id=source_id,
                )
                .prefix_with("OR IGNORE")
            )
            self.session.execute(stmt)


def test_enqueue_search_retry_persists_ids(tmp_path):
    """_enqueue_search_retry writes rows to PendingSearchIndex."""
    from sqlmodel import Session, SQLModel, create_engine, select

    from chaoscypher_core.adapters.sqlite import models as m  # noqa: F401
    from chaoscypher_core.adapters.sqlite.models import PendingSearchIndex

    engine = create_engine(f"sqlite:///{tmp_path / 't.db'}")
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        adapter = _FakeAdapter(session)
        from chaoscypher_core.services.sources.engine.commit.service import (
            SourceCommitService,
        )

        service = SourceCommitService.__new__(SourceCommitService)
        service.adapter = adapter
        service._enqueue_search_retry(
            ids=["n1", "n2", "n3"],
            source_id="src-1",
            kind="node",
        )

        rows = session.exec(select(PendingSearchIndex)).all()
        assert {r.item_id for r in rows} == {"n1", "n2", "n3"}
        assert all(r.kind == "node" for r in rows)
        assert all(r.source_id == "src-1" for r in rows)


def test_enqueue_search_retry_chunk_kind(tmp_path):
    """_enqueue_search_retry accepts kind='chunk' using file_id as item_id."""
    from sqlmodel import Session, SQLModel, create_engine, select

    from chaoscypher_core.adapters.sqlite import models as m  # noqa: F401
    from chaoscypher_core.adapters.sqlite.models import PendingSearchIndex

    engine = create_engine(f"sqlite:///{tmp_path / 't.db'}")
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        adapter = _FakeAdapter(session)
        from chaoscypher_core.services.sources.engine.commit.service import (
            SourceCommitService,
        )

        service = SourceCommitService.__new__(SourceCommitService)
        service.adapter = adapter
        service._enqueue_search_retry(
            ids=["file-abc"],
            source_id="file-abc",
            kind="chunk",
        )

        rows = session.exec(select(PendingSearchIndex)).all()
        assert len(rows) == 1
        assert rows[0].item_id == "file-abc"
        assert rows[0].kind == "chunk"
        assert rows[0].source_id == "file-abc"


def test_enqueue_search_retry_deduplicates_on_conflict(tmp_path):
    """Calling _enqueue_search_retry twice for the same id is idempotent."""
    from sqlmodel import Session, SQLModel, create_engine, select

    from chaoscypher_core.adapters.sqlite import models as m  # noqa: F401
    from chaoscypher_core.adapters.sqlite.models import PendingSearchIndex

    engine = create_engine(f"sqlite:///{tmp_path / 't.db'}")
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        adapter = _FakeAdapter(session)
        from chaoscypher_core.services.sources.engine.commit.service import (
            SourceCommitService,
        )

        service = SourceCommitService.__new__(SourceCommitService)
        service.adapter = adapter

        service._enqueue_search_retry(
            ids=["n1"],
            source_id="src-1",
            kind="node",
        )

        # Second call should not raise (INSERT OR IGNORE semantics).
        service._enqueue_search_retry(
            ids=["n1"],
            source_id="src-1",
            kind="node",
        )

        rows = session.exec(select(PendingSearchIndex)).all()
        assert len(rows) == 1
