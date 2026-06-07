# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Local auth wiring: ``build_router`` factory + no inline service construction.

The legacy ``auth`` feature was consolidated into ``local_auth`` (a single
``LocalAuthService`` plus a ``build_router(service, ...)`` factory). These
tests pin that contract: the router must be assembled via ``build_router``
(not via FastAPI ``Depends`` factories) and the service itself must never
be constructed inside a route handler.
"""

from __future__ import annotations

import ast
from pathlib import Path


AUTH_API = (
    Path(__file__).resolve().parents[4]
    / "src"
    / "chaoscypher_cortex"
    / "features"
    / "local_auth"
    / "api.py"
)


def test_build_router_factory_exists() -> None:
    """``build_router`` must be the public entry point for the local-auth router."""
    tree = ast.parse(AUTH_API.read_text(encoding="utf-8"))
    top_level_funcs = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
    assert "build_router" in top_level_funcs


def test_no_inline_local_auth_service_instantiation_in_handlers() -> None:
    """No ``LocalAuthService(...)`` call lives inside a route handler.

    The service is constructed once in ``app_factory.py`` and passed into
    ``build_router(service=...)``; handlers close over that captured
    reference. Any inline construction would defeat the single-instance
    invariant.
    """
    tree = ast.parse(AUTH_API.read_text(encoding="utf-8"))
    forbidden = {"LocalAuthService"}
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for inner in ast.walk(node):
            if (
                isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Name)
                and inner.func.id in forbidden
            ):
                offenders.append(
                    f"{node.name} (line {inner.lineno}): direct {inner.func.id}(...) call"
                )
    assert not offenders, "Inline service instantiations remain:\n  " + "\n  ".join(offenders)
