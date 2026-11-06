# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the frontend license scan's coverage guard (scripts/license_check_interface.py).

The guard fails the scan when the checker's output is missing a direct
production dependency, so a scanner that silently reads only part of the
tree cannot pass the gate.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_script():
    """Import scripts/license_check_interface.py as a module."""
    script_path = Path(__file__).resolve().parents[5] / "scripts" / "license_check_interface.py"
    spec = importlib.util.spec_from_file_location("license_check_interface", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_SCRIPT = _load_script()


def test_full_scan_has_no_missing_direct_dependencies() -> None:
    scanned = {"react@19.0.0": {}, "@mui/material@9.5.0": {}, "dom-helpers@5.2.1": {}}
    package_json = {"dependencies": {"react": "^19.0.0", "@mui/material": "^9.5.0"}}
    assert _SCRIPT.missing_direct_dependencies(scanned, package_json) == []


def test_partial_scan_reports_missing_direct_dependencies() -> None:
    # The shape license-checker-rseidelsohn 4.x produced: one stray package.
    scanned = {"dom-helpers@5.2.1": {}}
    package_json = {"dependencies": {"react": "^19.0.0", "@mui/material": "^9.5.0"}}
    assert _SCRIPT.missing_direct_dependencies(scanned, package_json) == [
        "@mui/material",
        "react",
    ]


def test_real_interface_package_json_lists_dependencies() -> None:
    # The guard is only meaningful if the real manifest has production deps.
    package_json = _SCRIPT.json.loads(
        (_SCRIPT.INTERFACE_DIR / "package.json").read_text(encoding="utf-8")
    )
    assert package_json["dependencies"]
    assert _SCRIPT.missing_direct_dependencies({}, package_json) == sorted(
        package_json["dependencies"]
    )
