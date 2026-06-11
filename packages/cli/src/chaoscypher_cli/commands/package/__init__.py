# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Package Commands - ChaosCypher CLI.

Commands for exporting and importing .ccx packages:
- export: Export knowledge graph to .ccx file
- load: Import/load a .ccx package file

Example:
    chaoscypher graph package export --output my-knowledge.ccx
    chaoscypher graph package load my-knowledge.ccx
"""

import click

from chaoscypher_cli.commands.package.export import export
from chaoscypher_cli.commands.package.load import load


@click.group()
def package() -> None:
    """Export and import .ccx knowledge packages.

    Packages are portable bundles containing knowledge graphs,
    templates, and configurations that can be shared and distributed.
    """


package.add_command(export)
package.add_command(load)

__all__ = ["package"]
