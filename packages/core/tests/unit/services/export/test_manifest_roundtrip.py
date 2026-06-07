# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for ExportManifest — GraphBreakdown inheritance and roundtrip serialisation."""

from chaoscypher_core.services.export.models.schemas import (
    ExportManifest,
    KnowledgeStats,
    SourceStats,
    TemplateStats,
)
from chaoscypher_core.services.graph.snapshot.models import (
    GraphBreakdown,
    GraphStats,
    SourceBreakdown,
    TemplateEntry,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_breakdown() -> GraphBreakdown:
    """Minimal but real GraphBreakdown with one source and one template."""
    return GraphBreakdown(
        database_name="test_db",
        stats=GraphStats(total_nodes=5, total_edges=3, total_sources=1),
        sources=[
            SourceBreakdown(
                id="src_001",
                name="Test Source",
                source_type="text",
                total_entities=5,
                total_internal_links=2,
                templates=[
                    TemplateEntry(id="tmpl_001", name="Person", color="#ff0000", count=5),
                ],
            )
        ],
    )


def _make_manifest(breakdown: GraphBreakdown | None = None) -> ExportManifest:
    """Build a minimal ExportManifest (all required fields)."""
    bd = breakdown or _make_breakdown()
    return ExportManifest(
        **bd.model_dump(mode="python"),
        ccx_version="2.0",
        package_type=["knowledge"],
        name="test/package",
        package_version="1.0.0",
        generator="chaoscypher@1.0.0",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestManifestInheritsGraphBreakdownFields:
    """ExportManifest exposes all GraphBreakdown fields transparently."""

    def test_stats_total_nodes(self):
        """manifest.stats.total_nodes reflects the breakdown aggregate."""
        manifest = _make_manifest()
        assert manifest.stats.total_nodes == 5

    def test_sources_list(self):
        """manifest.sources[0].name reflects the breakdown source."""
        manifest = _make_manifest()
        assert len(manifest.sources) == 1
        assert manifest.sources[0].name == "Test Source"

    def test_generated_at_is_accessible(self):
        """manifest.generated_at is set (datetime, not None)."""
        manifest = _make_manifest()
        assert manifest.generated_at is not None

    def test_database_name(self):
        """manifest.database_name reflects the breakdown database."""
        manifest = _make_manifest()
        assert manifest.database_name == "test_db"

    def test_version_is_int(self):
        """manifest.version is the schema int from GraphBreakdown, not the package semver."""
        manifest = _make_manifest()
        assert isinstance(manifest.version, int)
        assert manifest.version == 2

    def test_package_version_is_str(self):
        """manifest.package_version is the package semver string."""
        manifest = _make_manifest()
        assert manifest.package_version == "1.0.0"


class TestManifestJsonRoundtrip:
    """JSON dump → re-parse preserves all inherited fields."""

    def test_inherited_stats_survive_roundtrip(self):
        """stats.total_nodes, sources[0].name, generated_at survive JSON roundtrip."""
        original = _make_manifest()
        json_str = original.model_dump_json()
        restored = ExportManifest.model_validate_json(json_str)

        assert restored.stats.total_nodes == original.stats.total_nodes
        assert restored.stats.total_edges == original.stats.total_edges
        assert restored.stats.total_sources == original.stats.total_sources
        assert restored.sources[0].name == original.sources[0].name
        assert restored.generated_at == original.generated_at

    def test_package_version_survives_roundtrip(self):
        """package_version survives JSON roundtrip."""
        original = _make_manifest()
        restored = ExportManifest.model_validate_json(original.model_dump_json())
        assert restored.package_version == "1.0.0"

    def test_database_name_survives_roundtrip(self):
        """database_name survives JSON roundtrip."""
        original = _make_manifest()
        restored = ExportManifest.model_validate_json(original.model_dump_json())
        assert restored.database_name == "test_db"


class TestManifestCcxVersionDefault:
    """ccx_version defaults to '2.0'."""

    def test_ccx_version_default_is_2_0(self):
        """Minimal manifest has ccx_version == '2.0'."""
        manifest = _make_manifest()
        assert manifest.ccx_version == "2.0"


class TestDroppedTemplateStatsFields:
    """TemplateStats no longer carries total_count, node_template_count, edge_template_count."""

    def test_no_total_count(self):
        """TemplateStats does not have total_count attribute."""
        stats = TemplateStats(avg_properties_per_template=0.0, most_complex_template=None)
        assert not hasattr(stats, "total_count")

    def test_no_node_template_count(self):
        """TemplateStats does not have node_template_count attribute."""
        stats = TemplateStats(avg_properties_per_template=0.0, most_complex_template=None)
        assert not hasattr(stats, "node_template_count")

    def test_no_edge_template_count(self):
        """TemplateStats does not have edge_template_count attribute."""
        stats = TemplateStats(avg_properties_per_template=0.0, most_complex_template=None)
        assert not hasattr(stats, "edge_template_count")


class TestDroppedSourceStatsFields:
    """SourceStats no longer carries total_sources (now on GraphStats)."""

    def test_no_total_sources(self):
        """SourceStats does not have total_sources attribute."""
        stats = SourceStats(vectors_included=False)
        assert not hasattr(stats, "total_sources")


class TestDroppedKnowledgeStatsFields:
    """KnowledgeStats no longer carries template_usage."""

    def test_no_template_usage(self):
        """KnowledgeStats does not have template_usage attribute."""
        stats = KnowledgeStats()
        assert not hasattr(stats, "template_usage")
