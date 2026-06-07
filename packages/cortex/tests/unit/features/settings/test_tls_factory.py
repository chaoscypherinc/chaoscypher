# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Phase 4 Task B: ``get_tls_service`` factory replaces inline TLSService()."""

from __future__ import annotations

import ast
from pathlib import Path


SETTINGS_API = (
    Path(__file__).resolve().parents[4]
    / "src"
    / "chaoscypher_cortex"
    / "features"
    / "settings"
    / "api.py"
)


def test_get_tls_service_factory_exists() -> None:
    """``get_tls_service`` factory is defined and follows CC001 naming."""
    tree = ast.parse(SETTINGS_API.read_text(encoding="utf-8"))
    factory_names = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and node.name.startswith("get_")
        and node.name.endswith("_service")
    }
    assert "get_tls_service" in factory_names


def test_no_inline_tls_service_instantiation() -> None:
    """AST scan: no ``TLSService(...)`` call remains inside route handlers.

    The only permitted construction point is inside ``get_tls_service``
    itself (the factory). Route handlers receive the service via
    ``Depends(get_tls_service)``.
    """
    tree = ast.parse(SETTINGS_API.read_text(encoding="utf-8"))
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        if node.name == "get_tls_service":
            continue
        for inner in ast.walk(node):
            if (
                isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Name)
                and inner.func.id == "TLSService"
            ):
                offenders.append(f"{node.name} (line {inner.lineno}): direct TLSService(...) call")
    assert not offenders, "Inline TLSService instantiations remain:\n  " + "\n  ".join(offenders)
