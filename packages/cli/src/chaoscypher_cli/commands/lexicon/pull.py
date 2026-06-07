# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Pull command - Download packages from Lexicon Hub.

Uses the core LexiconClient to download packages from the registry.

Example:
    chaoscypher pull medical-ontology
    chaoscypher pull john/research-corpus --version 1.2.0
"""

from __future__ import annotations

import click


@click.command()
@click.argument("package")
@click.option("--version", "-v", help="Specific version to pull (default: latest)")
@click.option("--force", "-f", is_flag=True, help="Overwrite existing files")
@click.option("--output", "-o", default=".", help="Output directory")
@click.option("--extract", "-x", is_flag=True, help="Extract package after download")
def pull(
    package: str,
    version: str | None,
    force: bool,
    output: str,
    extract: bool,
) -> None:
    """Download a package from the Lexicon Hub.

    PACKAGE should be in format: username/packagename
    or just packagename for official packages.

    Example:
        chaoscypher pull medical-ontology
        chaoscypher pull john/research-corpus
        chaoscypher pull john/research-corpus --version 1.2.0
        chaoscypher pull john/research-corpus --output ./packages/
        chaoscypher pull medical-ontology --extract
    """
    # Defer heavy imports to runtime (not completion time)
    import asyncio
    import sys
    from pathlib import Path

    from rich.progress import BarColumn, DownloadColumn, Progress, TextColumn

    from chaoscypher_cli.commands.lexicon.login import get_auth_config, get_lexicon_url
    from chaoscypher_cli.utils.console import get_console, print_error, print_success
    from chaoscypher_core.services.lexicon import LexiconClient, LexiconClientError
    from chaoscypher_core.services.package import extract_archive, format_size

    console = get_console()

    # Get auth and hub URL
    auth = get_auth_config()
    lexicon_url = get_lexicon_url()

    if not auth:
        console.print(
            "[yellow]Warning:[/yellow] Not logged in. Some packages may require authentication."
        )
        console.print("Run 'chaoscypher lexicon login' to authenticate.\n")

    console.print(f"[cyan]Pulling package:[/cyan] {package}")
    console.print(f"  [dim]Version:[/dim] {version or 'latest'}")
    console.print(f"  [dim]Output:[/dim] {output}")

    # Prepare output directory
    output_path = Path(output)
    output_path.mkdir(parents=True, exist_ok=True)

    # Determine output filename
    safe_name = package.replace("/", "-")
    filename = f"{safe_name}-{version}.ccx" if version else f"{safe_name}.ccx"

    archive_path = output_path / filename

    # Check if file exists
    if archive_path.exists() and not force:
        print_error(f"File already exists: {archive_path}")
        console.print("[dim]Use --force to overwrite[/dim]")
        sys.exit(1)

    # Parse owner/name from package string
    if "/" in package:
        owner_username, repo_name = package.split("/", 1)
    else:
        owner_username = ""
        repo_name = package

    async def do_download() -> tuple[bytes, str]:
        """Fetch the package archive and its resolved version from Lexicon."""
        async with LexiconClient(base_url=lexicon_url, auth=auth) as client:
            # Get package info first to get the actual version
            info = await client.get_package_info(owner_username, repo_name, version)
            actual_version = info.version

            # Download the archive
            archive_bytes = await client.download(owner_username, repo_name, version or "latest")
            return archive_bytes, actual_version

    try:
        with Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            DownloadColumn(),
            transient=True,
        ) as progress:
            task = progress.add_task(f"Downloading {package}...", total=None)

            archive_bytes, actual_version = asyncio.run(do_download())

            progress.update(task, completed=len(archive_bytes), total=len(archive_bytes))

        # Write the archive file
        archive_path.write_bytes(archive_bytes)

        print_success(f"Downloaded {package} v{actual_version}")
        console.print(f"  [dim]File:[/dim] {archive_path}")
        console.print(f"  [dim]Size:[/dim] {format_size(len(archive_bytes))}")

        # Extract if requested
        if extract:
            extract_dir = output_path / safe_name
            console.print(f"\n[dim]Extracting to {extract_dir}...[/dim]")

            extract_archive(archive_path, extract_dir)
            print_success(f"Extracted to {extract_dir}")

            # Show next steps
            console.print("\n[dim]Next steps:[/dim]")
            console.print(f"  chaoscypher package load {extract_dir}")
        else:
            console.print("\n[dim]Next steps:[/dim]")
            console.print(f"  chaoscypher package load {archive_path}")

    except LexiconClientError as e:
        print_error(f"Download failed: {e}")
        sys.exit(1)
