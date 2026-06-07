# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Workflow Execution Orchestrator.

Orchestrates workflow execution using the refactored engine components.
This replaces the monolithic WorkflowEngine with composable services.

Architecture:
- Uses engine services (WorkflowService, build_workflow_graph, etc.)
- Uses backend execution tracking (WorkflowExecutionRepository)
- Dependency injection for all services (testable, flexible)
- No framework dependencies (works in worker, CLI, tests)
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

import structlog

from chaoscypher_core.exceptions import NotFoundError, ValidationError
from chaoscypher_core.operations.workflows.repository import (
    WorkflowExecutionRepository,
)
from chaoscypher_core.operations.workflows.status import WorkflowExecutionStatus
from chaoscypher_core.services.workflows.engine.builder import build_workflow_graph
from chaoscypher_core.services.workflows.engine.output_parser import OutputManager
from chaoscypher_core.services.workflows.engine.state import WorkflowState
from chaoscypher_core.services.workflows.engine.step_executor import StepExecutor
from chaoscypher_core.services.workflows.engine.tool_executor_adapter import (
    BackendToolExecutorAdapter,
)
from chaoscypher_core.services.workflows.engine.validator import WorkflowValidator
from chaoscypher_core.utils.id import generate_id


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite.repos import GraphRepository

logger = structlog.get_logger(__name__)


async def _ensure_execution_record(
    execution_repo: WorkflowExecutionRepository,
    execution_id: str | None,
    workflow_id: str,
    triggered_by: str,
    trigger_id: str | None,
    parent_execution_id: str | None,
    inputs: dict[str, Any],
) -> str:
    """Create or verify an execution record, returning the execution ID.

    The ``create_execution`` write goes through ``asyncio.to_thread`` so
    its internal ``with adapter.transaction(): ...`` block — and any
    ``SafeSession._retry_delay`` ``time.sleep`` it triggers under
    SQLITE_BUSY contention — runs on a worker thread instead of blocking
    the event loop (2026-05-23 perf fix).

    Args:
        execution_repo: Repository for execution tracking.
        execution_id: Pre-created execution ID (if any).
        workflow_id: Workflow being executed.
        triggered_by: Trigger source label.
        trigger_id: Optional trigger ID.
        parent_execution_id: Optional parent execution ID.
        inputs: Workflow input parameters.

    Returns:
        The execution ID (existing or newly created).

    """
    if execution_id:
        existing = execution_repo.get_execution(execution_id)
        if existing:
            return execution_id
        logger.warning(
            "execution_record_not_found_creating_new",
            provided_execution_id=execution_id,
            workflow_id=workflow_id,
        )

    final_id = execution_id or generate_id()
    await asyncio.to_thread(
        execution_repo.create_execution,
        {
            "id": final_id,
            "workflow_id": workflow_id,
            "triggered_by": triggered_by,
            "trigger_id": trigger_id,
            "parent_execution_id": parent_execution_id,
            "inputs": inputs,
            "status": WorkflowExecutionStatus.PENDING,
            "created_at": datetime.now(UTC),
        },
    )
    return final_id


