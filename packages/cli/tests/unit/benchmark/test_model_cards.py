# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the model-card generator."""

from __future__ import annotations

from chaoscypher_cli.benchmark.cards import render_cards


def test_render_cards_includes_opus():
    md = render_cards()
    assert "Claude Opus 4.8" in md
    assert "$5.00" in md and "$25.00" in md
    assert "| Model | Provider |" in md  # table header
    # Docusaurus frontmatter must be present.
    assert md.startswith("---\n")
    assert "title: Benchmark Model Cards" in md
