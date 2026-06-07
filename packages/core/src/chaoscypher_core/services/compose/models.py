# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Compose Models - Configuration and data structures for package composition.

This module defines the schema for axiomatize.yaml and related data structures
used to compose multiple knowledge packages into a unified runtime database.

Example axiomatize.yaml:
    name: my-knowledge-system
    version: 1.0.0

    packages:
      - medical-ontology:2.1.0
      - john/research-corpus
      - ./local-data.ccx

    settings:
      merge_strategy: namespace
      output_dir: ./.chaoscypher/
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator


class MergeStrategy(StrEnum):
    """Strategy for merging knowledge from multiple packages.

    Attributes:
        NAMESPACE: Prefix all entities with package name for isolation (default).
            Each package's data lives in its own namespace, preventing conflicts.
        MERGE: Combine entities, deduplicating by URI/identifier.
            Entities with same URI are merged, relationships combined.
        REPLACE: Later packages override earlier ones for conflicts.
            Last package wins for any conflicting entity.
    """

    NAMESPACE = "namespace"
    MERGE = "merge"
    REPLACE = "replace"


class PackageSpec(BaseModel):
    """Specification for a single package in the composition.

    Supports multiple formats:
    - Hub package: "package-name" or "username/package-name"
    - Hub package with version: "package-name:1.2.0"
    - Local file: "./path/to/package.ccx"
    - Local directory: "./path/to/package/"

    Attributes:
        raw: The original package specification string.
        name: Parsed package name (without version).
        version: Parsed version string, or None for latest.
        is_local: Whether this is a local path (file or directory).
        local_path: Resolved local path, if is_local is True.

    Example:
        spec = PackageSpec.parse("john/research-corpus:1.2.0")
        spec.name  # "john/research-corpus"
        spec.version  # "1.2.0"
        spec.is_local  # False
    """

    raw: str = Field(description="Original package specification string")
    name: str = Field(description="Package name without version")
    version: str | None = Field(default=None, description="Version or None for latest")
    is_local: bool = Field(default=False, description="Whether this is a local path")
    local_path: Path | None = Field(default=None, description="Resolved local path")

    @classmethod
    def parse(cls, spec: str, base_path: Path | None = None) -> PackageSpec:
        """Parse a package specification string.

        Args:
            spec: Package specification string (e.g., "pkg:1.0", "./local.ccx").
            base_path: Base path for resolving relative local paths.

        Returns:
            Parsed PackageSpec instance.

        Example:
            PackageSpec.parse("medical-ontology:2.1.0")
            PackageSpec.parse("./my-package.ccx", base_path=Path("/project"))
        """
        spec = spec.strip()
        base = base_path or Path.cwd()

        # Check if it's a local path
        if spec.startswith(("./", "../", "/")):
            path = Path(spec)
            if not path.is_absolute():
                path = base / path
            path = path.resolve()

            return cls(
                raw=spec,
                name=path.stem,
                version=None,
                is_local=True,
                local_path=path,
            )

        # Hub package - check for version specifier
        if ":" in spec:
            name, version = spec.rsplit(":", 1)
            return cls(
                raw=spec,
                name=name,
                version=version,
                is_local=False,
                local_path=None,
            )

        # Hub package without version (latest)
        return cls(
            raw=spec,
            name=spec,
            version=None,
            is_local=False,
            local_path=None,
        )

    def __str__(self) -> str:
        """Return string representation."""
        if self.is_local:
            return str(self.local_path or self.raw)
        if self.version:
            return f"{self.name}:{self.version}"
        return self.name


class ComposeSettings(BaseModel):
    """Settings for the composition process.

    Attributes:
        merge_strategy: How to merge data from multiple packages.
        output_dir: Directory for the runtime database.
        port: API server port when serving.
        ui_port: Web UI port when serving.
    """

    merge_strategy: MergeStrategy = Field(
        default=MergeStrategy.NAMESPACE,
        description="Strategy for merging package data",
    )
    output_dir: Path = Field(
        default=Path(".chaoscypher"),
        description="Output directory for runtime database",
    )
    port: int = Field(default=8081, description="API server port")
    ui_port: int = Field(default=8080, description="Web UI port")

    @field_validator("output_dir", mode="before")
    @classmethod
    def parse_output_dir(cls, v: Any) -> Path:
        """Convert string to Path."""
        if isinstance(v, str):
            return Path(v)
        if isinstance(v, Path):
            return v
        # Fallback for other types
        return Path(str(v))


