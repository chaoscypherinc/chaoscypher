# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Namespace Merger - Merges knowledge from multiple packages.

Provides strategies for combining knowledge graphs, entities, and relationships
from multiple packages into a unified runtime database.

Merge Strategies:
- NAMESPACE: Prefix all entities with package name for isolation (default)
- MERGE: Combine entities, deduplicating by URI/identifier
- REPLACE: Later packages override earlier ones for conflicts

Example:
    from chaoscypher_core.services.compose import NamespaceMerger, MergeStrategy

    merger = NamespaceMerger(
        output_dir=Path(".chaoscypher/db"),
        strategy=MergeStrategy.NAMESPACE,
    )

    # Merge resolved packages
    result = await merger.merge(resolved_packages)
    print(f"Merged {result.total_entities} entities")
"""

from __future__ import annotations

import json
import shutil
from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.exceptions import ChaosCypherException
from chaoscypher_core.services.compose.models import (
    CompositionResult,
    MergeStrategy,
    ResolvedPackage,
)


if TYPE_CHECKING:
    from pathlib import Path


logger = structlog.get_logger(__name__)


class MergerError(ChaosCypherException):
    """Error during package merging.

    Attributes:
        package: Package that caused the error.
    """

    def __init__(self, message: str, package: str | None = None) -> None:
        """Initialize merger error.

        Args:
            message: Error description.
            package: Package that caused the error.
        """
        details: dict = {}
        if package:
            details["package"] = package
        super().__init__(message=message, code="MERGER_ERROR", details=details)
        self.package = package


class NamespaceMerger:
    """Merges knowledge packages into a unified runtime database.

    Handles the merging of knowledge graphs, entities, relationships,
    and search indices from multiple packages using the specified strategy.

    The default NAMESPACE strategy prefixes all entity URIs with the package
    namespace to ensure data isolation and prevent conflicts.

    Attributes:
        output_dir: Directory for the merged database.
        strategy: Merge strategy to use.

    Example:
        merger = NamespaceMerger(
            output_dir=Path(".chaoscypher/db"),
            strategy=MergeStrategy.NAMESPACE,
        )

        result = await merger.merge(packages)
        if result.success:
            print(f"Database ready at {result.output_dir}")
    """

    def __init__(
        self,
        output_dir: Path,
        strategy: MergeStrategy = MergeStrategy.NAMESPACE,
    ) -> None:
        """Initialize namespace merger.

        Args:
            output_dir: Directory for the merged database.
            strategy: Merge strategy to use.
        """
        self.output_dir = output_dir
        self.strategy = strategy

    async def merge(
        self,
        packages: list[ResolvedPackage],
        clean: bool = True,
    ) -> CompositionResult:
        """Merge multiple packages into a unified database.

        Args:
            packages: List of resolved packages to merge.
            clean: Whether to clean output directory before merging.

        Returns:
            CompositionResult with merge statistics.

        Raises:
            MergerError: If merging fails.
        """
        logger.info(
            "merger_starting",
            package_count=len(packages),
            strategy=self.strategy.value,
            output_dir=str(self.output_dir),
        )

        errors: list[str] = []
        warnings: list[str] = []
        packages_included: list[str] = []
        total_entities = 0
        total_relationships = 0

        try:
            # Prepare output directory
            if clean and self.output_dir.exists():
                shutil.rmtree(self.output_dir)

            self.output_dir.mkdir(parents=True, exist_ok=True)

            # Create database subdirectories
            db_dir = self.output_dir / "databases" / "default"
            db_dir.mkdir(parents=True, exist_ok=True)

            # Initialize merged data structures
            merged_entities: dict[str, Any] = {}
            merged_relationships: list[dict[str, Any]] = []
            merged_sources: dict[str, Any] = {}
            merged_metadata: dict[str, Any] = {
                "packages": [],
                "strategy": self.strategy.value,
            }

            # Process each package
            for pkg in packages:
                try:
                    pkg_stats = await self._merge_package(
                        pkg,
                        merged_entities,
                        merged_relationships,
                        merged_sources,
                    )

                    total_entities += pkg_stats["entities"]
                    total_relationships += pkg_stats["relationships"]
                    packages_included.append(f"{pkg.name}:{pkg.version}")

                    merged_metadata["packages"].append(
                        {
                            "name": pkg.name,
                            "version": pkg.version,
                            "namespace": pkg.namespace,
                            "entities": pkg_stats["entities"],
                            "relationships": pkg_stats["relationships"],
                        }
                    )

                    logger.info(
                        "merger_package_merged",
                        package=pkg.name,
                        entities=pkg_stats["entities"],
                        relationships=pkg_stats["relationships"],
                    )

                except Exception as e:
                    error_msg = f"Failed to merge {pkg.name}: {e}"
                    errors.append(error_msg)
                    logger.exception("merger_package_failed", package=pkg.name)

            # Write merged data to database files
            if not errors:
                await self._write_database(
                    db_dir,
                    merged_entities,
                    merged_relationships,
                    merged_sources,
                    merged_metadata,
                )

            success = len(errors) == 0

            logger.info(
                "merger_completed",
                success=success,
                total_entities=total_entities,
                total_relationships=total_relationships,
                packages=packages_included,
            )

            return CompositionResult(
                success=success,
                output_dir=self.output_dir,
                packages_included=packages_included,
                total_entities=total_entities,
                total_relationships=total_relationships,
                errors=errors,
                warnings=warnings,
            )

        except Exception as e:
            logger.exception("merger_failed")
            return CompositionResult(
                success=False,
                output_dir=self.output_dir,
                packages_included=packages_included,
                total_entities=total_entities,
                total_relationships=total_relationships,
                errors=[f"Merge failed: {e}"],
                warnings=warnings,
            )

    async def _merge_package(
        self,
        pkg: ResolvedPackage,
        merged_entities: dict[str, Any],
        merged_relationships: list[dict[str, Any]],
        merged_sources: dict[str, Any],
    ) -> dict[str, int]:
        """Merge a single package into the merged data structures.

        Args:
            pkg: Package to merge.
            merged_entities: Accumulated entities dict.
            merged_relationships: Accumulated relationships list.
            merged_sources: Accumulated sources dict.

        Returns:
            Statistics dict with entity and relationship counts.
        """
        stats = {"entities": 0, "relationships": 0}

        # Look for data directory in package
        data_dir = pkg.path / "data"
        if not data_dir.exists():
            logger.warning("merger_no_data_dir", package=pkg.name)
            return stats

        # Merge entities
        entities_file = data_dir / "entities.json"
        if entities_file.exists():
            with entities_file.open() as f:
                entities = json.load(f)

            for entity_id, entity_data in entities.items():
                namespaced_id = self._namespace_id(entity_id, pkg)
                namespaced_entity = self._namespace_entity(entity_data, pkg)

                if self._should_include(namespaced_id, merged_entities):
                    merged_entities[namespaced_id] = namespaced_entity
                    stats["entities"] += 1

        # Merge relationships
        relationships_file = data_dir / "relationships.json"
        if relationships_file.exists():
            with relationships_file.open() as f:
                relationships = json.load(f)

            for rel in relationships:
                namespaced_rel = self._namespace_relationship(rel, pkg)
                merged_relationships.append(namespaced_rel)
                stats["relationships"] += 1

        # Merge sources
        sources_file = data_dir / "sources.json"
        if sources_file.exists():
            with sources_file.open() as f:
                sources = json.load(f)

            for source_id, source_data in sources.items():
                namespaced_id = self._namespace_id(source_id, pkg)
                namespaced_source = self._namespace_source(source_data, pkg)

                if self._should_include(namespaced_id, merged_sources):
                    merged_sources[namespaced_id] = namespaced_source

        # Copy any additional data files
        await self._copy_package_data(pkg, data_dir)

        return stats

    def _namespace_id(self, entity_id: str, pkg: ResolvedPackage) -> str:
        """Add namespace prefix to an entity ID.

        Args:
            entity_id: Original entity ID.
            pkg: Package providing the entity.

        Returns:
            Namespaced entity ID.
        """
        if self.strategy == MergeStrategy.NAMESPACE:
            return f"{pkg.namespace}:{entity_id}"
        return entity_id

    def _namespace_entity(
        self,
        entity: dict[str, Any],
        pkg: ResolvedPackage,
    ) -> dict[str, Any]:
        """Add namespace metadata to an entity.

        Args:
            entity: Entity data dict.
            pkg: Package providing the entity.

        Returns:
            Entity with namespace metadata.
        """
        if self.strategy == MergeStrategy.NAMESPACE:
            entity = entity.copy()
            entity["_source_package"] = pkg.name
            entity["_source_version"] = pkg.version
            entity["_namespace"] = pkg.namespace

            # Namespace any referenced entity IDs
            if "relationships" in entity:
                entity["relationships"] = [
                    self._namespace_id(r, pkg) for r in entity["relationships"]
                ]

        return entity

    def _namespace_relationship(
        self,
        rel: dict[str, Any],
        pkg: ResolvedPackage,
    ) -> dict[str, Any]:
        """Add namespace to relationship source and target.

        Args:
            rel: Relationship data dict.
            pkg: Package providing the relationship.

        Returns:
            Relationship with namespaced IDs.
        """
        if self.strategy == MergeStrategy.NAMESPACE:
            rel = rel.copy()
            if "source" in rel:
                rel["source"] = self._namespace_id(rel["source"], pkg)
            if "target" in rel:
                rel["target"] = self._namespace_id(rel["target"], pkg)
            rel["_source_package"] = pkg.name
            rel["_namespace"] = pkg.namespace

        return rel

    def _namespace_source(
        self,
        source: dict[str, Any],
        pkg: ResolvedPackage,
    ) -> dict[str, Any]:
        """Add namespace metadata to a source.

        Args:
            source: Source data dict.
            pkg: Package providing the source.

        Returns:
            Source with namespace metadata.
        """
        if self.strategy == MergeStrategy.NAMESPACE:
            source = source.copy()
            source["_source_package"] = pkg.name
            source["_namespace"] = pkg.namespace

        return source

    def _should_include(
        self,
        entity_id: str,
        existing: dict[str, Any],
    ) -> bool:
        """Determine if an entity should be included based on merge strategy.

        Args:
            entity_id: Entity ID (possibly namespaced).
            existing: Existing merged entities.

        Returns:
            True if entity should be included.
        """
        if entity_id not in existing:
            return True

        # NAMESPACE: unique IDs, REPLACE: always replace, MERGE: skip duplicates
        return self.strategy in (MergeStrategy.NAMESPACE, MergeStrategy.REPLACE)

    async def _copy_package_data(
        self,
        pkg: ResolvedPackage,
        data_dir: Path,
    ) -> None:
        """Copy additional package data files (embeddings, graphs, etc.).

        Args:
            pkg: Package being merged.
            data_dir: Package data directory.
        """
        # Copy knowledge graph files
        graph_dir = data_dir / "graphs"
        if graph_dir.exists():
            dest = self.output_dir / "databases" / "default" / "graphs" / pkg.namespace
            dest.mkdir(parents=True, exist_ok=True)
            for graph_file in graph_dir.glob("*.ttl"):
                shutil.copy2(graph_file, dest / graph_file.name)
            for graph_file in graph_dir.glob("*.json"):
                shutil.copy2(graph_file, dest / graph_file.name)

        # Search indices (vector + FTS5) now live in app.db — no separate
        # directory to copy.  They are rebuilt automatically from the
        # committed graph data on first access.

        # Copy embeddings
        embeddings_dir = data_dir / "embeddings"
        if embeddings_dir.exists():
            dest = self.output_dir / "databases" / "default" / "embeddings" / pkg.namespace
            dest.mkdir(parents=True, exist_ok=True)
            for emb_file in embeddings_dir.iterdir():
                if emb_file.is_file():
                    shutil.copy2(emb_file, dest / emb_file.name)

    async def _write_database(
        self,
        db_dir: Path,
        entities: dict[str, Any],
        relationships: list[dict[str, Any]],
        sources: dict[str, Any],
        metadata: dict[str, Any],
    ) -> None:
        """Write merged data to database files.

        Args:
            db_dir: Database directory path.
            entities: Merged entities dict.
            relationships: Merged relationships list.
            sources: Merged sources dict.
            metadata: Composition metadata.
        """
        # Write entities
        entities_file = db_dir / "entities.json"
        with entities_file.open("w") as f:
            json.dump(entities, f, indent=2)

        # Write relationships
        relationships_file = db_dir / "relationships.json"
        with relationships_file.open("w") as f:
            json.dump(relationships, f, indent=2)

        # Write sources
        sources_file = db_dir / "sources.json"
        with sources_file.open("w") as f:
            json.dump(sources, f, indent=2)

        # Write composition metadata
        metadata_file = db_dir / "composition.json"
        with metadata_file.open("w") as f:
            json.dump(metadata, f, indent=2)

        logger.debug(
            "merger_database_written",
            path=str(db_dir),
            entities=len(entities),
            relationships=len(relationships),
        )


__all__ = [
    "MergerError",
    "NamespaceMerger",
]
