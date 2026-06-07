# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Validation utilities for templates and properties.

Provides comprehensive validation for:
- Template property definitions
"""

import re
from datetime import date, datetime
from typing import Any

from chaoscypher_core.exceptions import PropertyValidationError, ValidationError
from chaoscypher_core.models import (
    PropertyDefinition,
    PropertyType,
)


class TemplateValidator:
    """Validators for template operations."""

    @staticmethod
    def validate_not_system_prefix(name: str) -> None:
        """Validate that template name doesn't use reserved system prefix.

        The "system_" and "system " prefixes are reserved for built-in
        system templates that ship with the Knowledge Engine.

        Args:
            name: Template name to validate

        Raises:
            ValidationError: If name starts with reserved prefix (maps to HTTP 400)

        Example:
            >>> TemplateValidator.validate_not_system_prefix("My Template")  # OK
            >>> TemplateValidator.validate_not_system_prefix("system_workflow")  # Raises ValidationError

        """
        if name.lower().startswith("system_") or name.lower().startswith("system "):
            msg = (
                "Template names cannot start with 'system_' or 'system ' - "
                "this prefix is reserved for system templates"
            )
            raise ValidationError(msg)


class PropertyValidator:
    """Validates properties against template property definitions."""

    @staticmethod
    def validate_properties(
        properties: dict[str, Any],
        property_defs: list[PropertyDefinition],
        apply_defaults: bool = True,
    ) -> dict[str, Any]:
        """Validate properties against property definitions.

        Args:
            properties: The properties to validate
            property_defs: List of property definitions from template
            apply_defaults: Whether to apply default values for missing properties

        Returns:
            Validated (and possibly augmented with defaults) properties dict

        Raises:
            PropertyValidationError: If validation fails

        """
        # Create a dict of property definitions by name for easy lookup
        {pd.name: pd for pd in property_defs}

        # Start with a copy of the provided properties
        validated = dict(properties)

        # Check all property definitions
        for prop_def in property_defs:
            prop_name = prop_def.name

            # Check if required property is present
            if prop_def.required and prop_name not in properties:
                # Try to apply default value if available
                if apply_defaults and prop_def.default_value is not None:
                    validated[prop_name] = prop_def.default_value
                else:
                    raise PropertyValidationError(prop_name, "Required property is missing")

            # Apply default value for optional properties if requested
            elif (
                apply_defaults
                and prop_name not in properties
                and prop_def.default_value is not None
            ):
                validated[prop_name] = prop_def.default_value

            # Validate property if it's present
            if prop_name in validated:
                value = validated[prop_name]
                validated[prop_name] = PropertyValidator._validate_property_value(
                    prop_name, value, prop_def
                )

        return validated

    @staticmethod
    def _validate_property_value(prop_name: str, value: Any, prop_def: PropertyDefinition) -> Any:
        """Validate a single property value against its definition.

        Returns the validated (and possibly coerced) value.
        """
        # Handle None values
        if value is None:
            if prop_def.required:
                raise PropertyValidationError(prop_name, "Required property cannot be None")
            return value

        # Type-specific validation
        if prop_def.property_type == PropertyType.STRING:
            return PropertyValidator._validate_string(prop_name, value, prop_def)

        if prop_def.property_type == PropertyType.TEXT:
            return PropertyValidator._validate_text(prop_name, value, prop_def)

        if prop_def.property_type == PropertyType.INTEGER:
            return PropertyValidator._validate_integer(prop_name, value)

        if prop_def.property_type == PropertyType.FLOAT:
            return PropertyValidator._validate_float(prop_name, value)

        if prop_def.property_type == PropertyType.BOOLEAN:
            return PropertyValidator._validate_boolean(prop_name, value)

        if prop_def.property_type == PropertyType.DATE:
            return PropertyValidator._validate_date(prop_name, value)

        if prop_def.property_type == PropertyType.DATETIME:
            return PropertyValidator._validate_datetime(prop_name, value)

        if prop_def.property_type == PropertyType.URL:
            return PropertyValidator._validate_url(prop_name, value, prop_def)

        if prop_def.property_type == PropertyType.EMAIL:
            return PropertyValidator._validate_email(prop_name, value, prop_def)

        if prop_def.property_type == PropertyType.ENUM:
            return PropertyValidator._validate_enum(prop_name, value, prop_def)

        if prop_def.property_type == PropertyType.JSON:
            return value  # Already a dict/list, no validation needed

        if prop_def.property_type == PropertyType.NODE_REFERENCE:
            return PropertyValidator._validate_node_reference(prop_name, value, prop_def)

        if prop_def.property_type == PropertyType.NODE_REFERENCE_LIST:
            return PropertyValidator._validate_node_reference_list(prop_name, value, prop_def)

        # All PropertyType enum values covered above - this should be unreachable
        raise PropertyValidationError(
            prop_name, f"Unsupported property type: {prop_def.property_type}"
        )

    @staticmethod
    def _validate_string(prop_name: str, value: Any, prop_def: PropertyDefinition) -> str:
        """Validate string type."""
        if not isinstance(value, str):
            raise PropertyValidationError(prop_name, f"Expected string, got {type(value).__name__}")

        # Check regex pattern if provided
        if prop_def.validation_pattern and not re.match(prop_def.validation_pattern, value):
            raise PropertyValidationError(
                prop_name,
                f"Value does not match required pattern: {prop_def.validation_pattern}",
            )

        return value

    @staticmethod
    def _validate_text(prop_name: str, value: Any, prop_def: PropertyDefinition) -> str:
        """Validate text type (multiline string)."""
        return PropertyValidator._validate_string(prop_name, value, prop_def)

    @staticmethod
    def _validate_integer(prop_name: str, value: Any) -> int:
        """Validate integer type."""
        if isinstance(value, bool):
            raise PropertyValidationError(prop_name, "Expected integer, got boolean")

        if isinstance(value, int):
            return value

        # Try to convert
        try:
            return int(value)
        except (ValueError, TypeError):  # fmt: skip
            raise PropertyValidationError(
                prop_name, f"Cannot convert {type(value).__name__} to integer"
            ) from None

    @staticmethod
    def _validate_float(prop_name: str, value: Any) -> float:
        """Validate float type."""
        if isinstance(value, bool):
            raise PropertyValidationError(prop_name, "Expected float, got boolean")

        if isinstance(value, (int, float)):
            return float(value)

        # Try to convert
        try:
            return float(value)
        except (ValueError, TypeError):  # fmt: skip
            raise PropertyValidationError(
                prop_name, f"Cannot convert {type(value).__name__} to float"
            ) from None

    @staticmethod
    def _validate_boolean(prop_name: str, value: Any) -> bool:
        """Validate boolean type."""
        if isinstance(value, bool):
            return value

        # Try to convert common string representations
        if isinstance(value, str):
            lower = value.lower()
            if lower in ("true", "1", "yes", "y"):
                return True
            if lower in ("false", "0", "no", "n"):
                return False

        raise PropertyValidationError(
            prop_name, f"Cannot convert {type(value).__name__} to boolean"
        )

    @staticmethod
    def _validate_date(prop_name: str, value: Any) -> str:
        """Validate date type (returns ISO format string)."""
        if isinstance(value, date):
            return value.isoformat()

        if isinstance(value, str):
            # Try to parse ISO format
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                return parsed.date().isoformat()
            except ValueError:
                raise PropertyValidationError(
                    prop_name, "Invalid date format (expected ISO 8601)"
                ) from None

        raise PropertyValidationError(prop_name, f"Cannot convert {type(value).__name__} to date")

    @staticmethod
    def _validate_datetime(prop_name: str, value: Any) -> str:
        """Validate datetime type (returns ISO format string)."""
        if isinstance(value, datetime):
            return value.isoformat()

        if isinstance(value, str):
            # Try to parse ISO format
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                return parsed.isoformat()
            except ValueError:
                raise PropertyValidationError(
                    prop_name, "Invalid datetime format (expected ISO 8601)"
                ) from None

        raise PropertyValidationError(
            prop_name, f"Cannot convert {type(value).__name__} to datetime"
        )

    @staticmethod
    def _validate_url(prop_name: str, value: Any, prop_def: PropertyDefinition) -> str:
        """Validate URL type."""
        if not isinstance(value, str):
            raise PropertyValidationError(
                prop_name, f"Expected string URL, got {type(value).__name__}"
            )

        # Basic URL pattern
        url_pattern = r"^https?://.+"
        if not re.match(url_pattern, value):
            raise PropertyValidationError(prop_name, "Invalid URL format")

        # Check custom pattern if provided
        if prop_def.validation_pattern and not re.match(prop_def.validation_pattern, value):
            raise PropertyValidationError(
                prop_name, f"URL does not match required pattern: {prop_def.validation_pattern}"
            )

        return value

    @staticmethod
    def _validate_email(prop_name: str, value: Any, prop_def: PropertyDefinition) -> str:
        """Validate email type."""
        if not isinstance(value, str):
            raise PropertyValidationError(
                prop_name, f"Expected string email, got {type(value).__name__}"
            )

        # Basic email pattern
        email_pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
        if not re.match(email_pattern, value):
            raise PropertyValidationError(prop_name, "Invalid email format")

        # Check custom pattern if provided
        if prop_def.validation_pattern and not re.match(prop_def.validation_pattern, value):
            raise PropertyValidationError(
                prop_name,
                f"Email does not match required pattern: {prop_def.validation_pattern}",
            )

        return value

    @staticmethod
    def _validate_enum(prop_name: str, value: Any, prop_def: PropertyDefinition) -> str:
        """Validate enum type."""
        if not isinstance(value, str):
            raise PropertyValidationError(
                prop_name, f"Expected string for enum, got {type(value).__name__}"
            )

        if not prop_def.enum_values:
            raise PropertyValidationError(prop_name, "No enum values defined for enum property")

        if value not in prop_def.enum_values:
            allowed = ", ".join(prop_def.enum_values)
            raise PropertyValidationError(
                prop_name, f"Value '{value}' is not in allowed enum values: {allowed}"
            )

        return value

    @staticmethod
    def _validate_node_reference(prop_name: str, value: Any, prop_def: PropertyDefinition) -> str:
        """Validate node reference type (stores node ID as string)."""
        if not isinstance(value, str):
            raise PropertyValidationError(
                prop_name, f"Expected node ID string, got {type(value).__name__}"
            )

        # Basic validation: node IDs should start with expected prefixes
        valid_prefixes = ("node_", "edge_", "template_", "system_", "system:", "workflow_")
        if not any(value.startswith(prefix) for prefix in valid_prefixes):
            raise PropertyValidationError(prop_name, f"Invalid node ID format: {value}")

        return value

    @staticmethod
    def _validate_node_reference_list(
        prop_name: str, value: Any, prop_def: PropertyDefinition
    ) -> list[str]:
        """Validate node reference list type (array of node IDs)."""
        if not isinstance(value, list):
            raise PropertyValidationError(
                prop_name, f"Expected list of node IDs, got {type(value).__name__}"
            )

        validated_list = []
        for i, item in enumerate(value):
            if not isinstance(item, str):
                raise PropertyValidationError(
                    prop_name, f"Item at index {i} is not a string (got {type(item).__name__})"
                )

            # Basic validation
            valid_prefixes = ("node_", "edge_", "template_", "system_", "system:", "workflow_")
            if not any(item.startswith(prefix) for prefix in valid_prefixes):
                raise PropertyValidationError(
                    prop_name, f"Invalid node ID format at index {i}: {item}"
                )

            validated_list.append(item)

        return validated_list
