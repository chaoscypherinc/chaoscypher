# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the actionability-aware npm audit gate (scripts/npm_audit_gate.py).

The gate exists so an upstream advisory with **no published fix** cannot halt
every push, while anything with a bump available still blocks. The load-bearing
behaviour is the split, so these tests pin both directions — a gate that only
ever passes would be worse than no gate at all.

The propagation case is the subtle one: npm emits an entry per affected
package, and a downstream entry can claim ``fixAvailable: true`` while the root
advisory it inherits has no fix (observed 2026-08-07, where npm reported fixes
for 8 ``@docusaurus/*`` packages whose only advisory came from ``image-size``
and ``npm audit fix`` cleared none). Classifying those would block on something
nothing can clear, so only advisory-carrying roots are classified.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest


def _load_gate():
    """Import scripts/npm_audit_gate.py as a module."""
    script_path = Path(__file__).resolve().parents[5] / "scripts" / "npm_audit_gate.py"
    spec = importlib.util.spec_from_file_location("npm_audit_gate", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _root(severity: str, fix: Any, title: str = "boom") -> dict:
    """Build a vulnerability entry that carries its own advisory."""
    return {
        "severity": severity,
        "range": "*",
        "via": [{"title": title, "url": f"https://github.com/advisories/{title}"}],
        "fixAvailable": fix,
    }


def _downstream(severity: str, fix: Any, root_name: str) -> dict:
    """Build an entry that merely depends on another vulnerable package."""
    return {"severity": severity, "range": "*", "via": [root_name], "fixAvailable": fix}


def test_unfixable_root_is_reported_but_not_blocking() -> None:
    """A high root with no published fix must not block."""
    gate = _load_gate()
    report = {"vulnerabilities": {"image-size": _root("high", False)}}

    fixable, unfixable, propagated = gate.classify(report, "high")

    assert fixable == []
    assert [r["name"] for r in unfixable] == ["image-size"]
    assert propagated == 0


def test_fixable_root_still_blocks() -> None:
    """The gate must keep failing when a bump is available — otherwise it is decorative."""
    gate = _load_gate()
    report = {
        "vulnerabilities": {
            "nanoid": _root(
                "high", {"name": "nanoid", "version": "5.1.16", "isSemVerMajor": False}
            ),
        }
    }

    fixable, unfixable, _ = gate.classify(report, "high")

    assert [r["name"] for r in fixable] == ["nanoid"]
    assert unfixable == []


def test_downstream_entries_do_not_block_when_root_is_unfixable() -> None:
    """Regression: npm marks downstream packages fixable even when the root is not.

    Without this rule the 2026-08-07 image-size advisory blocked every push via
    8 ``@docusaurus/*`` entries that no bump could clear.
    """
    gate = _load_gate()
    report = {
        "vulnerabilities": {
            "image-size": _root("high", False),
            "@docusaurus/theme-classic": _downstream("high", True, "image-size"),
            "@docusaurus/plugin-sitemap": _downstream("high", True, "image-size"),
        }
    }

    fixable, unfixable, propagated = gate.classify(report, "high")

    assert fixable == []
    assert [r["name"] for r in unfixable] == ["image-size"]
    assert propagated == 2


def test_a_fixable_root_blocks_even_alongside_an_unfixable_one() -> None:
    """One unfixable finding must not mask a second, actionable one."""
    gate = _load_gate()
    report = {
        "vulnerabilities": {
            "image-size": _root("high", False),
            "nanoid": _root("high", True),
        }
    }

    fixable, unfixable, _ = gate.classify(report, "high")

    assert [r["name"] for r in fixable] == ["nanoid"]
    assert [r["name"] for r in unfixable] == ["image-size"]


def test_severity_floor_excludes_lower_severities() -> None:
    """Moderate findings are below the default floor and are ignored entirely."""
    gate = _load_gate()
    report = {"vulnerabilities": {"dompurify": _root("moderate", True)}}

    fixable, unfixable, propagated = gate.classify(report, "high")

    assert (fixable, unfixable, propagated) == ([], [], 0)


def test_a_downgrade_is_not_treated_as_a_fix() -> None:
    """Regression: npm offers *any* version avoiding the advisory, including older ones.

    Observed 2026-08-07 — the only remediation npm produced for ``image-size``
    was ``@easyops-cn/docusaurus-search-local@0.29.0`` while the project runs
    ``0.55.3``, the latest published. Rolling a dependency back 26 minor
    versions is a regression, so it reports rather than blocks.
    """
    gate = _load_gate()
    report = {
        "vulnerabilities": {
            "image-size": _root(
                "high",
                {
                    "name": "@easyops-cn/docusaurus-search-local",
                    "version": "0.29.0",
                    "isSemVerMajor": True,
                },
            )
        }
    }
    current = {"@easyops-cn/docusaurus-search-local": "0.55.3"}

    fixable, unfixable, _ = gate.classify(report, "high", current)

    assert fixable == []
    assert [r["name"] for r in unfixable] == ["image-size"]
    assert unfixable[0]["downgrade"] is True


def test_a_forward_bump_still_blocks_even_when_semver_major() -> None:
    """A genuine forward upgrade stays blocking — majorness alone is not an excuse."""
    gate = _load_gate()
    report = {
        "vulnerabilities": {
            "leftpad": _root("high", {"name": "leftpad", "version": "2.0.0", "isSemVerMajor": True})
        }
    }

    fixable, _, _ = gate.classify(report, "high", {"leftpad": "1.4.2"})

    assert [r["name"] for r in fixable] == ["leftpad"]


def test_unknown_severity_is_treated_as_blocking() -> None:
    """An unrecognised severity string must fail closed, not silently pass."""
    gate = _load_gate()
    report = {"vulnerabilities": {"mystery": _root("apocalyptic", True)}}

    fixable, _, _ = gate.classify(report, "high")

    assert [r["name"] for r in fixable] == ["mystery"]


class _FakeProc:
    """Minimal stand-in for ``subprocess.CompletedProcess``."""

    def __init__(self, stdout: str, stderr: str = "", returncode: int = 1) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def _patch_npm_output(monkeypatch: pytest.MonkeyPatch, gate: Any, stdout: str) -> None:
    """Make the gate's ``subprocess.run`` hand back ``stdout`` without invoking npm."""
    monkeypatch.setattr(gate.subprocess, "run", lambda *args, **kwargs: _FakeProc(stdout=stdout))


def test_run_audit_fails_closed_on_an_error_shaped_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Regression (#530): a registry 503 is a JSON error body, not a clean report.

    During the 2026-09-04 npm registry outage the audit endpoint returned 503 and
    npm printed ``{"error": {...}}`` on stdout. That parsed, had no
    ``vulnerabilities`` key, classified to zero findings and the gate printed PASS
    while a known high-severity root was present. It must raise instead.
    """
    gate = _load_gate()
    body = json.dumps(
        {"error": {"code": "E503", "summary": "503 Service Unavailable", "detail": "x"}}
    )
    _patch_npm_output(monkeypatch, gate, body)

    with pytest.raises(RuntimeError) as excinfo:
        gate._run_audit(tmp_path)

    assert "E503" in str(excinfo.value)
    assert "503 Service Unavailable" in str(excinfo.value)


def test_run_audit_fails_closed_when_vulnerabilities_key_is_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A report with no ``vulnerabilities`` object is degraded, not clean."""
    gate = _load_gate()
    _patch_npm_output(monkeypatch, gate, json.dumps({"auditReportVersion": 2, "metadata": {}}))

    with pytest.raises(RuntimeError):
        gate._run_audit(tmp_path)


def test_run_audit_fails_closed_when_report_is_not_an_object(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A JSON list parses fine but is not an audit report."""
    gate = _load_gate()
    _patch_npm_output(monkeypatch, gate, json.dumps([]))

    with pytest.raises(RuntimeError):
        gate._run_audit(tmp_path)


def test_run_audit_returns_a_clean_report_unchanged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An empty ``vulnerabilities`` object is a valid clean report and passes through as-is."""
    gate = _load_gate()
    report = {
        "auditReportVersion": 2,
        "vulnerabilities": {},
        "metadata": {"vulnerabilities": {"total": 0}},
    }
    _patch_npm_output(monkeypatch, gate, json.dumps(report))

    assert gate._run_audit(tmp_path) == report


def test_main_fails_closed_when_the_audit_cannot_be_evaluated(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """A degraded audit must exit 2 with a clear message, not a traceback or a PASS."""
    gate = _load_gate()
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "package-lock.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(gate.sys, "argv", ["npm_audit_gate.py", str(pkg)])

    def _boom(directory: Path) -> dict:
        raise RuntimeError("boom")

    monkeypatch.setattr(gate, "_run_audit", _boom)

    assert gate.main() == 2
    err = capsys.readouterr().err
    assert "failing closed" in err
    assert "boom" in err
