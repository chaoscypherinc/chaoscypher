# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""``url_upload_options``: the one mapping from a URL import's choices to ``upload_file`` options.

The single-URL route, the site import (web UI, CLI, MCP) all store a page's
upload options through it, so a page imported from any surface lands with the
same columns.
"""

from __future__ import annotations

from chaoscypher_core.operations.sources.url_import_options import url_upload_options


def test_the_defaults_are_the_single_url_imports() -> None:
    assert url_upload_options() == {
        "auto_analyze": True,
        "extraction_depth": "full",
        "generate_embeddings": True,
        "enable_normalization": None,
        "enable_vision": True,
        "forced_packs": None,
        "forced_domain": None,
        "skip_duplicates": False,
        "content_filtering": True,
        "filtering_mode": None,
        "enable_direction_correction": None,
        "protect_orphans": None,
        "enable_inverse_relationships": None,
        "max_entity_degree_override": None,
        "auto_confirm": False,
    }


def test_forced_packs_keep_the_old_single_name_column_in_step() -> None:
    options = url_upload_options(
        extract_entities=False,
        analysis_depth="quick",
        forced_packs=("historical", "literary"),
        auto_confirm=True,
    )

    assert options["auto_analyze"] is False
    assert options["extraction_depth"] == "quick"
    assert options["forced_packs"] == ["historical", "literary"]
    assert options["forced_domain"] == "historical"
    assert options["auto_confirm"] is True
