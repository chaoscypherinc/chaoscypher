# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
# ruff: noqa: D301  -- Click \x08 paragraph escape, intentional non-raw docstring.

"""Mount command - pull a knowledge package and serve it over MCP.

``chaoscypher mount <package>`` is the one-command form of "build it once,
mount it anywhere": resolve a ``.ccx`` package (a local file or a Lexicon
Hub reference), import it into a database of its own, make the imported
knowledge searchable, and then hand that database to the stdio MCP server
so any MCP client — Claude Desktop, Claude Code, Cursor, a local agent —
can query it with the package's citations intact.

The command is idempotent: a marker file (``mount.json``) in the target
database records the SHA-256 of the mounted package, so re-running the
same command (as an MCP host does on every launch) skips the import and
goes straight to serving.

Example:
    chaoscypher mount ./research.ccx
    chaoscypher mount acme/eu-ai-act
    chaoscypher mount acme/eu-ai-act --version 1.2.0 --database aiact
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click


if TYPE_CHECKING:
    from rich.console import Console

    from chaoscypher_core.services.package.importer.models import ImportStats


MOUNT_MARKER_FILENAME = "mount.json"

_DB_NAME_RE = re.compile(r"[^A-Za-z0-9_-]+")


def _stderr_console() -> Console:
    """Return a Rich console bound to stderr.

    Every line this command prints goes to stderr on purpose: after the
    import step the process becomes an MCP stdio server, and stdout is the
    JSON-RPC channel. A stray progress line on stdout would corrupt the
    handshake, so the command never touches stdout at all.
    """
    from rich.console import Console

    return Console(stderr=True)


def derive_database_name(package: str) -> str:
    """Derive a database name from a package reference.

    ``owner/name`` becomes ``owner-name``; a local path uses its stem.
    Anything outside ``[A-Za-z0-9_-]`` collapses to ``-`` so the result is
    always a valid database name.

    Args:
        package: Local ``.ccx`` path or a ``owner/name`` hub reference.

    Returns:
        A database name that ``chaoscypher db create`` would accept.
    """
    stem = Path(package).stem if package.lower().endswith(".ccx") else package
    name = _DB_NAME_RE.sub("-", stem).strip("-")
    if not name or name == "default":
        # ``get_database_name`` treats a literal "default" override as unset
        # (it is Click's flag default on ~30 commands), so a mount can never
        # target that name implicitly — pass --database default to insist.
        return "mounted-default" if name else "mounted"
    return name


def is_local_package(package: str) -> bool:
    """Return True when ``package`` names an existing local ``.ccx`` file."""
    path = Path(package)
    return path.suffix.lower() == ".ccx" and path.is_file()


def read_mount_marker(database_dir: Path) -> dict[str, Any] | None:
    """Read the mount marker for a database, or ``None`` when absent/unreadable."""
    marker = database_dir / MOUNT_MARKER_FILENAME
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except OSError, ValueError:
        return None
    return data if isinstance(data, dict) else None


def write_mount_marker(
    database_dir: Path,
    *,
    package: str,
    sha256: str,
    version: str | None,
    archive_path: Path,
    stats: ImportStats,
) -> None:
    """Persist what was mounted so the next launch can skip the import."""
    marker = database_dir / MOUNT_MARKER_FILENAME
    payload = {
        "package": package,
        "sha256": sha256,
        "version": version,
        "archive_path": str(archive_path),
        "mounted_at": datetime.now(UTC).isoformat(),
        "nodes_imported": stats.nodes_imported,
        "edges_imported": stats.edges_imported,
        "sources_imported": stats.sources_imported,
        "chunks_imported": stats.chunks_imported,
        "citations_imported": stats.citations_imported,
    }
    marker.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _pull_from_hub(
    package: str,
    version: str | None,
    console: Console,
) -> tuple[Path, str | None]:
    """Download a hub package into the local packages directory.

    Returns the archive path and the resolved version. A version-pinned
    download already on disk is reused without a network call.
    """
    from chaoscypher_cli.commands.lexicon.pull import PackageExistsError, download_package
    from chaoscypher_cli.utils.paths import get_packages_dir
    from chaoscypher_core.exceptions import ExternalServiceError
    from chaoscypher_core.services.lexicon import LexiconClientError

    packages_dir = get_packages_dir()
    safe_name = package.replace("/", "-")

    # Cache hit: an explicit version already on disk needs no network call.
    if version:
        cached = packages_dir / f"{safe_name}-{version}.ccx"
        if cached.is_file():
            console.print(f"[dim]Using cached package:[/dim] {cached}")
            return cached, version

    console.print(f"[cyan]Pulling package:[/cyan] {package} ({version or 'latest'})")
    try:
        archive_path, actual_version = download_package(package, version, packages_dir, force=True)
    except PackageExistsError as e:  # pragma: no cover - force=True never raises it
        msg = f"Package already exists: {e}"
        raise click.ClickException(msg) from e
    except LexiconClientError as e:
        msg = f"Download failed: {e}"
        raise click.ClickException(msg) from e
    except ExternalServiceError as e:
        msg = f"Cannot reach Lexicon Hub: {e}"
        raise click.ClickException(msg) from e

    console.print(f"[dim]Saved:[/dim] {archive_path}")
    return archive_path, actual_version


def _import_package(ctx: Any, archive_path: Path, console: Console) -> ImportStats:
    """Import the package, sources and citations included, into ``ctx``'s database."""
    import asyncio

    from chaoscypher_core.services.package.importer import CcxImporter, ImportOptions

    # Unlike ``graph package load``, mount passes the storage adapter as the
    # sources repository so ``sources.jsonl`` (chunks + citations) lands too.
    # That is what keeps citations intact on the far side of the mount.
    importer = CcxImporter(
        graph_repository=ctx.graph_repository,
        sources_repository=ctx.storage_adapter,
        workflow_db=None,
    )
    options = ImportOptions(
        import_templates=True,
        import_knowledge=True,
        import_workflows=True,
        import_sources=True,
        database_name=ctx.database_name,
    )
    console.print(f"[cyan]Importing:[/cyan] {archive_path.name}")
    return asyncio.run(importer.import_from_path(archive_path, options))