class ComposeConfig(BaseModel):
    """Configuration for a package composition (axiomatize.yaml).

    Attributes:
        name: Name of the composed knowledge system.
        version: Version of the composition.
        packages: List of package specifications to include.
        settings: Composition settings.

    Example:
        config = ComposeConfig.from_yaml("axiomatize.yaml")
        for pkg in config.package_specs:
            print(f"Including: {pkg.name}")
    """

    name: str = Field(description="Name of the composed knowledge system")
    version: str = Field(default="1.0.0", description="Version of the composition")
    packages: list[str] = Field(
        default_factory=list,
        description="List of package specifications",
    )
    settings: ComposeSettings = Field(
        default_factory=ComposeSettings,
        description="Composition settings",
    )

    # Internal: parsed package specs (populated after parsing)
    _package_specs: list[PackageSpec] | None = None
    _config_path: Path | None = None

    @classmethod
    def from_yaml(cls, path: Path | str) -> ComposeConfig:
        """Load configuration from a YAML file.

        Args:
            path: Path to axiomatize.yaml file.

        Returns:
            Parsed ComposeConfig instance.

        Raises:
            FileNotFoundError: If the config file doesn't exist.
            ValueError: If the YAML is invalid.
        """
        import yaml

        path = Path(path)
        if not path.exists():
            msg = f"Config file not found: {path}"
            raise FileNotFoundError(msg)

        with open(path) as f:
            data = yaml.safe_load(f) or {}

        config = cls.model_validate(data)
        config._config_path = path.resolve()
        return config

    @classmethod
    def from_dict(cls, data: dict[str, Any], base_path: Path | None = None) -> ComposeConfig:
        """Create configuration from a dictionary.

        Args:
            data: Configuration dictionary.
            base_path: Base path for resolving relative paths.

        Returns:
            Parsed ComposeConfig instance.
        """
        config = cls.model_validate(data)
        config._config_path = base_path
        return config

    @property
    def package_specs(self) -> list[PackageSpec]:
        """Get parsed package specifications.

        Returns:
            List of PackageSpec instances.
        """
        if self._package_specs is None:
            base_path = self._config_path.parent if self._config_path else Path.cwd()
            self._package_specs = [
                PackageSpec.parse(pkg, base_path=base_path) for pkg in self.packages
            ]
        return self._package_specs

    @property
    def resolved_output_dir(self) -> Path:
        """Get the resolved output directory path.

        Returns:
            Absolute path to output directory.
        """
        output = self.settings.output_dir
        if not output.is_absolute():
            base = self._config_path.parent if self._config_path else Path.cwd()
            output = base / output
        return output.resolve()

    def to_yaml(self, path: Path | str) -> None:
        """Save configuration to a YAML file.

        Args:
            path: Path to write the YAML file.
        """
        import yaml

        data = self.model_dump(exclude={"_package_specs", "_config_path"})
        # Convert Path to string for YAML
        data["settings"]["output_dir"] = str(data["settings"]["output_dir"])

        with open(path, "w") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)


class ResolvedPackage(BaseModel):
    """A package that has been resolved and is ready for composition.

    Attributes:
        spec: Original package specification.
        name: Package name.
        version: Resolved version.
        path: Local path to the package (file or directory).
        manifest: Package manifest data.
        dependencies: List of dependency package names.
    """

    spec: PackageSpec = Field(description="Original package specification")
    name: str = Field(description="Package name")
    version: str = Field(description="Resolved version")
    path: Path = Field(description="Local path to package")
    manifest: dict[str, Any] = Field(
        default_factory=dict,
        description="Package manifest data",
    )
    dependencies: list[str] = Field(
        default_factory=list,
        description="List of dependency package names",
    )

    @property
    def namespace(self) -> str:
        """Get namespace for this package.

        Returns:
            Namespace string (package name with / replaced by _).
        """
        return self.name.replace("/", "_").replace("-", "_")


class CompositionResult(BaseModel):
    """Result of a composition operation.

    Attributes:
        success: Whether the composition succeeded.
        output_dir: Path to the output database directory.
        packages_included: List of packages that were composed.
        total_entities: Total number of entities in the composed database.
        total_relationships: Total number of relationships.
        errors: List of any errors encountered.
        warnings: List of any warnings.
    """

    success: bool = Field(description="Whether composition succeeded")
    output_dir: Path = Field(description="Path to output database")
    packages_included: list[str] = Field(
        default_factory=list,
        description="Packages included in composition",
    )
    total_entities: int = Field(default=0, description="Total entities")
    total_relationships: int = Field(default=0, description="Total relationships")
    errors: list[str] = Field(default_factory=list, description="Errors encountered")
    warnings: list[str] = Field(default_factory=list, description="Warnings")
