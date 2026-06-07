# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Workflow Step Executor - Individual Step Execution Logic.

Handles execution of individual workflow steps:
1. System tool execution (via plugin registry)
2. User tool execution (with configuration merging)
3. Nested workflow execution
4. Parameter interpolation with context
5. Error handling and result formatting

Extracted from workflow_engine.py for Single Responsibility Principle (SRP).
Refactored to use plugin architecture instead of executor dispatch.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.exceptions import OperationError, ValidationError
from chaoscypher_core.models import StepToolType
from chaoscypher_core.services.workflows.tools.engine import (
    ToolExecutionContext,
    ToolRegistry,
    validate_inputs,
)


if TYPE_CHECKING:
    from chaoscypher_core.ports.graph import GraphRepositoryProtocol

logger = structlog.get_logger(__name__)


class StepExecutor:
    """Executes individual workflow steps.

    Handles all step execution logic including tool dispatch,
    parameter interpolation, and error handling.

    Args:
        graph_repository: Graph repository for tool execution
        search_repository: Search repository for tool execution
        llm_service: LLM service for AI tools
        tool_service: Tool service for user tool lookups
        parameter_resolver: Parameter resolver for context interpolation
        discovery_service: Discovery service for discovery tools (optional, removed)

    Example:
        >>> executor = StepExecutor(graph_repo, search_repo, llm_svc, tool_svc, param_resolver)
        >>> result = await executor.execute_step(
        ...     step_config={'tool_type': 'system_tool', 'tool_id': 'data.transform'},
        ...     step_inputs={'data': [1, 2, 3]},
        ...     workflow_context={'inputs': {...}, 'steps': {...}}
        ... )
        >>> print(result['success'])  # True

    """

    def __init__(
        self,
        graph_repository: GraphRepositoryProtocol,
        search_repository: Any,
        llm_service: Any,
        tool_service: Any,
        parameter_resolver: Any,
        workflow_executor: Any | None = None,
        discovery_service: Any = None,
    ):
        """Initialize the instance.

        Args:
            graph_repository: Repository for graph operations.
            search_repository: Repository for search operations.
            llm_service: Service for LLM operations.
            tool_service: Service for user tool lookups.
            parameter_resolver: Resolver for parameter interpolation.
            workflow_executor: Executor for nested workflows.
            discovery_service: Service for discovery operations (optional, removed).

        """
        self.graph_repository = graph_repository
        self.search_repository = search_repository
        self.llm_service = llm_service
        self.tool_service = tool_service
        self.discovery_service = discovery_service
        self.parameter_resolver = parameter_resolver
        self.workflow_executor = workflow_executor  # For nested workflows

    async def execute_step(
        self,
        step_config: dict[str, Any],
        step_inputs: dict[str, Any],
        workflow_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute a single workflow step independently.

        This is a lightweight step execution that doesn't require full workflow
        execution context. Used by the queue system to execute steps independently.

        Supports three tool types:
        1. system_tool: Built-in system tools
        2. user_tool: Custom user-configured tools (wraps system tools)
        3. workflow: Nested workflow execution

        Args:
            step_config: Step configuration containing:
                - tool_type: Type of tool (system_tool, user_tool, workflow)
                - tool_id: ID of tool to execute
                - thinking_mode: Optional thinking mode for AI tools
            step_inputs: Parameters to pass to the tool
            workflow_context: Optional workflow context (for accessing previous step outputs)

        Returns:
            Step output dictionary with:
                - success: Boolean indicating success/failure
                - output: Tool execution result
                - error: Error message (if success=False)

        Example:
            >>> result = await executor.execute_step(
            ...     step_config={
            ...         'tool_type': 'system_tool',
            ...         'tool_id': 'ai.prompt',
            ...         'thinking_mode': 'extended'
            ...     },
            ...     step_inputs={'prompt': 'Analyze {{inputs.text}}'},
            ...     workflow_context={'inputs': {'text': 'Hello world'}}
            ... )
            >>> result['success']  # True
            >>> result['output']  # AI response

        Note:
            - Parameter interpolation happens automatically if workflow_context provided
            - User tools merge their configuration with step inputs
            - Nested workflows receive interpolated inputs

        Raises:
            ValidationError: If the tool_type is not recognised (caught internally;
                result["success"] will be False).

        """
        tool_type = step_config.get("tool_type")
        tool_id = step_config.get("tool_id")

        logger.info(
            "step_execution_started",
            tool_type=tool_type,
            tool_id=tool_id,
            has_workflow_context=workflow_context is not None,
        )

        try:
            thinking_mode = step_config.get("thinking_mode")

            # Interpolate inputs if workflow_context is provided
            if workflow_context:
                interpolated_inputs = self.parameter_resolver.interpolate_parameters(
                    step_inputs, workflow_context
                )
            else:
                interpolated_inputs = step_inputs

            # Execute based on tool type
            if tool_type in (StepToolType.SYSTEM_TOOL, "system_tool"):
                assert tool_id is not None, "tool_id must be set for system_tool"
                output = await self._execute_system_tool(
                    tool_id, interpolated_inputs, thinking_mode
                )

            elif tool_type in (StepToolType.USER_TOOL, "user_tool"):
                assert tool_id is not None, "tool_id must be set for user_tool"
                output = await self._execute_user_tool(tool_id, interpolated_inputs, thinking_mode)

            elif tool_type in (StepToolType.WORKFLOW, "workflow"):
                assert tool_id is not None, "tool_id must be set for workflow"
                output = await self._execute_nested_workflow(tool_id, interpolated_inputs)

            else:
                msg = f"Unknown tool type: {tool_type}"
                raise ValidationError(msg)

            return {"success": True, "output": output}

        except Exception as e:
            logger.exception(
                "step_execution_failed",
                tool_type=tool_type,
                tool_id=tool_id,
                error_type=type(e).__name__,
                error_message=str(e),
            )
            return {"success": False, "output": {}, "error": "Step execution failed"}

    async def _execute_system_tool(
        self, tool_id: str, inputs: dict[str, Any], thinking_mode: str | None
    ) -> dict[str, Any]:
        """Execute system tool using plugin registry.

        Raises:
            ValidationError: If the tool is not found in the registry or if
                the provided inputs fail the tool's schema validation.

        """
        # Get plugin from registry
        registry = ToolRegistry()
        plugin = registry.get(tool_id)

        if not plugin:
            msg = f"Unknown tool: {tool_id}"
            raise ValidationError(msg)

        # Validate inputs against plugin schema
        validation_result = validate_inputs(inputs, plugin.input_schema)
        if not validation_result.is_valid:
            msg = f"Invalid inputs for {tool_id}: {', '.join(validation_result.errors)}"
            raise ValidationError(msg)

        # Create execution context
        context = ToolExecutionContext(
            graph_manager=self.graph_repository,
            llm_service=self.llm_service,
            thinking_mode=thinking_mode,
            discovery_service=self.discovery_service,
            search_repository=self.search_repository,
            workflow_state={},
            database_name=None,
        )

        # Execute plugin
        return await plugin.execute(inputs, context)

    async def _execute_user_tool(
        self, tool_id: str, inputs: dict[str, Any], thinking_mode: str | None
    ) -> dict[str, Any]:
        """Execute user tool (custom configuration wrapping system tool).

        Raises:
            ValidationError: If the user tool is not found via tool_service.

        """
        # Get user tool configuration
        user_tool = self.tool_service.get_user_tool(tool_id)
        if not user_tool:
            msg = f"User tool not found: {tool_id}"
            raise ValidationError(msg)

        # Merge user tool config with step parameters
        merged_params = {**user_tool["configuration"], **inputs}

        # Execute underlying system tool using plugin system
        return await self._execute_system_tool(
            tool_id=user_tool["system_tool_id"], inputs=merged_params, thinking_mode=thinking_mode
        )

    async def _execute_nested_workflow(
        self, workflow_id: str, inputs: dict[str, Any]
    ) -> dict[str, Any]:
        """Execute nested workflow.

        Raises:
            OperationError: If workflow_executor is not configured.

        """
        if not self.workflow_executor:
            msg = "Workflow executor not available for nested workflow execution"
            raise OperationError(msg, operation="execute_step")

        from typing import cast

        result = await self.workflow_executor.execute_workflow(
            workflow_id=workflow_id, inputs=inputs, triggered_by="workflow"
        )
        return cast("dict[str, Any]", result.get("outputs", {}))
