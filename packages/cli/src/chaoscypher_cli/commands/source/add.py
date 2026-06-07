# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Add command - Unified source_processing pipeline with progress UI.

Provides a single command for the complete source_processing workflow:
- Upload: Stage files (or fetch URLs) for processing
- Index: Chunk documents and generate embeddings
- Extract: Extract entities using LLM (optional)
- Commit: Write entities to knowledge graph

Supports file paths, directories, URLs, file IDs (resume), and interactive
resume picker. Multiple inputs can be processed in a single command.

Example:
    cc source add document.pdf                             # Single file
    cc source add a.pdf b.pdf c.pdf                        # Multiple files
    cc source add ./documents/                             # All files in directory
    cc source add https://example.com/article              # URL import
    cc source add document.pdf --index-only                # Stop after indexing
    cc source add if_abc123                                # Resume by file ID
    cc source add --resume                                 # Interactive resume picker
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, get_args

import click

from chaoscypher_cli.sources.domains import ADD_DOMAIN_CHOICES
from chaoscypher_core.exceptions import ChaosCypherException
from chaoscypher_core.models import SourceStatus
from chaoscypher_core.ports.types import FilteringMode


if TYPE_CHECKING:
    from pathlib import Path

    from chaoscypher_cli.context import CLIContext
    from chaoscypher_cli.sources.pipeline import PipelineResult


def _is_file_id(value: str) -> bool:
    """Check if value looks like a file ID."""
    return value.startswith("if_") and len(value) == 15


def _is_url(value: str) -> bool:
    """Check if value looks like a URL."""
    return value.startswith(("http://", "https://"))


def _get_pending_files(ctx: CLIContext) -> list[dict]:
    """Get files that haven't been committed yet."""
    all_files = ctx.storage_adapter.list_files(ctx.database_name)
    return [f for f in all_files if f.get("status") not in (SourceStatus.COMMITTED, "failed")]


def _show_resume_picker(ctx: CLIContext, console: Any) -> str | None:
    """Show interactive picker for resumable files.

    Returns:
        Selected file ID or None if cancelled
    """
    from rich.prompt import Prompt
    from rich.table import Table

    from chaoscypher_cli.utils.display import get_status_color

    pending = _get_pending_files(ctx)

    if not pending:
        console.print("[dim]No pending files to resume.[/dim]")
        console.print("\nAdd files with: cc source add <file>")
        return None

    console.print("\n[bold]Select file to resume:[/bold]\n")

    # Build selection table
    table = Table(show_header=True, box=None, padding=(0, 2))
    table.add_column("#", style="dim", width=3)
    table.add_column("Filename", style="cyan")
    table.add_column("ID", style="dim")
    table.add_column("Status", style="green")
    table.add_column("Chunks", justify="right")

    for i, f in enumerate(pending, 1):
        status = f.get("status", "unknown")
        scolor = get_status_color(status)
        status_display = f"[{scolor}]{status}[/{scolor}]"

        # Get chunk count if indexed
        chunks = ctx.storage_adapter.list_chunks(ctx.database_name, source_id=f.get("id"))
        chunk_count = str(len(chunks)) if chunks else "--"

        table.add_row(
            str(i),
            f.get("filename", ""),
            f.get("id", ""),
            status_display,
            chunk_count,
        )

    console.print(table)
    console.print()

    # Get user selection
    try:
        choice = Prompt.ask(
            "Enter number to resume (or 'q' to quit)",
            default="q",
        )

        if choice.lower() == "q":
            return None

        idx = int(choice) - 1
        if 0 <= idx < len(pending):
            return pending[idx].get("id")
        console.print("[red]Invalid selection.[/red]")
        return None

    except (ValueError, KeyboardInterrupt):  # fmt: skip
        return None


