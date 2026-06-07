# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Render orchestration configs from Pydantic settings."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from chaoscypher_core.exceptions import ConfigError


if TYPE_CHECKING:
    from chaoscypher_core.app_config import Settings


logger = structlog.get_logger(__name__)

_TEMPLATE_DIR = Path(__file__).parent / "templates"

# Mapping: template basename (without .j2) -> output filename written by render_all.
_TEMPLATE_OUTPUT_MAP: dict[str, str] = {
    "nginx-http.conf": "nginx-http.conf",
    "nginx-https.conf": "nginx-https.conf",
    "proxy-common.conf": "proxy-common.conf",
    "multi-interface-nginx.conf": "multi-interface-nginx.conf",
    "supervisord.conf": "supervisord.conf",
    "valkey-args.txt": "valkey-args.txt",
}


def _build_env() -> Environment:
    """Build the Jinja Environment used to render orchestration templates."""
    return Environment(
        loader=FileSystemLoader(str(_TEMPLATE_DIR)),
        autoescape=select_autoescape(disabled_extensions=("j2",), default=False),
        undefined=StrictUndefined,  # fail loud on typos in templates
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )


def list_templates() -> list[str]:
    """Return the canonical list of template basenames (without ``.j2``)."""
    return sorted(_TEMPLATE_OUTPUT_MAP.keys())


def render_template(name: str, settings: Settings) -> str:
    """Render a single template by basename (without ``.j2``).

    Args:
        name: Template basename, e.g. ``"nginx-http.conf"``.
        settings: Settings instance whose values populate the template.

    Returns:
        The rendered template as a string.

    Raises:
        ConfigError: if ``name`` is not a known template. This is a
            programmer-error condition (the call site passed a name the
            renderer doesn't know about) but is raised as a domain
            exception rather than a stdlib ``KeyError`` so the Cortex
            error mapper produces a structured envelope rather than a
            generic 500.
    """
    if name not in _TEMPLATE_OUTPUT_MAP:
        msg = f"Unknown orchestration template: {name!r}. Known: {sorted(_TEMPLATE_OUTPUT_MAP)}"
        raise ConfigError(msg)

    env = _build_env()
    template = env.get_template(f"{name}.j2")
    return template.render(settings=settings)


def render_all(settings: Settings, output_dir: Path) -> list[Path]:
    """Render every known template and write to ``output_dir``.

    The render is atomic: all templates are first written to a sibling
    temporary directory, then moved into place. A partial render (e.g.
    due to a disk-full error mid-batch) leaves ``output_dir`` untouched
    rather than producing a half-written set.

    Args:
        settings: Settings instance whose values populate the templates.
        output_dir: Directory to write rendered files into. Created if missing.

    Returns:
        List of written paths, in the same order as ``list_templates()``.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    # Stage in a sibling tempdir so the rename is on the same filesystem.
    with tempfile.TemporaryDirectory(
        prefix=".orchestration-render-",
        dir=output_dir.parent,
    ) as staging_str:
        staging = Path(staging_str)
        rendered_paths: list[Path] = []
        for name, output_name in sorted(_TEMPLATE_OUTPUT_MAP.items()):
            content = render_template(name, settings)
            staged = staging / output_name
            staged.write_text(content, encoding="utf-8")
            rendered_paths.append(staged)

        # Atomic move into place — overwrites any existing files.
        moved: list[Path] = []
        for staged in rendered_paths:
            target = output_dir / staged.name
            shutil.move(str(staged), str(target))
            moved.append(target)
            logger.info(
                "orchestration_template_rendered",
                template=staged.name,
                output=str(target),
                bytes=target.stat().st_size,
            )
        return moved