def _index_for_search(ctx: Any, stats: ImportStats, console: Console) -> bool:
    """Make the imported knowledge searchable (embeddings + vector index).

    Runs the same two indexing passes the worker enqueues after a hub import
    (see ``index_imported_package``). Best-effort — without an embedding
    model the mount is still keyword-searchable through FTS.
    """
    import asyncio

    from chaoscypher_core.operations.importing.imported_source_handler import (
        index_imported_package,
    )

    # The source pass needs the real IndexingService (``embed_chunks``), which
    # the Engine already wires; the node pass only reads ``embedding_service``
    # off it, so one object serves both handlers.
    engine = ctx._engine
    if engine is None:
        msg = "Engine not initialized — context.connect() must be called first"
        raise RuntimeError(msg)

    embedding_model = getattr(getattr(ctx.settings, "embedding", None), "model", None)
    console.print(
        f"[dim]Indexing for search with the local embedding model"
        f"{f' ({embedding_model})' if embedding_model else ''}; "
        "the first use downloads it.[/dim]"
    )
    try:
        counts = asyncio.run(
            index_imported_package(
                imported_source_ids=stats.imported_source_ids,
                imported_node_ids=stats.imported_node_ids,
                storage_adapter=ctx.storage_adapter,
                graph_repository=ctx.graph_repository,
                indexing_service=engine.indexing_service,
                search_repository=ctx.search_repository,
                database_name=ctx.database_name,
            )
        )
        console.print(
            f"[green]✓ Indexed {counts['sources_indexed']} sources and "
            f"{len(stats.imported_node_ids)} nodes for search[/green]"
        )
    except Exception as e:
        # Best-effort: the knowledge is imported and FTS keyword search works
        # without vectors, so a missing embedding model must not fail the mount.
        # The marker is NOT written in this case, so the next launch retries
        # (the import is an upsert by IRI and repeats cheaply).
        console.print(f"[yellow]⚠ Search indexing skipped: {e} — will retry next launch[/yellow]")
        return False
    return True