def _validate_file(file_path: Path, settings: Any, console: Any) -> None:
    """Validate file exists and is supported.

    Args:
        file_path: Path to the file to validate
        settings: EngineSettings for loader registry initialization
        console: Rich console for output
    """
    import sys

    if not file_path.exists():
        console.print(f"[red]File not found:[/red] {file_path}")
        sys.exit(1)

    from chaoscypher_core.services.sources.loaders.factory import get_loader_registry

    registry = get_loader_registry(settings)
    supported = set(registry.list_supported_extensions())

    if file_path.suffix.lower() not in supported:
        console.print(f"[red]Unsupported file type:[/red] {file_path.suffix}")
        console.print(f"Supported: {', '.join(sorted(supported))}")
        sys.exit(1)


def _expand_inputs(
    inputs: tuple[str, ...],
    settings: Any,
    console: Any,
) -> list[dict[str, Any]]:
    """Expand inputs into a list of processable items.

    Handles file paths, directories (non-recursive expansion to supported
    files), URLs, and file IDs.

    Args:
        inputs: Raw CLI arguments
        settings: EngineSettings for loader registry
        console: Rich console for output

    Returns:
        List of dicts with keys: type ('file', 'url', 'file_id'),
        path (Path | None), url (str | None), file_id (str | None)
    """
    import sys
    from pathlib import Path

    from chaoscypher_core.services.sources.loaders.factory import get_loader_registry

    items: list[dict[str, Any]] = []

    for value in inputs:
        if _is_file_id(value):
            items.append({"type": "file_id", "path": None, "url": None, "file_id": value})
        elif _is_url(value):
            items.append({"type": "url", "path": None, "url": value, "file_id": None})
        else:
            p = Path(value).resolve()
            if p.is_dir():
                # Expand directory to supported files (non-recursive)
                registry = get_loader_registry(settings)
                supported = set(registry.list_supported_extensions())
                dir_files = sorted(
                    f for f in p.iterdir() if f.is_file() and f.suffix.lower() in supported
                )
                if not dir_files:
                    console.print(f"[yellow]No supported files in:[/yellow] {p}")
                    continue
                items.extend(
                    {"type": "file", "path": f, "url": None, "file_id": None} for f in dir_files
                )
            elif p.exists():
                items.append({"type": "file", "path": p, "url": None, "file_id": None})
            else:
                console.print(f"[red]File not found:[/red] {value}")
                sys.exit(1)

    return items


def _result_to_dict(result: PipelineResult) -> dict[str, Any]:
    """Convert a PipelineResult to a JSON-serializable dict.

    Args:
        result: Pipeline result to convert

    Returns:
        Dict suitable for JSON serialization
    """
    return {
        "file_id": result.file_id,
        "filename": result.filename,
        "success": result.success,
        "status": result.status,
        "stages_completed": result.stages_completed,
        "stages_skipped": result.stages_skipped,
        "chunks_count": result.chunks_count,
        "tokens_count": result.tokens_count,
        "entities_count": result.entities_count,
        "relationships_count": result.relationships_count,
        "nodes_created": result.nodes_created,
        "edges_created": result.edges_created,
        "detected_domain": result.detected_domain,
        "detection_confidence": result.detection_confidence,
        "detection_ranking": result.detection_ranking,
        "detection_low_confidence": result.detection_low_confidence,
        "parked_for_confirmation": result.parked_for_confirmation,
        "duration_seconds": result.duration_seconds,
        "error": result.error,
        "llm_metrics": {
            "total_calls": result.llm_total_calls,
            "successful_calls": result.llm_successful_calls,
            "failed_calls": result.llm_failed_calls,
            "retry_calls": result.llm_retry_calls,
            "retry_rate": result.llm_retry_rate,
            "success_rate": result.llm_success_rate,
            "total_input_tokens": result.llm_total_input_tokens,
            "total_output_tokens": result.llm_total_output_tokens,
            "total_tokens": result.llm_total_input_tokens + result.llm_total_output_tokens,
            "wasted_tokens": result.llm_wasted_tokens,
            "estimated_cost_usd": result.llm_estimated_cost_usd,
            "model": result.llm_model,
        }
        if result.llm_total_calls > 0
        else None,
    }


