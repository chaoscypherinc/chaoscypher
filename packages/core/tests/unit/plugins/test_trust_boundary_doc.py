# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Smoke tests that verify the trust-boundary documentation exists
and contains the required warnings. These tests exist to prevent the
file from being accidentally deleted or emptied during refactors.
"""

from pathlib import Path

import chaoscypher_core.plugins as plugins_pkg


TRUST_DOC = Path(plugins_pkg.__file__).parent / "TRUST_BOUNDARY.md"


def test_trust_boundary_file_exists() -> None:
    assert TRUST_DOC.is_file(), f"expected {TRUST_DOC} to exist"


def test_trust_boundary_file_mentions_exec_module() -> None:
    text = TRUST_DOC.read_text(encoding="utf-8")
    assert "exec_module" in text, "trust doc must mention exec_module"


def test_trust_boundary_file_mentions_disable_env_var() -> None:
    text = TRUST_DOC.read_text(encoding="utf-8")
    assert "CHAOSCYPHER_ALLOW_USER_PLUGINS" in text


def test_base_module_docstring_points_at_trust_doc() -> None:
    import chaoscypher_core.plugins.base as base_mod

    assert "TRUST_BOUNDARY.md" in (base_mod.__doc__ or "")
