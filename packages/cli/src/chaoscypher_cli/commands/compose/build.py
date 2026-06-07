# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Build command - Compile axiomatize.yaml into a runtime database.

Uses the core ComposeService to resolve packages, download from Lexicon,
and merge into a unified knowledge database.

Example:
    chaoscypher compose build
    chaoscypher compose build --config my-compose.yaml
    chaoscypher compose build --clean
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
    "--clean",
    is_flag=True,
    help="Clean output directory before building",
)
def build(config: str, clean: bool) -> None:
    """Compile axiomatize.yaml into a runtime database.

    Reads the composition configuration and resolves all referenced
    packages (from Lexicon or local), then merges them into a unified
    knowledge database ready for serving.

    Example:
        chaoscypher compose build
        chaoscypher compose build --config my-compose.yaml
        chaoscypher compose build --clean
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

    console.print(f"[cyan]Building composition:[/cyan] {compose_config.name}")
    console.print(f"  [dim]Config:[/dim] {config}")
    console.print(f"  [dim]Packages:[/dim] {len(compose_config.packages)}")
    console.print(f"  [dim]Strategy:[/dim] {compose_config.settings.merge_strategy.value}")
    console.print(f"  [dim]Output:[/dim] {compose_config.resolved_output_dir}")

    if clean:
        console.print("  [dim]Clean build:[/dim] Yes")

    console.print()

    # Get auth and Lexicon URL
    auth = get_auth_config()
    lexicon_url = get_lexicon_url()

    if not auth:
        console.print(
            "[yellow]Note:[/yellow] Not logged in. Lexicon packages may require authentication."
        )
        console.print("Run 'chaoscypher login' to authenticate.\n")

    async def do_build() -> Any:
        """Run the async ComposeService.build call."""
        service = ComposeService(auth=auth, lexicon_url=lexicon_url)
        return await service.build(compose_config, clean=clean)

    try:
        console.print("[dim]Resolving packages...[/dim]")
        result = asyncio.run(do_build())

        if result.success:
            print_success(f"Built composition: {compose_config.name}")
            console.print(f"  [dim]Packages:[/dim] {len(result.packages_included)}")
            for pkg in result.packages_included:
                console.print(f"    • {pkg}")
            console.print(f"  [dim]Entities:[/dim] {result.total_entities:,}")
            console.print(f"  [dim]Relationships:[/dim] {result.total_relationships:,}")
            console.print(f"  [dim]Database:[/dim] {result.output_dir}")

            if result.warnings:
                console.print("\n[yellow]Warnings:[/yellow]")
                for warning in result.warnings:
                    console.print(f"  • {warning}")

            console.print("\n[dim]Next steps:[/dim]")
            console.print(f"  chaoscypher compose up -c {config}")
        else:
            print_error("Build failed")
            for error in result.errors:
                console.print(f"  [red]✗[/red] {error}")
            sys.exit(1)

    except ComposeError as e:
        print_error(f"Build failed: {e.message}")
        if e.details:
            for key, value in e.details.items():
                console.print(f"  [dim]{key}:[/dim] {value}")
        sys.exit(1)
