# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The upload options of a URL import, from its choices: one mapping for every surface.

``import_url_page`` hands these to ``upload_file`` for each page, so the
single-URL route (``POST /sources/url``) and a site import (the web UI's
``POST /sources/sites``, the CLI's ``source add --site``, the MCP server's
``add_site``) all store a page's choices on its row the same way.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from chaoscypher_core.services.sources.engine.extraction.domains.packs import pack_columns


def url_upload_options(
    *,
    extract_entities: bool = True,
    analysis_depth: str = "full",
    enable_normalization: bool | None = None,
    enable_vision: bool = True,
    forced_packs: Sequence[str] | None = None,
    skip_duplicates: bool = False,
    content_filtering: bool = True,
    filtering_mode: str | None = None,
    enable_direction_correction: bool | None = None,
    protect_orphans: bool | None = None,
    enable_inverse_relationships: bool | None = None,
    max_entity_degree_override: int | None = None,
    auto_confirm: bool = False,
) -> dict[str, Any]:
    """The ``upload_file`` options a URL import applies to each page it imports.

    Args:
        extract_entities: Extract entities after indexing (``auto_analyze``).
        analysis_depth: ``quick`` or ``full`` (``extraction_depth``).
        enable_normalization: ``None`` defers to the file-type default.
        enable_vision: Use the vision model on images.
        forced_packs: The packs to extract with (``[]`` the floor alone);
            ``None`` detects them. The old single-name column follows.
        skip_duplicates: Skip a page whose content is already a source.
        content_filtering: Apply the domain's content-exclusion rules.
        filtering_mode: The extraction filtering preset, or ``None``.
        enable_direction_correction: Per-source toggle, ``None`` the default.
        protect_orphans: Per-source toggle, ``None`` the default.
        enable_inverse_relationships: Per-source toggle, ``None`` the default.
        max_entity_degree_override: Per-source degree cap, or ``None``.
        auto_confirm: Skip the domain-confirmation gate for detected packs.

    Returns:
        The options dict, as stored on a site and passed to ``upload_file``.
    """
    return {
        "auto_analyze": extract_entities,
        "extraction_depth": analysis_depth,
        "generate_embeddings": True,
        "enable_normalization": enable_normalization,
        "enable_vision": enable_vision,
        **pack_columns({"forced_packs": list(forced_packs) if forced_packs is not None else None}),
        "skip_duplicates": skip_duplicates,
        "content_filtering": content_filtering,
        "filtering_mode": filtering_mode,
        "enable_direction_correction": enable_direction_correction,
        "protect_orphans": protect_orphans,
        "enable_inverse_relationships": enable_inverse_relationships,
        "max_entity_degree_override": max_entity_degree_override,
        "auto_confirm": auto_confirm,
    }


__all__ = ["url_upload_options"]
