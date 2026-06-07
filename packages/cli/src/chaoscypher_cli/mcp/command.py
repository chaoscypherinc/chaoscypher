# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
# ruff: noqa: D301  -- Click \x08 paragraph escape, intentional non-raw docstring.

"""CLI command for starting the MCP server.

Starts a stdio-based MCP server that exposes Chaos Cypher's knowledge
graph tools to MCP-compatible hosts (Claude Desktop, Cursor, etc.).
"""

import asyncio
import os
import sys
from typing import Literal, cast

import click
import structlog

from chaoscypher_cli.context import get_context, get_database_name


logger = structlog.get_logger(__name__)


@click.command()
@click.option(
    "--database",
    "-d",
    default=None,
    help="Database name (default: from settings)",
)
@click.option(
    "--mode",
    "-m",
    type=click.Choice(["read", "write"]),
    default=None,
    help="Tool access mode (default: from settings, usually 'read')",
)
@click.option(
    "--server-extraction",
    is_flag=True,
    help="Use server-side LLM for extraction instead of client-driven",
)
def mcp(database: str | None, mode: str | None, server_extraction: bool) -> None:
    """Start an MCP server over stdio.

    Exposes Chaos Cypher knowledge graph tools to MCP-compatible hosts
    like Claude Desktop, Cursor, and Windsurf.

    By default, extraction is client-driven: the MCP client (e.g. Claude)
    performs entity extraction itself after indexing. Use --server-extraction
    to have the server's LLM handle extraction instead.

    \x08
    Configure in Claude Desktop:
        {"mcpServers": {"chaoscypher": {"command": "chaoscypher", "args": ["mcp"]}}}
    """
    # MCP stdio transport uses stdout for JSON-RPC protocol messages.
    # All logging MUST go to stderr to avoid corrupting the protocol stream.
    from chaoscypher_core.utils.logging import configure_logging

    configure_logging(
        log_level=os.getenv("LOG_LEVEL", "WARNING"),
        stream=sys.stderr,
    )

    # Run the same startup migration the Engine would run, BEFORE building the
    # heavy Engine. With auto_apply_destructive on (default) this heals the DB
    # to head; with it off and a destructive migration pending (or a failed
    # backup preflight) it records a blocked upgrade-state and returns without
    # raising. Routing on that record here — rather than letting get_context()
    # build the Engine first — means we never wire an Engine against a blocked,
    # behind-head schema, and a blocked DB starts a maintenance-mode server
    # instead of dying before the stdio handshake (the opaque -32000 this fixes).
    from chaoscypher_core.database.engine import get_db_path
    from chaoscypher_core.database.migrations.startup import run_startup_migrations
    from chaoscypher_core.database.migrations.state import get_upgrade_state

    db_name = get_database_name(database)
    db_path = get_db_path(db_name)
    try:
        run_startup_migrations(db_path)
    except Exception:
        # The gate is designed to be silent; only a genuine apply error reaches
        # here. Log to stderr and fall through to the state check, which routes
        # to maintenance mode so the client gets an actionable message.
        logger.error("mcp_startup_migration_error", database=db_name)  # noqa: TRY400
        logger.debug("mcp_startup_migration_error_traceback", exc_info=True)

    state = get_upgrade_state(db_path)

    # Pre-import heavy modules BEFORE entering the anyio event loop.
    # Python's import lock can deadlock when imports happen inside anyio's
    # task group because anyio wraps the asyncio event loop and may hold
    # internal locks that conflict with the import system.
    import langchain_text_splitters  # noqa: F401
    from mcp.server.stdio import stdio_server

    if state.ready:
        ctx = get_context(database_name=database)

        if mode:
            # click.Choice(["read", "write"]) above guarantees the value
            ctx.settings.mcp.mode = cast("Literal['read', 'write']", mode)
        if server_extraction:
            ctx.settings.mcp.auto_extract = True

        from chaoscypher_core.mcp.server import create_mcp_server

        engine = ctx._engine  # noqa: SLF001
        if engine is None:
            msg = "Engine not initialized — context.connect() must be called first"
            raise RuntimeError(msg)
        server = create_mcp_server(engine)
        logger.info(
            "mcp_server_starting",
            database=ctx.database_name,
            mode=ctx.settings.mcp.mode,
            auto_extract=ctx.settings.mcp.auto_extract,
        )
    else:
        # Blocked DB: start the degraded maintenance-mode server (no Engine).
        # This is the MCP analog of the web maintenance page — never invisible.
        from chaoscypher_core.mcp.maintenance import create_maintenance_mcp_server

        server = create_maintenance_mcp_server(db_name)
        logger.warning(
            "mcp_server_maintenance_mode",
            database=db_name,
            blocked_on=state.blocked_on,
            message=state.message,
        )

    async def run() -> None:
        """Serve the MCP protocol over stdio until the client disconnects."""
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )

    asyncio.run(run())
