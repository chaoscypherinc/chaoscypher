# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Remove command - Remove local packages."""

from __future__ import annotations

import shutil
import sys
from typing import TYPE_CHECKING

import click


if TYPE_CHECKING:
    from pathlib import Path
from rich.console import Console
from rich.prompt import Confirm

from chaoscypher_cli.utils.paths import get_packages_dir


console = Console()


@click.command()
@click.argument("package")
@click.option("--version", "-v", help="Specific version to remove")
@click.option("--all", "remove_all", is_flag=True, help="Remove all versions")
@click.option("--force", "-f", is_flag=True, help="Skip confirmation")
def remove(package: str, version: str | None, remove_all: bool, force: bool) -> None:
    """Remove a locally installed package.

    PACKAGE should be in format: user/packagename or just packagename

    Example:
        chaoscypher remove john/medical-ontology
        chaoscypher remove john/medical-ontology --version 1.2.0
        chaoscypher remove my-package --all
        chaoscypher remove my-package --force
    """
    try:
        packages_dir = get_packages_dir()

        # Parse package name
        package_path: Path
        if "/" in package:
            parts = package.split("/", 1)
            package_path = packages_dir / parts[0] / parts[1]
        else:
            # Search for package in all user directories
            found_path: Path | None = None
            for user_dir in packages_dir.iterdir():
                if user_dir.is_dir():
                    candidate = user_dir / package
                    if candidate.exists():
                        found_path = candidate
                        break

            # Check if it's a direct package or use found_path
            package_path = packages_dir / package if found_path is None else found_path

        if not package_path.exists():
            console.print(f"[red]Package not found:[/red] {package}")
            console.print("\nUse 'chaoscypher list' to see installed packages.")
            sys.exit(1)

        # Get versions
        if package_path.is_file() and package_path.suffix == ".ccx":
            # Single .ccx file
            versions_to_remove = [package_path]
        elif package_path.is_dir():
            if version:
                # Specific version
                version_path = package_path / version
                if not version_path.exists():
                    console.print(f"[red]Version not found:[/red] {version}")
                    versions = [d.name for d in package_path.iterdir() if d.is_dir()]
                    if versions:
                        console.print(f"Available versions: {', '.join(versions)}")
                    sys.exit(1)
                versions_to_remove = [version_path]
            elif remove_all:
                # All versions
                versions_to_remove = [d for d in package_path.iterdir() if d.is_dir()]
                if not versions_to_remove:
                    versions_to_remove = [package_path]
            else:
                # Default: remove the whole package directory
                versions_to_remove = [package_path]
        else:
            console.print(f"[red]Invalid package path:[/red] {package_path}")
            sys.exit(1)

        # Show what will be removed
        console.print(f"[cyan]Package to remove:[/cyan] {package}")
        for v in versions_to_remove:
            console.print(f"  [dim]→[/dim] {v}")

        if not force:
            if not Confirm.ask("\nAre you sure you want to remove?", default=False):
                console.print("[yellow]Cancelled.[/yellow]")
                return

        # Remove packages
        for path in versions_to_remove:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()

        # Clean up empty parent directories
        parent = package_path.parent
        if parent.exists() and parent != packages_dir and not any(parent.iterdir()):
            parent.rmdir()

        console.print("[green]✓ Package removed successfully[/green]")

    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)
