# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""chaoscypher source confirm <id> — confirm a parked source's domain and extract.

A source that was uploaded with auto-detection and no bypass is parked at
``awaiting_confirmation`` with a stored detection proposal. This command reads
that proposal, lets the operator accept the recommended domain (``ranking[0]``)
or override the domain and extraction options, flips the source back to
``indexed``, and runs extraction synchronously (the CLI has no worker/queue).

Usage:
    chaoscypher source confirm if_abc123                  # accept recommended domain
    chaoscypher source confirm if_abc123 --domain legal   # override
    chaoscypher source confirm if_abc123 --yes            # non-interactive accept
    chaoscypher source confirm --all --yes                # confirm every parked source
"""

from __future__ import annotations

import sys
from typing import Any, get_args

import click
from rich.console import Console

from chaoscypher_cli.sources.domains import DOMAIN_NAMES
from chaoscypher_core.ports.types import FilteringMode


@click.command("confirm")
@click.argument("source_id", required=False)
@click.option(
    "--all", "confirm_all", is_flag=True, default=False, help="Confirm every parked source."
)
@click.option(
    "--domain",
    type=click.Choice(list(DOMAIN_NAMES)),
    default=None,
    help="Override the detected domain (default: accept the recommendation).",
)
@click.option(
    "--depth",
    type=click.Choice(["quick", "full"]),
    default=None,
    show_default="the source's stored depth",
    help="Extraction depth.",
)
@click.option(
    "--filtering-mode",
    type=click.Choice(list(get_args(FilteringMode))),
    default=None,
    help="Extraction filtering mode preset (overrides domain default).",
)
@click.option(
    "--yes",
    "-y",
    is_flag=True,
    default=False,
    help="Accept the recommended domain without prompting (required when not a TTY).",
)
@click.option("--database", "-d", default="default", help="Target database.")
@click.option("--quiet", "-q", is_flag=True, help="Minimal output.")
def confirm_cmd(
    source_id: str | None,
    confirm_all: bool,
    domain: str | None,
    depth: str | None,
    filtering_mode: FilteringMode | None,
    yes: bool,
    database: str,
    quiet: bool,
) -> None:
    """Confirm a parked source's extraction domain, then extract.

    SOURCE_ID is the file ID of a source in ``awaiting_confirmation`` status.
    Omit it and pass ``--all`` to confirm every parked source.
    """
    from chaoscypher_cli.context import get_context
    from chaoscypher_cli.sources import CLISourceProcessingService
    from chaoscypher_core.models import SourceStatus

    console = Console()

    if not source_id and not confirm_all:
        console.print("[yellow]Usage:[/yellow] chaoscypher source confirm <id> | --all")
        sys.exit(1)

    try:
        ctx = get_context(database_name=database)

        with CLISourceProcessingService(ctx) as service:
            if confirm_all:
                awaiting = [
                    f
                    for f in service.ctx.storage_adapter.list_files(
                        database_name=service.ctx.database_name
                    )
                    if f.get("status") == SourceStatus.AWAITING_CONFIRMATION
                ]
                if not awaiting:
                    console.print("[dim]No sources awaiting confirmation.[/dim]")
                    return
                if domain is not None:
                    console.print(
                        f"[yellow]--domain {domain} overrides every parked source's "
                        "detected recommendation.[/yellow]"
                    )
                failures = 0
                for src in awaiting:
                    ok = _confirm_one(
                        service, src["id"], domain, depth, filtering_mode, yes, quiet, console
                    )
                    failures += 0 if ok else 1
                if failures:
                    sys.exit(1)
                return

            assert source_id is not None
            if not _confirm_one(
                service, source_id, domain, depth, filtering_mode, yes, quiet, console
            ):
                sys.exit(1)

    except KeyboardInterrupt:
        console.print("\n[yellow]Cancelled[/yellow]")
        sys.exit(130)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)


def _confirm_one(
    service: Any,
    source_id: str,
    domain: str | None,
    depth: str | None,
    filtering_mode: FilteringMode | None,
    yes: bool,
    quiet: bool,
    console: Console,
) -> bool:
    """Confirm a single parked source. Returns True on success."""
    from datetime import UTC, datetime

    from rich.prompt import Prompt

    from chaoscypher_cli.commands.source.extract import _run_extraction
    from chaoscypher_core.models import SourceStatus

    source = service.get_file_status(source_id)
    if source is None:
        console.print(f"[red]Source not found:[/red] {source_id}")
        return False

    status = source.get("status", "")
    if status != SourceStatus.AWAITING_CONFIRMATION:
        console.print(
            f"[red]Cannot confirm source with status '{status}'.[/red]\n"
            "Expected: awaiting_confirmation."
        )
        return False

    # Abort before mutating any state if extraction can't run — otherwise the
    # source is flipped out of awaiting_confirmation but never extracted,
    # stranding it out of the confirmation queue.
    if not service.has_llm:
        console.print("[red]No LLM provider configured.[/red] Entity extraction requires an LLM.")
        return False

    proposal = source.get("detection_proposal") or {}
    ranking = proposal.get("ranking") or []
    recommended = ranking[0]["domain"] if ranking else proposal.get("detected_domain", "generic")
    low_confidence = proposal.get("low_confidence", False)

    # Resolve the domain: explicit --domain wins; else prompt (TTY) or accept (--yes).
    chosen = domain
    if chosen is None:
        is_tty = sys.stdin.isatty() and sys.stderr.isatty()
        if yes:
            chosen = recommended
        elif not is_tty:
            console.print(
                f"[red]{source_id} is awaiting confirmation but no TTY is available.[/red]\n"
                "Re-run with --domain <name> or --yes to accept the recommendation."
            )
            return False
        else:
            if low_confidence:
                console.print("[yellow]Detection wasn't confident — please pick a domain.[/yellow]")
            else:
                ranked = ", ".join(f"{r['domain']} ({r['score']:.1f})" for r in ranking[:3])
                console.print(
                    f"[dim]Recommended:[/dim] [magenta]{recommended}[/magenta]"
                    + (f"  [dim]({ranked})[/dim]" if ranked else "")
                )
            chosen = Prompt.ask(
                "Confirm extraction domain",
                choices=[*DOMAIN_NAMES, "cancel"],
                default=recommended if recommended in DOMAIN_NAMES else "generic",
            )
            if chosen == "cancel":
                console.print("[dim]Cancelled.[/dim]")
                return False

    # Persist forced domain + flip back to INDEXED in one update; write-once
    # extraction_confirmed_at so a re-dispatch never re-parks.
    service.ctx.storage_adapter.update_file(
        source_id,
        database_name=service.ctx.database_name,
        updates={"forced_domain": chosen, "status": SourceStatus.INDEXED},
    )
    if source.get("extraction_confirmed_at") is None:
        service.ctx.storage_adapter.update_file(
            source_id,
            database_name=service.ctx.database_name,
            updates={"extraction_confirmed_at": datetime.now(UTC).isoformat()},
        )

    if not quiet:
        effective_depth = depth or source.get("extraction_depth") or "full"
        console.print(
            f"\n[bold]Confirming:[/bold] [cyan]{source.get('filename', source_id)}[/cyan]  "
            f"[dim](domain: {chosen}, depth: {effective_depth})[/dim]\n"
        )

    _run_extraction(
        service=service,
        source_id=source_id,
        depth=depth,
        domain=chosen,
        filtering_mode=filtering_mode,
        quiet=quiet,
        console=console,
    )
    return True


__all__ = ["confirm_cmd"]
