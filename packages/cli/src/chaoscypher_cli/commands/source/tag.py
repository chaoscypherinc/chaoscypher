# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tag command - add, remove or replace a document's tags."""

import sys

import click
from rich.console import Console

from chaoscypher_cli.sources.tag_options import tag_list
from chaoscypher_cli.utils.console import print_caught_error, print_error
from chaoscypher_core.policy import TAG_NAME_MAX_LENGTH, TAGS_PER_SOURCE_MAX


console = Console()

CLEAR_WITH_REPLACE = "--clear cannot be combined with --replace"

TAG_HELP = f"""Add, remove or replace the tags of the document SOURCE_ID.

Tags are lower-case: a-z, 0-9, ':', '_' or '-', starting with a letter
or digit, at most {TAG_NAME_MAX_LENGTH} characters; a document carries at most {TAGS_PER_SOURCE_MAX}.
--replace and --clear cannot be combined with --add or --remove.
With no option it prints the document's tags.

\b
Examples:
    chaoscypher source tag if_abc123 --add alpha --add beta
    chaoscypher source tag if_abc123 --remove beta
    chaoscypher source tag if_abc123 --replace alpha
    chaoscypher source tag if_abc123 --clear
"""
"""The command's help; its limits come from ``policy`` (core is loaded here already)."""


@click.command(help=TAG_HELP)
@click.argument("source_id")
@click.option("--add", "add", multiple=True, metavar="TAG", help="Add a tag (repeatable)")
@click.option(
    "--remove",
    "remove",
    multiple=True,
    metavar="TAG",
    help="Remove a tag (repeatable); one the document lacks is ignored",
)
@click.option(
    "--replace",
    "replace",
    multiple=True,
    metavar="TAG",
    help="Make these the document's whole set of tags (repeatable)",
)
@click.option("--clear", is_flag=True, help="Remove every tag from the document")
@click.option("--database", "-d", default="default", help="Database name")
def tag(
    source_id: str,
    add: tuple[str, ...],
    remove: tuple[str, ...],
    replace: tuple[str, ...],
    clear: bool,
    database: str,
) -> None:
    """Add, remove or replace the tags of the document SOURCE_ID (help: ``TAG_HELP``)."""
    from chaoscypher_cli.context import get_context
    from chaoscypher_core.services.sources.tags import edit_source_tags

    if clear and replace:
        print_error(CLEAR_WITH_REPLACE, console=console)
        sys.exit(1)
    try:
        ctx = get_context(database_name=database)
        after = edit_source_tags(
            ctx.storage_adapter,
            ctx.database_name,
            source_id,
            add=add,
            remove=remove,
            replace=list(replace) if (clear or replace) else None,
        )
    except Exception as e:
        print_caught_error(e, console=console)
        sys.exit(1)
    console.print(f"Tags of {source_id}: {tag_list(after)}", soft_wrap=True, markup=False)
