# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Unit tests for BulkOperationsService internal handlers.

Covers grouping, per-type processing (create/update/delete), the batch vs.
sequential delete branch selection, batch-result interpretation, and the
end-to-end ``execute_bulk_operations`` orchestration with mixed operations.

All tests drive the service with a MagicMock graph repository so no real
storage is touched.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from chaoscypher_core.operations.bulk.bulk_service import BulkOperationsService


def _make_service() -> BulkOperationsService:
    """Construct the service with a MagicMock repository."""
    return BulkOperationsService(graph_repository=MagicMock())


def _entity_model(**kwargs: Any) -> Any:
    """A permissive model class accepting arbitrary kwargs."""

    class _Model:
        def __init__(self, **data: Any) -> None:
            self.__dict__.update(data)

    return _Model


# ---------------------------------------------------------------------------
# _group_operations_by_type
# ---------------------------------------------------------------------------
class TestGroupOperationsByType:
    def test_sorts_into_buckets(self) -> None:
        service = _make_service()
        ops = [
            {"operation": "create", "data": {"name": "a"}},
            {"operation": "update", "data": {"id": "1"}},
            {"operation": "delete", "data": {"id": "2"}},
        ]
        creates, updates, deletes, errors = service._group_operations_by_type(ops)
        assert creates == [(0, {"name": "a"})]
        assert updates == [(1, {"id": "1"})]
        assert deletes == [(2, {"id": "2"})]
        assert errors == []

    def test_unknown_type_recorded_as_error(self) -> None:
        service = _make_service()
        ops = [{"operation": "frobnicate", "data": {}}]
        creates, updates, deletes, errors = service._group_operations_by_type(ops)
        assert (creates, updates, deletes) == ([], [], [])
        assert len(errors) == 1
        assert errors[0]["operation_index"] == 0
        assert "Unknown operation type" in errors[0]["error"]

    def test_missing_data_defaults_to_empty_dict(self) -> None:
        service = _make_service()
        ops = [{"operation": "create"}]  # no "data" key
        creates, _u, _d, _e = service._group_operations_by_type(ops)
        assert creates == [(0, {})]


# ---------------------------------------------------------------------------
# _process_creates
# ---------------------------------------------------------------------------
class TestProcessCreates:
    def test_success_records_created_id(self) -> None:
        service = _make_service()
        repo = MagicMock()
        repo.create_node.return_value = MagicMock(id="new-1")

        results, errors = service._process_creates(
            creates=[(0, {"name": "x"})],
            entity_type="node",
            create_model_class=_entity_model(),
            create_method="create_node",
            graph_repo=repo,
        )
        assert results == [{"operation": "create", "id": "new-1"}]
        assert errors == []

    def test_exception_recorded_as_error(self) -> None:
        service = _make_service()
        repo = MagicMock()
        repo.create_node.side_effect = RuntimeError("boom")

        results, errors = service._process_creates(
            creates=[(3, {"name": "x"})],
            entity_type="node",
            create_model_class=_entity_model(),
            create_method="create_node",
            graph_repo=repo,
        )
        assert results == []
        assert errors == [{"operation_index": 3, "error": "Operation failed"}]


# ---------------------------------------------------------------------------
# _process_updates
# ---------------------------------------------------------------------------
class TestProcessUpdates:
    def test_success_records_updated_id(self) -> None:
        service = _make_service()
        repo = MagicMock()
        repo.update_node.return_value = MagicMock(id="u-1")

        results, errors = service._process_updates(
            updates=[(0, {"id": "u-1", "name": "renamed"})],
            entity_type="node",
            update_model_class=_entity_model(),
            update_method="update_node",
            graph_repo=repo,
        )
        assert results == [{"operation": "update", "id": "u-1"}]
        assert errors == []
        # The id key is stripped before constructing the update model.
        _args, kwargs = repo.update_node.call_args
        assert kwargs == {}
        # Positional: (entity_id, entity_update)
        assert repo.update_node.call_args[0][0] == "u-1"

    def test_missing_id_recorded_as_error(self) -> None:
        service = _make_service()
        results, errors = service._process_updates(
            updates=[(2, {"name": "no id"})],
            entity_type="node",
            update_model_class=_entity_model(),
            update_method="update_node",
            graph_repo=MagicMock(),
        )
        assert results == []
        assert errors == [{"operation_index": 2, "error": "Operation failed"}]