class _WorkflowExecutorWrapper:
    """Wrapper to enable nested workflow execution via recursive orchestrator calls.

    Captures the outer service references so that nested workflow steps
    can invoke ``execute_workflow_task`` without passing every dependency
    manually.
    """

    def __init__(
        self,
        *,
        workflow_service: Any,
        tool_service: Any,
        llm_service: Any,
        graph_repository: GraphRepository,
        search_repository: Any,
        database_name: str,
        depth: int = 0,
        lineage: frozenset[str] = frozenset(),
        max_recursion_depth: int = 10,
    ) -> None:
        """Store outer-orchestrator references for nested workflow execution."""
        self._workflow_service = workflow_service
        self._tool_service = tool_service
        self._llm_service = llm_service
        self._graph_repository = graph_repository
        self._search_repository = search_repository
        self._database_name = database_name
        self._depth = depth
        self._lineage = lineage
        self._max_recursion_depth = max_recursion_depth

    async def execute_workflow(
        self,
        workflow_id: str,
        inputs: dict[str, Any],
        triggered_by: str = "workflow",
        parent_execution_id: str | None = None,
    ) -> dict[str, Any]:
        """Execute a nested workflow.

        Enforces recursion cycle + depth bounds before dispatching to the
        orchestrator task.

        Args:
            workflow_id: Workflow to execute.
            inputs: Input parameters.
            triggered_by: Trigger source label.
            parent_execution_id: Parent execution for lineage tracking.

        Returns:
            Execution result dictionary.

        Raises:
            WorkflowRecursionError: If workflow_id is already in the
                lineage (cycle) or the current depth reached the bound.
        """
        from chaoscypher_core.exceptions import WorkflowRecursionError

        if workflow_id in self._lineage:
            raise WorkflowRecursionError(
                message=(
                    f"Workflow recursion cycle: {workflow_id} already in lineage "
                    f"{sorted(self._lineage)}"
                ),
                workflow_id=workflow_id,
                depth=self._depth,
                lineage=self._lineage,
            )
        if self._depth >= self._max_recursion_depth:
            raise WorkflowRecursionError(
                message=(
                    f"Workflow recursion depth {self._depth} reached bound "
                    f"{self._max_recursion_depth}"
                ),
                workflow_id=workflow_id,
                depth=self._depth,
                lineage=self._lineage,
            )
        return await execute_workflow_task(
            workflow_id=workflow_id,
            inputs=inputs,
            workflow_service=self._workflow_service,
            tool_service=self._tool_service,
            llm_service=self._llm_service,
            graph_repository=self._graph_repository,
            search_repository=self._search_repository,
            database_name=self._database_name,
            triggered_by=triggered_by,
            parent_execution_id=parent_execution_id,
            depth=self._depth + 1,
            lineage=self._lineage | {workflow_id},
            max_recursion_depth=self._max_recursion_depth,
        )


