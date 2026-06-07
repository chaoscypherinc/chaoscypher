# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""README Generator for CCX Export Packages.

Generates README.txt content for CCX export packages with metadata,
statistics, and content listing.

Pure utility class with zero dependencies - works in both backend and CLI.
"""

from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from chaoscypher_core.services.export.models.schemas import ExportManifest


class ReadmeGenerator:
    """Generates README.txt content for CCX export packages.

    All methods are static - pure utility functions.
    """

    @staticmethod
    def generate_readme(manifest: ExportManifest) -> str:
        """Generate README content from export manifest.

        Args:
            manifest: Export manifest with package metadata

        Returns:
            README text content

        Example:
            >>> readme = ReadmeGenerator.generate_readme(manifest)
            >>> with open("README.txt", "w") as f:
            ...     f.write(readme)

        """
        lines = []

        # Header
        lines.append("=" * 80)
        lines.append(f"CCX Package: {manifest.name}")
        lines.append(f"Version: {manifest.package_version}")
        lines.append("=" * 80)
        lines.append("")

        # Description
        if manifest.description:
            lines.append("DESCRIPTION")
            lines.append("-" * 80)
            lines.append(manifest.description)
            lines.append("")

        # Metadata
        lines.append("METADATA")
        lines.append("-" * 80)
        lines.append(f"Format Version: CCX {manifest.ccx_version}")
        lines.append(f"Package Type: {', '.join(manifest.package_type)}")
        if manifest.author:
            lines.append(f"Author: {manifest.author}")
        if manifest.license:
            lines.append(f"License: {manifest.license}")

        # Format created_at timestamp
        created_str = manifest.created_at.strftime("%Y-%m-%d %H:%M:%S UTC")
        lines.append(f"Created: {created_str}")

        if manifest.generator:
            lines.append(f"Generator: {manifest.generator}")
        lines.append("")

        # Tags
        if manifest.tags:
            lines.append("TAGS")
            lines.append("-" * 80)
            lines.append(", ".join(manifest.tags))
            lines.append("")

        # Provenance
        if manifest.derived_from:
            lines.append("DERIVED FROM")
            lines.append("-" * 80)
            for pkg_name, version in manifest.derived_from.items():
                lines.append(f"  - {pkg_name} ({version})")
            lines.append("")

        if manifest.dependencies:
            lines.append("DEPENDENCIES")
            lines.append("-" * 80)
            for pkg_name, version in manifest.dependencies.items():
                lines.append(f"  - {pkg_name} ({version})")
            lines.append("")

        # Contents
        lines.append("CONTENTS")
        lines.append("-" * 80)
        for content in manifest.contents:
            size_kb = content.file_size_bytes / 1024
            lines.append(f"  {content.path}")
            lines.append(f"    Type: {content.type}")
            lines.append(f"    Size: {size_kb:.2f} KB")
            lines.append(f"    Media Type: {content.media_type}")
            lines.append(f"    SHA-256: {content.checksum_sha256[:16]}...")
            lines.append("")

        # Statistics
        lines.append("STATISTICS")
        lines.append("-" * 80)

        # Template stats
        if manifest.template_stats:
            stats = manifest.template_stats
            # Total count is now available via breakdown.stats.total_sources or len(sources);
            # for the README summary we report what we have.
            if stats.avg_properties_per_template:
                lines.append(
                    f"  - Avg Properties/Template: {stats.avg_properties_per_template:.1f}"
                )
            if stats.most_complex_template:
                lines.append(f"  - Most Complex Template: {stats.most_complex_template}")
            lines.append("")

        # Knowledge stats
        if manifest.knowledge_stats:
            knowledge_stats = manifest.knowledge_stats
            lines.append(
                f"Knowledge Graph: {knowledge_stats.node_count} nodes, {knowledge_stats.edge_count} edges"
            )
            if knowledge_stats.node_count > 0 and knowledge_stats.avg_degree:
                lines.append(f"  - Avg Connections/Node: {knowledge_stats.avg_degree:.1f}")
            if knowledge_stats.embeddings and knowledge_stats.embeddings.is_present:
                emb = knowledge_stats.embeddings
                if emb.vectors_included:
                    lines.append(
                        f"  - Embedding Vectors: Included ({emb.node_count}/{knowledge_stats.node_count} nodes)"
                    )
                else:
                    lines.append(
                        f"  - Embedding Vectors: Not included (metadata only, {emb.node_count}/{knowledge_stats.node_count} nodes have embeddings)"
                    )
                if emb.dimensions:
                    lines.append(f"  - Embedding Dimensions: {emb.dimensions}")
            lines.append("")

        # Lens stats
        if manifest.lens_stats:
            lens_stats = manifest.lens_stats
            lines.append(f"Lenses: {lens_stats.total_count}")
            if lens_stats.total_count > 0:
                input_count = len(lens_stats.input_templates) if lens_stats.input_templates else 0
                output_count = (
                    len(lens_stats.output_templates) if lens_stats.output_templates else 0
                )
                lines.append(f"  - Input Templates: {input_count}")
                lines.append(f"  - Output Templates: {output_count}")
                lines.append(f"  - With Transformations: {lens_stats.has_transformation_rules}")
            lines.append("")

        # Workflow stats
        if manifest.workflow_stats:
            workflow_stats = manifest.workflow_stats
            lines.append(f"Workflows: {workflow_stats.total_workflows}")
            if workflow_stats.total_workflows > 0:
                lines.append(f"  - Total Steps: {workflow_stats.total_steps}")
                if workflow_stats.avg_steps_per_workflow:
                    lines.append(
                        f"  - Avg Steps/Workflow: {workflow_stats.avg_steps_per_workflow:.1f}"
                    )
                lines.append(f"  - Triggers: {workflow_stats.trigger_count}")
            lines.append("")

        # Source stats — total_sources now comes from breakdown.stats.total_sources
        if manifest.source_stats:
            source_stats = manifest.source_stats
            total_sources = manifest.stats.total_sources
            lines.append(f"Sources: {total_sources}")
            if total_sources > 0:
                lines.append(f"  - Total Chunks: {source_stats.total_chunks}")
                if source_stats.avg_chunks_per_source:
                    lines.append(f"  - Avg Chunks/Source: {source_stats.avg_chunks_per_source:.1f}")
                if source_stats.source_types:
                    type_names = list(source_stats.source_types.keys())
                    lines.append(f"  - Source Types: {', '.join(type_names)}")
                if source_stats.date_range and (
                    source_stats.date_range.earliest or source_stats.date_range.latest
                ):
                    dr = source_stats.date_range
                    lines.append(f"  - Date Range: {dr.earliest} to {dr.latest}")
            lines.append("")

        # Footer
        lines.append("=" * 80)
        lines.append("End of README")
        lines.append("=" * 80)

        return "\n".join(lines)


__all__ = ["ReadmeGenerator"]
