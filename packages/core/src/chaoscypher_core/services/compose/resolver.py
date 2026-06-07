# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Package Resolver - Resolves and downloads packages for composition.

Handles hub packages, local files/directories, and dependency resolution.
Downloads hub packages to a cache directory and resolves transitive dependencies.

Example:
    from chaoscypher_core.services.compose import PackageResolver, ComposeConfig

    config = ComposeConfig.from_yaml("axiomatize.yaml")
    resolver = PackageResolver(cache_dir=Path(".chaoscypher/cache"))

    # Resolve all packages (downloads hub packages, validates local packages)
    resolved = await resolver.resolve_all(config.package_specs)

    for pkg in resolved:
        print(f"{pkg.name}:{pkg.version} -> {pkg.path}")
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from collections import deque
from pathlib import Path

import structlog

from chaoscypher_core.exceptions import ChaosCypherException
from chaoscypher_core.services.compose.models import (
    PackageSpec,
    ResolvedPackage,
)
from chaoscypher_core.services.lexicon import AuthConfig, LexiconClient, LexiconClientError
from chaoscypher_core.services.package import (
    PackageManifest,
    extract_archive,
    validate_package_directory,
)


logger = structlog.get_logger(__name__)


class ResolverError(ChaosCypherException):
    """Error during package resolution.

    Attributes:
        package: Package that caused the error.
    """

    def __init__(
        self,
        message: str,
        package: str | None = None,
        details: dict | None = None,
    ) -> None:
        """Initialize resolver error.

        Args:
            message: Error description.
            package: Package that caused the error.
            details: Additional error details.
        """
        error_details = details or {}
        if package:
            error_details["package"] = package
        super().__init__(message=message, code="RESOLVER_ERROR", details=error_details)
        self.package = package


