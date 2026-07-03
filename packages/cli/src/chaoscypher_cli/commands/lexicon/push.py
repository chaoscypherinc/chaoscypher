# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Push command - Upload packages to Lexicon Hub.

Uses the core LexiconClient to upload packages to the registry. The package
must be a pre-built CCX 3.0 ``.ccx`` file (build one with
``chaoscypher graph package export``); the archive is validated via
``ccx-format`` before upload.

Example:
    chaoscypher push ./my-package.ccx
    chaoscypher push ./my-package.ccx --message "Initial release"
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

    PATH must be a pre-built .ccx file. Build one first with
    ``chaoscypher graph package export``.

    Example:
        chaoscypher push ./my-package.ccx
        chaoscypher push ./my-package.ccx --message "Major update"
        chaoscypher push ./private-data.ccx --private
    """
    # Defer heavy imports to runtime (not completion time)
    import asyncio
    import sys
    from pathlib import Path

    import ccx
    from rich.progress import BarColumn, Progress, TextColumn, TransferSpeedColumn

    from chaoscypher_cli.commands.lexicon.login import get_auth_config, get_lexicon_url
    from chaoscypher_cli.utils.console import get_console, print_error, print_success
    from chaoscypher_core.exceptions import ExternalServiceError
    from chaoscypher_core.services.lexicon import LexiconClient, LexiconClientError
    from chaoscypher_core.services.package import format_size

    console = get_console()

    # Check authentication
    auth = get_auth_config()
    lexicon_url = get_lexicon_url()

    if not auth:
        print_error("Not logged in")
        console.print("Run 'chaoscypher lexicon login' to authenticate first.")
        sys.exit(1)

    path_obj = Path(path).resolve()

    # A directory is no longer a supported push input: build the .ccx first.
    if path_obj.is_dir():
        print_error(f"Cannot push a directory: {path_obj}")
        console.print(
            "Build a .ccx package first with "
            "[cyan]chaoscypher graph package export[/cyan], then push that file."
        )
        sys.exit(1)

    if not path_obj.is_file():
        print_error(f"Path not found: {path_obj}")
        sys.exit(1)

    if path_obj.suffix.lower() != ".ccx":
        print_error(f"File must have .ccx extension: {path_obj}")
        sys.exit(1)

    # Validate the package and read display metadata via ccx-format.
    try:
        pkg = ccx.open_package(path_obj)
        report = pkg.validate()
    except Exception as e:
        print_error(f"Invalid .ccx package: {e}")
        sys.exit(1)

    if not report.ok:
        print_error("Package validation failed:")
        for error in report.errors:
            console.print(f"  [red]✗[/red] {error}")
        sys.exit(1)

    package_name = pkg.manifest.name
    package_version = pkg.manifest.package_version

    # The hub derives the target repo from the part of ``manifest.name`` after
    # ``owner/`` and REQUIRES the ``owner/`` segment to match the publisher's
    # username (a mismatch is rejected with HTTP 403). We don't hard-block here
    # — the hub is the source of truth — but a one-line heads-up makes a 403
    # understandable rather than mysterious.
    if "/" in package_name:
        name_owner = package_name.split("/", 1)[0]
        if auth.username and name_owner != auth.username:
            console.print(
                f"  [yellow]Note:[/yellow] package owner '[bold]{name_owner}[/bold]' "
                f"does not match your username '[bold]{auth.username}[/bold]'. "
                "The hub publishes under your username and will reject a mismatched "
                "owner segment (HTTP 403)."
            )

    archive_path = path_obj

    # Read archive for upload
    archive_bytes = archive_path.read_bytes()
    archive_size = len(archive_bytes)

    console.print(f"\n[cyan]Pushing package:[/cyan] {package_name}")
    console.print(f"  [dim]Version:[/dim] {package_version}")
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

        # CCX 3.0: the hub processes uploads asynchronously and returns a
        # job envelope (job_id/status), not finished package metadata.
        print_success(f"Upload queued for {package_name} v{package_version}")
        if result.status:
            console.print(f"  [dim]Status:[/dim] {result.status}")
        if result.job_id:
            console.print(f"  [dim]Job ID:[/dim] {result.job_id}")
        if result.message:
            console.print(f"  [dim]{result.message}[/dim]")

        # Show next steps
        console.print("\n[dim]Once processing completes, share with:[/dim]")
        console.print(f"  chaoscypher pull {package_name}")

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
