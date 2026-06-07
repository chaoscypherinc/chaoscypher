# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Settings tier markers for UI surfacing."""

from chaoscypher_core.settings import ChunkingSettings


def test_basic_chunking_knobs_marked() -> None:
    """The 3 basic chunking knobs carry tier=basic in json_schema_extra."""
    fields = ChunkingSettings.model_fields
    basic = {"small_chunk_size", "small_chunk_overlap", "group_size"}
    for name in basic:
        extra = fields[name].json_schema_extra or {}
        assert extra.get("tier") == "basic", f"{name} missing tier=basic"


def test_advanced_chunking_knobs_marked() -> None:
    """Advanced chunking knobs carry tier=advanced in json_schema_extra."""
    fields = ChunkingSettings.model_fields
    advanced_candidates = {"min_chunk_size", "max_chunk_size", "group_overlap"}
    for name in advanced_candidates:
        extra = fields[name].json_schema_extra or {}
        assert extra.get("tier") == "advanced", f"{name} missing tier=advanced"


def test_basic_and_advanced_knobs_are_disjoint() -> None:
    """No field is marked both basic and advanced."""
    fields = ChunkingSettings.model_fields
    basic = {n for n, f in fields.items() if (f.json_schema_extra or {}).get("tier") == "basic"}
    advanced = {
        n for n, f in fields.items() if (f.json_schema_extra or {}).get("tier") == "advanced"
    }
    overlap = basic & advanced
    assert not overlap, f"Fields marked both basic and advanced: {overlap}"
