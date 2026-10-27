# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Memory Commands - ChaosCypher CLI.

Commands for the memories the MCP memory tools store:
- export: Export one scope's memories to a .ccx memory pack
- relink: Link the memories not linked yet (counts only, never text)

A pack is imported like any package, with ``chaoscypher graph package load``.

Example:
    chaoscypher memory export --scope work work.ccx
    chaoscypher graph package load work.ccx
    chaoscypher memory relink --dry-run
"""

import click

from chaoscypher_cli.commands.memory.export import export
from chaoscypher_cli.commands.memory.relink import relink


@click.group()
def memory() -> None:
    """Export and link the memories the MCP memory tools store.

    A memory pack is a .ccx package of one scope's memories; import it
    elsewhere with chaoscypher graph package load. relink links the
    memories not linked yet.
    """


memory.add_command(export)
memory.add_command(relink)

__all__ = ["memory"]
