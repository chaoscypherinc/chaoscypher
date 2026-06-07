# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Compose Service - ChaosCypher Package Composition.

Provides functionality for composing multiple knowledge packages into
a unified runtime database. Supports packages from Lexicon Hub and
local files/directories.

Submodules:
    - models: Configuration schema (axiomatize.yaml) and data structures
    - resolver: Package resolution (hub + local + dependencies)
    - merger: Knowledge graph merging with namespace isolation
    - service: Main orchestrator for the composition workflow

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

Example usage:
    from chaoscypher_core.services.compose import (
        ComposeConfig,
        ComposeService,
        MergeStrategy,
    )

    # Load configuration
    config = ComposeConfig.from_yaml("axiomatize.yaml")

    # Build the composed database
    service = ComposeService()
    result = await service.build(config)

    # Or build and serve
    await service.up(config, detach=False)
"""

# Models
# Merger
from chaoscypher_core.services.compose.merger import (
    MergerError,
    NamespaceMerger,
)
from chaoscypher_core.services.compose.models import (
    ComposeConfig,
    ComposeSettings,
    CompositionResult,
    MergeStrategy,
    PackageSpec,
    ResolvedPackage,
)

# Resolver
from chaoscypher_core.services.compose.resolver import (
    PackageResolver,
    ResolverError,
)

# Service
from chaoscypher_core.services.compose.service import (
    ComposeError,
    ComposeService,
)


__all__ = [
    "ComposeConfig",
    "ComposeError",
    "ComposeService",
    "ComposeSettings",
    "CompositionResult",
    "MergeStrategy",
    "MergerError",
    "NamespaceMerger",
    "PackageResolver",
    "PackageSpec",
    "ResolvedPackage",
    "ResolverError",
]
