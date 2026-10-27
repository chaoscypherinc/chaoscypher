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


def test_the_committed_model_cards_page_is_the_rendered_one():
    """`packages/docs/docs/reference/model-cards.md` equals `render_cards()` today.

    The page is committed generated Markdown (`make benchmark-cards`), and no CI
    step compared it with the registry, so a registry edit could leave the public
    page stale without anything failing (2026-10-22 docs audit). Regenerate with
    `make benchmark-cards` when this fails.
    """
    from pathlib import Path

    page = (
        Path(__file__).resolve().parents[5]
        / "packages"
        / "docs"
        / "docs"
        / "reference"
        / "model-cards.md"
    )
    assert page.read_text(encoding="utf-8") == render_cards(), (
        "run `make benchmark-cards` and commit the page"
    )
