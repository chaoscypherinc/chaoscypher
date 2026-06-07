# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""enable_normalization should default based on content type."""

from __future__ import annotations

import pytest

from chaoscypher_core.utils.normalization_default import (
    resolve_normalization_default,
)


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("notes.md", True),
        ("paper.pdf", True),
        ("data.csv", False),
        ("manifest.json", False),
        ("audio.mp3", True),
        ("table.tsv", False),
        ("logs.jsonl", False),
        ("config.xml", False),
    ],
)
def test_resolve_normalization_default(filename: str, expected: bool):
    assert resolve_normalization_default(filename=filename) is expected
