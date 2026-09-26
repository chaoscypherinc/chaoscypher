# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Init command - write a starter axiomatize.yaml.

Example:
    chaoscypher compose init
    chaoscypher compose init --name research-stack ./research.ccx acme/eu-ai-act
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

from chaoscypher_cli.utils.console import get_console, print_error, print_success


TEMPLATE = """\
# Chaos Cypher composition — `chaoscypher compose build` merges every package
# listed here into one database; `compose up` serves it over HTTP and
# `compose mcp` serves it to an AI assistant over MCP.
name: {name}
version: 1.0.0

# Local .ccx files, extracted package directories, or Lexicon Hub references
# (`owner/name` or `owner/name:1.2.0`). Order matters only on collisions.
packages:
{packages}

settings:
  output_dir: ./output
  port: 8081
"""


@click.command()
@click.argument("packages", nargs=-1)
@click.option("--name", "-n", default=None, help="Composition name (default: the directory name)")
@click.option(
    "--config",
    "-c",
    default="axiomatize.yaml",
    type=click.Path(dir_okay=False),
    help="Where to write the config",
)
@click.option("--force", "-f", is_flag=True, help="Overwrite an existing config")
def init(packages: tuple[str, ...], name: str | None, config: str, force: bool) -> None:
    """Write a starter axiomatize.yaml.

    PACKAGES are optional package references to list right away; without any,
    the file carries a commented example to edit.

    Example:
        chaoscypher compose init
        chaoscypher compose init --name research ./research.ccx acme/eu-ai-act
    """
    console = get_console()
    path = Path(config)
    if path.exists() and not force:
        print_error(f"{path} already exists (use --force to overwrite)")
        sys.exit(1)

    composition_name = name or Path.cwd().name or "my-knowledge-system"
    if packages:
        package_lines = "\n".join(f"  - {pkg}" for pkg in packages)
    else:
        package_lines = "  # - ./research.ccx\n  # - acme/eu-ai-act:1.2.0"

    path.write_text(
        TEMPLATE.format(name=composition_name, packages=package_lines), encoding="utf-8"
    )
    print_success(f"Wrote {path}")
    console.print("\n[dim]Next steps:[/dim]")
    if not packages:
        console.print(f"  edit {path} and list your packages")
    console.print("  chaoscypher compose build      # merge them into one database")
    console.print("  chaoscypher compose mcp        # serve it to an AI assistant")
    console.print("  chaoscypher compose up         # or serve the HTTP API")
