# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for SourcesMixin.transition_source_status compare-and-swap semantics."""

import pytest
from sqlalchemy import create_engine
from sqlmodel import Session, SQLModel

from chaoscypher_core.adapters.sqlite.mixins.sources import SourcesMixin
from chaoscypher_core.adapters.sqlite.models import SourceRow


class _StubAdapter(SourcesMixin):
    def __init__(self, session: Session, database_name: str = "default") -> None:
        self.session = session
        self.database_name = database_name
        self._connected = True
        self._transaction_depth = 0

    def _ensure_connected(self) -> None:
        if not self._connected:
            raise RuntimeError("disconnected")

    def _maybe_commit(self) -> None:
        if self._transaction_depth == 0:
            self.session.commit()

    def _entity_to_dict(self, entity):
        return entity.model_dump() if entity else None


@pytest.fixture
def adapter(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    SQLModel.metadata.create_all(engine, tables=[SourceRow.__table__])
    with Session(engine) as session:
        a = _StubAdapter(session)
        session.add(
            SourceRow(
                id="src_A",
                database_name="default",
                filename="a.pdf",
                filepath="/tmp/a.pdf",
                file_type="pdf",
                file_size=0,
                status="indexed",
            )
        )
        session.commit()
        yield a


class TestTransitionSourceStatusCAS:
    def test_cas_succeeds_when_status_matches(self, adapter):
        ok = adapter.transition_source_status(
            "src_A", "indexed", "mcp_extracting", database_name="default"
        )
        assert ok is True
        row = adapter.session.get(SourceRow, "src_A")
        assert row.status == "mcp_extracting"

    def test_cas_fails_when_status_mismatches(self, adapter):
        ok = adapter.transition_source_status(
            "src_A", "committed", "mcp_extracting", database_name="default"
        )
        assert ok is False
        row = adapter.session.get(SourceRow, "src_A")
        assert row.status == "indexed"

    def test_only_one_of_two_cas_calls_wins(self, adapter):
        first = adapter.transition_source_status(
            "src_A", "indexed", "mcp_extracting", database_name="default"
        )
        second = adapter.transition_source_status(
            "src_A", "indexed", "mcp_extracting", database_name="default"
        )
        assert first is True
        assert second is False

    def test_cas_nonexistent_id_returns_false(self, adapter):
        ok = adapter.transition_source_status(
            "nope", "indexed", "committed", database_name="default"
        )
        assert ok is False