# ---------------------------------------------------------------------------
# _extract_delete_ids
# ---------------------------------------------------------------------------
class TestExtractDeleteIds:
    def test_primary_id_field(self) -> None:
        service = _make_service()
        ids, idx_map, errors = service._extract_delete_ids(
            deletes=[(0, {"id": "n1"})],
            entity_type="node",
            id_field_alternatives=["id"],
        )
        assert ids == ["n1"]
        assert idx_map == {"n1": 0}
        assert errors == []

    def test_id_field_alternative_fallback(self) -> None:
        service = _make_service()
        ids, idx_map, errors = service._extract_delete_ids(
            deletes=[(1, {"node_id": "n2"})],
            entity_type="node",
            id_field_alternatives=["id", "node_id"],
        )
        assert ids == ["n2"]
        assert idx_map == {"n2": 1}
        assert errors == []

    def test_missing_id_recorded_as_error(self) -> None:
        service = _make_service()
        ids, idx_map, errors = service._extract_delete_ids(
            deletes=[(4, {"label": "no id here"})],
            entity_type="edge",
            id_field_alternatives=["id"],
        )
        assert ids == []
        assert idx_map == {}
        assert len(errors) == 1
        assert errors[0]["operation_index"] == 4
        assert "Edge ID required for delete" in errors[0]["error"]


# ---------------------------------------------------------------------------
# _process_deletes — branch selection (batch vs sequential)
# ---------------------------------------------------------------------------
class TestProcessDeletesBranchSelection:
    def test_no_deletes_short_circuits(self) -> None:
        service = _make_service()
        results, errors = service._process_deletes(
            deletes=[],
            entity_type="node",
            id_field_alternatives=["id"],
            delete_method="delete_node",
            graph_repo=MagicMock(),
        )
        assert results == []
        assert errors == []

    def test_all_ids_missing_returns_only_errors(self) -> None:
        service = _make_service()
        results, errors = service._process_deletes(
            deletes=[(0, {"no_id": True})],
            entity_type="node",
            id_field_alternatives=["id"],
            delete_method="delete_node",
            graph_repo=MagicMock(),
        )
        assert results == []
        assert len(errors) == 1

    def test_batch_branch_when_batch_method_present(self) -> None:
        service = _make_service()
        # spec restricts attributes so only the batch method exists.
        repo = MagicMock(spec=["delete_nodes_batch"])
        repo.delete_nodes_batch.return_value = {
            "nodes_deleted": 2,
            "not_found": [],
            "errors": [],
        }
        results, errors = service._process_deletes(
            deletes=[(0, {"id": "a"}), (1, {"id": "b"})],
            entity_type="node",
            id_field_alternatives=["id"],
            delete_method="delete_node",
            graph_repo=repo,
        )
        assert repo.delete_nodes_batch.called
        assert {r["id"] for r in results} == {"a", "b"}
        assert errors == []

    def test_sequential_branch_when_batch_method_absent(self) -> None:
        service = _make_service()
        # spec WITHOUT the batch method forces hasattr(...) == False.
        repo = MagicMock(spec=["delete_node"])
        results, errors = service._process_deletes(
            deletes=[(0, {"id": "a"}), (1, {"id": "b"})],
            entity_type="node",
            id_field_alternatives=["id"],
            delete_method="delete_node",
            graph_repo=repo,
        )
        assert repo.delete_node.call_count == 2
        assert {r["id"] for r in results} == {"a", "b"}
        assert errors == []


