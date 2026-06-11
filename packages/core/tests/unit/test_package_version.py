# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the package __version__ attribute.

__version__ must be derived from the installed distribution metadata
(single source of truth: packages/core/pyproject.toml) rather than a
hardcoded string that drifts on every release.
"""

import importlib
import importlib.metadata
from unittest import mock

import pytest

import chaoscypher_core


@pytest.mark.unit
@pytest.mark.core
class TestPackageVersion:
    """__version__ tracks the installed distribution metadata."""

    def test_version_matches_installed_metadata(self):
        """__version__ equals the chaoscypher-core dist version (no drift)."""
        assert chaoscypher_core.__version__ == importlib.metadata.version("chaoscypher-core")

    def test_version_fallback_when_package_not_installed(self):
        """Without an installed dist, __version__ falls back to a sentinel."""
        try:
            with mock.patch(
                "importlib.metadata.version",
                side_effect=importlib.metadata.PackageNotFoundError,
            ):
                reloaded = importlib.reload(chaoscypher_core)
            assert reloaded.__version__ == "0.0.0+unknown"
        finally:
            # Restore the real version for the rest of the suite.
            importlib.reload(chaoscypher_core)
