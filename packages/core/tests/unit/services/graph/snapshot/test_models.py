# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for GraphBreakdown Pydantic models."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from chaoscypher_core.services.graph.snapshot.models import (
    GraphBreakdown,
    GraphStats,
    SourceBreakdown,
    TemplateEntry,
)


def test_template_entry_roundtrip():
    tpl = TemplateEntry(id="template_abc", name="Person", color="#00e5ff", count=42)
    assert tpl.model_dump()["color"] == "#00e5ff"
    assert TemplateEntry.model_validate(tpl.model_dump()) == tpl


def test_graph_breakdown_full_roundtrip():
    snap = GraphBreakdown(
        version=2,
        generated_at=datetime.now(UTC),
        database_name="default",
        title=None,
        stats=GraphStats(total_nodes=100, total_edges=200, total_sources=3),
        sources=[
            SourceBreakdown(
                id="src_1",
                name="doc.txt",
                source_type="text",
                total_entities=60,
                total_internal_links=150,
                templates=[
                    TemplateEntry(id="t1", name="Person", color="#00e5ff", count=40),
                    TemplateEntry(id="t2", name="Place", color="#ffaa55", count=20),
                ],
            ),
        ],
    )
    dumped = snap.model_dump(mode="json")
    restored = GraphBreakdown.model_validate(dumped)
    assert restored == snap
    assert restored.sources[0].total_internal_links == 150


def test_version_defaults_to_2():
    snap = GraphBreakdown(
        generated_at=datetime.now(UTC),
        database_name="db",
        stats=GraphStats(total_nodes=0, total_edges=0, total_sources=0),
        sources=[],
    )
    assert snap.version == 2


def test_extra_fields_rejected():
    """Every model must reject unknown fields — protects schema stability across phases."""
    with pytest.raises(ValidationError):
        TemplateEntry(id="x", name="n", color="#fff", count=1, extra_field="nope")
    with pytest.raises(ValidationError):
        GraphStats(total_nodes=0, total_edges=0, total_sources=0, extra_field="nope")
    with pytest.raises(ValidationError):
        SourceBreakdown(
            id="s",
            name="n",
            source_type="text",
            total_entities=0,
            templates=[],
            extra_field="nope",
        )
    with pytest.raises(ValidationError):
        GraphBreakdown(
            database_name="db",
            stats=GraphStats(total_nodes=0, total_edges=0, total_sources=0),
            sources=[],
            extra_field="nope",
        )


def test_negative_counts_rejected():
    """`ge=0` must reject negative values."""
    with pytest.raises(ValidationError):
        TemplateEntry(id="x", name="n", color="#fff", count=-1)
    with pytest.raises(ValidationError):
        SourceBreakdown(
            id="s",
            name="n",
            source_type="text",
            total_entities=-1,
            templates=[],
        )
    with pytest.raises(ValidationError):
        SourceBreakdown(
            id="s",
            name="n",
            source_type="text",
            total_entities=0,
            total_internal_links=-5,
            templates=[],
        )
    with pytest.raises(ValidationError):
        GraphStats(total_nodes=-1, total_edges=0, total_sources=0)
