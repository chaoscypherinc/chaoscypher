# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Workflow Validator.

Validates workflow definitions and execution inputs.
"""

from typing import Any


class WorkflowValidator:
    """Validates workflow definitions and execution inputs.

    Provides clean separation of validation logic from execution.
    All methods are static and have zero dependencies.
    """

    @staticmethod
    def validate_workflow(workflow: dict[str, Any]) -> list[str]:
        """Validate workflow definition structure.

        Args:
            workflow: Workflow definition dict

        Returns:
            List of validation errors (empty if valid)

        """
        errors = []

        # Check required fields
        if not workflow.get("id"):
            errors.append("Workflow missing 'id' field")

        if not workflow.get("name"):
            errors.append("Workflow missing 'name' field")

        if "steps" not in workflow:
            errors.append("Workflow missing 'steps' field")
            return errors  # Can't validate steps without this field

        if not isinstance(workflow["steps"], list):
            errors.append("Workflow 'steps' must be a list")
            return errors

        if len(workflow["steps"]) == 0:
            errors.append("Workflow has no steps")
            return errors

        # Validate each step
        for idx, step in enumerate(workflow["steps"]):
            step_errors = WorkflowValidator.validate_step(step, idx)
            errors.extend(step_errors)

        # Dependency-graph validation (cycles + dangling refs)
        dep_errors = WorkflowValidator.validate_dependencies(workflow)
        errors.extend(dep_errors)

        return errors

    @staticmethod
    def validate_step(step: dict[str, Any], step_index: int) -> list[str]:
        """Validate a single workflow step.

        Args:
            step: Step definition dict
            step_index: Index of step in workflow

        Returns:
            List of validation errors

        """
        errors = []

        # Check required step fields
        if "id" not in step:
            errors.append(f"Step {step_index}: Missing 'id' field")

        if "tool_id" not in step:
            errors.append(f"Step {step_index}: Missing 'tool_id' field")

        # Steps use 'configuration' for parameters (not 'inputs')
        if "configuration" not in step:
            errors.append(f"Step {step_index}: Missing 'configuration' field")
        elif not isinstance(step["configuration"], dict):
            errors.append(f"Step {step_index}: 'configuration' must be an object")

        return errors

    @staticmethod
    def validate_inputs(workflow: dict[str, Any], provided_inputs: dict[str, Any]) -> list[str]:
        """Validate that required inputs are provided.

        Args:
            workflow: Workflow definition
            provided_inputs: Inputs provided for execution

        Returns:
            List of validation errors (empty if valid)

        """
        errors = []

        # Get required inputs from workflow definition
        required_inputs = workflow.get("required_inputs", [])

        # Check each required input is provided
        for input_name in required_inputs:
            if input_name not in provided_inputs:
                errors.append(f"Missing required input: '{input_name}'")
            elif provided_inputs[input_name] is None:
                errors.append(f"Required input '{input_name}' cannot be null")

        return errors

    @staticmethod
    def validate_dependencies(workflow: dict[str, Any]) -> list[str]:
        """Validate step dependency graph (Kahn's algorithm).

        Returns errors for:
        - references to step IDs that don't exist in the workflow
        - cycles in the dependency graph

        Args:
            workflow: Workflow definition dict with 'steps' list

        Returns:
            List of validation errors (empty if valid)

        """
        errors: list[str] = []
        steps = workflow.get("steps") or []
        step_ids: set[str] = {s["id"] for s in steps if "id" in s}

        # Dangling-reference check
        for step in steps:
            step_id = step.get("id", "<unknown>")
            errors.extend(
                f"Step '{step_id}' depends_on references unknown step '{dep}'"
                for dep in step.get("depends_on") or []
                if dep not in step_ids
            )

        # If we already have dangling refs, skip cycle check (graph is malformed)
        if errors:
            return errors

        # Kahn's algorithm for cycle detection
        in_degree: dict[str, int] = dict.fromkeys(step_ids, 0)
        adjacency: dict[str, list[str]] = {sid: [] for sid in step_ids}
        for step in steps:
            sid = step["id"]
            for dep in step.get("depends_on") or []:
                adjacency[dep].append(sid)
                in_degree[sid] += 1

        queue = [sid for sid, deg in in_degree.items() if deg == 0]
        visited = 0
        while queue:
            node = queue.pop(0)
            visited += 1
            for successor in adjacency[node]:
                in_degree[successor] -= 1
                if in_degree[successor] == 0:
                    queue.append(successor)

        if visited < len(step_ids):
            cyclic = sorted(sid for sid, deg in in_degree.items() if deg > 0)
            errors.append(f"Dependency cycle detected involving steps: {', '.join(cyclic)}")
        return errors

    @staticmethod
    def validate_execution_state(
        workflow: dict[str, Any], provided_inputs: dict[str, Any]
    ) -> dict[str, Any]:
        """Validate workflow and inputs together for execution.

        This is a convenience method that runs all validations.

        Args:
            workflow: Workflow definition
            provided_inputs: Execution inputs

        Returns:
            Dict with 'valid' (bool) and 'errors' (list of str)

        """
        all_errors = []

        # Validate workflow structure
        workflow_errors = WorkflowValidator.validate_workflow(workflow)
        all_errors.extend(workflow_errors)

        # Validate inputs (only if workflow structure is valid)
        if not workflow_errors:
            input_errors = WorkflowValidator.validate_inputs(workflow, provided_inputs)
            all_errors.extend(input_errors)

        return {"valid": len(all_errors) == 0, "errors": all_errors}
