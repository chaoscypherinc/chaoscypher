# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Mcp command - serve a composition to an MCP host over stdio.

The one-line way to give an AI assistant a whole stack of packages: build the
composition if its database is missing, then replace this process with
``chaoscypher mcp`` pointed at the composed database. Safe to use as an MCP
server command line — a built composition starts serving immediately.

Example:
    chaoscypher compose mcp
    chaoscypher compose mcp -c ./research/axiomatize.yaml --mode write
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import click

from chaoscypher_cli.commands.lexicon.login import get_auth_config, get_lexicon_url
from chaoscypher_core.services.compose import ComposeConfig, ComposeError, ComposeService
from chaoscypher_core.services.compose.merger import COMPOSED_DATABASE_NAME


@click.command()
@click.option(
    "--config",
    "-c",
    default="axiomatize.yaml",
    type=click.Path(exists=True),
    help="Path to composition config file",
)
@click.option(
    "--mode",
    "-m",
    type=click.Choice(["read", "write"]),
    default=None,
    help="MCP tool access mode (default: from settings, usually 'read')",
)
@click.option("--build", "-b", "rebuild", is_flag=True, help="Force a rebuild before serving")
def mcp(config: str, mode: str | None, rebuild: bool) -> None:
    """Serve the composition to an MCP host over stdio.

    Builds the composed database if it does not exist yet, then serves it
    exactly like `chaoscypher mcp` would. Put this command in Claude
    Desktop's / Claude Code's / Cursor's server config.

    Example:
        chaoscypher compose mcp
        chaoscypher compose mcp -c research.yaml --mode write
    """
    # stdout is the MCP JSON-RPC channel once we exec into `chaoscypher mcp`;
    # everything printed here goes to stderr.
    err = click.get_text_stream("stderr")

    try:
        compose_config = ComposeConfig.from_yaml(Path(config))
    except Exception as e:
        msg = f"Failed to load config: {e}"
        raise click.ClickException(msg) from e

    output_dir = compose_config.resolved_output_dir
    db_exists = (output_dir / "databases" / COMPOSED_DATABASE_NAME).exists()

    if rebuild or not db_exists:
        click.echo(f"Building composition: {compose_config.name}", file=err)
        service = ComposeService(auth=get_auth_config(), lexicon_url=get_lexicon_url())
        try:
            result = asyncio.run(service.build(compose_config, clean=rebuild))
        except ComposeError as e:
            msg = f"Build failed: {e.message}"
            raise click.ClickException(msg) from e
        if not result.success:
            raise click.ClickException("Build failed: " + "; ".join(result.errors))
        click.echo(
            f"Built: {result.total_entities} entities, {result.total_relationships} relationships",
            file=err,
        )

    env = os.environ.copy()
    env.update(ComposeService._composition_env(compose_config))
    args = [sys.executable, "-m", "chaoscypher_cli", "mcp", "--database", COMPOSED_DATABASE_NAME]
    if mode:
        args += ["--mode", mode]
    click.echo(f"Serving composition '{compose_config.name}' over MCP stdio…", file=err)
    # Replace this process: stdio passes straight through to the MCP server,
    # no relay, no buffering, one pid for the host to manage.
    os.execve(sys.executable, args, env)  # noqa: S606 — our own interpreter, fixed argv
