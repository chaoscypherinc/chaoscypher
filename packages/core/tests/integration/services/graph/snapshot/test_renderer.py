# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Integration tests for SnapshotRenderer.

Tests render to tmp_path (file-backed) so no :memory: SQLite anti-pattern.
All GraphBreakdown instances are hand-constructed to keep tests fast
and fully deterministic without requiring the full build service.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from PIL import Image

from chaoscypher_core.services.graph.snapshot.models import (
    GraphBreakdown,
    GraphStats,
    SourceBreakdown,
    TemplateEntry,
)
from chaoscypher_core.services.graph.snapshot.renderer import SnapshotRenderer


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _make_two_source_breakdown() -> GraphBreakdown:
    """Minimal two-source breakdown with a fixed generated_at for determinism."""
    return GraphBreakdown(
        version=2,
        generated_at=datetime(2026, 4, 22, 12, 0, tzinfo=UTC),
        database_name="test_db",
        title="Test Graph",
        stats=GraphStats(total_nodes=14, total_edges=8, total_sources=2),
        sources=[
            SourceBreakdown(
                id="src_a",
                name="Paper A",
                source_type="pdf",
                total_entities=8,
                total_internal_links=3,
                templates=[
                    TemplateEntry(id="tpl_person", name="Person", color="#00e5ff", count=5),
                    TemplateEntry(id="tpl_place", name="Place", color="#ffaa55", count=3),
                ],
            ),
            SourceBreakdown(
                id="src_b",
                name="Paper B",
                source_type="text",
                total_entities=6,
                total_internal_links=2,
                templates=[
                    TemplateEntry(id="tpl_concept", name="Concept", color="#9573e0", count=6),
                ],
            ),
        ],
    )


def _make_single_source_breakdown() -> GraphBreakdown:
    """Minimal single-source breakdown — exercises single_body layout."""
    return GraphBreakdown(
        version=2,
        generated_at=datetime(2026, 4, 22, 14, 30, tzinfo=UTC),
        database_name="single_db",
        title=None,
        stats=GraphStats(total_nodes=10, total_edges=5, total_sources=1),
        sources=[
            SourceBreakdown(
                id="src_only",
                name="Only Source",
                source_type="markdown",
                total_entities=10,
                total_internal_links=5,
                templates=[
                    TemplateEntry(id="tpl_event", name="Event", color="#1de9b6", count=10),
                ],
            ),
        ],
    )


# ---------------------------------------------------------------------------
# Test 1: output is a valid 1080x1080 PNG
# ---------------------------------------------------------------------------


def test_render_produces_1080_png(tmp_path: Path) -> None:
    """render_png must write a 1080x1080 PNG file."""
    breakdown = _make_two_source_breakdown()
    renderer = SnapshotRenderer()
    out = tmp_path / "out.png"

    renderer.render_png(breakdown, out)

    assert out.exists(), "Output file must exist"
    with Image.open(out) as img:
        assert img.size == (1080, 1080)
        assert img.format == "PNG"


# ---------------------------------------------------------------------------
# Test 2: output is byte-for-byte identical across two renders
# ---------------------------------------------------------------------------


def test_render_is_deterministic(tmp_path: Path) -> None:
    """Two renders of the same breakdown must produce identical byte streams."""
    breakdown = GraphBreakdown(
        version=2,
        generated_at=datetime(2026, 4, 22, 12, 0, tzinfo=UTC),
        database_name="det_db",
        title="Determinism Test",
        stats=GraphStats(total_nodes=6, total_edges=4, total_sources=1),
        sources=[
            SourceBreakdown(
                id="src_det",
                name="Det Source",
                source_type="text",
                total_entities=6,
                total_internal_links=4,
                templates=[
                    TemplateEntry(id="tpl_a", name="A", color="#d85fa5", count=6),
                ],
            ),
        ],
    )
    renderer = SnapshotRenderer()
    path_a = tmp_path / "render_a.png"
    path_b = tmp_path / "render_b.png"

    renderer.render_png(breakdown, path_a)
    renderer.render_png(breakdown, path_b)

    assert path_a.read_bytes() == path_b.read_bytes(), (
        "Two renders of identical input must produce byte-for-byte identical PNGs"
    )


# ---------------------------------------------------------------------------
# Test 3: single_body layout completes without crash
# ---------------------------------------------------------------------------


def test_render_single_body_layout(tmp_path: Path) -> None:
    """Single-source breakdown must render without error and produce a valid PNG."""
    breakdown = _make_single_source_breakdown()
    renderer = SnapshotRenderer()
    out = tmp_path / "single_body.png"

    renderer.render_png(breakdown, out)

    assert out.exists(), "Single-body output file must exist"
    with Image.open(out) as img:
        assert img.size == (1080, 1080)
        assert img.format == "PNG"
