# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Pull command - Download packages from Lexicon Hub.

Uses the core LexiconClient to download packages from the registry.

Example:
    chaoscypher pull medical-ontology
    chaoscypher pull john/research-corpus --version 1.2.0
"""

from __future__ import annotations

from pathlib import Path

import click


class PackageExistsError(FileExistsError):
    """The target archive already exists and ``force`` was not given."""


def download_package(
    package: str,
    version: str | None,
    output_dir: Path,
    *,
    force: bool = False,
) -> tuple[Path, str]:
    """Download a hub package to ``output_dir`` and return ``(path, version)``.

    Shared by ``pull`` and ``mount``. The file is named
    ``<owner>-<name>-<version>.ccx`` (``<owner>-<name>.ccx`` when no
    version was requested), so a version-pinned download is a stable cache
    key. Raises ``PackageExistsError`` when the target exists and ``force``
    is False, ``LexiconClientError`` on hub errors and
    ``ExternalServiceError`` when the hub is unreachable.
    """
    import asyncio

    from chaoscypher_cli.commands.lexicon.login import get_auth_config, get_lexicon_url
    from chaoscypher_core.services.lexicon import LexiconClient

    auth = get_auth_config()
    lexicon_url = get_lexicon_url()

    output_dir.mkdir(parents=True, exist_ok=True)
    safe_name = package.replace("/", "-")
    filename = f"{safe_name}-{version}.ccx" if version else f"{safe_name}.ccx"
    archive_path = output_dir / filename
    if archive_path.exists() and not force:
        raise PackageExistsError(str(archive_path))

    if "/" in package:
        owner_username, repo_name = package.split("/", 1)
    else:
        owner_username, repo_name = "", package

    async def do_download() -> tuple[bytes, str]:
        """Fetch the package archive and its resolved version from Lexicon."""
        async with LexiconClient(base_url=lexicon_url, auth=auth) as client:
            info = await client.get_package_info(owner_username, repo_name, version)
            # Download with the RESOLVED owner: for a bare package name the
            # local owner_username is "", and get_package_info's name-only
            # fallback is what recovered the real owner — reusing "" here
            # built a malformed …/packages//<name>/… download URL.
            resolved_owner = info.owner_username or owner_username
            archive_bytes = await client.download(resolved_owner, repo_name, version or "latest")
            return archive_bytes, info.version

    archive_bytes, actual_version = asyncio.run(do_download())
    archive_path.write_bytes(archive_bytes)
    return archive_path, actual_version


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
    import sys

    from rich.progress import BarColumn, DownloadColumn, Progress, TextColumn

    from chaoscypher_cli.commands.lexicon.login import get_auth_config, get_lexicon_url
    from chaoscypher_cli.utils.console import get_console, print_error, print_success
    from chaoscypher_core.exceptions import ExternalServiceError
    from chaoscypher_core.services.lexicon import LexiconClientError
    from chaoscypher_core.services.package import extract_archive, format_size

    console = get_console()
    lexicon_url = get_lexicon_url()

    if not get_auth_config():
        console.print(
            "[yellow]Warning:[/yellow] Not logged in. Some packages may require authentication."
        )
        console.print("Run 'chaoscypher lexicon login' to authenticate.\n")

    console.print(f"[cyan]Pulling package:[/cyan] {package}")
    console.print(f"  [dim]Version:[/dim] {version or 'latest'}")
    console.print(f"  [dim]Output:[/dim] {output}")

    try:
        with Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            DownloadColumn(),
            transient=True,
        ) as progress:
            task = progress.add_task(f"Downloading {package}...", total=None)
            archive_path, actual_version = download_package(
                package, version, Path(output), force=force
            )
            size = archive_path.stat().st_size
            progress.update(task, completed=size, total=size)

        print_success(f"Downloaded {package} v{actual_version}")
        console.print(f"  [dim]File:[/dim] {archive_path}")
        console.print(f"  [dim]Size:[/dim] {format_size(size)}")

        # Extract if requested
        if extract:
            safe_name = package.replace("/", "-")
            extract_dir = Path(output) / safe_name
            console.print(f"\n[dim]Extracting to {extract_dir}...[/dim]")

            extract_archive(archive_path, extract_dir)
            print_success(f"Extracted to {extract_dir}")

            # Show next steps
            console.print("\n[dim]Next steps:[/dim]")
            console.print(f"  chaoscypher graph package load {extract_dir}")
        else:
            console.print("\n[dim]Next steps:[/dim]")
            console.print(f"  chaoscypher graph package load {archive_path}")
            console.print(f"  chaoscypher mount {archive_path}")

    except PackageExistsError as e:
        print_error(f"File already exists: {e}")
        console.print("[dim]Use --force to overwrite[/dim]")
        sys.exit(1)
    except LexiconClientError as e:
        print_error(f"Download failed: {e}")
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
