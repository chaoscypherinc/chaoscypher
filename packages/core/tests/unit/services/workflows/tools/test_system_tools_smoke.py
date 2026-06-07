# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Smoke tests for the relocated system_tools module.

Canonical location is `chaoscypher_core.services.workflows.tools.system_tools`.
The Phase 1 shim at `chaoscypher_core.adapters.llm.system_tools` was
deleted in Phase 2; see ``tests/test_phase2_shims_deleted.py`` for the
regression guard against it coming back.
"""


def test_canonical_module_loads():
    from chaoscypher_core.services.workflows.tools import system_tools

    assert system_tools is not None


def test_canonical_module_path():
    """Callables defined in system_tools.py should report the canonical __module__."""
    from chaoscypher_core.services.workflows.tools import system_tools

    if hasattr(system_tools, "__all__"):
        names = system_tools.__all__
    else:
        names = [n for n in dir(system_tools) if not n.startswith("_")]

    callables = [getattr(system_tools, n) for n in names if callable(getattr(system_tools, n))]
    if callables:
        home_modules = {c.__module__ for c in callables if hasattr(c, "__module__")}
        assert "chaoscypher_core.services.workflows.tools.system_tools" in home_modules, (
            f"no callable reports canonical __module__; got {home_modules}"
        )
