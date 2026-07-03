# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Bulk Operations Service - orchestrator for batch graph operations.

Thin orchestrator that delegates to entity-specific handler modules
for nodes, edges, and templates. All operations are queued and
executed asynchronously.
"""

from functools import partial
from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.constants import QUEUE_OPERATIONS
from chaoscypher_core.exceptions import OperationError, ValidationError
from chaoscypher_core.operations.bulk.bulk_edge_ops import (
    bulk_edges_handler,
)
from chaoscypher_core.operations.bulk.bulk_node_ops import (
    bulk_nodes_handler,
)
from chaoscypher_core.operations.bulk.bulk_template_ops import (
    bulk_templates_handler,
)
from chaoscypher_core.queue import queue_client


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite.repos import GraphRepository
    from chaoscypher_core.app_config import Settings


logger = structlog.get_logger(__name__)


def _is_foreign_key_error(exc: Exception) -> bool:
    """True if ``exc`` is a foreign-key / integrity violation from the store.

    Detected by exception type name + message so this stays in Core's operations
    layer without importing the SQL driver (the adapter raises SQLAlchemy's
    ``IntegrityError``; SQLite's message is ``FOREIGN KEY constraint failed``).
    """
    return type(exc).__name__ == "IntegrityError" or "foreign key" in str(exc).lower()


class BulkOperationsService:
    """Service for queuing bulk graph operations.

    Handles batch operations for nodes, edges, and templates.
    All operations are queued and executed asynchronously.
    """

    def __init__(
        self,
        graph_repository: GraphRepository | None = None,
        settings: Settings | None = None,
    ):
        """Initialize bulk operations service.

        Args:
            graph_repository: GraphRepository for graph operations
            settings: Application settings (used for service URLs)

        """
        self.graph_repository = graph_repository
        self.settings = settings

        self.operation_handlers = {
            "bulk_nodes": partial(bulk_nodes_handler, self),
            "bulk_edges": partial(bulk_edges_handler, self),
            "bulk_templates": partial(bulk_templates_handler, self),
        }

        logger.info("bulk_operations_service_initialized")

    def register_handlers(self) -> None:
        """Register bulk operation handlers with queue."""
        queue_client.register_handlers(QUEUE_OPERATIONS, self.operation_handlers)  # type: ignore[arg-type]

    # ------------------------------------------------------------------
    # Generic bulk execution engine
    # ------------------------------------------------------------------
    async def execute_bulk_operations(
        self,
        operations: list[dict[str, Any]],
        entity_type: str,
        create_model_class: type,
        update_model_class: type,
        create_method: str,
        update_method: str,
        delete_method: str,
        id_field_alternatives: list[str] | None = None,
    ) -> dict[str, Any]:
        """Handle bulk operations generically (DRY principle).

        Optimizations:
        - Batch deletes use delete_nodes_batch/delete_edges_batch for massive speedup
        - Creates and updates still use individual calls (batch methods could be added later)

        Args:
            operations: List of operations to execute
            entity_type: Type of entity (node, edge, template) for logging
            create_model_class: Pydantic model for create operations
            update_model_class: Pydantic model for update operations
            create_method: Graph repo method name for create
            update_method: Graph repo method name for update
            delete_method: Graph repo method name for delete (unused if batch available)
            id_field_alternatives: Alternative field names for ID (e.g., ["id", "node_id"])

        Returns:
            Result dictionary with success/failed counts and details

        Raises:
            OperationError: If the graph repository is unavailable.

        """
        graph_repo = self.graph_repository
        if not graph_repo:
            msg = "Graph repository unavailable"
            raise OperationError(msg, operation="bulk")

        if id_field_alternatives is None:
            id_field_alternatives = ["id"]

        # Accumulator for results
        results: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []

        # Group operations by type for batch processing
        creates, updates, deletes, group_errors = self._group_operations_by_type(operations)
        errors.extend(group_errors)

        # Process creates
        create_results, create_errors = self._process_creates(
            creates, entity_type, create_model_class, create_method, graph_repo
        )
        results.extend(create_results)
        errors.extend(create_errors)

        # Process updates
        update_results, update_errors = self._process_updates(
            updates, entity_type, update_model_class, update_method, graph_repo
        )
        results.extend(update_results)
        errors.extend(update_errors)

        # Process deletes (batch when possible)
        delete_results, delete_errors = self._process_deletes(
            deletes, entity_type, id_field_alternatives, delete_method, graph_repo
        )
        results.extend(delete_results)
        errors.extend(delete_errors)

        return {
            "success": len(results),
            "failed": len(errors),
            "results": results,
            "errors": errors,
        }

    def _group_operations_by_type(
        self, operations: list
    ) -> tuple[list, list, list, list[dict[str, Any]]]:
        """Group operations by type (create, update, delete).

        Args:
            operations: List of operations to group

        Returns:
            Tuple of (creates, updates, deletes, errors)

        """
        creates = []
        updates = []
        deletes = []
        errors = []

        for idx, op in enumerate(operations):
            operation_type = op.get("operation")
            operation_data = op.get("data", {})

            if operation_type == "create":
                creates.append((idx, operation_data))
            elif operation_type == "update":
                updates.append((idx, operation_data))
            elif operation_type == "delete":
                deletes.append((idx, operation_data))
            else:
                errors.append(
                    {
                        "operation_index": idx,
                        "error": f"Unknown operation type: {operation_type}",
                    }
                )

        return creates, updates, deletes, errors

    def _process_creates(
        self,
        creates: list[tuple[int, dict[str, Any]]],
        entity_type: str,
        create_model_class: type,
        create_method: str,
        graph_repo: GraphRepository,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Process create operations one-by-one.

        Args:
            creates: List of (index, data) tuples for create operations
            entity_type: Type of entity for logging
            create_model_class: Pydantic model class for creates
            create_method: Graph repo method name
            graph_repo: Graph repository instance

        Returns:
            Tuple of (results, errors)

        """
        results = []
        errors = []

        for idx, operation_data in creates:
            try:
                entity_create = create_model_class(**operation_data)
                created_entity = getattr(graph_repo, create_method)(entity_create)
                results.append({"operation": "create", "id": created_entity.id})
            except Exception as e:
                logger.exception(
                    "bulk_operation_create_failed",
                    entity_type=entity_type,
                    operation_index=idx,
                    error_type=type(e).__name__,
                    error_message=str(e),
                )
                errors.append({"operation_index": idx, "error": "Operation failed"})

        return results, errors

    def _process_updates(
        self,
        updates: list[tuple[int, dict[str, Any]]],
        entity_type: str,
        update_model_class: type,
        update_method: str,
        graph_repo: GraphRepository,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Process update operations one-by-one.

        Args:
            updates: List of (index, data) tuples for update operations
            entity_type: Type of entity for logging
            update_model_class: Pydantic model class for updates
            update_method: Graph repo method name
            graph_repo: Graph repository instance

        Returns:
            Tuple of (results, errors)

        Raises:
            ValidationError: If an update operation is missing its entity ID.

        """
        results = []
        errors = []

        for idx, operation_data in updates:
            entity_id = operation_data.get("id")
            try:
                if not entity_id:
                    msg = f"{entity_type.capitalize()} ID required for update"
                    raise ValidationError(msg, field="id")

                entity_update = update_model_class(
                    **{k: v for k, v in operation_data.items() if k != "id"}
                )
                updated_entity = getattr(graph_repo, update_method)(entity_id, entity_update)
                results.append({"operation": "update", "id": updated_entity.id})
            except Exception as e:
                logger.exception(
                    "bulk_operation_update_failed",
                    entity_type=entity_type,
                    operation_index=idx,
                    entity_id=entity_id,
                    error_type=type(e).__name__,
                    error_message=str(e),
                )
                errors.append({"operation_index": idx, "error": "Operation failed"})

        return results, errors

    def _process_deletes(
        self,
        deletes: list[tuple[int, dict[str, Any]]],
        entity_type: str,
        id_field_alternatives: list[str],
        delete_method: str,
        graph_repo: GraphRepository,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Process delete operations (batch when possible).

        Args:
            deletes: List of (index, data) tuples for delete operations
            entity_type: Type of entity for logging
            id_field_alternatives: Alternative field names for ID
            delete_method: Graph repo method name for single deletes
            graph_repo: Graph repository instance

        Returns:
            Tuple of (results, errors)

        """
        if not deletes:
            return [], []

        results: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []

        # Extract IDs and build index map
        delete_ids, delete_idx_map, extract_errors = self._extract_delete_ids(
            deletes, entity_type, id_field_alternatives
        )
        errors.extend(extract_errors)

        if not delete_ids:
            return results, errors

        # Try batch delete first, fall back to sequential
        batch_method = f"delete_{entity_type}s_batch"
        if hasattr(graph_repo, batch_method):
            batch_results, batch_errors = self._execute_batch_delete(
                delete_ids, delete_idx_map, entity_type, batch_method, graph_repo
            )
            results.extend(batch_results)
            errors.extend(batch_errors)
        else:
            seq_results, seq_errors = self._execute_sequential_deletes(
                delete_ids, delete_idx_map, entity_type, delete_method, graph_repo
            )
            results.extend(seq_results)
            errors.extend(seq_errors)

        return results, errors

    def _extract_delete_ids(
        self,
        deletes: list[tuple[int, dict[str, Any]]],
        entity_type: str,
        id_field_alternatives: list[str],
    ) -> tuple[list[str], dict[str, int], list[dict[str, Any]]]:
        """Extract IDs from delete operations.

        Args:
            deletes: List of (index, data) tuples
            entity_type: Type of entity for error messages
            id_field_alternatives: Alternative field names for ID

        Returns:
            Tuple of (delete_ids, id_to_index_map, errors)

        """
        delete_ids = []
        delete_idx_map: dict[str, int] = {}
        errors = []

        for idx, operation_data in deletes:
            entity_id = None
            for field in id_field_alternatives:
                entity_id = operation_data.get(field)
                if entity_id:
                    break

            if entity_id:
                delete_ids.append(entity_id)
                delete_idx_map[entity_id] = idx
            else:
                errors.append(
                    {
                        "operation_index": idx,
                        "error": f"{entity_type.capitalize()} ID required for delete",
                    }
                )

        return delete_ids, delete_idx_map, errors

    def _execute_batch_delete(
        self,
        delete_ids: list[str],
        delete_idx_map: dict[str, int],
        entity_type: str,
        batch_method: str,
        graph_repo: GraphRepository,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Execute batch delete operation.

        Args:
            delete_ids: List of entity IDs to delete
            delete_idx_map: Map of ID to operation index
            entity_type: Type of entity for logging
            batch_method: Batch delete method name
            graph_repo: Graph repository instance

        Returns:
            Tuple of (results, errors)

        """
        results = []
        errors = []

        try:
            logger.info(
                "bulk_operation_batch_deleting",
                entity_type=entity_type,
                delete_count=len(delete_ids),
                delete_ids=delete_ids,
            )
            # GraphRepository's batch-delete methods are keyword-only and return
            # a row count (one ``DELETE ... WHERE id IN (...)``), not per-id
            # detail. Call by the entity's id-list keyword (node_ids / edge_ids /
            # template_ids) and synthesize the result shape
            # _process_batch_delete_results consumes. A delete is idempotent — an
            # id that isn't present is simply a no-op — so every requested id is
            # reported deleted (a raised FK/IntegrityError is handled below).
            deleted_count = getattr(graph_repo, batch_method)(**{f"{entity_type}_ids": delete_ids})
            batch_result = {
                "not_found": [],
                "errors": [],
                f"{entity_type}s_deleted": deleted_count,
            }

            # Process batch results
            batch_results, batch_errors = self._process_batch_delete_results(
                delete_ids, delete_idx_map, entity_type, batch_result
            )
            results.extend(batch_results)
            errors.extend(batch_errors)

            logger.info(
                "bulk_operation_batch_deleted",
                entity_type=entity_type,
                deleted_count=deleted_count,
            )

        except Exception as e:
            logger.exception(
                "bulk_operation_batch_delete_failed",
                entity_type=entity_type,
                error_type=type(e).__name__,
                error_message=str(e),
            )
            # A FK RESTRICT violation (e.g. deleting a template still referenced
            # by nodes) raises an IntegrityError for the whole batch. Surface
            # that as "in use" rather than a generic failure so the user knows
            # why — and that the items must be detached/deleted first. (The batch
            # is one statement, so it is all-or-nothing; per-id resilience is
            # deliberately not attempted for a cleanup operation.)
            message = (
                f"Cannot delete: one or more {entity_type}s are still in use by other entities"
                if _is_foreign_key_error(e)
                else "Batch delete failed"
            )
            for entity_id in delete_ids:
                idx = delete_idx_map[entity_id]
                errors.append(
                    {
                        "operation_index": idx,
                        "error": message,
                    }
                )

        return results, errors

    def _process_batch_delete_results(
        self,
        delete_ids: list[str],
        delete_idx_map: dict[str, int],
        entity_type: str,
        batch_result: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Process results from batch delete operation.

        Args:
            delete_ids: List of IDs that were deleted
            delete_idx_map: Map of ID to operation index
            entity_type: Type of entity for error messages
            batch_result: Result from batch delete method

        Returns:
            Tuple of (results, errors)

        """
        errors = []
        not_found = set(batch_result.get("not_found", []))

        # Collect IDs that have errors (to exclude from success count)
        error_ids = set()

        # Record not found failures
        for entity_id in not_found:
            error_ids.add(entity_id)
            idx = delete_idx_map[entity_id]
            errors.append(
                {
                    "operation_index": idx,
                    "error": f"{entity_type.capitalize()} not found: {entity_id}",
                }
            )

        # Record errors from batch method (e.g., templates in use)
        for error_info in batch_result.get("errors", []):
            entity_id = (
                error_info.get("template_id")
                or error_info.get("node_id")
                or error_info.get("edge_id")
            )
            if entity_id and entity_id in delete_idx_map:
                error_ids.add(entity_id)
                idx = delete_idx_map[entity_id]
                errors.append(
                    {
                        "operation_index": idx,
                        "error": error_info.get("error", "Unknown error"),
                    }
                )

        # Record successes (only IDs that are NOT in error_ids)
        results = [
            {"operation": "delete", "id": entity_id}
            for entity_id in delete_ids
            if entity_id not in error_ids
        ]

        return results, errors

    def _execute_sequential_deletes(
        self,
        delete_ids: list[str],
        delete_idx_map: dict[str, int],
        entity_type: str,
        delete_method: str,
        graph_repo: GraphRepository,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Execute deletes one-by-one (fallback when batch not available).

        Args:
            delete_ids: List of entity IDs to delete
            delete_idx_map: Map of ID to operation index
            entity_type: Type of entity for logging
            delete_method: Delete method name
            graph_repo: Graph repository instance

        Returns:
            Tuple of (results, errors)

        """
        results = []
        errors = []

        logger.warning(
            "bulk_operation_no_batch_delete",
            entity_type=entity_type,
            fallback="sequential",
        )

        for entity_id in delete_ids:
            idx = delete_idx_map[entity_id]
            try:
                getattr(graph_repo, delete_method)(entity_id)
                results.append({"operation": "delete", "id": entity_id})
            except Exception as e:
                logger.exception(
                    "bulk_operation_delete_failed",
                    entity_type=entity_type,
                    entity_id=entity_id,
                    error_type=type(e).__name__,
                    error_message=str(e),
                )
                errors.append({"operation_index": idx, "error": "Operation failed"})

        return results, errors
