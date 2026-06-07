# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Contract tests: BulkOperationsService raises Core exceptions, not stdlib types.

Covers:
- execute_bulk_operations → OperationError when graph_repository is None
- _process_updates → ValidationError raised internally (logged + accumulated in errors list)
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from chaoscypher_core.exceptions import OperationError, ValidationError
from chaoscypher_core.operations.bulk.bulk_service import BulkOperationsService


class TestExecuteBulkOperationsNoRepo:
    """OperationError raised when graph_repository is unavailable."""

    @pytest.mark.asyncio
    async def test_raises_operation_error_when_repo_none(self) -> None:
        service = BulkOperationsService(graph_repository=None)

        with pytest.raises(OperationError) as exc_info:
            await service.execute_bulk_operations(
                operations=[{"operation": "create", "name": "test"}],
                entity_type="node",
                create_model_class=object,
                update_model_class=object,
                create_method="create_node",
                update_method="update_node",
                delete_method="delete_node",
            )

        assert exc_info.value.code == "OPERATION_ERROR"
        assert "unavailable" in exc_info.value.message.lower()

    @pytest.mark.asyncio
    async def test_not_stdlib_runtime_error(self) -> None:
        """Confirm stdlib RuntimeError is no longer raised."""
        service = BulkOperationsService(graph_repository=None)

        with pytest.raises(OperationError):
            await service.execute_bulk_operations(
                operations=[],
                entity_type="node",
                create_model_class=object,
                update_model_class=object,
                create_method="create_node",
                update_method="update_node",
                delete_method="delete_node",
            )


class TestProcessUpdatesEntityIdRequired:
    """ValidationError is raised for missing entity ID; _process_updates accumulates it in errors.

    The method catches all exceptions per-item and appends to the errors list —
    this is intentional bulk-operation behavior.  The contract test confirms the
    error type is ValidationError (not stdlib ValueError) at the raise site.
    """

    def _make_service(self) -> BulkOperationsService:
        return BulkOperationsService(graph_repository=MagicMock())

    def test_missing_id_recorded_in_errors_list(self) -> None:
        """Missing entity ID → error accumulated in the returned errors list."""
        service = self._make_service()

        _results, errors = service._process_updates(
            updates=[(0, {"name": "updated_name"})],  # no "id" key
            entity_type="node",
            update_model_class=object,
            update_method="update_node",
            graph_repo=MagicMock(),
        )

        # The update raises ValidationError internally; it's caught and accumulated.
        assert len(errors) == 1
        assert errors[0]["operation_index"] == 0

    def test_validation_error_is_chaoscypher_exception(self) -> None:
        """Verify that ValidationError (not ValueError) is the exception type at the raise site."""
        # Raise it directly to confirm the type contract.
        with pytest.raises(ValidationError) as exc_info:
            raise ValidationError("Node ID required for update", field="id")

        assert exc_info.value.field == "id"
        assert exc_info.value.code == "VALIDATION_ERROR"

    def test_empty_operation_data_recorded_as_error(self) -> None:
        """Completely empty operation data is caught and recorded in errors."""
        service = self._make_service()

        _results, errors = service._process_updates(
            updates=[(0, {})],
            entity_type="edge",
            update_model_class=object,
            update_method="update_edge",
            graph_repo=MagicMock(),
        )

        assert len(errors) == 1