class PackageResolver:
    """Resolves packages from hub, local files, and dependencies.

    Handles the complete resolution workflow:
    1. Parse package specifications
    2. Download hub packages to cache
    3. Validate local packages
    4. Resolve transitive dependencies
    5. Return list of resolved packages ready for composition

    Attributes:
        cache_dir: Directory for caching downloaded packages.
        lexicon_url: Lexicon API URL.
        auth: Optional authentication config.

    Example:
        resolver = PackageResolver(
            cache_dir=Path(".chaoscypher/cache"),
            auth=AuthConfig(token="..."),
        )

        specs = [
            PackageSpec.parse("medical-ontology:1.0.0"),
            PackageSpec.parse("./local-data.ccx"),
        ]

        resolved = await resolver.resolve_all(specs)
    """

    def __init__(
        self,
        cache_dir: Path | None = None,
        lexicon_url: str | None = None,
        auth: AuthConfig | None = None,
    ) -> None:
        """Initialize package resolver.

        Args:
            cache_dir: Directory for caching downloaded packages.
            lexicon_url: Lexicon API URL (uses default if not specified).
            auth: Optional authentication config for Lexicon.
        """
        self.cache_dir = cache_dir or Path.cwd() / ".chaoscypher" / "cache"
        self.lexicon_url = lexicon_url
        self.auth = auth

        # Resolved packages cache (name:version -> ResolvedPackage)
        self._resolved: dict[str, ResolvedPackage] = {}

    async def resolve_all(
        self,
        specs: list[PackageSpec],
        include_dependencies: bool = True,
    ) -> list[ResolvedPackage]:
        """Resolve all package specifications.

        Downloads hub packages, validates local packages, and optionally
        resolves transitive dependencies.

        Args:
            specs: List of package specifications.
            include_dependencies: Whether to resolve transitive dependencies.

        Returns:
            List of resolved packages in dependency order.

        Raises:
            ResolverError: If resolution fails for any package.
        """
        logger.info(
            "resolver_starting",
            package_count=len(specs),
            include_dependencies=include_dependencies,
        )

        # Clear cache for fresh resolution
        self._resolved.clear()

        # Use BFS to resolve packages with dependencies
        to_resolve: deque[PackageSpec] = deque(specs)
        resolution_order: list[str] = []

        async with LexiconClient(
            base_url=self.lexicon_url or "",
            auth=self.auth,
        ) as client:
            while to_resolve:
                spec = to_resolve.popleft()
                cache_key = self._get_cache_key(spec)

                # Skip if already resolved
                if cache_key in self._resolved:
                    continue

                # Resolve this package
                resolved = await self._resolve_single(spec, client)
                self._resolved[cache_key] = resolved
                resolution_order.append(cache_key)

                # Queue dependencies
                if include_dependencies and resolved.dependencies:
                    for dep_name in resolved.dependencies:
                        dep_spec = PackageSpec.parse(dep_name)
                        dep_key = self._get_cache_key(dep_spec)
                        if dep_key not in self._resolved:
                            to_resolve.append(dep_spec)
                            logger.debug(
                                "resolver_queued_dependency",
                                parent=resolved.name,
                                dependency=dep_name,
                            )

        # Return in resolution order (dependencies come first due to BFS)
        result = [self._resolved[key] for key in resolution_order]

        logger.info(
            "resolver_completed",
            resolved_count=len(result),
            packages=[p.name for p in result],
        )

        return result

    async def _resolve_single(
        self,
        spec: PackageSpec,
        client: LexiconClient,
    ) -> ResolvedPackage:
        """Resolve a single package specification.

        Args:
            spec: Package specification.
            client: Hub client for downloading.

        Returns:
            Resolved package.

        Raises:
            ResolverError: If resolution fails.
        """
        logger.debug("resolver_resolving", package=str(spec), is_local=spec.is_local)

        if spec.is_local:
            return await self._resolve_local(spec)
        return await self._resolve_hub(spec, client)

    async def _resolve_local(self, spec: PackageSpec) -> ResolvedPackage:
        """Resolve a local package (file or directory).

        Args:
            spec: Package specification with local path.

        Returns:
            Resolved package.

        Raises:
            ResolverError: If local package is invalid.
        """
        if not spec.local_path:
            raise ResolverError(
                "Local path not specified",
                package=spec.raw,
            )

        path = spec.local_path

        if not path.exists():
            msg = f"Package not found: {path}"
            raise ResolverError(
                msg,
                package=spec.raw,
            )

        # Handle .ccx archive
        if path.is_file() and path.suffix.lower() == ".ccx":
            return await self._resolve_archive(spec, path)

        # Handle directory
        if path.is_dir():
            return await self._resolve_directory(spec, path)

        msg = f"Invalid package path (must be .ccx file or directory): {path}"
        raise ResolverError(
            msg,
            package=spec.raw,
        )

    async def _resolve_archive(
        self,
        spec: PackageSpec,
        archive_path: Path,
    ) -> ResolvedPackage:
        """Resolve a .ccx archive file.

        Extracts the archive to cache and loads manifest.

        Args:
            spec: Package specification.
            archive_path: Path to .ccx file.

        Returns:
            Resolved package.
        """
        # Extract to cache directory
        extract_dir = self.cache_dir / "extracted" / archive_path.stem
        extract_dir.mkdir(parents=True, exist_ok=True)

        try:
            extract_archive(archive_path, extract_dir)
        except Exception as e:
            msg = f"Failed to extract archive: {e}"
            raise ResolverError(
                msg,
                package=spec.raw,
            ) from e

        # Load manifest
        manifest = self._load_manifest(extract_dir, spec.raw)

        return ResolvedPackage(
            spec=spec,
            name=manifest.name,
            version=manifest.package_version,
            path=extract_dir,
            manifest=manifest.to_dict(),
            dependencies=list(manifest.dependencies.keys()) if manifest.dependencies else [],
        )

    async def _resolve_directory(
        self,
        spec: PackageSpec,
        dir_path: Path,
    ) -> ResolvedPackage:
        """Resolve a package directory.

        Validates the directory structure and loads manifest.

        Args:
            spec: Package specification.
            dir_path: Path to package directory.

        Returns:
            Resolved package.
        """
        # Validate package structure
        result = validate_package_directory(dir_path)
        if not result.is_valid:
            msg = f"Invalid package structure: {'; '.join(result.errors)}"
            raise ResolverError(
                msg,
                package=spec.raw,
                details={"errors": result.errors, "warnings": result.warnings},
            )

        # Load manifest
        manifest = self._load_manifest(dir_path, spec.raw)

        return ResolvedPackage(
            spec=spec,
            name=manifest.name,
            version=manifest.package_version,
            path=dir_path,
            manifest=manifest.to_dict(),
            dependencies=list(manifest.dependencies.keys()) if manifest.dependencies else [],
        )

    async def _resolve_hub(
        self,
        spec: PackageSpec,
        client: LexiconClient,
    ) -> ResolvedPackage:
        """Resolve a hub package.

        Downloads the package from hub and extracts to cache.

        Args:
            spec: Package specification.
            client: Hub client.

        Returns:
            Resolved package.

        Raises:
            ResolverError: If download fails.
        """
        try:
            # Get package info (resolves "latest" to actual version)
            info = await client.get_package_info(spec.name, spec.version or "latest")
            actual_version = info.version

            # Check cache first
            cache_path = self.cache_dir / "packages" / spec.name.replace("/", "-")
            version_dir = cache_path / actual_version

            if version_dir.exists() and (version_dir / "manifest.json").exists():
                logger.debug(
                    "resolver_cache_hit",
                    package=spec.name,
                    version=actual_version,
                )
                manifest = self._load_manifest(version_dir, spec.raw)
            else:
                # Download and extract
                logger.info(
                    "resolver_downloading",
                    package=spec.name,
                    version=actual_version,
                )

                archive_bytes = await client.download(spec.name, actual_version)

                # Write to temp file and extract
                version_dir.mkdir(parents=True, exist_ok=True)

                with tempfile.NamedTemporaryFile(suffix=".ccx", delete=False) as tmp:
                    tmp.write(archive_bytes)
                    tmp_path = Path(tmp.name)

                try:
                    extract_archive(tmp_path, version_dir)
                finally:
                    await asyncio.to_thread(tmp_path.unlink, True)

                manifest = self._load_manifest(version_dir, spec.raw)

                logger.info(
                    "resolver_downloaded",
                    package=spec.name,
                    version=actual_version,
                    path=str(version_dir),
                )

            return ResolvedPackage(
                spec=spec,
                name=spec.name,
                version=actual_version,
                path=version_dir,
                manifest=manifest.to_dict(),
                dependencies=list(manifest.dependencies.keys()) if manifest.dependencies else [],
            )

        except LexiconClientError as e:
            msg = f"Hub error: {e.message}"
            raise ResolverError(
                msg,
                package=spec.raw,
                details={"status_code": e.status_code, "details": e.details},
            ) from e

    def _load_manifest(self, path: Path, package_ref: str) -> PackageManifest:
        """Load manifest from package directory.

        Args:
            path: Package directory path.
            package_ref: Package reference for error messages.

        Returns:
            Loaded PackageManifest.

        Raises:
            ResolverError: If manifest is missing or invalid.
        """
        manifest_path = path / "manifest.json"

        if not manifest_path.exists():
            msg = f"Missing manifest.json in {path}"
            raise ResolverError(
                msg,
                package=package_ref,
            )

        try:
            return PackageManifest.from_json(manifest_path)
        except json.JSONDecodeError as e:
            msg = f"Invalid manifest JSON: {e}"
            raise ResolverError(
                msg,
                package=package_ref,
            ) from e
        except Exception as e:
            msg = f"Failed to load manifest: {e}"
            raise ResolverError(
                msg,
                package=package_ref,
            ) from e

    def _get_cache_key(self, spec: PackageSpec) -> str:
        """Get cache key for a package specification.

        Args:
            spec: Package specification.

        Returns:
            Unique cache key string.
        """
        if spec.is_local:
            return f"local:{spec.local_path}"
        if spec.version:
            return f"{spec.name}:{spec.version}"
        return spec.name


__all__ = [
    "PackageResolver",
    "ResolverError",
]
