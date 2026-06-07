# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Pin QueuedResetResponse to shared/api/responses, not features/settings.

QueuedResetResponse is the canonical 202-queued envelope used by both
the settings reset routes and the graph cleanup route. Living in
features/settings/models forced graph to do a cross-feature import,
breaking the VSA slice boundary. This test pins the move so it can't
silently regress.
"""

import ast
import inspect

from chaoscypher_cortex.shared.api.responses import QueuedResetResponse


def _assert_imported_from(
    module: object,
    *,
    symbol: str,
    expected_module: str,
    forbidden_module_prefixes: tuple[str, ...],
) -> None:
    """Walk the module's AST and assert which `from X import …` brought in `symbol`.

    ``forbidden_module_prefixes`` matches both exact module names and any
    submodule rooted under that prefix. So passing
    ``("chaoscypher_cortex.features.settings",)`` rejects imports from the
    package itself (via barrel re-export) AND any
    ``chaoscypher_cortex.features.settings.<sub>`` submodule.
    """
    src = inspect.getsource(module)
    tree = ast.parse(src)
    found_in: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                imported_name = alias.asname or alias.name
                if imported_name == symbol:
                    found_in.add(node.module)
    assert expected_module in found_in, (
        f"Expected {symbol!r} to be imported from {expected_module!r}, got: {sorted(found_in)}"
    )
    forbidden_hits = sorted(
        m
        for m in found_in
        if any(m == prefix or m.startswith(prefix + ".") for prefix in forbidden_module_prefixes)
    )
    assert not forbidden_hits, (
        f"{symbol!r} must not be imported from any of "
        f"{sorted(forbidden_module_prefixes)!r}; "
        f"VSA boundary violation. Found imports from: {forbidden_hits}"
    )


def test_queued_reset_response_lives_in_shared_api_responses():
    module = inspect.getmodule(QueuedResetResponse)
    assert module is not None
    assert module.__name__ == "chaoscypher_cortex.shared.api.responses"


def test_features_graph_api_imports_from_shared():
    """graph/api.py must import QueuedResetResponse from shared, not from features.settings."""
    from chaoscypher_cortex.features.graph import api

    assert "QueuedResetResponse" in inspect.getsource(api)
    _assert_imported_from(
        api,
        symbol="QueuedResetResponse",
        expected_module="chaoscypher_cortex.shared.api.responses",
        forbidden_module_prefixes=("chaoscypher_cortex.features.settings",),
    )


def test_features_settings_api_imports_from_shared():
    """settings/api.py must import QueuedResetResponse from shared, not from its own .models."""
    from chaoscypher_cortex.features.settings import api

    assert "QueuedResetResponse" in inspect.getsource(api)
    _assert_imported_from(
        api,
        symbol="QueuedResetResponse",
        expected_module="chaoscypher_cortex.shared.api.responses",
        forbidden_module_prefixes=("chaoscypher_cortex.features.settings",),
    )
