# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Integration test PINNING the chaoscypher.workflows import gap.

The exporter writes the ``chaoscypher.workflows`` named graph from trigger
rows, but the export shape does NOT carry the ``Workflow`` definitions those
triggers reference (a trigger's ``workflow_id`` is a NOT-NULL FK to a
``workflows`` row that is never exported). So the importer cannot faithfully
rebuild a trigger from this graph.

Rather than half-implement, the importer surfaces the graph honestly: it
counts the members, logs a warning, and records the gap on the import stats.
This test PINS that behavior — the workflows graph is NOT silently dropped
(a warning fires) and no partial/dangling trigger lands. See
``internal/TODO.md`` (P2) for the bounded work to make workflows round-trip.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from chaoscypher_core.adapters.sqlite.models import Trigger, Workflow
from chaoscypher_core.adapters.sqlite.repos import GraphRepository
from chaoscypher_core.app_config import get_settings
from chaoscypher_core.app_config.engine_factory import build_engine_settings
from chaoscypher_core.services.export import CcxExporter
from chaoscypher_core.services.package.importer import CcxImporter, ImportOptions


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter


def _seed_workflow_with_trigger(adapter: SqliteAdapter, database_name: str = "default") -> None:
    """Seed one Workflow + one Trigger that references it."""
    assert adapter.session is not None
    session = adapter.session

    session.add(
        Workflow(
            id="wf_1",
            database_name=database_name,
            name="Auto Enrich",
            input_schema={},
        )
    )
    session.flush()
    session.add(
        Trigger(
            id="trig_1",
            database_name=database_name,
            name="On node created",
            event_source="node.created",
            filters={},
            workflow_id="wf_1",
            enabled=True,
        )
    )
    session.commit()


@pytest.mark.asyncio
async def test_ccx_importer_workflows_graph_not_silently_dropped(
    integration_adapter: SqliteAdapter,
) -> None:
    """The workflows graph is surfaced (warned), not silently ignored."""
    _seed_workflow_with_trigger(integration_adapter)
    assert integration_adapter.session is not None

    graph_repo = GraphRepository(integration_adapter.session, "default")
    settings = build_engine_settings(get_settings())
    exporter = CcxExporter(
        graph_repository=graph_repo,
        sources_repository=integration_adapter,
        settings=settings,
        # workflow_db wired so the exporter populates chaoscypher.workflows
        # from the trigger row (via list_triggers).
        workflow_db=integration_adapter,
    )
    data = exporter.export(include_embeddings=False, include_workflows=True)

    target_db = "imported_wf"
    target_repo = GraphRepository(integration_adapter.session, target_db)
    importer = CcxImporter(
        graph_repository=target_repo,
        sources_repository=integration_adapter,
        workflow_db=integration_adapter,
    )
    stats = await importer.import_from_bytes(data, ImportOptions(database_name=target_db))

    # Import did not error overall.
    assert not stats.errors, stats.errors

    # The workflows graph carried at least the one trigger member, and the
    # importer surfaced it with a warning rather than silently dropping it.
    assert any("chaoscypher.workflows" in w for w in stats.warnings), stats.warnings

    # The gap is visible: no trigger was reconstructed in the target database
    # (faithful reconstruction needs the unexported Workflow definition).
    assert integration_adapter.count_triggers(database_name=target_db) == 0
