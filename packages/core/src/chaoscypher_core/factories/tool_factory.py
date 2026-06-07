# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tool Service Factory - Shared factory for ToolService construction.

Provides a single factory function to create properly configured ToolService
instances. This avoids code duplication between tools/api.py and workflows/api.py.

The ToolService requires a ToolStorageProtocol adapter. SqliteAdapter implements
this protocol via ToolsMixin.

Example:
    from chaoscypher_core.factories import get_tool_service

    def get_my_tool_service(settings):
        return get_tool_service(settings.current_database)

"""

from chaoscypher_core.database import get_sqlite_adapter
from chaoscypher_core.services.workflows.tools.management import ToolService


def get_tool_service(database_name: str) -> ToolService:
    """Create ToolService with proper dependency injection.

    Uses SqliteAdapter which implements ToolStorageProtocol via ToolsMixin.
    This is the correct hexagonal architecture pattern - services depend on
    storage protocols, adapters implement them.

    Args:
        database_name: Current database name for filtering user tools

    Returns:
        Configured ToolService instance ready for use

    """
    # Get singleton SqliteAdapter (implements ToolStorageProtocol)
    adapter = get_sqlite_adapter(database_name=database_name)

    # Create and return service
    return ToolService(storage=adapter, database_name=database_name)
