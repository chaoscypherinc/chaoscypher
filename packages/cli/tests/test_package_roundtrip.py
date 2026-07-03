# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""End-to-end round-trip test for the `package export` / `package load` CLI.

Unlike the mock-based unit tests in ``test_cmd_serve_package.py`` (which stub
``CcxExporter``/``CcxImporter``), this test drives the *real* CCX 3.0 exporter
and importer through the CLI commands against a file-backed SQLite adapter:

1. Seed a tiny graph (1 template, 2 nodes, 1 property edge) via the real adapter.
2. Run ``chaoscypher graph package export -o out.ccx`` -> writes real .ccx bytes.
3. Assert the produced bytes validate via ``ccx.open_package(...).validate()``
   (report ``.ok`` is True and the ``core`` conformance class is declared).
4. Run ``chaoscypher graph package load out.ccx`` into a *fresh* database and
   assert the nodes/edges/templates round-trip (upsert-by-IRI is idempotent).

This pins the Phase-3/4 wiring: the commands really construct CcxExporter /
CcxImporter with the repos/settings they have access to.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import ccx
import pytest
from click.testing import CliRunner
from sqlmodel import SQLModel

from chaoscypher_cli.commands.package.export import export
from chaoscypher_cli.commands.package.load import load
from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.adapters.sqlite.models import (
    GraphEdge,
    GraphNode,
    GraphTemplate,
)
from chaoscypher_core.adapters.sqlite.repos import GraphRepository
from chaoscypher_core.app_config import get_settings
from chaoscypher_core.app_config.engine_factory import build_engine_settings


class _FakeCtx:
    """Minimal CLIContext stand-in backed by a real adapter + GraphRepository.

    The export() command reads ``graph_repository`` / ``settings`` / ``get_stats``;
    the load() command reads ``graph_repository`` / ``database_name``. Sources and
    workflows are intentionally absent (None) — CLI export/import omits both.
    """

    def __init__(self, adapter: SqliteAdapter, database_name: str) -> None:
        self.database_name = database_name
        self._adapter = adapter
        assert adapter.session is not None
        self.graph_repository = GraphRepository(adapter.session, database_name)
        settings = build_engine_settings(get_settings())
        settings.current_database = database_name
        self.settings = settings

    def get_stats(self) -> dict[str, Any]:
        return {
            "database_name": self.database_name,
            "nodes": self.graph_repository.count_nodes(),
            "edges": self.graph_repository.count_edges(),
            "templates": self.graph_repository.count_templates(database_name=self.database_name),
        }


def _make_adapter(tmp_path: Path, name: str) -> SqliteAdapter:
    db_dir = tmp_path / name
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "app.db"
    engine = get_engine(db_path)
    SQLModel.metadata.create_all(engine, checkfirst=True)
    adapter = SqliteAdapter(str(db_path), database_name="default")
    adapter.connect()
    return adapter


@pytest.fixture
def source_adapter(tmp_path: Path) -> Generator[SqliteAdapter]:
    adapter = _make_adapter(tmp_path, "source-db")
    yield adapter
    adapter.disconnect()


@pytest.fixture
def dest_adapter(tmp_path: Path) -> Generator[SqliteAdapter]:
    adapter = _make_adapter(tmp_path, "dest-db")
    yield adapter
    adapter.disconnect()


