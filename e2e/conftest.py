# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Shared fixtures for all E2E test tiers."""

import os

import pytest


def pytest_collection_modifyitems(config, items):
    """Auto-apply e2e marker to all tests in this directory."""
    for item in items:
        if "/e2e/" in str(item.fspath) or "\\e2e\\" in str(item.fspath):
            item.add_marker(pytest.mark.e2e)


@pytest.fixture(scope="session")
def e2e_phase() -> str:
    """Return the current E2E phase: 'fresh' or 'resume'."""
    return os.environ.get("E2E_PHASE", "fresh")


@pytest.fixture(scope="session")
def e2e_fixtures_dir() -> str:
    """Return path to E2E fixture files."""
    return os.path.join(os.path.dirname(__file__), "fixtures")


@pytest.fixture(scope="session")
def sample_data_dir() -> str:
    """Return path to shared sample data files."""
    return os.path.join(os.path.dirname(__file__), "fixtures", "sample_data")
