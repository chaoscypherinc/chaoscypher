# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Phase 2 Tasks A + B: utility relocation and TaskType port migration.

Asserts that:
- ``extract_content_with_fallback`` lives in ``chaoscypher_core.utils.llm_response``
  and is no longer exported by ``chaoscypher_core.adapters.llm.utils``.
- ``TaskType`` is imported from ``chaoscypher_core.ports.llm`` (not the
  adapter schema) in the three migrating service files.
"""

from __future__ import annotations

import ast
from pathlib import Path


SERVICES_ROOT = Path(__file__).resolve().parents[1] / "src" / "chaoscypher_core" / "services"


def _module_imports(path: Path) -> list[tuple[str, str]]:
    """Return ``(module, name)`` pairs for every ImportFrom in the file."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    pairs: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                pairs.append((node.module, alias.name))
    return pairs


# ---------------------------------------------------------------------------
# Task A — extract_content_with_fallback moved to core.utils.llm_response
# ---------------------------------------------------------------------------


def test_extract_content_with_fallback_importable_from_utils() -> None:
    from chaoscypher_core.utils.llm_response import extract_content_with_fallback

    assert callable(extract_content_with_fallback)


def test_extract_content_with_fallback_removed_from_adapter_utils() -> None:
    import chaoscypher_core.adapters.llm.utils as adapter_utils

    assert not hasattr(adapter_utils, "extract_content_with_fallback"), (
        "adapter utils must not re-export extract_content_with_fallback — "
        "callers must import from chaoscypher_core.utils.llm_response"
    )


def test_extract_content_text_fallback_to_thinking() -> None:
    from chaoscypher_core.utils.llm_response import extract_content_with_fallback

    response = {"content": "", "thinking": "reasoning output"}
    assert extract_content_with_fallback(response) == "reasoning output"


def test_extract_content_json_fallback_pulls_json_from_thinking() -> None:
    from chaoscypher_core.utils.llm_response import extract_content_with_fallback

    response = {"content": "", "thinking": 'Some reasoning then {"answer": 42}.'}
    result = extract_content_with_fallback(response, expected_format="json")
    assert "42" in result


def test_service_callers_import_from_utils_not_adapter() -> None:
    callers = [
        SERVICES_ROOT / "chat" / "engine" / "research.py",
        SERVICES_ROOT / "search" / "engine" / "research.py",
    ]
    for caller in callers:
        pairs = _module_imports(caller)
        adapter_hits = [
            (m, n)
            for m, n in pairs
            if m == "chaoscypher_core.adapters.llm.utils" and n == "extract_content_with_fallback"
        ]
        assert not adapter_hits, f"{caller} still imports from adapter utils"
        assert (
            "chaoscypher_core.utils.llm_response",
            "extract_content_with_fallback",
        ) in pairs, f"{caller} missing port-layer import"


# ---------------------------------------------------------------------------
# Task B — TaskType imports routed through ports.llm
# ---------------------------------------------------------------------------


def test_tasktype_exported_from_ports_llm() -> None:
    from chaoscypher_core.ports.llm import TaskType

    assert hasattr(TaskType, "CHAT")
    assert hasattr(TaskType, "TOOL")


def test_services_import_tasktype_from_ports() -> None:
    callers = [
        SERVICES_ROOT / "chat" / "engine" / "executor.py",
        SERVICES_ROOT / "chat" / "engine" / "research.py",
        SERVICES_ROOT / "search" / "engine" / "research.py",
    ]
    for caller in callers:
        pairs = _module_imports(caller)
        adapter_hits = [
            (m, n)
            for m, n in pairs
            if m == "chaoscypher_core.adapters.llm.schema" and n == "TaskType"
        ]
        assert not adapter_hits, f"{caller} still imports TaskType from adapter"
        assert (
            "chaoscypher_core.ports.llm",
            "TaskType",
        ) in pairs, f"{caller} missing ports.llm TaskType import"
