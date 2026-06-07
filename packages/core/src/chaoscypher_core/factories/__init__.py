# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Shared Factories - Reusable service factory functions.

This module provides factory functions that create properly configured
service instances with dependency injection. These factories are shared
across multiple API modules and the neuron worker to avoid code
duplication (DRY principle).

Example:
    from chaoscypher_core.factories import (
        get_tool_service,
        get_workflow_service,
        get_trigger_service,
    )

    tool_service = get_tool_service(database_name)
    workflow_service = get_workflow_service(database_name)
    trigger_service = get_trigger_service(database_name)

"""

from chaoscypher_core.factories.tool_factory import get_tool_service
from chaoscypher_core.factories.trigger_factory import get_trigger_service
from chaoscypher_core.factories.workflow_factory import get_workflow_service


__all__ = [
    "get_tool_service",
    "get_trigger_service",
    "get_workflow_service",
]
