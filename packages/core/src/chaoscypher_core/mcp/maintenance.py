# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""MCP maintenance-mode server.

When a database is blocked on a pending schema upgrade — an operator set
``auto_apply_destructive=false`` and a destructive migration is pending, or the
startup backup preflight failed — the normal MCP server can't safely serve
knowledge-graph tools against a behind-head schema. Rather than letting
``chaoscypher mcp`` die during the stdio handshake (which the client only sees
as an opaque JSON-RPC ``-32000``), it starts THIS degraded server.

The maintenance server advertises only ``upgrade_status`` and ``apply_upgrade``
and answers every other (normal) tool call with a clear, actionable error. It
needs no ``Engine``: it drives :class:`UpgradeService` and the migration-state
helpers directly on the database file, so it works even though the schema is
behind head. This is the MCP analog of the web maintenance page.
"""

from __future__ import annotations

import json
from typing import Any

import structlog
from mcp.server import Server
from mcp.types import TextContent, Tool

from chaoscypher_core.database.engine import get_db_path
from chaoscypher_core.database.migrations.runner import pending_revisions
from chaoscypher_core.database.migrations.state import (
    describe_apply_failure,
    get_upgrade_state,
)
from chaoscypher_core.database.migrations.tiers import MigrationTier, read_migration_info
from chaoscypher_core.database.migrations.upgrade import UpgradeService
from chaoscypher_core.mcp.tools import get_maintenance_tools


logger = structlog.get_logger(__name__)

_BLOCKED_TOOL_ERROR = (
    "Database needs a one-time upgrade before this tool can run. Call "
    "apply_upgrade (or open the web upgrade page) to finish the upgrade, then "
    "reconnect the MCP server."
)


def _maintenance_tool_list() -> list[Tool]:
    """Return the degraded toolset advertised while the DB is blocked."""
    return [
        Tool(name=t.name, description=t.description, inputSchema=t.input_schema)
        for t in get_maintenance_tools()
    ]


def _handle_upgrade_status(database_name: str) -> dict[str, Any]:
    """Report pending migrations + plain-language message + backup info."""
    pending = UpgradeService(database_name).pending()
    state = get_upgrade_state(get_db_path(database_name))
    return {
        "success": True,
        "ready": pending.ready,
        "blocked_on": [
            {
                "revision": migration.revision,
                "tier": migration.tier.value,
                "description": migration.description,
            }
            for migration in pending.blocked_on
        ],
        "message": pending.message,
        "last_backup": pending.last_backup,
        "last_applied": state.last_applied,
    }


def _handle_apply_upgrade(database_name: str, *, confirm_destructive: bool) -> dict[str, Any]:
    """Apply pending migrations, gating destructive (manual) ones on confirm.

    ``UpgradeService.apply`` applies ALL pending revisions (it is the explicit
    human path), so the destructive gate is enforced HERE before we call it.
    On success we instruct the client to reconnect — the running process keeps
    its degraded toolset; reconnecting rebuilds the server with normal tools
    against the now-healed schema.
    """
    db_path = get_db_path(database_name)
    revisions = pending_revisions(db_path)
    if not revisions:
        return {
            "success": True,
            "applied": [],
            "message": (
                "No pending migrations — the database is already up to date. "
                "Reconnect the MCP server to use the knowledge-graph tools."
            ),
        }

    destructive = [
        rev for rev in revisions if read_migration_info(rev).tier is MigrationTier.MANUAL
    ]
    if destructive and not confirm_destructive:
        return {
            "success": False,
            "error_code": "CONFIRM_DESTRUCTIVE_REQUIRED",
            "error": (
                f"{len(destructive)} pending migration(s) are destructive and "
                f"may drop data or force re-extraction: {', '.join(destructive)}. "
                "Re-call apply_upgrade with confirm_destructive=true to proceed "
                "(a verified backup is taken first)."
            ),
            "destructive": destructive,
        }

    try:
        applied = UpgradeService(database_name).apply()
    except Exception as exc:
        logger.error(  # noqa: TRY400 - traceback logged separately at DEBUG
            "mcp_apply_upgrade_failed",
            database=database_name,
            error_type=type(exc).__name__,
        )
        logger.debug("mcp_apply_upgrade_failed_traceback", database=database_name, exc_info=True)
        # Surface the REAL cause (e.g. the schema is ahead of its stamp), not a
        # generic "check the logs" — otherwise apply_upgrade is a dead-end loop.
        last_backup = get_upgrade_state(db_path).last_backup
        return {
            "success": False,
            "error_code": "UPGRADE_FAILED",
            "error": describe_apply_failure(exc, last_backup=last_backup),
        }

    logger.info(
        "mcp_apply_upgrade_succeeded",
        database=database_name,
        applied=applied.applied,
        backup=applied.backup_path,
    )
    return {
        "success": True,
        "applied": applied.applied,
        "current_revision": applied.current_revision,
        "backup_path": applied.backup_path,
        "message": (
            "Upgrade applied successfully. Reconnect the MCP server to access "
            "the knowledge-graph tools."
        ),
    }


async def _maintenance_dispatch(
    database_name: str, name: str, arguments: dict | None
) -> list[TextContent]:
    """Route a maintenance-mode tool call; reject normal tools with guidance.

    The two maintenance handlers do synchronous SQLite work (millisecond-level,
    except ``apply_upgrade`` which runs Alembic to head once). Calling them
    inline briefly blocks the anyio event loop, which is acceptable: the
    degraded server has nothing else to serve.
    """
    args = arguments or {}
    if name == "upgrade_status":
        result = _handle_upgrade_status(database_name)
    elif name == "apply_upgrade":
        result = _handle_apply_upgrade(
            database_name,
            confirm_destructive=bool(args.get("confirm_destructive", False)),
        )
    else:
        result = {
            "success": False,
            "error_code": "DATABASE_UPGRADE_REQUIRED",
            "error": _BLOCKED_TOOL_ERROR,
        }
    return [TextContent(type="text", text=json.dumps(result, default=str))]


def create_maintenance_mcp_server(database_name: str) -> Server:
    """Build a degraded MCP server for a DB blocked on a schema upgrade.

    Exposes only ``upgrade_status`` and ``apply_upgrade``; every other tool
    name returns an actionable error instead of failing opaquely. Requires no
    ``Engine`` — it operates directly on the database file.

    Args:
        database_name: Name of the database whose upgrade is blocked.

    Returns:
        A configured MCP ``Server`` ready for stdio transport binding.
    """
    server: Server = Server("chaoscypher")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        """Advertise only the maintenance toolset."""
        return _maintenance_tool_list()

    @server.call_tool()
    async def call_tool(name: str, arguments: dict | None = None) -> list[TextContent]:
        """Route to the maintenance handlers; reject normal tools."""
        return await _maintenance_dispatch(database_name, name, arguments)

    state = get_upgrade_state(get_db_path(database_name))
    logger.info(
        "mcp_maintenance_server_created",
        database=database_name,
        blocked_on=state.blocked_on,
        tool_count=len(get_maintenance_tools()),
    )
    return server


__all__ = ["create_maintenance_mcp_server"]
