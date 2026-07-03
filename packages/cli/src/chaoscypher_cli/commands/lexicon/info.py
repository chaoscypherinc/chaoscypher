# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Info command - Show package metadata.

Displays detailed information about a package, either from the hub or local file.

Example:
    chaoscypher lexicon info john/medical-ontology
    chaoscypher lexicon info john/medical-ontology --version 1.2.0
    chaoscypher lexicon info ./my-package.ccx --local
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import click
from rich.console import Console
from rich.panel import Panel

from chaoscypher_cli.commands.lexicon.login import get_auth_config, get_lexicon_url
from chaoscypher_cli.utils.console import get_console, print_error
from chaoscypher_core.services.lexicon import LexiconClient, LexiconClientError
from chaoscypher_core.services.package import get_archive_info


console = Console()


@click.command()
@click.argument("package")
@click.option("--version", "-v", help="Specific version")
@click.option(
    "--local",
    "-l",
    is_flag=True,
    help="Show info for local .ccx file instead of hub package",
)
def info(package: str, version: str | None, local: bool) -> None:
    """Show detailed information about a package.

    PACKAGE can be:
    - A hub package: username/packagename
    - A local file: path/to/package.ccx (with --local)

    Example:
        chaoscypher lexicon info john/medical-ontology
        chaoscypher lexicon info john/medical-ontology --version 1.2.0
        chaoscypher lexicon info ./my-package.ccx --local
    """
    console = get_console()

    if local:
        _show_local_info(package, console)
    else:
        _show_hub_info(package, version, console)


def _show_local_info(package: str, console: Console) -> None:
    """Show info for local .ccx file."""
    package_path = Path(package)

    if not package_path.exists():
        print_error(f"File not found: {package}")
        sys.exit(1)

    if package_path.suffix.lower() != ".ccx":
        console.print("[yellow]Warning:[/yellow] File doesn't have .ccx extension")

    console.print(f"[cyan]Package:[/cyan] {package_path.name}\n")

    try:
        # Use core archive inspection
        archive_info = get_archive_info(package_path)

        # Show basic info
        console.print(
            Panel(
                f"[bold]{package_path.name}[/bold]\n"
                f"Compressed: {archive_info.compressed_size_formatted}\n"
                f"Uncompressed: {archive_info.uncompressed_size_formatted}",
                title="Package Info",
                border_style="cyan",
            )
        )

        # Show contents
        console.print(f"\n[cyan]Files:[/cyan] ({archive_info.file_count} total)")
        for f in archive_info.contents[:10]:
            console.print(f"  - {f}")
        if archive_info.file_count > 10:
            console.print(f"  ... and {archive_info.file_count - 10} more")

        # File size
        console.print(f"\n[dim]Archive size:[/dim] {archive_info.compressed_size_formatted}")

    except Exception as e:
        print_error(f"Failed to read package: {e}")
        sys.exit(1)


def _show_hub_info(package: str, version: str | None, console: Console) -> None:
    """Show info for hub package."""
    console.print(f"[cyan]Package:[/cyan] {package}")

    if version:
        console.print(f"[dim]Version:[/dim] {version}")

    console.print()

    # Get auth and hub URL
    auth = get_auth_config()
    lexicon_url = get_lexicon_url()

    # Parse owner/name from package string
    if "/" not in package:
        print_error("Package must be in format: owner/name")
        sys.exit(1)

    owner_username, repo_name = package.split("/", 1)

    async def do_fetch() -> Any:
        """Fetch package metadata from the Lexicon hub."""
        async with LexiconClient(base_url=lexicon_url, auth=auth) as client:
            return await client.get_package_info(owner_username, repo_name, version)

    try:
        pkg_info = asyncio.run(do_fetch())

        # Show package info
        console.print(
            Panel(
                f"[bold]{pkg_info.name}[/bold]\n"
                f"Version: {pkg_info.version}\n"
                f"Owner: {pkg_info.owner_username}\n"
                f"[dim]{pkg_info.description or 'No description'}[/dim]",
                title="Package Info",
                border_style="cyan",
            )
        )

        # Show metadata
        console.print("\n[cyan]Details:[/cyan]")
        if pkg_info.conformance_classes:
            console.print(f"  Conformance: {', '.join(pkg_info.conformance_classes)}")
        if pkg_info.is_signed is not None:
            console.print(f"  Signed: {'yes' if pkg_info.is_signed else 'no'}")
        console.print(f"  Downloads: {pkg_info.download_count:,}")
        console.print(f"  Stars: {pkg_info.star_count:,}")
        console.print(f"  Versions: {pkg_info.version_count}")

        if pkg_info.created_at:
            console.print(f"  Created: {pkg_info.created_at}")
        if pkg_info.updated_at:
            console.print(f"  Updated: {pkg_info.updated_at}")

        # Show install command
        console.print("\n[dim]To install:[/dim]")
        console.print(f"  chaoscypher pull {package}")

    except LexiconClientError as e:
        if e.status_code == 404:
            print_error(f"Package not found: {package}")
        else:
            print_error(f"Hub error: {e.message}")
        sys.exit(1)
