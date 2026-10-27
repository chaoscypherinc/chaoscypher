# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""``is_memory_source``: the one test of whether a source row is a memory."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from chaoscypher_core.models import is_memory_source


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        ({"id": "m", "source_type": "memory"}, True),
        ({"id": "d", "source_type": None}, False),  # most rows predate the column
        ({"id": "d"}, False),  # a projection that does not read it
        ({"id": "p", "source_type": "pack"}, False),
        (SimpleNamespace(source_type="memory"), True),  # an ORM row
        (SimpleNamespace(source_type=None), False),
    ],
)
def test_is_memory_source(row: Any, expected: bool) -> None:
    assert is_memory_source(row) is expected
