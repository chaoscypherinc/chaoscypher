# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Execute copy-paste examples from ``packages/core/README.md``.

The Core README is the documented entry point for library users and is
published to PyPI, so its runnable examples must actually run.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


README = Path(__file__).resolve().parents[2] / "README.md"


def _python_block_after(heading: str) -> str:
    """Return the first ```python block that follows ``heading`` in the README."""
    text = README.read_text(encoding="utf-8")
    start = text.index(heading)
    match = re.search(r"```python\n(.*?)```", text[start:], re.DOTALL)
    assert match is not None, f"no python block after {heading!r}"
    return match.group(1)


def test_direct_service_access_example_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The "Advanced: Direct Service Access" snippet runs end to end."""
    monkeypatch.setenv("CHAOSCYPHER_DATA_DIR", str(tmp_path))
    code = _python_block_after("### Advanced: Direct Service Access")

    namespace: dict[str, object] = {}
    exec(compile(code, str(README), "exec"), namespace)  # noqa: S102

    nodes = namespace["nodes"]
    assert isinstance(nodes, dict)
    assert sorted(n["label"] for n in nodes["data"]) == ["Alice", "Bob"]
