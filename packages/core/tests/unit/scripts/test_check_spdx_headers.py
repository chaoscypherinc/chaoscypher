# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the SPDX-header guard (scripts/check_spdx_headers.py).

The guard fails if any *shipped* source file is missing the
``SPDX-License-Identifier: AGPL-3.0-only`` header. These tests pin the scan
scope to the full shipped set — ``packages/*/src``, ``scripts/``, ``tools/``,
and ``e2e/`` — with the semgrep rule fixtures explicitly excluded (their
``ruleid:`` line positions are load-bearing for rule-match assertions, so
inserting header lines would break the rule self-tests).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_guard():
    """Import scripts/check_spdx_headers.py as a module."""
    script_path = Path(__file__).resolve().parents[5] / "scripts" / "check_spdx_headers.py"
    spec = importlib.util.spec_from_file_location("check_spdx_headers", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_GUARD = _load_guard()

_HEADER = "# Copyright (C) 2024-2026 Chaos Cypher, Inc.\n# SPDX-License-Identifier: AGPL-3.0-only\n"


def _missing(repo_root: Path) -> list[str]:
    return [str(path) for path in _GUARD.find_missing(repo_root)]


def test_finds_missing_header_in_packages_src(tmp_path: Path) -> None:
    src = tmp_path / "packages" / "core" / "src"
    src.mkdir(parents=True)
    (src / "thing.py").write_text("x = 1\n", encoding="utf-8")
    assert any("thing.py" in p for p in _missing(tmp_path))


def test_finds_missing_header_in_scripts(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "helper.py").write_text('#!/usr/bin/env python3\n"""Doc."""\n', encoding="utf-8")
    assert any("helper.py" in p for p in _missing(tmp_path))


def test_finds_missing_header_in_tools(tmp_path: Path) -> None:
    tools = tmp_path / "tools" / "licensing"
    tools.mkdir(parents=True)
    (tools / "policy_check.py").write_text("x = 1\n", encoding="utf-8")
    assert any("policy_check.py" in p for p in _missing(tmp_path))


def test_finds_missing_header_in_e2e(tmp_path: Path) -> None:
    e2e = tmp_path / "e2e" / "api"
    e2e.mkdir(parents=True)
    (e2e / "test_smoke.py").write_text("x = 1\n", encoding="utf-8")
    assert any("test_smoke.py" in p for p in _missing(tmp_path))


def test_semgrep_fixtures_are_excluded(tmp_path: Path) -> None:
    # Rule fixtures are deliberately header-free: their ruleid: annotations
    # assert on specific line numbers, which a header insertion would shift.
    fixtures = tmp_path / "tools" / "semgrep" / "tests"
    fixtures.mkdir(parents=True)
    (fixtures / "cc-999-example-rule.py").write_text("bad_pattern()\n", encoding="utf-8")
    assert _missing(tmp_path) == []


def test_file_with_header_passes(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "helper.py").write_text(_HEADER + "x = 1\n", encoding="utf-8")
    assert _missing(tmp_path) == []


def test_header_after_shebang_passes(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "helper.py").write_text(
        "#!/usr/bin/env python3\n" + _HEADER + "x = 1\n", encoding="utf-8"
    )
    assert _missing(tmp_path) == []


def test_skips_unreadable_file(tmp_path: Path) -> None:
    # A non-UTF-8 blob must be skipped (exercises the except branch), not crash.
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "blob.py").write_bytes(b"\xff\xfe\x00\x01 x = 1")
    assert _missing(tmp_path) == []
