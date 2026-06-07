# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Shared integration-test fixtures.

Re-exports ``integration_adapter`` from the sources/ subpackage so any
``packages/core/tests/integration/**`` test can request it via pytest's
parent-directory conftest discovery without needing an explicit import.

Lives at this level (instead of letting tests import directly from
``sources/conftest``) so the package test tree stays independent of the
root ``tests/`` namespace — this is what lets PR2 rename ``tests/`` → ``e2e/``
without touching any package test.
"""

from .sources.conftest import integration_adapter


__all__ = ["integration_adapter"]
