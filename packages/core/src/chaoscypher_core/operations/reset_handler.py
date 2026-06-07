# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Background worker handlers for reset + cleanup operations.

These used to run synchronously in the API thread:

- POST /settings/reset/knowledge — delete sources, wipe graph, reset indices
- POST /settings/reset/all — nuclear reset (drop & recreate app.db)
- POST /graph/cleanup — remove corrupt/orphaned graph items
- POST /settings/cleanup/orphans — remove orphan references

Per 2026-04-18 decision 3, they now enqueue Valkey tasks and return 202.
The workers here execute the existing ``ResetOperations`` /
``GraphCleanupService`` code paths unchanged — only the dispatch
location moved from the API thread to the Neuron worker.
"""

from __future__ import annotations

from typing import Any

import structlog

from chaoscypher_core.exceptions import ValidationError


logger = structlog.get_logger(__name__)


async def handle_reset_knowledge_base(
    data: dict[str, Any],
    metadata: dict[str, Any] | None = None,
    task_id: str | None = None,
) -> dict[str, Any]:
    """Worker handler: reset the knowledge base for a database.

    Runs the full wipe-and-reseed flow: delete source data, clear the
    knowledge graph, wipe import files, reset search indices, re-seed
    default templates. Exceptions propagate so the queue records a
    failed task.

    Args:
        data: Task data with ``database_name``.
        metadata: Task metadata (database_name, operation_type, user_id).
        task_id: Queue task id for logging.

    Returns:
        ResetResponse.data dict (status, counts).

    Raises:
        ValidationError: If ``data['database_name']`` is missing.

    """
    from chaoscypher_core.app_config import get_config_manager
    from chaoscypher_core.services.reset import ResetOperations

    database_name = data.get("database_name")
    if not database_name:
        msg = "reset_knowledge_base requires data.database_name"
        raise ValidationError(msg, field="database_name")

    logger.info(
        "reset_knowledge_base_worker_started",
        task_id=task_id,
        database_name=database_name,
    )

    config_manager = get_config_manager()
    ops = ResetOperations(database_name=database_name, settings_manager=config_manager)
    response = await ops.reset_knowledge_base()

    logger.info(
        "reset_knowledge_base_worker_complete",
        task_id=task_id,
        database_name=database_name,
    )
    return response


async def handle_reset_all(
    data: dict[str, Any],
    metadata: dict[str, Any] | None = None,
    task_id: str | None = None,
) -> dict[str, Any]:
    """Worker handler: nuclear database reset (drop + recreate app.db).

    Args:
        data: Task data with ``database_name``.
        metadata: Task metadata.
        task_id: Queue task id for logging.

    Returns:
        ResetResponse.data dict.

    Raises:
        ValidationError: If ``data['database_name']`` is missing.

    """
    from chaoscypher_core.app_config import get_config_manager
    from chaoscypher_core.services.reset import ResetOperations

    database_name = data.get("database_name")
    if not database_name:
        msg = "reset_all requires data.database_name"
        raise ValidationError(msg, field="database_name")

    logger.info(
        "reset_all_worker_started",
        task_id=task_id,
        database_name=database_name,
    )

    config_manager = get_config_manager()
    ops = ResetOperations(database_name=database_name, settings_manager=config_manager)
    response = await ops.reset_all()

    logger.info(
        "reset_all_worker_complete",
        task_id=task_id,
        database_name=database_name,
    )
    return response


async def handle_graph_cleanup(
    data: dict[str, Any],
    metadata: dict[str, Any] | None = None,
    task_id: str | None = None,
) -> dict[str, Any]:
    """Worker handler: remove corrupt graph items.

    Args:
        data: Task data with ``database_name``.
        metadata: Task metadata.
        task_id: Queue task id for logging.

    Returns:
        Cleanup-result dict.

    Raises:
        ValidationError: If ``data['database_name']`` is missing.

    """
    from chaoscypher_core.services.reset import GraphCleanupService

    database_name = data.get("database_name")
    if not database_name:
        msg = "graph_cleanup requires data.database_name"
        raise ValidationError(msg, field="database_name")

    logger.info(
        "graph_cleanup_worker_started",
        task_id=task_id,
        database_name=database_name,
    )

    service = GraphCleanupService(database_name=database_name)
    # The existing service exposes cleanup_corrupt_items; fall back to
    # cleanup_orphaned_items if that's the only method available.
    if hasattr(service, "cleanup_corrupt_items"):
        result = service.cleanup_corrupt_items()
    else:
        result = service.cleanup_orphaned_items()

    logger.info(
        "graph_cleanup_worker_complete",
        task_id=task_id,
        database_name=database_name,
    )
    return result


async def handle_cleanup_orphans(
    data: dict[str, Any],
    metadata: dict[str, Any] | None = None,
    task_id: str | None = None,
) -> dict[str, Any]:
    """Worker handler: remove orphaned graph references.

    Args:
        data: Task data with ``database_name``.
        metadata: Task metadata.
        task_id: Queue task id for logging.

    Returns:
        Cleanup-result dict.

    Raises:
        ValidationError: If ``data['database_name']`` is missing.

    """
    from chaoscypher_core.services.reset import GraphCleanupService

    database_name = data.get("database_name")
    if not database_name:
        msg = "cleanup_orphans requires data.database_name"
        raise ValidationError(msg, field="database_name")

    logger.info(
        "cleanup_orphans_worker_started",
        task_id=task_id,
        database_name=database_name,
    )

    service = GraphCleanupService(database_name=database_name)
    result = service.cleanup_orphaned_items()

    logger.info(
        "cleanup_orphans_worker_complete",
        task_id=task_id,
        database_name=database_name,
    )
    return result
