# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Template Commands - ChaosCypher CLI.

Commands for managing knowledge templates:
- list: Show available templates
- create: Define a new template
- get: Show template details
- update: Modify a template
- delete: Remove a template

Templates define the schema for nodes in the knowledge graph,
specifying properties, relationships, and constraints.

Example:
    chaoscypher graph template list
    chaoscypher graph template create --name Person -p name:string:required
    chaoscypher graph template get Person
    chaoscypher graph template update Person -a email:email
    chaoscypher graph template delete Person
"""

import click

from chaoscypher_cli.commands.template.create import create
from chaoscypher_cli.commands.template.delete import delete
from chaoscypher_cli.commands.template.get import get
from chaoscypher_cli.commands.template.list import list_templates
from chaoscypher_cli.commands.template.update import update


@click.group()
def template() -> None:
    """Manage knowledge templates.

    Templates define the structure and schema for nodes,
    including properties, types, and relationship constraints.
    """


template.add_command(list_templates, name="list")
template.add_command(create)
template.add_command(get)
template.add_command(update)
template.add_command(delete)

__all__ = ["template"]
