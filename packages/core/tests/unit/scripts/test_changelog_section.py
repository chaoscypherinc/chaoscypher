# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for scripts/changelog_section.py — release notes from the docs changelog."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _load_module():
    """Import scripts/changelog_section.py as a module."""
    script_path = Path(__file__).resolve().parents[5] / "scripts" / "changelog_section.py"
    spec = importlib.util.spec_from_file_location("changelog_section", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_MOD = _load_module()

_CHANGELOG = """---
id: changelog
---

# Changelog

## Recent Changes

### September 2026

#### v0.4.2 (2026-09-06)

A small patch release.

##### Fixes

- **Thing one** — fixed.
- **Thing two** — also fixed.

### August 2026

#### v0.4.1 (2026-08-25)

A large fixes-only patch release.

##### Security

- **Leak** — closed.
"""


def test_extract_section_stops_at_the_next_release_or_month():
    """The section body runs from the version heading to the next h1-h4 heading."""
    body = _MOD.extract_section(_CHANGELOG, "v0.4.2")
    assert body is not None
    assert body.startswith("A small patch release.")
    assert "##### Fixes" in body
    assert "Thing two" in body
    assert "August 2026" not in body
    assert "v0.4.1" not in body


def test_extract_section_accepts_bare_versions_and_the_last_section():
    """``0.4.1`` matches ``#### v0.4.1`` and the final section runs to EOF."""
    body = _MOD.extract_section(_CHANGELOG, "0.4.1")
    assert body is not None
    assert body.endswith("- **Leak** — closed.")


def test_extract_section_returns_none_for_an_unknown_version():
    """A version with no heading yields ``None`` rather than a neighbour's notes."""
    assert _MOD.extract_section(_CHANGELOG, "v0.4.10") is None
    assert _MOD.extract_section(_CHANGELOG, "v9.9.9") is None


def test_release_notes_appends_the_changelog_link():
    """Release bodies end with the canonical changelog URL."""
    notes = _MOD.release_notes(_CHANGELOG, "v0.4.2")
    assert notes is not None
    assert notes.rstrip().endswith(f"Full changelog: {_MOD.CHANGELOG_URL}")


def test_main_prints_notes_and_fails_loudly_when_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    """``main`` writes the notes to stdout and exits 1 for an unknown tag."""
    changelog = tmp_path / "changelog.md"
    changelog.write_text(_CHANGELOG, encoding="utf-8")

    assert _MOD.main(["v0.4.2", "--changelog", str(changelog)]) == 0
    out = capsys.readouterr().out
    assert "Thing one" in out

    assert _MOD.main(["v0.0.0", "--changelog", str(changelog)]) == 1
    assert "no changelog section for v0.0.0" in capsys.readouterr().err
