# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Workflow Operations Service - handles workflow execution operations."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.constants import QUEUE_OPERATIONS
from chaoscypher_core.queue import queue_client
from chaoscypher_core.queue.handler_spec import HandlerSpec
from chaoscypher_core.services.events import event_bus


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite.repos import GraphRepository, SearchRepository
    from chaoscypher_core.llm_queue.queue_service import LLMQueueService
    from chaoscypher_core.services.workflows.management import WorkflowService
    from chaoscypher_core.services.workflows.tools.management import ToolService


logger = structlog.get_logger(__name__)


class WorkflowOperationsService:
    """Service for queuing workflow execution operations.

    Handles executing complete workflows and individual workflow steps.
    All executions are queued and executed asynchronously.

    Uses refactored orchestrator functions instead of monolithic WorkflowEngine.
    """

    def __init__(
        self,
        workflow_service: WorkflowService,
        tool_service: ToolService,
        llm_service: LLMQueueService,
        graph_repository: GraphRepository,
        search_repository: SearchRepository,
        database_name: str,
    ):
        """Initialize workflow operations service.

        Args:
            workflow_service: Backend WorkflowService for workflow data
            tool_service: ToolService for tool execution
            llm_service: LLM service for AI operations
            graph_repository: GraphRepository for graph operations
            search_repository: SearchRepository for search operations
            database_name: Current database name

        """
        # Store services for handler access
        self.services = {
            "workflow_service": workflow_service,
            "tool_service": tool_service,
            "llm_service": llm_service,
            "graph_repository": graph_repository,
            "search_repository": search_repository,
            "database_name": database_name,
        }

        # Workflow handler: idempotent via execution-status short-circuit
        # (completed/failed executions skip re-execution). Step handler:
        # stateless — re-execution is safe (no persistent side effects).
        self.operation_handlers = {
            "execute_workflow": HandlerSpec(
                handler=self._workflow_handler,
                retry_on_crash=True,
            ),
            "execute_step": HandlerSpec(
                handler=self._step_handler,
                retry_on_crash=True,
            ),
        }

        logger.info("workflow_operations_service_initialized", database_name=database_name)

    def register_handlers(self) -> None:
        """Register workflow operation handlers with queue."""
        queue_client.register_handlers(QUEUE_OPERATIONS, self.operation_handlers)  # type: ignore[arg-type]

    # ------------------------------------------------------------------
    # Operation handlers
    # ------------------------------------------------------------------
    async def _workflow_handler(
        self,
        data: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        """Execute workflow operation using refactored orchestrator.

        Args:
            data: Task data with workflow ID and inputs
            metadata: Task metadata
            task_id: Task ID for tracking

        Returns:
            Result dictionary with execution results or error

        """
        from chaoscypher_core.database.adapter_factory import get_sqlite_adapter
        from chaoscypher_core.operations.workflows.orchestrator import execute_workflow_task
        from chaoscypher_core.operations.workflows.repository import WorkflowExecutionRepository
        from chaoscypher_core.operations.workflows.status import WorkflowExecutionStatus

        workflow_id = data["workflow_id"]
        inputs = data["inputs"]
        execution_id = data.get("execution_id")

        # Idempotency guard: if a pre-created execution record already
        # reached a terminal state (completed/failed), the previous
        # attempt finished before the worker crashed — skip re-execution.
        if execution_id:
            idempotency_adapter = get_sqlite_adapter(
                database_name=str(self.services["database_name"]),
            )
            try:
                exec_repo = WorkflowExecutionRepository(idempotency_adapter)
                existing = exec_repo.get_execution(execution_id)
            finally:
                idempotency_adapter.disconnect()
            if existing and existing.get("status") in (
                WorkflowExecutionStatus.COMPLETED,
                WorkflowExecutionStatus.FAILED,
            ):
                logger.info(
                    "workflow_already_terminal",
                    execution_id=execution_id,
                    status=existing["status"],
                )
                return {
                    "success": existing["status"] == WorkflowExecutionStatus.COMPLETED,
                    "skipped": "already_terminal",
                    "step_outputs": {},
                    "output": existing.get("outputs", {}),
                    "error": existing.get("error"),
                }

        logger.info("workflow_operation_executing", workflow_id=workflow_id)

        database_name = str(self.services["database_name"])

        try:
            result = await execute_workflow_task(
                workflow_id=workflow_id,
                inputs=inputs,
                workflow_service=self.services["workflow_service"],
                tool_service=self.services["tool_service"],
                llm_service=self.services["llm_service"],
                graph_repository=self.services["graph_repository"],
                search_repository=self.services["search_repository"],
                database_name=database_name,
                triggered_by=(metadata.get("triggered_by", "manual") if metadata else "manual"),
                execution_id=data.get(
                    "execution_id"
                ),  # Use pre-created execution record if available
            )

            # Record success or failure event
            if result.get("success"):
                event_bus.emit(
                    "task_completed",
                    action="Workflow execution complete",
                    source="worker",
                    details={"workflow_id": workflow_id},
                    database_name=database_name,
                )
            else:
                event_bus.emit(
                    "task_failed",
                    action="Workflow execution failed",
                    source="worker",
                    reason=result.get("error"),
                    details={"workflow_id": workflow_id},
                    database_name=database_name,
                )

            return {
                "success": result.get("success", False),
                "step_outputs": result.get("step_outputs", {}),
                "output": result.get(
                    "outputs", {}
                ),  # Note: orchestrator returns "outputs" not "output"
                "error": result.get("error"),
            }
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.exception(
                "workflow_operation_execution_failed",
                workflow_id=workflow_id,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )

            event_bus.emit(
                "task_failed",
                action="Workflow execution failed",
                source="worker",
                reason=str(exc),
                details={"workflow_id": workflow_id},
                database_name=database_name,
            )

            return {
                "success": False,
                "step_outputs": {},
                "output": {},
                "error": str(exc),
            }

    async def _step_handler(
        self,
        data: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        """Execute workflow step operation using refactored orchestrator.

        Args:
            data: Task data with step configuration and inputs
            metadata: Task metadata
            task_id: Task ID for tracking

        Returns:
            Result dictionary with step output or error

        """
        from chaoscypher_core.operations.workflows.orchestrator import execute_step_task

        step_id = data["step_id"]
        step_config = data["step_config"]
        step_inputs = data["step_inputs"]
        workflow_context = data.get("workflow_context", {})

        logger.info("workflow_step_operation_executing", step_id=step_id)

        try:
            result = await execute_step_task(
                step_config=step_config,
                step_inputs=step_inputs,
                workflow_context=workflow_context,
                workflow_service=self.services["workflow_service"],
                tool_service=self.services["tool_service"],
                llm_service=self.services["llm_service"],
                graph_repository=self.services["graph_repository"],
                search_repository=self.services["search_repository"],
                database_name=str(self.services["database_name"]),
            )
            return {
                "success": result.get("success", False),
                "output": result.get("output", {}),
                "error": result.get("error"),
            }
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.exception(
                "workflow_step_operation_execution_failed",
                step_id=step_id,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            return {
                "success": False,
                "output": {},
                "error": str(exc),
            }
