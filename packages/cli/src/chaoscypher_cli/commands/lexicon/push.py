# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Push command - Upload packages to Lexicon Hub.

Uses the core LexiconClient to upload packages to the registry.

Example:
    chaoscypher push ./my-package.ccx
    chaoscypher push . --message "Initial release"
"""

from __future__ import annotations

from typing import Any

import click


@click.command()
@click.argument("path", type=click.Path(exists=True))
@click.option("--message", "-m", help="Release message")
@click.option("--public/--private", default=True, help="Package visibility (default: public)")
@click.option("--force", "-f", is_flag=True, help="Skip confirmation prompt")
def push(path: str, message: str | None, public: bool, force: bool) -> None:
    """Upload a package to the Lexicon Hub.

    PATH should be a local .ccx file or directory with manifest.json

    Example:
        chaoscypher push ./my-package.ccx
        chaoscypher push ./my-package
        chaoscypher push . --message "Major update"
        chaoscypher push ./private-data --private
    """
    # Defer heavy imports to runtime (not completion time)
    import asyncio
    import sys
    from pathlib import Path

    from rich.progress import BarColumn, Progress, TextColumn, TransferSpeedColumn

    from chaoscypher_cli.commands.lexicon.login import get_auth_config, get_lexicon_url
    from chaoscypher_cli.utils.console import get_console, print_error, print_success
    from chaoscypher_core.exceptions import ExternalServiceError
    from chaoscypher_core.services.lexicon import LexiconClient, LexiconClientError
    from chaoscypher_core.services.package import (
        PackageManifest,
        create_archive,
        format_size,
        validate_package_directory,
    )

    console = get_console()

    # Check authentication
    auth = get_auth_config()
    lexicon_url = get_lexicon_url()

    if not auth:
        print_error("Not logged in")
        console.print("Run 'chaoscypher lexicon login' to authenticate first.")
        sys.exit(1)

    path_obj = Path(path).resolve()

    # Determine if we have a .ccx file or a directory
    if path_obj.is_file():
        if path_obj.suffix.lower() != ".ccx":
            print_error(f"File must have .ccx extension: {path_obj}")
            sys.exit(1)
        archive_path = path_obj
        # Try to extract manifest info from filename
        package_name = path_obj.stem
    elif path_obj.is_dir():
        # Check for manifest.json
        manifest_path = path_obj / "manifest.json"
        if not manifest_path.exists():
            print_error(f"No manifest.json found in {path_obj}")
            console.print("Create one with 'chaoscypher graph package export'.")
            sys.exit(1)

        # Validate the package
        result = validate_package_directory(path_obj)
        if not result.is_valid:
            print_error("Package validation failed:")
            for error in result.errors:
                console.print(f"  [red]✗[/red] {error}")
            sys.exit(1)

        # Load manifest to get package info
        manifest = PackageManifest.from_json(manifest_path)
        package_name = manifest.name

        # Build the archive
        console.print("[dim]Building package archive...[/dim]")
        safe_name = package_name.replace("/", "-")
        archive_path = path_obj.parent / f"{safe_name}-{manifest.package_version}.ccx"
        create_archive(path_obj, archive_path)
        console.print(f"[green]✓[/green] Built {archive_path.name}")
    else:
        print_error(f"Path not found: {path_obj}")
        sys.exit(1)

    # Read archive for upload
    archive_bytes = archive_path.read_bytes()
    archive_size = len(archive_bytes)

    console.print(f"\n[cyan]Pushing package:[/cyan] {package_name}")
    console.print(f"  [dim]File:[/dim] {archive_path.name}")
    console.print(f"  [dim]Size:[/dim] {format_size(archive_size)}")
    console.print(f"  [dim]Visibility:[/dim] {'Public' if public else 'Private'}")
    if message:
        console.print(f"  [dim]Message:[/dim] {message}")

    # Confirm upload unless --force
    if not force:
        console.print()
        if not click.confirm("Proceed with upload?", default=True):
            console.print("[dim]Upload cancelled.[/dim]")
            return

    async def do_upload() -> Any:
        """Upload the package archive to the Lexicon hub."""
        async with LexiconClient(base_url=lexicon_url, auth=auth) as client:
            return await client.upload(
                archive_data=archive_bytes,
                public=public,
                message=message,
            )

    try:
        with Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TransferSpeedColumn(),
            transient=True,
        ) as progress:
            task = progress.add_task(f"Uploading {package_name}...", total=archive_size)

            result = asyncio.run(do_upload())

            progress.update(task, completed=archive_size)

        print_success(f"Published {result.name} v{result.version}")
        console.print(f"  [dim]URL:[/dim] {lexicon_url}/packages/{result.name}")

        # Show next steps
        console.print("\n[dim]Share with:[/dim]")
        console.print(f"  chaoscypher pull {result.name}")

    except LexiconClientError as e:
        print_error(f"Upload failed: {e}")
        sys.exit(1)
    except ExternalServiceError as e:
        # LexiconClient wraps httpx.ConnectError into ExternalServiceError when
        # the hub isn't reachable — turn it into a one-line operator hint
        # instead of a raw traceback.
        print_error(f"Cannot reach Lexicon Hub at {lexicon_url}: {e}")
        console.print(
            "  [dim]Set LEXICON_URL or run a local hub. "
            "Check connectivity with [cyan]curl -I "
            f"{lexicon_url}[/cyan].[/dim]",
        )
        sys.exit(1)
