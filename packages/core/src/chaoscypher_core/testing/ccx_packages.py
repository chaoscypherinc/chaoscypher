# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Real ``.ccx`` packages for tests: seeded in a SQLite database, exported with ``CcxExporter``.

Test infrastructure only. The compose merger tests (Core) and the ``compose build`` CLI tests
both need genuine packages to merge, built the same way; this is the one builder they share.
Each package gets one ``Person`` node template, one ``WorksWith`` edge template, a node per
name and a chain of edges between consecutive names.
"""

from __future__ import annotations

import itertools
from typing import TYPE_CHECKING

from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import evict_engine, get_engine
from chaoscypher_core.adapters.sqlite.models import GraphEdge, GraphNode, GraphTemplate
from chaoscypher_core.repo_factories.graph_factory import get_transaction_graph_repository


if TYPE_CHECKING:
    from pathlib import Path


def _seed(adapter: SqliteAdapter, *, suffix: str, people: tuple[str, ...]) -> None:
    """Seed one Person template + ``people`` nodes + a chain of edges."""
    session = adapter.session
    if session is None:
        msg = "the adapter is not connected"
        raise RuntimeError(msg)
    session.add(
        GraphTemplate(
            id=f"tpl_person_{suffix}",
            database_name="default",
            name="Person",
            template_type="node",
            color="#ff0000",
        )
    )
    session.add(
        GraphTemplate(
            id=f"tpl_rel_{suffix}",
            database_name="default",
            name="WorksWith",
            template_type="edge",
            color=None,
        )
    )
    session.flush()
    for person in people:
        session.add(
            GraphNode(
                id=f"node_{person.lower()}_{suffix}",
                database_name="default",
                graph_name="knowledge",
                template_id=f"tpl_person_{suffix}",
                label=person,
            )
        )
    session.flush()
    for left, right in itertools.pairwise(people):
        session.add(
            GraphEdge(
                id=f"edge_{left.lower()}_{right.lower()}_{suffix}",
                database_name="default",
                graph_name="knowledge",
                template_id=f"tpl_rel_{suffix}",
                source_node_id=f"node_{left.lower()}_{suffix}",
                target_node_id=f"node_{right.lower()}_{suffix}",
                label="worksWith",
                properties={"since": 2020},
            )
        )
    session.commit()


def _export(adapter: SqliteAdapter) -> bytes:
    """Export the adapter's database as ``.ccx`` package bytes."""
    from chaoscypher_core.app_config import get_settings
    from chaoscypher_core.app_config.engine_factory import build_engine_settings
    from chaoscypher_core.services.export import CcxExporter

    exporter = CcxExporter(
        graph_repository=get_transaction_graph_repository(adapter, "default"),
        sources_repository=adapter,
        settings=build_engine_settings(get_settings()),
    )
    return exporter.export(include_embeddings=False)


def build_ccx_package(work_dir: Path, name: str, people: tuple[str, ...]) -> Path:
    """Write a real ``<work_dir>/<name>.ccx`` holding ``people`` and return its path.

    The source database it is exported from lives at ``<work_dir>/src/<name>/app.db``;
    its cached engine is evicted before returning.
    """
    db_dir = work_dir / "src" / name
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "app.db"
    SQLModel.metadata.create_all(get_engine(db_path), checkfirst=True)
    adapter = SqliteAdapter(str(db_path), database_name="default")
    adapter.connect()
    try:
        _seed(adapter, suffix=name, people=people)
        data = _export(adapter)
    finally:
        adapter.disconnect()
        evict_engine(db_path)
    archive = work_dir / f"{name}.ccx"
    archive.write_bytes(data)
    return archive


__all__ = ["build_ccx_package"]
