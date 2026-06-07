# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Input Validation Utilities for Tool Plugins.

Provides JSON schema validation for plugin inputs. Validates inputs before
execution to catch errors early and provide clear error messages.

Architecture:
    - Uses jsonschema library for validation
    - Validates against plugin's input_schema property
    - Returns ValidationResult with success/failure and errors
    - Used by tool execution service before calling plugin.execute()

Example Usage:
    ```python
    from chaoscypher_core.services.workflows.tools.engine.validators import validate_inputs

    # Get plugin
    plugin = registry.get_plugin("ai.prompt")

    # Validate inputs
    result = validate_inputs(
        inputs={"prompt": "Analyze this..."},
        schema=plugin.input_schema
    )

    if result.is_valid:
        # Execute plugin
        output = await plugin.execute(inputs, context)
    else:
        # Return error
        raise ValueError(f"Invalid inputs: {result.errors}")
    ```

Validation Features:
    - Required field checking
    - Type validation (string, integer, array, object)
    - Enum validation (allowed values)
    - Range validation (min/max for numbers)
    - Pattern validation (regex for strings)
    - Format validation (email, url, date, etc.)
"""

from dataclasses import dataclass
from typing import Any, cast

import structlog


try:
    import jsonschema
    from jsonschema import ValidationError

    JSONSCHEMA_AVAILABLE = True
except ImportError:
    JSONSCHEMA_AVAILABLE = False
    ValidationError = Exception

logger = structlog.get_logger(__name__)


@dataclass
class ValidationResult:
    """Result of input validation.

    Attributes:
        is_valid: True if inputs pass validation
        errors: List of error messages (empty if valid)
        warnings: List of warning messages (non-fatal issues)

    Example:
        result = validate_inputs(inputs, schema)
        if not result.is_valid:
            print(f"Validation failed: {', '.join(result.errors)}")

    """

    is_valid: bool
    errors: list[str]
    warnings: list[str] | None = None

    def __post_init__(self) -> None:
        """Initialize warnings list if not provided."""
        if self.warnings is None:
            self.warnings = []


def validate_inputs(inputs: dict[str, Any], schema: dict[str, Any]) -> ValidationResult:
    """Validate tool inputs against JSON schema.

    Uses jsonschema library to validate inputs. If jsonschema not available,
    logs warning and returns valid (permissive mode).

    Args:
        inputs: Input parameters to validate
        schema: JSON schema (Draft 7) to validate against

    Returns:
        ValidationResult with success/failure and error messages

    Example:
        schema = {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1}
            },
            "required": ["query"]
        }

        result = validate_inputs(
            inputs={"query": "test", "limit": 10},
            schema=schema
        )

        if result.is_valid:
            # Proceed with execution
            pass

    """
    if not JSONSCHEMA_AVAILABLE:
        logger.warning(
            "jsonschema_not_available",
            note="Install jsonschema for input validation: pip install jsonschema",
        )
        return ValidationResult(
            is_valid=True,
            errors=[],
            warnings=["jsonschema library not available - validation skipped"],
        )

    try:
        # Validate against schema
        jsonschema.validate(instance=inputs, schema=schema)

        logger.debug("inputs_validated", input_keys=list(inputs.keys()))

        return ValidationResult(is_valid=True, errors=[])

    except ValidationError as e:
        # Extract user-friendly error message
        error_msg = _format_validation_error(e)

        logger.warning(
            "input_validation_failed",
            error=error_msg,
            path=list(e.absolute_path) if hasattr(e, "absolute_path") else [],
            inputs=inputs,
        )

        return ValidationResult(is_valid=False, errors=[error_msg])

    except Exception as e:
        # Unexpected validation error
        error_msg = f"Validation error: {e!s}"

        logger.exception("validation_exception", error_type=type(e).__name__, error_message=str(e))

        return ValidationResult(is_valid=False, errors=[error_msg])


def _format_validation_error(error: ValidationError) -> str:
    """Format JSON schema validation error into user-friendly message.

    Args:
        error: ValidationError from jsonschema

    Returns:
        Formatted error message

    Example:
        Input: ValidationError("'query' is a required property")
        Output: "Missing required field: query"

    """
    message = error.message

    # Extract field path if available
    if error.absolute_path:
        field_path = ".".join(str(p) for p in error.absolute_path)
        return f"Field '{field_path}': {message}"

    # Handle common error patterns
    if "'required property" in message.lower():
        # Extract field name from message
        import re

        match = re.search(r"'(\w+)'", message)
        if match:
            field = match.group(1)
            return f"Missing required field: {field}"

    if "is not of type" in message.lower():
        # Extract expected type
        import re

        match = re.search(r"is not of type '(\w+)'", message)
        if match:
            expected_type = match.group(1)
            return f"Invalid type (expected {expected_type}): {message}"

    # Return original message if no special formatting
    return cast("str", message)


def get_required_fields(schema: dict[str, Any]) -> list[str]:
    """Extract list of required fields from JSON schema.

    Args:
        schema: JSON schema dict

    Returns:
        List of required field names

    Example:
        schema = {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer"}
            },
            "required": ["query"]
        }

        fields = get_required_fields(schema)
        # Returns: ["query"]

    """
    return cast("list[str]", schema.get("required", []))


def get_optional_fields(schema: dict[str, Any]) -> list[str]:
    """Extract list of optional fields from JSON schema.

    Args:
        schema: JSON schema dict

    Returns:
        List of optional field names (fields in properties but not in required)

    Example:
        schema = {
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer"}
            },
            "required": ["query"]
        }

        fields = get_optional_fields(schema)
        # Returns: ["limit"]

    """
    properties = schema.get("properties", {})
    required = schema.get("required", [])
    return [field for field in properties if field not in required]


__all__ = ["ValidationResult", "get_optional_fields", "get_required_fields", "validate_inputs"]
