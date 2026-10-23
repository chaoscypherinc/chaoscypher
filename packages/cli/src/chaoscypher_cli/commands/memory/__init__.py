# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Memory Commands - ChaosCypher CLI.

Commands for the memories the MCP memory tools store:
- export: Export one scope's memories to a .ccx memory pack

A pack is imported like any package, with ``chaoscypher graph package load``.

Example:
    chaoscypher memory export --scope work work.ccx
    chaoscypher graph package load work.ccx
"""

import click

from chaoscypher_cli.commands.memory.export import export


@click.group()
def memory() -> None:
    """Export the memories the MCP memory tools store.

    A memory pack is a .ccx package of one scope's memories; import it
    elsewhere with chaoscypher graph package load.
    """


memory.add_command(export)

__all__ = ["memory"]
