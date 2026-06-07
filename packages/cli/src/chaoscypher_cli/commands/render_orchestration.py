# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""``chaoscypher render-orchestration`` — render orchestration configs from Pydantic settings.

A thin CLI wrapper around the canonical renderer module at
``chaoscypher_core.services.orchestration``.  The actual container
entrypoint (``packages/docker/config/entrypoint.sh``) invokes the
module directly via ``python -m chaoscypher_core.services.orchestration``
so an all-in-one image doesn't need the CLI installed to boot.  This
subcommand is kept for dev / debugging use where having ``chaoscypher``
on PATH is convenient.
"""

from __future__ import annotations

from pathlib import Path

import click

from chaoscypher_core.app_config import get_settings
from chaoscypher_core.services.orchestration import list_templates, render_all


@click.command("render-orchestration")
@click.option(
    "--output-dir",
    type=click.Path(file_okay=False, path_type=Path),
    required=False,
    help="Directory to write rendered configs into. Required unless --list is passed.",
)
@click.option(
    "--list",
    "do_list",
    is_flag=True,
    default=False,
    help="List known templates and exit.",
)
def render_orchestration_command(output_dir: Path | None, do_list: bool) -> None:
    """Render orchestration templates (nginx, supervisord, valkey) from current settings.

    Eliminates drift between the Python config layer and the orchestration
    layer by ensuring nginx/supervisord/valkey configs always reflect the
    current Pydantic Settings instance. The all-in-one container's
    ``entrypoint.sh`` calls ``python -m chaoscypher_core.services.orchestration``
    directly rather than this CLI subcommand, since the renderer lives in
    ``core`` and the container does not require the CLI to boot.
    """
    if do_list:
        for name in list_templates():
            click.echo(name)
        return

    if output_dir is None:
        msg = "--output-dir is required unless --list is passed"
        raise click.UsageError(msg)

    settings = get_settings()
    written = render_all(settings, output_dir)
    for path in written:
        click.echo(f"  rendered: {path}")
    click.echo(f"  total: {len(written)} templates")


__all__ = ["render_orchestration_command"]
