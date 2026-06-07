# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Orchestration template renderer.

Renders nginx, supervisord, valkey, and compose-fragment templates from the
current Pydantic settings, eliminating drift between the Python config layer
and the orchestration layer.

Called by the all-in-one container's entrypoint before nginx/supervisor start.
"""

from chaoscypher_core.services.orchestration.renderer import (
    list_templates,
    render_all,
    render_template,
)


__all__ = ["list_templates", "render_all", "render_template"]
