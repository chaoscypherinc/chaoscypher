# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Exception-contract tests for services/workflows/management/{io,history}.py.

Pins the exception types raised at each validation and operation-failure site
so that callers (Cortex error mapper, Neuron worker) receive structured
ChaosCypherException subclasses instead of bare stdlib errors.

Sites covered (10 total):
  io.py       — 9 sites (lines ~93, 270, 282, 285, 288, 293, 301, 331, 380)
  history.py  — 1 site  (line ~62, kept as TypeError + noqa: programmer error)
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.exceptions import (
    ChaosCypherException,
    ConflictError,
    NotFoundError,
    OperationError,
    ValidationError,
)
from chaoscypher_core.services.workflows.management.history import _normalize_user
from chaoscypher_core.services.workflows.management.io import WorkflowPortabilityService


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def adapter(tmp_path: Path) -> Generator[SqliteAdapter]:
    db_path = tmp_path / "app.db"
    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)
    a = SqliteAdapter(str(db_path), database_name="default")
    a.connect()
    yield a
    a.disconnect()


@pytest.fixture
def svc(adapter: SqliteAdapter) -> WorkflowPortabilityService:
    return WorkflowPortabilityService(repository=adapter, database_name="default")


def _valid_export(name: str = "My Workflow") -> dict[str, Any]:
    """Return a minimal valid export payload."""
    return {
        "version": "1.0",
        "exported_at": datetime.now(UTC).isoformat() + "Z",
        "workflow": {
            "name": name,
            "input_schema": {"type": "object"},
            "output_schema": {"type": "object"},
        },
        "steps": [],
    }


# ---------------------------------------------------------------------------
# io.py:~93 — export_workflow() when workflow not found
# NotFoundError for a missing workflow ID.
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExportWorkflowNotFound:
    """NotFoundError is raised when the requested workflow does not exist."""

    def test_raises_not_found_error(self, svc: WorkflowPortabilityService) -> None:
        with pytest.raises(NotFoundError) as exc_info:
            svc.export_workflow("nonexistent-workflow-id")
        exc = exc_info.value
        assert isinstance(exc, ChaosCypherException)
        assert exc.code == "NOT_FOUND"
        assert exc.details["resource_type"] == "Workflow"
        assert exc.details["identifier"] == "nonexistent-workflow-id"

    def test_not_found_error_is_chaoscypher_exception(
        self, svc: WorkflowPortabilityService
    ) -> None:
        with pytest.raises(ChaosCypherException):
            svc.export_workflow("ghost-id")


# ---------------------------------------------------------------------------
# io.py:~282 — _validate_import_data() missing 'version' field
# ValidationError(field="version").
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidateImportDataMissingVersion:
    """ValidationError(field='version') when 'version' is absent from export payload."""

    def test_raises_validation_error_missing_version(self, svc: WorkflowPortabilityService) -> None:
        payload: dict[str, Any] = {"workflow": {}, "steps": []}  # no 'version'
        with pytest.raises(ValidationError) as exc_info:
            svc.import_workflow(payload)
        exc = exc_info.value
        assert isinstance(exc, ChaosCypherException)
        assert exc.code == "VALIDATION_ERROR"
        assert exc.details.get("field") == "version"


# ---------------------------------------------------------------------------
# io.py:~285 — _validate_import_data() missing 'workflow' field
# ValidationError(field="workflow").
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidateImportDataMissingWorkflow:
    """ValidationError(field='workflow') when 'workflow' key is absent."""

    def test_raises_validation_error_missing_workflow(
        self, svc: WorkflowPortabilityService
    ) -> None:
        payload: dict[str, Any] = {"version": "1.0", "steps": []}  # no 'workflow'
        with pytest.raises(ValidationError) as exc_info:
            svc.import_workflow(payload)
        exc = exc_info.value
        assert isinstance(exc, ChaosCypherException)
        assert exc.code == "VALIDATION_ERROR"
        assert exc.details.get("field") == "workflow"


