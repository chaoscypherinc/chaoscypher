# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Conftest for the migration-roundtrip e2e tier.

Intentionally separate from ``e2e/api/conftest.py`` because the
migration test brings up its own isolated stack on port 8889 with a
pre-seeded snapshot. The api conftest's autouse fixtures (cookie
auth, requires_llm skip) would otherwise pull in the default
``http://localhost:8888`` stack and fail with Connection refused.
"""

import pytest


def pytest_collection_modifyitems(config, items):
    """Auto-apply markers used elsewhere in the e2e tree."""
    for item in items:
        path = str(item.fspath).replace("\\", "/")
        if "/migrations/" in path:
            item.add_marker(pytest.mark.migrations)


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "migrations: marks tests that boot a self-contained snapshot stack.",
    )