def _seed_graph(adapter: SqliteAdapter, database_name: str = "default") -> None:
    """Seed 1 user node template, 2 nodes, 1 property edge (no source)."""
    assert adapter.session is not None
    session = adapter.session

    session.add(
        GraphTemplate(
            id="tpl_person",
            database_name=database_name,
            name="Person",
            template_type="node",
            color="#ff0000",
        )
    )
    session.add(
        GraphTemplate(
            id="tpl_rel",
            database_name=database_name,
            name="WorksWith",
            template_type="edge",
            color=None,
        )
    )
    session.flush()

    session.add(
        GraphNode(
            id="node_alice",
            database_name=database_name,
            graph_name="knowledge",
            template_id="tpl_person",
            label="Alice",
        )
    )
    session.add(
        GraphNode(
            id="node_bob",
            database_name=database_name,
            graph_name="knowledge",
            template_id="tpl_person",
            label="Bob",
        )
    )
    session.flush()

    # A property-bearing edge reifies to a ccx:Relationship in the default graph.
    session.add(
        GraphEdge(
            id="edge_1",
            database_name=database_name,
            graph_name="knowledge",
            template_id="tpl_rel",
            source_node_id="node_alice",
            target_node_id="node_bob",
            label="worksWith",
            properties={"since": 2020},
        )
    )
    session.commit()


def test_export_then_load_roundtrips_through_cli(
    source_adapter: SqliteAdapter,
    dest_adapter: SqliteAdapter,
    tmp_path: Path,
) -> None:
    """Export -> validate(.ok) -> load: knowledge survives a full CLI round-trip."""
    _seed_graph(source_adapter)

    out_path = tmp_path / "roundtrip.ccx"
    runner = CliRunner()

    # --- export -----------------------------------------------------------
    src_ctx = _FakeCtx(source_adapter, "default")
    with patch("chaoscypher_cli.commands.package.export.get_context", return_value=src_ctx):
        # Sources/workflows aren't available from the CLI; export knowledge +
        # templates only (lenses default on but emit nothing here).
        result = runner.invoke(export, ["--output", str(out_path), "--no-workflows"])

    assert result.exit_code == 0, result.output
    assert out_path.exists()

    # --- the produced .ccx validates as a conformant CCX 3.0 package ------
    data = out_path.read_bytes()
    report = ccx.open_package(data).validate()
    assert report.ok, report.errors
    assert "core" in report.classes

    # --- load into a fresh database ---------------------------------------
    dest_ctx = _FakeCtx(dest_adapter, "default")
    with patch("chaoscypher_cli.commands.package.load.get_context", return_value=dest_ctx):
        result = runner.invoke(load, [str(out_path), "--no-workflows"])

    assert result.exit_code == 0, result.output

    # The destination graph now carries the round-tripped knowledge.
    dest_repo = dest_ctx.graph_repository
    assert dest_repo.count_nodes() == 2
    assert dest_repo.count_edges() == 1
    # Templates round-trip too (the property edge needs its edge template).
    assert dest_repo.count_templates(database_name="default") >= 1

    labels = {node.label for node in dest_repo.list_nodes(limit=100)}
    assert {"Alice", "Bob"} <= labels


def test_load_is_idempotent_through_cli(
    source_adapter: SqliteAdapter,
    dest_adapter: SqliteAdapter,
    tmp_path: Path,
) -> None:
    """Re-loading the same .ccx is idempotent (upsert-by-IRI, no duplicates)."""
    _seed_graph(source_adapter)

    out_path = tmp_path / "idempotent.ccx"
    runner = CliRunner()

    src_ctx = _FakeCtx(source_adapter, "default")
    with patch("chaoscypher_cli.commands.package.export.get_context", return_value=src_ctx):
        result = runner.invoke(export, ["--output", str(out_path), "--no-workflows"])
    assert result.exit_code == 0, result.output

    dest_ctx = _FakeCtx(dest_adapter, "default")
    with patch("chaoscypher_cli.commands.package.load.get_context", return_value=dest_ctx):
        first = runner.invoke(load, [str(out_path), "--no-workflows"])
        assert first.exit_code == 0, first.output
        second = runner.invoke(load, [str(out_path), "--no-workflows"])
        assert second.exit_code == 0, second.output

    # Two imports of the same bytes must NOT double the graph.
    assert dest_ctx.graph_repository.count_nodes() == 2
    assert dest_ctx.graph_repository.count_edges() == 1
