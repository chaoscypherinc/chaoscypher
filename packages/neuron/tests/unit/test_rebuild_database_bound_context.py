# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for rebuild_database_bound_context's event-bus re-pointing.

A UI database switch rebuilds the worker's storage adapter, but the
singleton ``event_bus`` routes ``emit()`` through the adapter it was
configured with at boot — engine-guarded session resolution means an
old-database adapter keeps writing system events into the OLD file.
The rebuild must therefore re-point the bus at the new adapter.
"""

from unittest.mock import MagicMock, patch


def _make_settings(database: str = "switched_db") -> MagicMock:
    settings = MagicMock()
    settings.current_database = database
    settings.search.vector_dimensions = 768
    settings.embedding.model = "test-model"
    settings.data_dir = "/data"
    settings.paths.app_db_filename = "app.db"
    return settings


def test_rebuild_reconfigures_event_bus_with_new_adapter() -> None:
    from chaoscypher_neuron.setup.shared import rebuild_database_bound_context

    ctx: dict = {}
    adapter = MagicMock()
    adapter.session = MagicMock()

    with (
        patch(
            "chaoscypher_core.adapters.sqlite.SqliteAdapter",
            return_value=adapter,
        ),
        patch("chaoscypher_core.adapters.sqlite.repos.GraphRepository"),
        patch("chaoscypher_core.adapters.sqlite.repos.SearchRepository"),
        patch("chaoscypher_core.database.engine.get_engine"),
        patch("chaoscypher_core.queue.service.register_worker_adapter"),
        patch("chaoscypher_core.services.events.event_bus") as mock_bus,
    ):
        rebuild_database_bound_context(ctx, _make_settings())

    # The bus now persists events through the NEW database's adapter.
    mock_bus.configure.assert_called_once_with(adapter)
    assert ctx["storage_adapter"] is adapter
    assert ctx["current_database"] == "switched_db"
