# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Smoke test for the in_memory_adapter fixture."""


def test_in_memory_adapter_resolves(in_memory_adapter) -> None:
    """Fixture resolves and the adapter is connected."""
    assert in_memory_adapter is not None
    assert in_memory_adapter._connected is True
    assert in_memory_adapter.database_name == "default"