# ---------------------------------------------------------------------------
# _execute_batch_delete
# ---------------------------------------------------------------------------
class TestExecuteBatchDelete:
    def test_success(self) -> None:
        service = _make_service()
        repo = MagicMock()
        repo.delete_nodes_batch.return_value = 1  # row count — the keyword-only int API
        results, errors = service._execute_batch_delete(
            delete_ids=["a"],
            delete_idx_map={"a": 0},
            entity_type="node",
            batch_method="delete_nodes_batch",
            graph_repo=repo,
        )
        # Must be called by the entity's id-list keyword, NOT positionally. The
        # real GraphRepository.delete_*_batch are keyword-only, so a positional
        # call raised "takes 1 positional argument but 2 were given".
        repo.delete_nodes_batch.assert_called_once_with(node_ids=["a"])
        assert results == [{"operation": "delete", "id": "a"}]
        assert errors == []

    def test_template_batch_delete_uses_keyword_api(self) -> None:
        """Regression: deleting leftover templates hit the keyword-only API.

        ``GraphRepository.delete_templates_batch(*, template_ids)`` is keyword
        only and returns a count; the bulk path used to call it positionally
        (``method(delete_ids)``) and expect a dict, raising a TypeError.
        """
        service = _make_service()
        repo = MagicMock()
        repo.delete_templates_batch.return_value = 2
        results, errors = service._execute_batch_delete(
            delete_ids=["t1", "t2"],
            delete_idx_map={"t1": 0, "t2": 1},
            entity_type="template",
            batch_method="delete_templates_batch",
            graph_repo=repo,
        )
        repo.delete_templates_batch.assert_called_once_with(template_ids=["t1", "t2"])
        assert {r["id"] for r in results} == {"t1", "t2"}
        assert errors == []

    def test_exception_marks_all_failed(self) -> None:
        service = _make_service()
        repo = MagicMock()
        repo.delete_nodes_batch.side_effect = RuntimeError("db down")
        results, errors = service._execute_batch_delete(
            delete_ids=["a", "b"],
            delete_idx_map={"a": 0, "b": 1},
            entity_type="node",
            batch_method="delete_nodes_batch",
            graph_repo=repo,
        )
        assert results == []
        assert len(errors) == 2
        assert all(e["error"] == "Batch delete failed" for e in errors)

    def test_foreign_key_error_reports_in_use(self) -> None:
        """A FK RESTRICT violation surfaces as 'in use', not a generic failure.

        Deleting a template still referenced by a node raises the store's
        ``IntegrityError``; the user should be told it is in use (so they detach
        it first), not see an opaque "Batch delete failed".
        """
        service = _make_service()
        repo = MagicMock()

        class IntegrityError(Exception):  # mirrors SQLAlchemy's exception type name
            pass

        repo.delete_templates_batch.side_effect = IntegrityError("FOREIGN KEY constraint failed")
        results, errors = service._execute_batch_delete(
            delete_ids=["t1"],
            delete_idx_map={"t1": 0},
            entity_type="template",
            batch_method="delete_templates_batch",
            graph_repo=repo,
        )
        assert results == []
        assert len(errors) == 1
        assert "still in use" in errors[0]["error"]
        assert "template" in errors[0]["error"]


