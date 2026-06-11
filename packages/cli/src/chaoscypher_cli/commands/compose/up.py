# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Up command - Build and run the composition.

Uses the core ComposeService to build the database and start the server.

Example:
    chaoscypher compose up
    chaoscypher compose up --port 9000
    chaoscypher compose up --detach
    chaoscypher compose up --build
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import click

from chaoscypher_cli.commands.lexicon.login import get_auth_config, get_lexicon_url
from chaoscypher_cli.utils.console import get_console, print_error, print_success
from chaoscypher_core.services.compose import ComposeConfig, ComposeError, ComposeService


@click.command()
@click.option(
    "--config",
    "-c",
    default="axiomatize.yaml",
    type=click.Path(exists=True),
    help="Path to composition config file",
)
@click.option(
    "--port",
    "-p",
    type=int,
    help="API port (overrides config setting)",
)
@click.option(
    "--detach",
    "-d",
    is_flag=True,
    help="Run in background",
)
@click.option(
    "--build",
    "-b",
    "rebuild",
    is_flag=True,
    help="Force rebuild before starting",
)
def up(
    config: str,
    port: int | None,
    detach: bool,
    rebuild: bool,
) -> None:
    """Run the composition defined in axiomatize.yaml.

    Starts a knowledge server from the composed packages.
    Builds the database if it doesn't exist or if --build is specified.

    Example:
        chaoscypher compose up
        chaoscypher compose up --port 9000
        chaoscypher compose up --detach
        chaoscypher compose up --build
    """
    console = get_console()

    # Load configuration
    try:
        compose_config = ComposeConfig.from_yaml(Path(config))
    except FileNotFoundError:
        print_error(f"Config file not found: {config}")
        sys.exit(1)
    except Exception as e:
        print_error(f"Failed to load config: {e}")
        sys.exit(1)

    # Override port if specified
    if port:
        compose_config.settings.port = port

    console.print(f"[cyan]Starting composition:[/cyan] {compose_config.name}")
    console.print(f"  [dim]Config:[/dim] {config}")
    console.print(f"  [dim]API port:[/dim] {compose_config.settings.port}")

    if rebuild:
        console.print("  [dim]Force rebuild:[/dim] Yes")
    if detach:
        console.print("  [dim]Detached mode:[/dim] Yes")

    console.print()

    # Get auth and Lexicon URL
    auth = get_auth_config()
    lexicon_url = get_lexicon_url()

    if not auth:
        console.print(
            "[yellow]Note:[/yellow] Not logged in. Lexicon packages may require authentication."
        )
        console.print("Run 'chaoscypher lexicon login' to authenticate.\n")

    async def do_up() -> Any:
        """Run the async ComposeService.up call."""
        service = ComposeService(auth=auth, lexicon_url=lexicon_url)
        return await service.up(compose_config, rebuild=rebuild, detach=detach)

    try:
        result = asyncio.run(do_up())

        if result.success:
            if detach:
                print_success("Composition started in background")
                console.print(
                    f"  [dim]Server:[/dim] http://localhost:{compose_config.settings.port}"
                )
                console.print("\n[dim]To stop:[/dim]")
                console.print(f"  chaoscypher compose down -c {config}")
            else:
                print_success("Composition stopped")
        else:
            print_error("Failed to start composition")
            for error in result.errors:
                console.print(f"  [red]✗[/red] {error}")
            sys.exit(1)

    except ComposeError as e:
        print_error(f"Failed to start: {e.message}")
        if e.details:
            for key, value in e.details.items():
                console.print(f"  [dim]{key}:[/dim] {value}")
        sys.exit(1)

    except KeyboardInterrupt:
        console.print("\n[yellow]Shutting down...[/yellow]")
