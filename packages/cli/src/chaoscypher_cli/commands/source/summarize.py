# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Summarize command - write the one-line summary of each document with the configured model."""

import asyncio
import sys

import click
from rich.console import Console

from chaoscypher_cli.context import get_context
from chaoscypher_cli.utils.console import print_caught_error, print_error


console = Console()

NO_MODEL_MESSAGE = (
    "no language model is configured, so no summaries can be written. "
    'Run "chaoscypher setup" or set llm.chat_provider.'
)


@click.command()
@click.option("--missing", is_flag=True, help="Only documents that have no summary yet")
@click.option("--database", "-d", default="default", help="Database name")
def summarize(missing: bool, database: str) -> None:
    """Write each document's one-line summary with the configured model.

    Without --missing every summary is rewritten, typed ones included.
    One model call per document, never retried; a failed call leaves the
    document's summary as it was.

    Example:
        chaoscypher source summarize --missing
        chaoscypher source summarize -d research
    """
    from chaoscypher_core.adapters.llm.summary import summary_chat
    from chaoscypher_core.services.sources.summaries import summarize_documents

    try:
        ctx = get_context(database_name=database)
        chat = summary_chat(ctx.settings)
        if chat is None:
            print_error(NO_MODEL_MESSAGE, console=console)
            sys.exit(1)
        run = asyncio.run(
            summarize_documents(
                ctx.storage_adapter,
                ctx.database_name,
                chat=chat,
                settings=ctx.settings,
                missing_only=missing,
            )
        )
    except Exception as e:
        print_caught_error(e, console=console)
        sys.exit(1)

    console.print(
        f"Summaries: {run.written} written, {run.failed} failed, {run.skipped} skipped.",
        soft_wrap=True,
    )
