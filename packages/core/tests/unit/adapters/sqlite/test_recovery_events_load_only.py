# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Verify list_recovery_events uses load_only() (CC003)."""

from __future__ import annotations

import re
from pathlib import Path


def test_list_recovery_events_uses_load_only() -> None:
    """Source contains a load_only(...) call in list_recovery_events."""
    src = Path(
        "packages/core/src/chaoscypher_core/adapters/sqlite/mixins/source_recovery_events.py"
    ).read_text(encoding="utf-8")

    # Find the list_recovery_events function body.
    match = re.search(
        r"def list_recovery_events\([^)]*\)[^:]*:.*?(?=\n    def |\Z)",
        src,
        re.DOTALL,
    )
    assert match, "list_recovery_events function not found"
    body = match.group(0)

    assert "load_only(" in body, "CC003 violation: list_recovery_events lacks load_only()"
