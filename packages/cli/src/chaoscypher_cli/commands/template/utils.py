# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Shared utilities for template commands."""

from chaoscypher_core.models import PropertyType


# Valid property types for template definitions — derived from core's canonical enum
PROPERTY_TYPES = [t.name for t in PropertyType]


def parse_property(prop_str: str) -> dict:
    """Parse property definition from string format.

    Format: name:type[:required]

    Examples:
        name:string:required
        age:integer
        email:email:required

    Args:
        prop_str: Property definition string

    Returns:
        Dict with name, display_name, property_type, required

    Raises:
        ValueError: If format is invalid or type is not recognized
    """
    parts = prop_str.split(":")
    if len(parts) < 2:
        msg = f"Invalid property format: {prop_str}. Use name:type[:required]"
        raise ValueError(msg)

    prop_name = parts[0].strip()
    prop_type = parts[1].strip().upper()
    required = len(parts) > 2 and parts[2].strip().lower() == "required"

    if prop_type not in PROPERTY_TYPES:
        msg = f"Invalid property type: {prop_type}. Valid types: {', '.join(PROPERTY_TYPES)}"
        raise ValueError(msg)

    return {
        "name": prop_name,
        "display_name": prop_name.replace("_", " ").title(),
        "property_type": prop_type,
        "required": required,
    }