# ---------------------------------------------------------------------------
# _process_batch_delete_results
# ---------------------------------------------------------------------------
class TestProcessBatchDeleteResults:
    def test_not_found_excluded_from_successes(self) -> None:
        service = _make_service()
        results, errors = service._process_batch_delete_results(
            delete_ids=["a", "b"],
            delete_idx_map={"a": 0, "b": 1},
            entity_type="node",
            batch_result={"not_found": ["b"], "errors": []},
        )
        # 'a' succeeds, 'b' is not found.
        assert results == [{"operation": "delete", "id": "a"}]
        assert len(errors) == 1
        assert errors[0]["operation_index"] == 1
        assert "not found" in errors[0]["error"]

    def test_in_use_errors_excluded_from_successes(self) -> None:
        service = _make_service()
        results, errors = service._process_batch_delete_results(
            delete_ids=["t1", "t2"],
            delete_idx_map={"t1": 0, "t2": 1},
            entity_type="template",
            batch_result={
                "not_found": [],
                "errors": [{"template_id": "t2", "error": "Template in use"}],
            },
        )
        assert results == [{"operation": "delete", "id": "t1"}]
        assert len(errors) == 1
        assert errors[0]["operation_index"] == 1
        assert errors[0]["error"] == "Template in use"

    def test_clean_batch_all_succeed(self) -> None:
        service = _make_service()
        results, errors = service._process_batch_delete_results(
            delete_ids=["a", "b"],
            delete_idx_map={"a": 0, "b": 1},
            entity_type="node",
            batch_result={"not_found": [], "errors": []},
        )
        assert {r["id"] for r in results} == {"a", "b"}
        assert errors == []


# ---------------------------------------------------------------------------
# _execute_sequential_deletes
# ---------------------------------------------------------------------------
class TestExecuteSequentialDeletes:
    def test_success_and_failure_mixed(self) -> None:
        service = _make_service()
        repo = MagicMock()

        def _delete(entity_id: str) -> None:
            if entity_id == "bad":
                raise RuntimeError("nope")

        repo.delete_node.side_effect = _delete

        results, errors = service._execute_sequential_deletes(
            delete_ids=["good", "bad"],
            delete_idx_map={"good": 0, "bad": 1},
            entity_type="node",
            delete_method="delete_node",
            graph_repo=repo,
        )
        assert results == [{"operation": "delete", "id": "good"}]
        assert errors == [{"operation_index": 1, "error": "Operation failed"}]


# ---------------------------------------------------------------------------
# execute_bulk_operations — orchestration
# ---------------------------------------------------------------------------
class TestExecuteBulkOperations:
    @pytest.mark.asyncio
    async def test_mixed_operations_success_and_failed_counts(self) -> None:
        repo = MagicMock(spec=["create_node", "update_node", "delete_node"])
        repo.create_node.return_value = MagicMock(id="c1")
        repo.update_node.return_value = MagicMock(id="u1")
        # No batch method on spec → sequential delete branch.
        service = BulkOperationsService(graph_repository=repo)

        ops = [
            {"operation": "create", "data": {"name": "new"}},
            {"operation": "update", "data": {"id": "u1", "name": "edit"}},
            {"operation": "delete", "data": {"id": "d1"}},
            {"operation": "bogus", "data": {}},  # unknown type → error
        ]

        result = await service.execute_bulk_operations(
            operations=ops,
            entity_type="node",
            create_model_class=_entity_model(),
            update_model_class=_entity_model(),
            create_method="create_node",
            update_method="update_node",
            delete_method="delete_node",
        )

        # 3 successes (create, update, delete), 1 failure (unknown type).
        assert result["success"] == 3
        assert result["failed"] == 1
        assert len(result["results"]) == 3
        assert len(result["errors"]) == 1

    @pytest.mark.asyncio
    async def test_default_id_field_alternatives(self) -> None:
        repo = MagicMock(spec=["create_node", "update_node", "delete_node"])
        service = BulkOperationsService(graph_repository=repo)

        # delete with only "id" works under the default ["id"] alternatives.
        result = await service.execute_bulk_operations(
            operations=[{"operation": "delete", "data": {"id": "x"}}],
            entity_type="node",
            create_model_class=_entity_model(),
            update_model_class=_entity_model(),
            create_method="create_node",
            update_method="update_node",
            delete_method="delete_node",
        )
        assert result["success"] == 1
        assert result["failed"] == 0
