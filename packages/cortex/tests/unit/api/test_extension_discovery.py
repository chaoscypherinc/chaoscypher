# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for extension router discovery."""

from unittest.mock import MagicMock, patch

from fastapi import APIRouter

from chaoscypher_cortex.api.v1.router import discover_extensions


class TestDiscoverExtensions:
    """Test extension entry point discovery."""

    def test_no_extensions_installed(self) -> None:
        """When no extensions are installed, nothing is mounted."""
        api = APIRouter()
        with patch("chaoscypher_cortex.api.v1.router.entry_points", return_value=[]):
            discover_extensions(api)
        assert len(api.routes) == 0

    def test_extension_registers_router(self) -> None:
        """When an extension is installed, its register function is called."""
        api = APIRouter()
        mock_register = MagicMock()

        mock_ep = MagicMock()
        mock_ep.name = "enterprise"
        mock_ep.load.return_value = mock_register

        with patch(
            "chaoscypher_cortex.api.v1.router.entry_points",
            return_value=[mock_ep],
        ):
            discover_extensions(api)

        mock_register.assert_called_once_with(api)

    def test_extension_failure_does_not_crash(self) -> None:
        """If an extension fails to load, it logs a warning and continues."""
        api = APIRouter()

        mock_ep = MagicMock()
        mock_ep.name = "broken"
        mock_ep.load.side_effect = ImportError("missing module")

        with patch(
            "chaoscypher_cortex.api.v1.router.entry_points",
            return_value=[mock_ep],
        ):
            discover_extensions(api)

        assert len(api.routes) == 0