def _show_batch_summary(
    results: list[PipelineResult],
    total_time: float,
    console: Any,
) -> None:
    """Show summary table for batch processing.

    Args:
        results: List of PipelineResult objects
        total_time: Total elapsed time in seconds
        console: Rich console for output
    """
    from rich.panel import Panel
    from rich.table import Table

    succeeded = sum(1 for r in results if r.success)
    failed = len(results) - succeeded

    table = Table(show_header=True, box=None, padding=(0, 2))
    table.add_column("#", style="dim", width=3)
    table.add_column("File", style="cyan")
    table.add_column("Status")
    table.add_column("Duration", justify="right", style="dim")

    for i, r in enumerate(results, 1):
        status = "[green]done[/green]" if r.success else "[red]failed[/red]"
        table.add_row(str(i), r.filename, status, f"{r.duration_seconds:.1f}s")

    table.add_row("", "", "", "")
    summary = f"{succeeded} succeeded"
    if failed:
        summary += f", [red]{failed} failed[/red]"
    table.add_row("", "[bold]Total[/bold]", summary, f"{total_time:.1f}s")

    icon = "[green]✓[/green]" if failed == 0 else "[yellow]![/yellow]"
    title = f"{icon} [bold]Batch Complete ({succeeded}/{len(results)})[/bold]"
    console.print()
    console.print(
        Panel(
            table,
            title=title,
            border_style="green" if failed == 0 else "yellow",
            padding=(1, 2),
        )
    )


