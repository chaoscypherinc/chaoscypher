# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""List command - List installed/cached packages."""

import click
from rich.console import Console
from rich.table import Table

from chaoscypher_cli.utils.console import print_json
from chaoscypher_cli.utils.paths import get_packages_dir


console = Console()


@click.command(name="list")
@click.option("--all", "show_all", is_flag=True, help="Show all cached versions")
@click.option(
    "--format",
    "-f",
    "output_format",
    default="table",
    type=click.Choice(["table", "json", "simple"]),
    help="Output format",
)
def list_packages(show_all: bool, output_format: str) -> None:
    """List locally installed/cached packages.

    Shows packages that have been pulled from the Lexicon Hub
    or loaded from local .ccx files.

    Example:
        chaoscypher list
        chaoscypher list --all
        chaoscypher list --format json
    """
    packages_dir = get_packages_dir()

    console.print("[cyan]Installed Packages[/cyan]\n")
    console.print(f"[dim]Packages directory:[/dim] {packages_dir}\n")

    # Check if packages directory exists
    if not packages_dir.exists():
        console.print(f"[dim]No packages installed yet in {packages_dir}.[/dim]")
        console.print("\nTo install packages:")
        console.print("  chaoscypher pull <package>")
        console.print("  chaoscypher graph package load <file.ccx>")
        return

    # Find .ccx files in packages directory
    packages = list(packages_dir.glob("**/*.ccx"))

    if not packages:
        console.print(f"[dim]No packages found in {packages_dir}.[/dim]")
        console.print("\nTo install packages:")
        console.print("  chaoscypher pull <package>")
        console.print("  chaoscypher graph package load <file.ccx>")
        return

    if output_format == "simple":
        for pkg in packages:
            console.print(pkg.stem)

    elif output_format == "json":
        import json

        pkg_list = [{"name": p.stem, "path": str(p), "size": p.stat().st_size} for p in packages]
        print_json(json.dumps(pkg_list, indent=2))

    else:  # table format
        table = Table(show_header=True)
        table.add_column("Package", style="cyan")
        table.add_column("Size", style="dim", justify="right")
        table.add_column("Path", style="dim")

        for pkg in packages:
            size = pkg.stat().st_size
            size_str = f"{size:,}" if size < 1024 else f"{size / 1024:.1f}KB"

            table.add_row(
                pkg.stem,
                size_str,
                str(pkg.relative_to(packages_dir)) if show_all else "",
            )

        console.print(table)
        console.print(f"\n[dim]Total: {len(packages)} package(s)[/dim]")
