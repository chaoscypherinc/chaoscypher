# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Parameter Interpolation Engine.

Interpolates template variables in workflow parameters.
Supports nested dictionaries, lists, and dot-notation path resolution.
"""

import re
from typing import Any


class ParameterInterpolator:
    """Interpolates workflow parameters with context variables.

    Supports:
    - {{inputs.var_name}} - Access workflow inputs
    - {{steps.step_id.output.field}} - Access step outputs
    - {{steps.step_id.field}} - Direct step output access
    - Nested dictionaries and lists
    - Type-preserving interpolation
    """

    @staticmethod
    def interpolate_parameters(parameters: Any, context: dict[str, Any]) -> Any:
        """Interpolate parameters with context variables.

        Args:
            parameters: Parameter dictionary
            context: Execution context (inputs, step outputs, etc.)

        Returns:
            Interpolated parameters with resolved values

        Example:
            >>> context = {"inputs": {"name": "Alice"}, "steps": {"step1": {"age": 30}}}
            >>> params = {"greeting": "Hello {{inputs.name}}", "age": "{{steps.step1.age}}"}
            >>> interpolate_parameters(params, context)
            {'greeting': 'Hello Alice', 'age': 30}

        """
        if not isinstance(parameters, dict):
            return parameters

        result = {}

        for key, value in parameters.items():
            if isinstance(value, str):
                # Interpolate string values
                result[key] = ParameterInterpolator._interpolate_string(value, context)
            elif isinstance(value, dict):
                # Recursively interpolate nested dicts
                result[key] = ParameterInterpolator.interpolate_parameters(value, context)
            elif isinstance(value, list):
                # Interpolate list items
                result[key] = [
                    (
                        ParameterInterpolator._interpolate_string(item, context)
                        if isinstance(item, str)
                        else item
                    )
                    for item in value
                ]
            else:
                result[key] = value

        return result

    @staticmethod
    def _interpolate_string(value: str, context: dict[str, Any]) -> Any:
        """Interpolate a string value with context variables.

        Preserves type if entire string is a single reference.
        Does string replacement for mixed content.

        Args:
            value: String to interpolate
            context: Execution context

        Returns:
            Interpolated value (might not be a string if entire value is a reference)

        Example:
            >>> context = {"inputs": {"count": 5}}
            >>> _interpolate_string("{{inputs.count}}", context)
            5  # Returns integer
            >>> _interpolate_string("Count: {{inputs.count}}", context)
            "Count: 5"  # Returns string

        """
        # Match double-brace interpolation syntax (e.g., path.to.value wrapped in braces)
        pattern = r"\{\{([^}]+)\}\}"

        # Check if entire string is a single interpolation
        match = re.fullmatch(pattern, value)
        if match:
            # Return the actual value (preserving type)
            path = match.group(1).strip()
            return ParameterInterpolator._resolve_path(path, context)

        # Multiple interpolations or mixed content - string replacement
        def replace_match(match: re.Match[str]) -> str:
            path = match.group(1).strip()
            resolved = ParameterInterpolator._resolve_path(path, context)
            return str(resolved) if resolved is not None else ""

        return re.sub(pattern, replace_match, value)

    @staticmethod
    def _resolve_path(path: str, context: dict[str, Any]) -> Any:
        """Resolve a dot-notation path in context.

        Supports:
        - Dictionary key access: inputs.file_path
        - Nested access: steps.step_1.output.result
        - List indexing: items.0.name

        Args:
            path: Dot-notation path
            context: Execution context

        Returns:
            Resolved value or None if path doesn't exist

        Example:
            >>> context = {"steps": {"step_1": {"output": {"nodes": [1, 2, 3]}}}}
            >>> _resolve_path("steps.step_1.output.nodes.0", context)
            1

        """
        parts = path.split(".")
        current: Any = context

        for part in parts:
            if isinstance(current, dict):
                current = current.get(part)
            elif isinstance(current, list) and part.isdigit():
                idx = int(part)
                current = current[idx] if 0 <= idx < len(current) else None
            else:
                return None

            if current is None:
                return None

        return current
