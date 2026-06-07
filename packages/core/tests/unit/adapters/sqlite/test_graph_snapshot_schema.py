# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Schema tests for GraphSnapshot singleton-per-database table."""

from datetime import UTC, datetime
from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine

from chaoscypher_core.adapters.sqlite.models import GraphSnapshot


def test_graph_snapshot_roundtrip(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    engine = create_engine(f"sqlite:///{db_path}")
    SQLModel.metadata.create_all(engine)

    generated = datetime.now(UTC)
    with Session(engine) as session:
        session.add(
            GraphSnapshot(
                database_name="example",
                generated_at=generated,
                payload_json='{"version": 1}',
                node_count=42,
                edge_count=17,
            )
        )
        session.commit()

    with Session(engine) as session:
        row = session.get(GraphSnapshot, "example")
        assert row is not None
        assert row.database_name == "example"
        assert row.payload_json == '{"version": 1}'
        assert row.node_count == 42
        assert row.edge_count == 17
        # SQLite stores datetime without timezone; verify the value matches ignoring tz
        assert row.generated_at.replace(tzinfo=None) == generated.replace(tzinfo=None)


def test_graph_snapshot_default_counts(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    engine = create_engine(f"sqlite:///{db_path}")
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        session.add(
            GraphSnapshot(
                database_name="defaults_db",
                payload_json="{}",
            )
        )
        session.commit()

    with Session(engine) as session:
        row = session.get(GraphSnapshot, "defaults_db")
        assert row is not None
        assert row.node_count == 0
        assert row.edge_count == 0