async def execute_workflow_task(
    workflow_id: str,
    inputs: dict[str, Any],
    workflow_service: Any,  # Backend WorkflowService
    tool_service: Any,
    llm_service: Any,
    graph_repository: GraphRepository,
    search_repository: Any,
    database_name: str,
    triggered_by: str = "manual",
    trigger_id: str | None = None,
    parent_execution_id: str | None = None,
    execution_id: str | None = None,
    depth: int = 0,
    lineage: frozenset[str] | None = None,
    max_recursion_depth: int = 10,
) -> dict[str, Any]:
    """Execute a complete workflow using refactored engine components.

    This function orchestrates workflow execution without needing a WorkflowEngine class.
    It composes the refactored services to achieve the same result.

    Args:
        workflow_id: Workflow ID to execute
        inputs: Workflow input parameters
        workflow_service: Backend WorkflowService instance (for getting workflow data)
        tool_service: ToolService for tool execution
        llm_service: LLM service for AI operations
        graph_repository: GraphRepository for graph operations
        search_repository: SearchRepository for search operations
        database_name: Current database name
        triggered_by: How workflow was triggered
        trigger_id: Optional trigger ID
        parent_execution_id: Optional parent execution ID
        execution_id: Optional pre-created execution ID (if None, creates new)
        depth: Current recursion depth for nested workflow calls.
        lineage: Frozen set of ancestor workflow ids already on the stack, used
            to detect cycles when a workflow invokes another workflow.
        max_recursion_depth: Hard cap on how deep nested workflow calls may go.

    Returns:
        Execution result dictionary with success, outputs, execution_id

    Raises:
        NotFoundError: If the workflow does not exist.
        ValidationError: If the workflow definition, inputs, or tool references are invalid,
            or if the workflow is inactive.
        Exception: If execution fails.
    """
    logger.info(
        "workflow_execution_started",
        workflow_id=workflow_id,
        input_keys=list(inputs.keys()),
        triggered_by=triggered_by,
        database_name=database_name,
    )

    # Initialize output manager
    output_manager = OutputManager()

    from chaoscypher_core.database.adapter_factory import get_sqlite_adapter

    exec_adapter = get_sqlite_adapter(database_name=database_name)
    execution_repo = WorkflowExecutionRepository(exec_adapter)

    # 1. Get workflow and steps
    workflow = workflow_service.get_workflow(workflow_id)
    if not workflow:
        raise NotFoundError("Workflow", workflow_id)

    # 2. Get workflow steps (needed for validation)
    steps = workflow_service.list_workflow_steps(workflow_id)
    steps.sort(key=lambda s: s["step_number"])

    # Validate tool references at execution time (tools may have been deleted after import)
    if tool_service is not None:
        available_system = {t["id"] for t in tool_service.list_system_tools()}
        available_user = {t["id"] for t in tool_service.list_user_tools()}
        missing: list[str] = []
        for step in steps:
            tool_type = step.get("tool_type")
            tool_id = step.get("tool_id")
            if tool_type in ("system_tool", "SYSTEM_TOOL") and tool_id not in available_system:
                missing.append(f"system_tool:{tool_id}")
            elif tool_type in ("user_tool", "USER_TOOL") and tool_id not in available_user:
                missing.append(f"user_tool:{tool_id}")
            # workflow type is validated by the nested-workflow path itself
        if missing:
            msg = f"Workflow references unknown tools: {', '.join(missing)}"
            logger.error(
                "workflow_tool_references_missing",
                workflow_id=workflow_id,
                missing=missing,
            )
            raise ValidationError(msg, details={"missing_tools": missing})

    # Merge steps into workflow for validation (validator expects 'steps' field)
    workflow_with_steps = {**workflow, "steps": steps}

    # 3. Validate workflow and inputs
    workflow_errors = WorkflowValidator.validate_workflow(workflow_with_steps)
    if workflow_errors:
        error_msg = f"Invalid workflow: {'; '.join(workflow_errors)}"
        logger.error(
            "workflow_validation_failed",
            workflow_id=workflow_id,
            errors=workflow_errors,
        )
        raise ValidationError(error_msg, details={"errors": workflow_errors})

    input_errors = WorkflowValidator.validate_inputs(workflow_with_steps, inputs)
    if input_errors:
        error_msg = f"Invalid inputs: {'; '.join(input_errors)}"
        logger.error(
            "workflow_input_validation_failed",
            workflow_id=workflow_id,
            errors=input_errors,
        )
        raise ValidationError(error_msg, details={"errors": input_errors})

    if not workflow.get("is_active"):
        msg = f"Workflow {workflow_id} is not active"
        raise ValidationError(msg, details={"workflow_id": workflow_id})

    if not steps:
        logger.warning("workflow_has_no_steps", workflow_id=workflow_id)
        return {"success": True, "outputs": {}, "message": "No steps to execute"}

    # 4. Use existing execution record or create new one
    execution_id = await _ensure_execution_record(
        execution_repo=execution_repo,
        execution_id=execution_id,
        workflow_id=workflow_id,
        triggered_by=triggered_by,
        trigger_id=trigger_id,
        parent_execution_id=parent_execution_id,
        inputs=inputs,
    )

    start_time = datetime.now(UTC)

    try:
        # 5. Update status to running. ``update_status`` runs a
        # ``with self.adapter.transaction(): ...`` block internally, so
        # we offload via ``asyncio.to_thread`` to keep
        # ``SafeSession._retry_delay`` ``time.sleep`` calls off the
        # event loop (2026-05-23 perf fix).
        await asyncio.to_thread(
            execution_repo.update_status,
            execution_id,
            WorkflowExecutionStatus.RUNNING,
        )

        # 6. Build LangGraph using refactored builder
        # (workflow_with_steps already created above for validation)

        # Create tool executor adapter
        tool_executor = BackendToolExecutorAdapter(
            graph_repository=graph_repository,
            search_repository=search_repository or graph_repository,
            llm_provider=llm_service,
            tool_service=tool_service,
        )

        # Create user tool resolver
        def user_tool_resolver(tool_id: str) -> Any:
            """Look up a user-defined tool by id, or return None if no tool service is wired."""
            if tool_service:
                return tool_service.get_user_tool(tool_id)
            return None

        # Create workflow executor for nested workflows (recursive call)
        workflow_executor = _WorkflowExecutorWrapper(
            workflow_service=workflow_service,
            tool_service=tool_service,
            llm_service=llm_service,
            graph_repository=graph_repository,
            search_repository=search_repository,
            database_name=database_name,
            depth=depth,
            lineage=(lineage or frozenset()) | {workflow_id},
            max_recursion_depth=max_recursion_depth,
        )

        # 7. Build and compile graph
        graph = build_workflow_graph(
            workflow_def=workflow_with_steps,
            tool_executor=tool_executor,
            workflow_executor=workflow_executor,
            user_tool_resolver=user_tool_resolver,
        )
        compiled = graph.compile()

        # 8. Create initial state
        initial_state = WorkflowState(  # type: ignore[call-arg]
            workflow_id=workflow_id,
            execution_id=execution_id,
            database_name=database_name,
            initial_inputs=inputs,
            started_at=start_time,
        )

        # 9. Execute workflow through LangGraph
        logger.debug(
            "langgraph_execution_starting",
            workflow_id=workflow_id,
            execution_id=execution_id,
            step_count=len(steps),
        )
        final_state = await compiled.ainvoke(cast("Any", initial_state))

        # 10. Calculate duration
        duration_ms = int((datetime.now(UTC) - start_time).total_seconds() * 1000)

        # LangGraph returns dict from ainvoke, use dict access
        state_error = final_state.get("error")
        state_current_step = final_state.get("current_step")
        state_step_results = final_state.get("step_results", {})

        # 11. Handle failure
        if state_error:
            await asyncio.to_thread(
                execution_repo.finalize_execution,
                execution_id,
                status=WorkflowExecutionStatus.FAILED,
                outputs=None,
                error_message=state_error,
                failed_step_id=state_current_step,
                duration_ms=duration_ms,
            )
            logger.error(
                "workflow_execution_failed",
                workflow_id=workflow_id,
                execution_id=execution_id,
                error=state_error,
            )
            from chaoscypher_core.exceptions import WorkflowExecutionError

            raise WorkflowExecutionError(
                message=state_error,
                workflow_id=workflow_id,
                execution_id=execution_id,
                failed_step_id=state_current_step,
            )

        # 12. Extract outputs and complete execution
        outputs = output_manager.extract_outputs(workflow, state_step_results)
        await asyncio.to_thread(
            execution_repo.finalize_execution,
            execution_id,
            status=WorkflowExecutionStatus.COMPLETED,
            outputs=outputs,
            error_message=None,
            failed_step_id=None,
            duration_ms=duration_ms,
        )

        logger.info(
            "workflow_execution_completed",
            workflow_id=workflow_id,
            execution_id=execution_id,
            duration_ms=duration_ms,
            output_keys=list(outputs.keys()),
        )

        return {
            "success": True,
            "outputs": outputs,
            "step_outputs": state_step_results,
            "execution_id": execution_id,
        }

    except Exception as e:
        logger.exception(
            "workflow_execution_exception",
            workflow_id=workflow_id,
            execution_id=execution_id,
            error_type=type(e).__name__,
            error_message=str(e),
        )
        duration_ms = int((datetime.now(UTC) - start_time).total_seconds() * 1000)
        await asyncio.to_thread(
            execution_repo.finalize_execution,
            execution_id,
            status=WorkflowExecutionStatus.FAILED,
            outputs=None,
            error_message=str(e),
            failed_step_id=None,
            duration_ms=duration_ms,
        )
        raise
    finally:
        exec_adapter.disconnect()