@click.command()
@click.argument("package")
@click.option(
    "--database",
    "-d",
    default=None,
    help="Database to mount into (default: derived from the package name)",
)
@click.option("--version", "-v", default=None, help="Hub package version (default: latest)")
@click.option(
    "--mode",
    "-m",
    type=click.Choice(["read", "write"]),
    default=None,
    help="MCP tool access mode (default: from settings, usually 'read')",
)
@click.option(
    "--no-serve",
    is_flag=True,
    help="Import the package and exit without starting the MCP server",
)
@click.option(
    "--refresh",
    is_flag=True,
    help="Re-import even if this package is already mounted in the database",
)
def mount(
    package: str,
    database: str | None,
    version: str | None,
    mode: str | None,
    no_serve: bool,
    refresh: bool,
) -> None:
    """Pull a knowledge package and serve it over MCP.

    PACKAGE is a local .ccx file or a Lexicon Hub reference (owner/name).

    The package is imported once into its own database — templates,
    entities, relationships, sources, chunks and citations — indexed for
    search, and then served to the calling MCP host over stdio. Re-running
    the command with the same package skips the import, so it is safe to
    use as an MCP server command line.

    \x08
    Examples:
        chaoscypher mount ./research.ccx
        chaoscypher mount acme/eu-ai-act
        chaoscypher mount acme/eu-ai-act --version 1.2.0 -d aiact
        chaoscypher mount ./research.ccx --no-serve

    \x08
    Claude Desktop:
        {"mcpServers": {"research": {"command": "chaoscypher",
                                     "args": ["mount", "acme/research"]}}}
    """
    # Route ALL logging to stderr before anything else runs — the import
    # step logs through structlog, and stdout belongs to the MCP protocol.
    from chaoscypher_core.utils.logging import configure_logging

    configure_logging(log_level="WARNING", stream=sys.stderr)

    console = _stderr_console()

    if package.lower().endswith(".ccx") and not is_local_package(package):
        msg = f"Package file not found: {package}"
        raise click.ClickException(msg)

    from chaoscypher_cli.context import get_context

    db_name = database or derive_database_name(package)
    ctx = get_context(database_name=db_name, explicit_database=True)
    marker = read_mount_marker(ctx.database_dir)

    resolved_version: str | None = None
    if is_local_package(package):
        archive_path = Path(package).resolve()
    elif (
        marker
        and not refresh
        and marker.get("package") == package
        and (version is None or marker.get("version") == version)
        and marker.get("archive_path")
        and Path(str(marker["archive_path"])).is_file()
    ):
        # A hub package mounted earlier: serve from the cached archive, no
        # network call — an MCP host relaunching daily must work offline.
        archive_path = Path(str(marker["archive_path"]))
        resolved_version = marker.get("version")
    else:
        archive_path, resolved_version = _pull_from_hub(package, version, console)

    sha256 = hashlib.sha256(archive_path.read_bytes()).hexdigest()

    if marker and marker.get("sha256") == sha256 and not refresh:
        console.print(
            f"[dim]Already mounted:[/dim] {package} → database [cyan]{db_name}[/cyan] "
            f"(mounted {marker.get('mounted_at', 'earlier')})"
        )
    else:
        stats = _import_package(ctx, archive_path, console)
        if not stats.is_success:
            for error in stats.errors:
                console.print(f"  [red]•[/red] {error}")
            raise click.ClickException("Import failed")
        for warning in stats.warnings:
            console.print(f"  [yellow]•[/yellow] {warning}")
        console.print(
            f"[green]✓ Mounted[/green] {package} → database [cyan]{db_name}[/cyan]: "
            f"{stats.nodes_imported} entities, {stats.edges_imported} relationships, "
            f"{stats.sources_imported} sources, {stats.citations_imported} citations"
        )
        if _index_for_search(ctx, stats, console):
            write_mount_marker(
                ctx.database_dir,
                package=package,
                sha256=sha256,
                version=resolved_version,
                archive_path=archive_path,
                stats=stats,
            )

    if no_serve:
        console.print(f"\n[dim]Serve it with:[/dim] chaoscypher mcp --database {db_name}")
        return

    # Release the import-time engine so the MCP server opens the database
    # fresh, exactly as `chaoscypher mcp --database <name>` would. The
    # singleton must be cleared, not just disconnected: ``get_context``
    # returns a cached instance by name without checking its connection.
    from chaoscypher_cli.context import reset_context

    reset_context()

    from chaoscypher_cli.mcp.command import serve_stdio

    console.print(f"[dim]Serving[/dim] [cyan]{db_name}[/cyan] [dim]over MCP stdio…[/dim]")
    serve_stdio(database=db_name, mode=mode, server_extraction=False, explicit_database=True)