# ---------------------------------------------------------------------------
# io.py:~288 — _validate_import_data() missing 'steps' field
# ValidationError(field="steps").
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidateImportDataMissingSteps:
    """ValidationError(field='steps') when 'steps' key is absent."""

    def test_raises_validation_error_missing_steps(self, svc: WorkflowPortabilityService) -> None:
        payload: dict[str, Any] = {"version": "1.0", "workflow": {}}  # no 'steps'
        with pytest.raises(ValidationError) as exc_info:
            svc.import_workflow(payload)
        exc = exc_info.value
        assert isinstance(exc, ChaosCypherException)
        assert exc.code == "VALIDATION_ERROR"
        assert exc.details.get("field") == "steps"


# ---------------------------------------------------------------------------
# io.py:~293 — _validate_import_data() incompatible version
# ValidationError(field="version").
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidateImportDataBadVersion:
    """ValidationError(field='version') when version is not '1.0'."""

    def test_raises_validation_error_wrong_version(self, svc: WorkflowPortabilityService) -> None:
        payload: dict[str, Any] = {
            "version": "2.0",
            "workflow": {"name": "X", "input_schema": {}, "output_schema": {}},
            "steps": [],
        }
        with pytest.raises(ValidationError) as exc_info:
            svc.import_workflow(payload)
        exc = exc_info.value
        assert isinstance(exc, ChaosCypherException)
        assert exc.code == "VALIDATION_ERROR"
        assert exc.details.get("field") == "version"
        assert "2.0" in exc.message


# ---------------------------------------------------------------------------
# io.py:~301 — _validate_import_data() missing required workflow sub-field
# ValidationError(field=<field>) for each of name/input_schema/output_schema.
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidateImportDataMissingWorkflowField:
    """ValidationError(field=<field>) for each missing required workflow sub-field."""

    @pytest.mark.parametrize("missing_field", ["name", "input_schema", "output_schema"])
    def test_raises_validation_error_for_missing_field(
        self, svc: WorkflowPortabilityService, missing_field: str
    ) -> None:
        base_workflow: dict[str, Any] = {
            "name": "X",
            "input_schema": {"type": "object"},
            "output_schema": {"type": "object"},
        }
        del base_workflow[missing_field]
        payload: dict[str, Any] = {
            "version": "1.0",
            "workflow": base_workflow,
            "steps": [],
        }
        with pytest.raises(ValidationError) as exc_info:
            svc.import_workflow(payload)
        exc = exc_info.value
        assert isinstance(exc, ChaosCypherException)
        assert exc.code == "VALIDATION_ERROR"
        assert exc.details.get("field") == missing_field


# ---------------------------------------------------------------------------
# io.py:~331 — _handle_duplicate_name() with on_duplicate="fail"
# ConflictError when the name already exists.
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHandleDuplicateNameFail:
    """ConflictError is raised when on_duplicate='fail' and name already exists."""

    def test_raises_conflict_error_on_duplicate(self, svc: WorkflowPortabilityService) -> None:
        # First import succeeds
        svc.import_workflow(_valid_export("Flow A"), on_duplicate="fail")
        # Second import with same name + on_duplicate="fail" must raise ConflictError
        with pytest.raises(ConflictError) as exc_info:
            svc.import_workflow(_valid_export("Flow A"), on_duplicate="fail")
        exc = exc_info.value
        assert isinstance(exc, ChaosCypherException)
        assert exc.code == "CONFLICT"
        assert "Flow A" in exc.message

    def test_conflict_error_is_chaoscypher_exception(self, svc: WorkflowPortabilityService) -> None:
        svc.import_workflow(_valid_export("Flow B"), on_duplicate="fail")
        with pytest.raises(ChaosCypherException):
            svc.import_workflow(_valid_export("Flow B"), on_duplicate="fail")


