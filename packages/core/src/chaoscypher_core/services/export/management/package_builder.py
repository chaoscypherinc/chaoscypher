# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Package building and assembly logic for CCX export.

Handles the final stages of export: constructing the CCX manifest,
assembling the zip archive (with README), and logging the export summary.
"""

from __future__ import annotations

import json
import zipfile
from datetime import UTC, datetime
from io import BytesIO
from typing import TYPE_CHECKING, Any

import structlog


if TYPE_CHECKING:
    from chaoscypher_core.settings import EngineSettings

from chaoscypher_core.services.export.models.schemas import (
    ContentFile,
    ExportManifest,
)
from chaoscypher_core.services.graph.snapshot.models import GraphBreakdown, GraphStats


logger = structlog.get_logger(__name__)


# Version info - could be parameterised for CLI vs Docker
GENERATOR_STRING = "ChaosCypher Knowledge Engine"
APP_VERSION = "1.0.0"  # Fallback version, should be passed as parameter


# ---------------------------------------------------------------------------
# Package contents
# ---------------------------------------------------------------------------


def build_package_contents(file_data: dict[str, dict[str, Any]]) -> list[str]:
    """Build list of package contents based on what files were actually created.

    Args:
        file_data: Dictionary of serialized file data (only non-empty files).

    Returns:
        Alphabetically sorted list of content types (e.g., ['knowledge', 'lenses']).

    """
    # Return sorted list of file types that were actually created
    return sorted(file_data.keys())


# ---------------------------------------------------------------------------
# Manifest creation
# ---------------------------------------------------------------------------


def create_manifest(
    *,
    package_type: list[str],
    file_data: dict[str, dict[str, Any]],
    stats: dict[str, Any | None],
    settings: EngineSettings,
    graph_breakdown: GraphBreakdown | None = None,
    preview_bytes: bytes | None = None,
) -> ExportManifest:
    """Create export manifest with metadata.

    Args:
        package_type: List of content types in this package.
        file_data: Serialized file data with checksums (only non-empty files).
        stats: Calculated statistics.
        settings: Export settings.
        graph_breakdown: Optional pre-built GraphBreakdown. When provided its
            fields are used for the inherited GraphBreakdown portion of the
            manifest. When None a minimal stub is constructed from settings.
        preview_bytes: Optional raw PNG bytes for graph_preview.png. When
            provided a ContentFile entry is appended to contents[].

    Returns:
        ExportManifest object.

    """
    from chaoscypher_core.services.export.utils import FileIntegrityChecker

    current_db_name = settings.current_database

    # Resolve GraphBreakdown base fields
    if graph_breakdown is None:
        graph_breakdown = GraphBreakdown(
            database_name=current_db_name,
            stats=GraphStats(total_nodes=0, total_edges=0, total_sources=0),
            sources=[],
        )

    # Create content file entries ONLY for files that were actually created
    contents = []
    for file_type in ["templates", "knowledge", "lenses", "workflows", "sources"]:
        if file_type in file_data:
            # Sources use .jsonl extension, others use .jsonld
            extension = "jsonl" if file_type == "sources" else "jsonld"
            media_type = (
                "application/jsonlines" if file_type == "sources" else "application/ld+json"
            )
            contents.append(
                ContentFile(
                    type=file_type,
                    path=f"{file_type}.{extension}",
                    media_type=media_type,
                    file_size_bytes=file_data[file_type]["size"],
                    checksum_sha256=file_data[file_type]["sha256"],
                    checksum_sha512=file_data[file_type]["sha512"],
                )
            )

    # Append graph_preview.png entry when preview bytes are provided
    if preview_bytes is not None:
        sha512, sha256 = FileIntegrityChecker.calculate_checksums(preview_bytes)
        contents.append(
            ContentFile(
                type="graph_preview",
                path="graph_preview.png",
                media_type="image/png",
                file_size_bytes=len(preview_bytes),
                checksum_sha256=sha256,
                checksum_sha512=sha512,
            )
        )

    return ExportManifest(
        **graph_breakdown.model_dump(mode="python"),
        ccx_version="2.0",
        package_type=package_type,
        name=settings.export.export_package_name or f"chaoscypher/{current_db_name}",
        package_version=settings.export.export_version or "1.0.0",
        author=settings.export.export_author,
        license=settings.export.export_license,
        description=settings.export.export_description or f"Export from {current_db_name} database",
        tags=settings.export.export_tags or [],
        created_at=datetime.now(UTC),
        derived_from=settings.export.export_derived_from or {},
        dependencies=settings.export.export_dependencies or {},
        contents=contents,
        template_stats=stats["template_stats"],
        knowledge_stats=stats["knowledge_stats"],
        lens_stats=stats["lens_stats"],
        workflow_stats=stats["workflow_stats"],
        source_stats=stats["source_stats"],
        generator=f"{GENERATOR_STRING}@{APP_VERSION}",
    )


# ---------------------------------------------------------------------------
# Zip file assembly
# ---------------------------------------------------------------------------


def create_zip_file(
    file_data: dict[str, dict[str, Any]],
    manifest: ExportManifest,
    preview_bytes: bytes | None = None,
) -> BytesIO:
    """Create .ccx zip file with only non-empty content.

    Args:
        file_data: Serialized file data (only non-empty files).
        manifest: Export manifest.
        preview_bytes: Optional raw PNG bytes to write as graph_preview.png.

    Returns:
        BytesIO buffer containing zip file.

    """
    from chaoscypher_core.services.export import ReadmeGenerator

    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zipf:
        # Add only the files that were created (non-empty)
        for file_type, data in file_data.items():
            # Use .jsonl extension for sources, .jsonld (JSON-LD) for graph data
            extension = "jsonl" if file_type == "sources" else "jsonld"
            zipf.writestr(f"{file_type}.{extension}", data["bytes"])

        # Add graph preview PNG when provided
        if preview_bytes is not None:
            zipf.writestr("graph_preview.png", preview_bytes)

        # Add manifest.json (conventionally last so tools find it at a predictable offset)
        manifest_json = json.dumps(manifest.model_dump(), indent=2, default=str)
        zipf.writestr("manifest.json", manifest_json)

        # Add README.txt
        readme_content = ReadmeGenerator.generate_readme(manifest)
        zipf.writestr("README.txt", readme_content)

    zip_buffer.seek(0)
    return zip_buffer


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def log_export_summary(
    *,
    package_type: list[str],
    separated_data: dict[str, Any],
    stats: dict[str, Any | None],
) -> None:
    """Log export summary.

    Args:
        package_type: List of content types in package.
        separated_data: Separated node/edge data.
        stats: Calculated statistics.

    """
    knowledge_stats = stats.get("knowledge_stats")
    has_embeddings = (
        knowledge_stats and knowledge_stats.embeddings and knowledge_stats.embeddings.is_present
    )

    workflow_nodes = [
        n for n in separated_data["workflow_nodes"] if n.get("template_id") == "system_workflow"
    ]

    logger.info(
        "export_package_created",
        export_version="CCX v2.0",
        package_type=package_type,
        template_count=len(separated_data["templates"]),
        knowledge_node_count=len(separated_data["knowledge_nodes"]),
        lens_node_count=len(separated_data["lens_nodes"]),
        workflow_count=len(workflow_nodes),
        has_embeddings=has_embeddings,
    )