@click.command()
@click.argument("files_or_ids", nargs=-1, type=click.Path())
@click.option("--resume", "-r", is_flag=True, help="Interactive picker to select file to resume")
@click.option("--index-only", is_flag=True, help="Stop after indexing (chunking + embeddings)")
@click.option("--extract-only", is_flag=True, help="Stop after extraction (skip commit)")
@click.option("--skip-index", is_flag=True, help="Skip indexing (use existing chunks)")
@click.option("--skip-extract", is_flag=True, help="Skip extraction (no LLM required)")
@click.option("--skip-commit", is_flag=True, help="Skip commit to graph")
@click.option(
    "--skip-embeddings", is_flag=True, help="Skip embedding generation during indexing (faster)"
)
@click.option(
    "--normalize/--no-normalize",
    default=None,
    help=(
        "Force normalization on/off (OCR cleaning, encoding fixes). "
        "Omit to use the file-type default (on for prose, off for "
        "structured formats)."
    ),
)
@click.option("--quick", is_flag=True, help="Fast extraction (3 groups max, ~30 seconds)")
@click.option(
    "--domain",
    type=click.Choice(list(ADD_DOMAIN_CHOICES)),
    default="auto",
    help="Domain for extraction: auto (detect automatically), or specific domain",
)
@click.option(
    "--filtering-mode",
    type=click.Choice(list(get_args(FilteringMode))),
    default=None,
    help="Extraction filtering mode preset (5=maximum to 0=unfiltered). Overrides domain default.",
)
@click.option("--database", "-d", default="default", help="Target database")
@click.option("--quiet", "-q", is_flag=True, help="Minimal output")
@click.option("--json", "output_json", is_flag=True, help="Output results as JSON")
@click.option("--verbose", "-v", is_flag=True, help="Show real-time log output")
@click.option(
    "--vision/--no-vision",
    default=True,
    help="Use vision model on images and scanned PDFs (default: on).",
)
@click.option(
    "--content-filtering/--no-content-filtering",
    default=True,
    help="Apply domain content-exclusion rules during extraction (default: on).",
)
@click.option(
    "--skip-duplicates",
    is_flag=True,
    default=False,
    help="Skip upload if identical content already exists (matched by SHA-256 hash).",
)
@click.option(
    "--no-confirm",
    "-y",
    is_flag=True,
    default=False,
    help="Skip the domain confirmation gate and extract with the auto-detected domain.",
)
def add(
    files_or_ids: tuple[str, ...],
    resume: bool,
    index_only: bool,
    extract_only: bool,
    skip_index: bool,
    skip_extract: bool,
    skip_commit: bool,
    skip_embeddings: bool,
    normalize: bool | None,
    quick: bool,
    domain: str,
    filtering_mode: FilteringMode | None,
    database: str,
    quiet: bool,
    output_json: bool,
    verbose: bool,
    vision: bool,
    content_filtering: bool,
    skip_duplicates: bool,
    no_confirm: bool,
) -> None:
    """Add and process sources through the processing pipeline.

    FILES_OR_IDS can be:

    \b
    - File paths to upload and process
    - Directories (expands to all supported files)
    - URLs (http:// or https://) to fetch and process
    - A file ID (if_*) to resume processing

    By default, runs the full pipeline: upload -> index -> extract -> commit.
    URLs are fetched, converted to markdown, and processed like files.

    \b
    Examples:
        chaoscypher source add document.pdf                        # Single file
        chaoscypher source add a.pdf b.pdf c.pdf                   # Multiple files
        chaoscypher source add ./documents/                         # All in directory
        chaoscypher source add https://example.com/article          # URL import
        chaoscypher source add document.pdf --quick                 # Fast extraction
        chaoscypher source add document.pdf --skip-duplicates       # Skip if already uploaded
        chaoscypher source add if_abc123def456                     # Resume by ID
        chaoscypher source add --resume                            # Interactive
    """  # noqa: D301
    # Defer heavy imports to runtime (not completion time)
    import sys

    from rich.console import Console

    from chaoscypher_cli.context import get_context
    from chaoscypher_cli.sources import CLISourceProcessingService, SourcePipeline

    console = Console()

    try:
        ctx = get_context(database_name=database)

        # Build items list from any input mode
        items: list[dict[str, Any]] = []

        if resume:
            # Interactive resume picker
            file_id = _show_resume_picker(ctx, console)
            if not file_id:
                return
            # Parked sources resume via the confirmation gate, not a full re-detect.
            picked = ctx.storage_adapter.get_file(file_id, ctx.database_name)
            if picked and picked.get("status") == SourceStatus.AWAITING_CONFIRMATION:
                from chaoscypher_cli.commands.source.confirm import confirm_cmd

                ctx_obj = click.get_current_context()
                ctx_obj.invoke(
                    confirm_cmd,
                    source_id=file_id,
                    confirm_all=False,
                    domain=None,
                    depth="quick" if quick else "full",
                    filtering_mode=filtering_mode,
                    yes=False,
                    database=database,
                    quiet=quiet,
                )
                return
            items = [{"type": "file_id", "path": None, "url": None, "file_id": file_id}]

        elif not files_or_ids:
            # No argument provided - show usage
            console.print("[yellow]Usage:[/yellow] chaoscypher source add <FILE|URL|FILE_ID> ...")
            console.print("\nOr use --resume for interactive picker")
            console.print("\nExamples:")
            console.print("  chaoscypher source add document.pdf")
            console.print("  chaoscypher source add a.pdf b.pdf c.pdf")
            console.print("  chaoscypher source add ./documents/")
            console.print("  chaoscypher source add https://example.com/article")
            console.print("  chaoscypher source add if_abc123def456")
            console.print("  chaoscypher source add --resume")
            return

        elif len(files_or_ids) == 1 and _is_file_id(files_or_ids[0]):
            # Single file ID — resume by ID
            file_id = files_or_ids[0]
            file_record = ctx.storage_adapter.get_file(file_id, ctx.database_name)
            if not file_record:
                console.print(f"[red]File ID not found:[/red] {file_id}")
                console.print("\nList files with: cc source list")
                sys.exit(1)
            if not quiet:
                console.print(f"[cyan]Resuming:[/cyan] {file_record.get('filename')} ({file_id})\n")
            items = [{"type": "file_id", "path": None, "url": None, "file_id": file_id}]

        else:
            # Expand inputs (files, directories, URLs)
            items = _expand_inputs(files_or_ids, ctx.settings, console)
            if not items:
                console.print("[yellow]No files to process.[/yellow]")
                return

            # Validate: file IDs cannot be mixed with other files
            file_id_items = [i for i in items if i["type"] == "file_id"]
            if file_id_items and len(items) > 1:
                console.print("[red]File IDs cannot be mixed with other files in batch mode.[/red]")
                sys.exit(1)

            # For single non-ID file, validate it
            if len(items) == 1 and items[0]["type"] == "file":
                _validate_file(items[0]["path"], ctx.settings, console)

        # Check LLM configuration if extraction is enabled
        if not skip_extract and not index_only:
            from chaoscypher_cli.utils.llm_check import check_llm_or_skip

            proceed, should_skip = check_llm_or_skip("entity extraction")
            if not proceed:
                console.print("[dim]Cancelled.[/dim]")
                return
            if should_skip:
                skip_extract = True
                if not quiet:
                    console.print("[dim]Continuing without entity extraction.[/dim]\n")

        # Process each item
        results: list[PipelineResult] = []
        with CLISourceProcessingService(ctx) as service:
            pipeline = SourcePipeline(service, console if not quiet else None)

            for item in items:
                result = pipeline.run(
                    file_path=item["path"],
                    file_id=item["file_id"],
                    url=item["url"],
                    skip_index=skip_index,
                    skip_extract=skip_extract,
                    skip_commit=skip_commit,
                    skip_embeddings=skip_embeddings,
                    enable_normalization=normalize,
                    enable_vision=vision,
                    content_filtering=content_filtering,
                    index_only=index_only,
                    extract_only=extract_only,
                    extraction_depth="quick" if quick else "full",
                    domain=domain,
                    filtering_mode=filtering_mode,
                    quiet=quiet,
                    verbose=verbose,
                    skip_duplicates=skip_duplicates,
                    no_confirm=no_confirm,
                )
                results.append(result)

        # Show batch summary for multiple files
        if len(results) > 1 and not quiet and not output_json:
            total_time = sum(r.duration_seconds for r in results)
            _show_batch_summary(results, total_time, console)

        # JSON output
        if output_json:
            import json

            output = [_result_to_dict(r) for r in results]
            # Single file: output dict directly (backwards compatible)
            console.print(json.dumps(output[0] if len(output) == 1 else output, indent=2))

        elif quiet:
            for r in results:
                if r.parked_for_confirmation:
                    console.print(
                        f"[yellow]AWAITING[/yellow] {r.file_id} "
                        f"(detected: {r.detected_domain}) — cc source confirm {r.file_id}"
                    )
                elif r.success:
                    extra = f" — domain: {r.detected_domain}" if r.detected_domain else ""
                    console.print(f"[green]OK[/green] {r.file_id}{extra}")
                else:
                    console.print(f"[red]FAILED[/red] {r.error}")

        # Exit with error if any failed
        if any(not r.success for r in results):
            sys.exit(1)

    except KeyboardInterrupt:
        # Suppress asyncio teardown noise (pending tasks, unclosed coroutines)
        import os
        import warnings

        warnings.filterwarnings("ignore", category=RuntimeWarning)
        devnull_fd = os.open(os.devnull, os.O_WRONLY)
        old_stderr = os.dup(2)
        os.dup2(devnull_fd, 2)

        console.print("\n[yellow]Cancelled[/yellow]")

        os.dup2(old_stderr, 2)
        os.close(devnull_fd)
        os.close(old_stderr)
        sys.exit(130)

    except FileNotFoundError as e:
        console.print(f"[red]Error:[/red] File not found: {e.filename or e}")
        sys.exit(1)
    except PermissionError as e:
        console.print(f"[red]Error:[/red] Permission denied: {e.filename or e}")
        sys.exit(1)
    except ChaosCypherException as e:
        console.print(f"[red]Error:[/red] {e.message}")
        sys.exit(1)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)
