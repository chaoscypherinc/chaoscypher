# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Smoke tests for template_visuals at its canonical home.

Canonical location is ``chaoscypher_core.templates.visuals``. The Phase 1
shim at ``chaoscypher_core.services.sources.engine.extraction.utils.template_visuals``
has been deleted; these assertions just confirm the canonical module
still loads and self-reports the right ``__module__`` on its callables.
"""

import importlib
from pathlib import Path


def test_canonical_module_loads():
    from chaoscypher_core.templates import visuals

    assert visuals is not None


def test_canonical_module_path_attribute():
    """Functions/classes defined in visuals.py should report the canonical __module__."""
    from chaoscypher_core.templates import visuals

    exported = getattr(visuals, "__all__", None) or [
        n for n in dir(visuals) if not n.startswith("_")
    ]
    defined_here = [getattr(visuals, n) for n in exported if callable(getattr(visuals, n))]
    home_names = {obj.__module__ for obj in defined_here if hasattr(obj, "__module__")}
    assert "chaoscypher_core.templates.visuals" in home_names, (
        f"no callable in visuals.py reports canonical __module__; got {home_names}"
    )


def test_old_shim_path_is_gone():
    """The Phase 1 template_visuals shim under services/sources/... is deleted."""
    shim_path = (
        Path(__file__).resolve().parents[4]
        / "src"
        / "chaoscypher_core"
        / "services"
        / "sources"
        / "engine"
        / "extraction"
        / "utils"
        / "template_visuals.py"
    )
    assert not shim_path.exists(), f"Shim still present at {shim_path}"

    try:
        importlib.import_module(
            "chaoscypher_core.services.sources.engine.extraction.utils.template_visuals"
        )
    except ModuleNotFoundError:
        pass
    else:  # pragma: no cover
        msg = "The deleted template_visuals shim is somehow still importable."
        raise AssertionError(msg)
