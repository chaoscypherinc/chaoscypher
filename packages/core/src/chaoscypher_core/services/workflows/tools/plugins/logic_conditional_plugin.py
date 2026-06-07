# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Conditional Plugin - Conditional branching logic.

Evaluates conditions and returns different values based on result.
Supports boolean expressions with safe evaluation.

Extracted from executors/logic_executor.py and converted to plugin architecture.
"""

import ast
from typing import TYPE_CHECKING, Any

import structlog


if TYPE_CHECKING:
    from chaoscypher_core.services.workflows.tools.plugins import ToolExecutionContext

logger = structlog.get_logger(__name__)


class ConditionalPlugin:
    """Conditional tool plugin.

    Evaluate conditional expressions and return different values based
    on true/false result. Uses safe evaluation (ast.literal_eval).
    """

    @property
    def tool_id(self) -> str:
        """Stub implementation - not yet implemented."""
        return "logic.conditional"

    @property
    def category(self) -> str:
        """Stub implementation - not yet implemented."""
        return "logic"

    @property
    def icon(self) -> str:
        """MUI icon name for UI display."""
        return "CallSplit"

    @property
    def name(self) -> str:
        """Stub implementation - not yet implemented."""
        return "Conditional"

    @property
    def description(self) -> str:
        """Stub implementation - not yet implemented."""
        return "Evaluate condition and return value based on true/false result"

    @property
    def input_schema(self) -> dict[str, Any]:
        """Stub implementation - not yet implemented."""
        return {
            "type": "object",
            "properties": {
                "condition": {"description": "Condition to evaluate (boolean or expression)"},
                "if_true": {"description": "Value to return if condition is true"},
                "if_false": {"description": "Value to return if condition is false"},
            },
            "required": ["condition", "if_true"],
        }

    @property
    def output_schema(self) -> dict[str, Any]:
        """Output schema for Conditional tool."""
        return {
            "type": "object",
            "properties": {
                "result": {
                    "description": "Value from if_true or if_false based on condition",
                },
                "branch_taken": {
                    "type": "string",
                    "enum": ["true", "false"],
                    "description": "Which branch was taken",
                },
                "condition_value": {
                    "type": "boolean",
                    "description": "The evaluated condition result",
                },
            },
            "required": ["result", "branch_taken", "condition_value"],
        }

    async def execute(
        self, inputs: dict[str, Any], context: ToolExecutionContext
    ) -> dict[str, Any]:
        """Evaluate conditional and return appropriate value.

        Args:
            inputs: Tool inputs (condition, if_true, if_false)
            context: Execution context

        Returns:
            Dictionary with result, branch taken, and condition value

        """
        condition = inputs["condition"]
        if_true = inputs["if_true"]
        if_false = inputs.get("if_false")

        # Check if condition is already a boolean (from template substitution)
        if isinstance(condition, bool):
            result = condition
        else:
            # Evaluate condition safely using ast.literal_eval
            # For safety, only support literal expressions
            try:
                result = ast.literal_eval(str(condition))
            except (ValueError, SyntaxError) as e:
                logger.warning(
                    "invalid_condition_expression",
                    condition=condition,
                    error_type=type(e).__name__,
                    error_message=str(e),
                )
                result = False
            except Exception as e:
                logger.exception(
                    "condition_evaluation_error",
                    error_type=type(e).__name__,
                    error_message=str(e),
                )
                result = False

        branch_taken = "true" if result else "false"
        output = if_true if result else if_false

        # Log the decision for debugging
        logger.info(
            "conditional_evaluated",
            condition_result=result,
            branch_taken=branch_taken,
            output=output,
        )

        return {"result": output, "branch_taken": branch_taken, "condition_value": result}


__all__ = ["ConditionalPlugin"]
