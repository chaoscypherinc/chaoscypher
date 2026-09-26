# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Namespace Merger - Merges knowledge from multiple packages.

Builds the composition's runtime database — a real Chaos Cypher SQLite
database at ``<output_dir>/databases/default`` — by importing each resolved
CCX 3.0 package through the same ``CcxImporter`` that ``chaoscypher graph
package load`` and ``chaoscypher mount`` use. Every entity, relationship,
source, chunk and citation lands with its stable CCX IRI intact, so the
composed database answers with the same citations as the packages it was
built from.

How packages meet: templates are unified by name (the importer keys them
on ``(name, template_type)``), and entities, relationships, sources and
chunks are upserted by their stable CCX IRIs — independently built packages
never collide, and a same-IRI record present in several packages is stored
once with the later package winning. ``settings.merge_strategy`` is recorded
in ``composition.json`` but does not change that result today; it is kept
as the seam for cross-package entity resolution.

Example:
    from chaoscypher_core.services.compose import NamespaceMerger, MergeStrategy

    merger = NamespaceMerger(
        output_dir=Path(".chaoscypher/db"),
        strategy=MergeStrategy.NAMESPACE,
    )

    result = await merger.merge(resolved_packages)
    print(f"Merged {result.total_entities} entities")
"""

from __future__ import annotations

import asyncio
import io
import json
import shutil
import zipfile
from datetime import UTC, datetime
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

# The composed database is always the output directory's ``default`` database,
# which is what ``compose up`` serves and ``compose run`` points tools at.
COMPOSED_DATABASE_NAME = "default"

# Written next to ``databases/`` after a successful merge: what went in.
COMPOSITION_MANIFEST_FILENAME = "composition.json"


class MergerError(ChaosCypherException):
    """Error during package merging.

    Attributes:
        package: Package that caused the error.
    """

    def __init__(
        self,
        message: str,
        package: str | None = None,
        details: dict | None = None,
    ) -> None:
        """Initialize merger error.

        Args:
            message: Error description.
            package: Package that caused the error.
            details: Additional error details.
        """
        error_details = details or {}
        if package:
            error_details["package"] = package
        super().__init__(message=message, code="MERGER_ERROR", details=error_details)
        self.package = package


class NamespaceMerger:
    """Merges resolved packages into one runtime database.

    Attributes:
        output_dir: Directory holding the composed ``databases/default``.
        strategy: How templates and same-IRI entities are reconciled.
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
        # Import stats of the current merge, consumed by the indexing pass.
        self._import_stats: list[Any] = []

    @property
    def database_dir(self) -> Path:
        """Directory of the composed runtime database."""
        return self.output_dir / "databases" / COMPOSED_DATABASE_NAME

    async def merge(
        self,
        packages: list[ResolvedPackage],
        clean: bool = True,
    ) -> CompositionResult:
        """Merge multiple packages into a unified database.

        Args:
            packages: List of resolved packages to merge, in order.
            clean: Whether to clean the output directory before merging.

        Returns:
            CompositionResult with merge statistics.
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
        package_records: list[dict[str, Any]] = []
        total_entities = 0
        total_relationships = 0
        self._import_stats = []

        try:
            # "Clean" means the artefacts a build owns: the composed database
            # and the composition manifest. The resolver cache (filled moments
            # ago), the runtime settings, the pid record and the server log
            # all live in the same output directory and must survive.
            if clean:
                self._remove_build_artefacts()
            self.database_dir.mkdir(parents=True, exist_ok=True)

            # The Engine builds the schema through the Alembic runner (never a
            # bare create_all), so the database `compose up` serves is exactly
            # what Cortex's own init_database would produce.
            from chaoscypher_core.bootstrap import Engine

            engine = Engine(data_dir=self.database_dir)
            try:
                for pkg in packages:
                    try:
                        stats = await self._merge_package(engine, pkg)
                    except Exception as e:
                        errors.append(f"Failed to merge {pkg.name}: {e}")
                        logger.exception("merger_package_failed", package=pkg.name)
                        continue

                    packages_included.append(f"{pkg.name}:{pkg.version}")
                    warnings.extend(f"{pkg.name}: {w}" for w in stats["warnings"])
                    package_records.append(
                        {
                            "name": pkg.name,
                            "version": pkg.version,
                            "namespace": pkg.namespace,
                            "entities": stats["entities"],
                            "relationships": stats["relationships"],
                            "sources": stats["sources"],
                            "citations": stats["citations"],
                        }
                    )
                    logger.info(
                        "merger_package_merged",
                        package=pkg.name,
                        entities=stats["entities"],
                        relationships=stats["relationships"],
                        sources=stats["sources"],
                    )

                # Totals come from the database, not the per-package counters:
                # under MERGE / REPLACE a same-IRI entity present in several
                # packages is stored once.
                total_entities = engine.graph_repository.count_nodes()
                total_relationships = engine.graph_repository.count_edges()

                if not errors:
                    await self._index_for_search(engine, warnings)
            finally:
                engine.close()

            if errors:
                # Never leave a half-built database behind: `up` keys on the
                # database directory's existence and would serve the partial
                # composition without rebuilding.
                self._remove_build_artefacts()
            else:
                self._write_composition_manifest(
                    package_records, total_entities, total_relationships
                )

            success = not errors
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
            self._remove_build_artefacts()
            return CompositionResult(
                success=False,
                output_dir=self.output_dir,
                packages_included=packages_included,
                total_entities=total_entities,
                total_relationships=total_relationships,
                errors=[f"Merge failed: {e}"],
                warnings=warnings,
            )

    def _remove_build_artefacts(self) -> None:
        """Delete the composed database and manifest (and nothing else)."""
        if self.database_dir.exists():
            shutil.rmtree(self.database_dir)
        manifest = self.output_dir / COMPOSITION_MANIFEST_FILENAME
        if manifest.exists():
            manifest.unlink()

    # ------------------------------------------------------------------
    # Per-package import
    # ------------------------------------------------------------------

    async def _merge_package(self, engine: Any, pkg: ResolvedPackage) -> dict[str, Any]:
        """Import one package into the composed database.

        Returns:
            Per-package counts plus the importer's warnings.

        Raises:
            MergerError: If the package cannot be read or fails validation.
        """
        from chaoscypher_core.services.package.importer import CcxImporter, ImportOptions

        data = await asyncio.to_thread(self._package_bytes, pkg)

        importer = CcxImporter(
            graph_repository=engine.graph_repository,
            sources_repository=engine.storage_adapter,
        )
        options = ImportOptions(
            import_sources=True,
            database_name=COMPOSED_DATABASE_NAME,
        )
        stats = await importer.import_from_bytes(data, options)
        if stats.errors:
            raise MergerError("; ".join(stats.errors), package=pkg.name)

        self._import_stats.append(stats)
        return {
            "entities": stats.nodes_imported,
            "relationships": stats.edges_imported,
            "sources": stats.sources_imported,
            "citations": stats.citations_imported,
            "warnings": list(stats.warnings),
        }

    @staticmethod
    def _package_bytes(pkg: ResolvedPackage) -> bytes:
        """Return the package as ``.ccx`` archive bytes.

        A package that came from an archive is read back from it. A package
        given as a directory (an extracted tree) is packed into an in-memory
        archive in the CCX container layout: ``mimetype`` first and stored
        uncompressed, everything else deflated.

        Raises:
            MergerError: If neither the archive nor the directory exists.
        """
        if pkg.archive_path is not None and pkg.archive_path.is_file():
            return pkg.archive_path.read_bytes()
        if pkg.path.is_file():
            return pkg.path.read_bytes()
        if not pkg.path.is_dir():
            msg = f"Package path not found: {pkg.path}"
            raise MergerError(msg, package=pkg.name)

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            mimetype = pkg.path / "mimetype"
            if mimetype.is_file():
                archive.writestr(
                    "mimetype", mimetype.read_bytes(), compress_type=zipfile.ZIP_STORED
                )
            for member in sorted(p for p in pkg.path.rglob("*") if p.is_file()):
                relative = member.relative_to(pkg.path).as_posix()
                if relative == "mimetype":
                    continue
                archive.write(member, relative, compress_type=zipfile.ZIP_DEFLATED)
        return buffer.getvalue()

    # ------------------------------------------------------------------
    # Post-merge: search indexing + composition manifest
    # ------------------------------------------------------------------

    async def _index_for_search(self, engine: Any, warnings: list[str]) -> None:
        """Embed and vector-index everything that was imported (best-effort)."""
        from chaoscypher_core.operations.importing.imported_source_handler import (
            index_imported_package,
        )

        source_ids: list[str] = []
        node_ids: list[str] = []
        for stats in self._import_stats:
            source_ids.extend(stats.imported_source_ids)
            node_ids.extend(stats.imported_node_ids)
        if not source_ids and not node_ids:
            return
        try:
            await index_imported_package(
                imported_source_ids=source_ids,
                imported_node_ids=node_ids,
                storage_adapter=engine.storage_adapter,
                graph_repository=engine.graph_repository,
                indexing_service=engine.indexing_service,
                search_repository=engine.search_repository,
                database_name=COMPOSED_DATABASE_NAME,
            )
        except Exception as e:
            # The knowledge is in the database and FTS keyword search works
            # without vectors; a missing embedding model must not fail the build.
            warnings.append(f"search indexing skipped: {e}")
            logger.warning("merger_index_skipped", error=str(e))

    def _write_composition_manifest(
        self,
        package_records: list[dict[str, Any]],
        total_entities: int,
        total_relationships: int,
    ) -> None:
        """Record what was composed, for `compose` tooling and for humans."""
        payload = {
            "strategy": self.strategy.value,
            "database": str(self.database_dir),
            "composed_at": datetime.now(UTC).isoformat(),
            "total_entities": total_entities,
            "total_relationships": total_relationships,
            "packages": package_records,
        }
        (self.output_dir / COMPOSITION_MANIFEST_FILENAME).write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )


__all__ = [
    "COMPOSED_DATABASE_NAME",
    "COMPOSITION_MANIFEST_FILENAME",
    "MergerError",
    "NamespaceMerger",
]
