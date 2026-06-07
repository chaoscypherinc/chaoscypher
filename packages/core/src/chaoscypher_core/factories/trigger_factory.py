# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Trigger Service Factory - Shared factory for TriggerService construction.

Provides a single factory function to create properly configured TriggerService
instances. Used by both cortex API (features/triggers/api.py) and neuron worker.

Example:
    from chaoscypher_core.factories import get_trigger_service

    service = get_trigger_service("my_database")

"""

from chaoscypher_core.database import get_sqlite_adapter
from chaoscypher_core.services.workflows.triggers import TriggerService


def get_trigger_service(database_name: str) -> TriggerService:
    """Create TriggerService with proper dependency injection.

    Uses SqliteAdapter which implements TriggerStorageProtocol via TriggersMixin.

    Args:
        database_name: Current database name for trigger storage.

    Returns:
        Configured TriggerService instance ready for use.

    """
    adapter = get_sqlite_adapter(database_name=database_name)

    return TriggerService(storage=adapter, database_name=database_name)
