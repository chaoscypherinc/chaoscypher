# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The MCP benchmark driver records the client's version, not a word of its banner."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _load_driver():
    script_path = Path(__file__).resolve().parents[5] / "scripts/benchmark/run_mcp_suite.py"
    spec = importlib.util.spec_from_file_location("run_mcp_suite", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


driver = _load_driver()


@pytest.mark.parametrize(
    ("banner", "expected"),
    [
        ("2.1.286 (Claude Code)", "2.1.286"),  # recorded as "Code)" before 2026-10-01
        ("codex-cli 0.48.0", "0.48.0"),
        ("claude 2.2.0-beta.1", "2.2.0-beta.1"),
        ("nightly", "nightly"),
        ("", "unknown"),
    ],
)
def test_parse_version_takes_the_version_token(banner: str, expected: str) -> None:
    assert driver._parse_version(banner) == expected
