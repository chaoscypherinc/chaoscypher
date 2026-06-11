# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Seed data system for Chaos Cypher Knowledge Engine.

Provides idempotent seeding of default data:
- System tools (40+ tools from registry)
- Default workflows (3 system workflows)
- Default triggers (1 auto-embed trigger on node.create)
"""

from datetime import UTC, datetime
from typing import Any

import structlog
from sqlmodel import Session, select

from chaoscypher_core.adapters.sqlite.models import (
    SystemTool,
    Trigger,
    Workflow,
    WorkflowStatistics,
    WorkflowStep,
)
from chaoscypher_core.adapters.sqlite.repos import GraphRepository
from chaoscypher_core.database.adapter_factory import get_sqlite_adapter
from chaoscypher_core.services.workflows.tools.engine import ToolRegistry
from chaoscypher_core.templates.default_templates import get_all_default_templates


logger = structlog.get_logger(__name__)


def seed_default_data(database_name: str) -> None:
    """Seed all default data for a database.

    This function is idempotent - safe to call multiple times.
    Uses upsert patterns to avoid duplicates.

    Args:
        database_name: Name of the database to seed

    """
    logger.info("seed_data_started", database_name=database_name)

    adapter = get_sqlite_adapter(database_name=database_name)
    try:
        with adapter.transaction():
            session = adapter.session
            assert session is not None
            # Seed system tools (global, not per-database)
            seed_system_tools(session)

            # Seed default workflows (per-database)
            seed_default_workflows(session, database_name)

            # Seed default triggers (per-database)
            seed_default_triggers(session, database_name)
    finally:
        adapter.disconnect()

    # Seed default templates (per-database)
    logger.info("seed_templates_starting", database_name=database_name)
    seed_default_templates(database_name)
    logger.info("seed_templates_returned")

    logger.info("seed_data_completed", database_name=database_name)


def seed_system_tools(session: Session) -> None:
    """Seed system tools from plugin registry.

    System tools are global (not per-database).
    Uses upsert pattern - updates existing tools if they exist.

    Args:
        session: Database session

    """
    logger.info("seeding_system_tools")

    registry = ToolRegistry()
    plugins = registry.list_all()

    for plugin in plugins.values():
        # Convert plugin to tool data format
        tool_data = {
            "id": plugin.tool_id,
            "category": plugin.category,
            "icon": getattr(plugin, "icon", None),
            "name": plugin.name,
            "description": plugin.description,
            "input_schema": plugin.input_schema,
            "output_schema": getattr(plugin, "output_schema", {}),
            "version": "1.0.0",
            "is_active": True,
        }

        # Check if tool already exists
        existing = session.get(SystemTool, tool_data["id"])

        if existing:
            # Update existing tool
            existing.category = str(tool_data["category"])
            existing.icon = str(tool_data["icon"]) if tool_data["icon"] else None
            existing.name = str(tool_data["name"])
            existing.description = str(tool_data["description"])
            existing.input_schema = dict(tool_data["input_schema"])  # type: ignore[arg-type]
            existing.output_schema = dict(tool_data["output_schema"])  # type: ignore[arg-type]
            existing.version = str(tool_data["version"])
            existing.is_active = bool(tool_data["is_active"])
            existing.updated_at = datetime.now(UTC)
            session.add(existing)
            logger.debug("system_tool_updated", tool_id=tool_data["id"])
        else:
            # Create new tool
            tool = SystemTool(
                id=tool_data["id"],
                category=tool_data["category"],
                icon=tool_data["icon"],
                name=tool_data["name"],
                description=tool_data["description"],
                input_schema=tool_data["input_schema"],
                output_schema=tool_data["output_schema"],
                version=tool_data["version"],
                is_active=tool_data["is_active"],
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            session.add(tool)
            logger.debug("system_tool_created", tool_id=tool_data["id"])

    session.commit()
    logger.info("system_tools_seeded", tool_count=len(plugins))


def seed_default_workflows(session: Session, database_name: str) -> None:
    """Seed default system workflows.

    Creates or updates:
    - Generate Embeddings workflow

    Args:
        session: Database session
        database_name: Name of the database

    """
    logger.info("seeding_default_workflows")

    workflows = [
        _get_generate_embeddings_workflow(database_name),
    ]

    for workflow_data, steps_data in workflows:
        workflow_id = workflow_data["id"]

        # Check if workflow already exists
        existing = session.get(Workflow, workflow_id)

        if existing:
            # Update existing workflow
            existing.name = workflow_data["name"]
            existing.description = workflow_data.get("description")
            existing.category = workflow_data.get("category")
            existing.input_schema = workflow_data["input_schema"]
            existing.output_schema = workflow_data.get("output_schema")
            existing.allow_parallel_execution = workflow_data.get("allow_parallel_execution", True)
            existing.timeout_seconds = workflow_data.get("timeout_seconds")
            existing.max_retries = workflow_data.get("max_retries", 0)
            existing.tags = workflow_data.get("tags", [])
            existing.icon = workflow_data.get("icon")
            existing.updated_at = datetime.now(UTC)
            session.add(existing)

            # Delete existing steps (will recreate them)
            stmt = select(WorkflowStep).where(WorkflowStep.workflow_id == workflow_id)
            existing_steps = session.exec(stmt).all()
            for step in existing_steps:
                session.delete(step)

            logger.info(
                "workflow_updated",
                workflow_name=workflow_data["name"],
                workflow_id=workflow_id,
            )
        else:
            # Create new workflow
            workflow = Workflow(
                id=workflow_id,
                database_name=database_name,
                name=workflow_data["name"],
                description=workflow_data.get("description"),
                category=workflow_data.get("category"),
                is_system=workflow_data.get("is_system", True),
                is_active=workflow_data.get("is_active", True),
                expose_as_ai_tool=workflow_data.get("expose_as_ai_tool", False),
                input_schema=workflow_data["input_schema"],
                output_schema=workflow_data.get("output_schema"),
                allow_parallel_execution=workflow_data.get("allow_parallel_execution", True),
                timeout_seconds=workflow_data.get("timeout_seconds"),
                max_retries=workflow_data.get("max_retries", 0),
                tags=workflow_data.get("tags", []),
                icon=workflow_data.get("icon"),
                version=workflow_data.get("version", "1.0.0"),
                created_by="system",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            session.add(workflow)

            # Create statistics entry
            stats = WorkflowStatistics(
                workflow_id=workflow_id,
                updated_at=datetime.now(UTC),
            )
            session.add(stats)

            logger.info(
                "workflow_created",
                workflow_name=workflow_data["name"],
                workflow_id=workflow_id,
            )

        # Create steps
        for step_data in steps_data:
            step = WorkflowStep(
                id=step_data["id"],
                workflow_id=workflow_id,
                step_number=step_data["step_number"],
                name=step_data["name"],
                description=step_data.get("description"),
                tool_type=step_data["tool_type"],
                tool_id=step_data["tool_id"],
                configuration=step_data["configuration"],
                condition=step_data.get("condition"),
                retry_on_failure=step_data.get("retry_on_failure", False),
                timeout_seconds=step_data.get("timeout_seconds"),
                depends_on=step_data.get("depends_on", []),
                continue_on_error=step_data.get("continue_on_error", False),
                thinking_mode=step_data.get("thinking_mode"),
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            session.add(step)
            logger.debug("workflow_step_created", step_name=step_data["name"])

    session.commit()
    logger.info("workflows_seeded")


def seed_default_triggers(session: Session, database_name: str) -> None:
    """Seed default system triggers.

    Creates:
    - Auto-embed on node create trigger

    A second "Auto-Embed on Node Update" trigger (``node.update``) used to
    be seeded here, but no code path ever publishes that event — Cortex's
    ``update_node`` re-embeds synchronously instead — so the dormant row
    was removed (migration 0002 deletes it from existing databases).

    Args:
        session: Database session
        database_name: Name of the database

    """
    logger.info("seeding_default_triggers")

    triggers = [
        {
            "id": "system_trigger_auto_embed_create_v1",
            "database_name": database_name,
            "name": "Auto-Embed on Node Create",
            "event_source": "node.create",
            "filters": {},
            "workflow_id": "system_workflow_generate_embeddings_v1",
            "workflow_inputs": {},
            "enabled": True,
            "priority": 0,
        },
    ]

    for trigger_data in triggers:
        # Check if trigger already exists
        existing = session.get(Trigger, trigger_data["id"])

        if existing:
            logger.info(
                "trigger_already_exists",
                trigger_name=trigger_data["name"],
                trigger_id=trigger_data["id"],
            )
            continue

        # Create new trigger
        trigger = Trigger(
            id=trigger_data["id"],
            database_name=trigger_data["database_name"],
            name=trigger_data["name"],
            event_source=trigger_data["event_source"],
            filters=trigger_data["filters"],
            workflow_id=trigger_data["workflow_id"],
            workflow_inputs=trigger_data.get("workflow_inputs"),
            enabled=trigger_data["enabled"],
            priority=trigger_data.get("priority", 0),
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        session.add(trigger)
        logger.info(
            "trigger_created",
            trigger_name=trigger_data["name"],
            trigger_id=trigger_data["id"],
        )

    session.commit()
    logger.info("triggers_seeded")


# ================================
# Workflow Definitions
# ================================


def _get_generate_embeddings_workflow(
    database_name: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Get Generate Embeddings workflow definition."""
    workflow_id = "system_workflow_generate_embeddings_v1"

    workflow = {
        "id": workflow_id,
        "name": "Generate Embeddings",
        "description": "Generates embeddings for nodes (triggered on create/update)",
        "category": "system",
        "is_system": True,
        "is_active": True,
        "expose_as_ai_tool": False,
        "input_schema": {
            "type": "object",
            "required": ["entity_id"],
            "properties": {
                "entity_type": {
                    "type": "string",
                    "description": "Type of entity (node, edge, etc.)",
                },
                "entity_id": {"type": "string", "description": "ID of the entity"},
                "entity": {"type": "object", "description": "Entity data"},
            },
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "success": {"type": "boolean"},
                "embedding": {"type": "array", "items": {"type": "number"}},
            },
        },
        "tags": ["system", "embeddings"],
        "icon": "🔢",
    }

    step_1_id = f"{workflow_id}_step_generate"

    steps = [
        {
            "id": step_1_id,
            "step_number": 1,
            "name": "Generate Embedding",
            "description": "Generate vector embedding for entity",
            "tool_type": "system_tool",
            "tool_id": "ai.generate_embedding",
            "configuration": {
                "entity_id": "{{inputs.entity_id}}",
                "entity_type": "{{inputs.entity_type}}",
                "entity": "{{inputs.entity}}",
            },
            "depends_on": [],
        }
    ]

    return workflow, steps


def seed_default_templates(database_name: str) -> None:
    """Seed default system templates in SQLite graph database.

    Creates all default node and edge templates if they don't exist.
    This is idempotent - safe to call multiple times.

    Args:
        database_name: Name of the database to seed

    """
    logger.info("seed_templates_starting_detailed", database_name=database_name)

    try:
        logger.info("seed_templates_creating_graph_repository")

        # Use adapter-backed GraphRepository
        adapter = get_sqlite_adapter(database_name=database_name)
        try:
            with adapter.transaction():
                session = adapter.session
                assert session is not None
                graph_repo = GraphRepository(session, database_name)

                # Ensure default templates exist (idempotent)
                logger.info("seed_templates_ensuring_defaults")
                count = graph_repo.ensure_default_templates_exist(
                    default_templates_provider=get_all_default_templates
                )

                if count > 0:
                    logger.info("seed_templates_created", template_count=count)
                else:
                    logger.info("seed_templates_already_exist")
        finally:
            adapter.disconnect()

        logger.info("seed_templates_completed")
    except Exception as e:
        logger.exception(
            "seed_templates_failed",
            error_type=type(e).__name__,
            error_message=str(e),
        )
        raise