async def execute_step_task(
    step_config: dict[str, Any],
    step_inputs: dict[str, Any],
    workflow_context: dict[str, Any],
    tool_service: Any,
    llm_service: Any,
    graph_repository: GraphRepository,
    search_repository: Any,
    workflow_service: Any,  # Needed for nested workflow execution
    database_name: str,
) -> dict[str, Any]:
    """Execute a single workflow step using refactored engine components.

    Args:
        step_config: Step configuration (tool_type, tool_id, etc.)
        step_inputs: Input parameters for the step
        workflow_context: Workflow execution context
        tool_service: ToolService instance
        llm_service: LLM service instance
        graph_repository: GraphRepository instance
        search_repository: SearchRepository instance
        workflow_service: WorkflowService (for nested workflows)
        database_name: Current database name

    Returns:
        Step output dictionary with success, output, error
    """
    logger.info("workflow_step_execution_started", step_config=step_config)

    # Create step executor
    step_executor = StepExecutor(  # type: ignore[call-arg]
        graph_repository=graph_repository,
        search_repository=search_repository,
        llm_service=llm_service,
        tool_service=tool_service,
    )

    # Create workflow executor for nested workflows
    workflow_executor = _WorkflowExecutorWrapper(
        workflow_service=workflow_service,
        tool_service=tool_service,
        llm_service=llm_service,
        graph_repository=graph_repository,
        search_repository=search_repository,
        database_name=database_name,
    )

    # Execute step with workflow executor for nested workflows
    result = await step_executor.execute_step(  # type: ignore[call-arg]
        step_config=step_config,
        step_inputs=step_inputs,
        workflow_context=workflow_context,
        workflow_executor=workflow_executor,
    )

    logger.info("workflow_step_execution_completed", success=result.get("success", False))

    return result
