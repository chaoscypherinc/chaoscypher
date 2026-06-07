# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Database Commands - Chaos Cypher CLI.

Commands for managing databases:
- list: Show all databases
- create: Create a new database
- delete: Delete a database
- switch: Set the current database
- current: Show the current database
- info: Show database details

Example:
    chaoscypher db list
    chaoscypher db create my-project
    chaoscypher db switch my-project
    chaoscypher db current
    chaoscypher db info my-project
    chaoscypher db delete old-project
"""

import click

from chaoscypher_cli.lazy import LazyGroup


LAZY_SUBCOMMANDS = {
    "list": ("chaoscypher_cli.commands.db.list:list_databases", "Show all databases (--json, -q)"),
    "create": ("chaoscypher_cli.commands.db.create:create", "Create a new database"),
    "current": ("chaoscypher_cli.commands.db.current:current", "Show current database (-v)"),
    "delete": ("chaoscypher_cli.commands.db.delete:delete", "Delete a database"),
    "info": ("chaoscypher_cli.commands.db.info:info", "Show database details (--json)"),
    "switch": ("chaoscypher_cli.commands.db.switch:switch", "Switch active database"),
    "migrate": (
        "chaoscypher_cli.commands.db.migrate:migrate",
        "Schema migrations (status/apply/rollback)",
    ),
}


@click.group(cls=LazyGroup, lazy_subcommands=LAZY_SUBCOMMANDS)
def db() -> None:
    """Manage databases.

    Create, list, switch, and delete knowledge databases.
    Each database is an isolated workspace with its own
    nodes, edges, templates, and search indices.
    """


__all__ = ["db"]