# ---------------------------------------------------------------------------
# io.py:~380 — _validate_tool_references() when a tool_id is not available
# ValidationError(field="tool_id").
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidateToolReferencesToolNotFound:
    """ValidationError(field='tool_id') when a step references a non-existent tool."""

    def test_raises_validation_error_for_missing_tool(
        self, svc: WorkflowPortabilityService
    ) -> None:
        # Attach a tool_service stub that returns no tools
        mock_tool_service = MagicMock()
        mock_tool_service.list_system_tools.return_value = []
        mock_tool_service.list_user_tools.return_value = []
        svc.tool_service = mock_tool_service

        payload: dict[str, Any] = {
            "version": "1.0",
            "workflow": {
                "name": "With Missing Tool",
                "input_schema": {"type": "object"},
                "output_schema": {"type": "object"},
            },
            "steps": [
                {
                    "step_number": 1,
                    "name": "Step 1",
                    "tool_type": "system",
                    "tool_id": "nonexistent.tool",
                    "configuration": {},
                }
            ],
        }
        with pytest.raises(ValidationError) as exc_info:
            svc.import_workflow(payload)
        exc = exc_info.value
        assert isinstance(exc, ChaosCypherException)
        assert exc.code == "VALIDATION_ERROR"
        assert exc.details.get("field") == "tool_id"
        assert "nonexistent.tool" in exc.message


# ---------------------------------------------------------------------------
# io.py:~270 — import_workflow() retry exhaustion → OperationError
# OperationError(operation="workflow_import") with __cause__ after 10 failures.
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestImportWorkflowRetryExhaustion:
    """OperationError is raised after all rename-collision retries are exhausted."""

    def test_raises_operation_error_after_max_retries(
        self, adapter: SqliteAdapter, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Force every _create_workflow_entities call to raise ConflictError."""
        svc = WorkflowPortabilityService(repository=adapter, database_name="default")

        # Make _handle_duplicate_name always return a new name (not "fail" / "skip"),
        # then make create_workflow_safe always raise ConflictError to exhaust retries.
        def _always_conflict(workflow: dict[str, Any]) -> dict[str, Any]:
            raise ConflictError("Simulated concurrent name collision")

        monkeypatch.setattr(adapter, "create_workflow_safe", _always_conflict)

        with pytest.raises(OperationError) as exc_info:
            svc.import_workflow(_valid_export("Clash"), on_duplicate="rename")
        exc = exc_info.value
        assert isinstance(exc, ChaosCypherException)
        assert exc.code == "OPERATION_ERROR"
        assert exc.details.get("operation") == "workflow_import"
        # __cause__ must be the last ConflictError from the retry loop
        assert isinstance(exc.__cause__, ConflictError)

    def test_operation_error_is_chaoscypher_exception(
        self, adapter: SqliteAdapter, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        svc = WorkflowPortabilityService(repository=adapter, database_name="default")

        def _always_conflict(workflow: dict[str, Any]) -> dict[str, Any]:
            raise ConflictError("collision")

        monkeypatch.setattr(adapter, "create_workflow_safe", _always_conflict)

        with pytest.raises(ChaosCypherException):
            svc.import_workflow(_valid_export("Clash2"), on_duplicate="rename")


# ---------------------------------------------------------------------------
# history.py:~62 — _normalize_user() with invalid type → TypeError (programmer error)
# Kept as stdlib TypeError + noqa; verified here to document the contract.
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestNormalizeUserInvalidType:
    """TypeError is kept for _normalize_user() — it is a programmer-error guard."""

    def test_raises_type_error_for_invalid_user_type(self) -> None:
        """Passing an unsupported type is a programmer error; TypeError is intentional."""
        with pytest.raises(TypeError, match="user must be"):
            _normalize_user(42)

    def test_raises_type_error_for_list_input(self) -> None:
        with pytest.raises(TypeError, match="user must be"):
            _normalize_user([{"id": 1}])

    def test_raises_type_error_message_includes_type_name(self) -> None:
        with pytest.raises(TypeError) as exc_info:
            _normalize_user(3.14)
        assert "float" in str(exc_info.value)
