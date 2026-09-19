# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the media-manifest gate (scripts/check_media_manifest.py).

Every committed screenshot must have a manifest row with a verdict, and no
``broken`` screenshot may be referenced by the docs site, the homepage, the
blog, or a queued marketing entry. The manifest lives in the private company
tree, so the gate is a no-op when that tree is absent (public export).
"""

from __future__ import annotations

import datetime as dt
import importlib.util
from pathlib import Path


def _load_gate():
    script_path = Path(__file__).resolve().parents[5] / "scripts" / "check_media_manifest.py"
    spec = importlib.util.spec_from_file_location("check_media_manifest", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_GATE = _load_gate()
_TODAY = dt.date(2026, 9, 15)


def _repo(tmp_path: Path, manifest_rows: str | None, shots: tuple[str, ...] = ("a.png",)) -> Path:
    shots_dir = tmp_path / "packages" / "docs" / "static" / "img" / "screenshots"
    shots_dir.mkdir(parents=True)
    for name in shots:
        (shots_dir / name).write_bytes(b"\x89PNG")
    if manifest_rows is not None:
        company = tmp_path / "internal" / "company"
        company.mkdir(parents=True)
        (company / "media-library.md").write_text(
            "# Media library\n\n"
            "| file | verdict | what it shows (alt text) | verified |\n"
            "|---|---|---|---|\n" + manifest_rows,
            encoding="utf-8",
        )
    return tmp_path


def _row(name: str, verdict: str, verified: str = "2026-09-15") -> str:
    return f"| {name} | {verdict} | something | {verified} |\n"


def _doc(repo: Path, rel: str, text: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_no_manifest_means_no_op(tmp_path: Path) -> None:
    repo = _repo(tmp_path, manifest_rows=None)
    errors, warnings = _GATE.find_problems(repo, today=_TODAY)
    assert errors == [] and warnings == []


def test_clean_library_passes(tmp_path: Path) -> None:
    repo = _repo(tmp_path, _row("a.png", "ok"))
    _doc(repo, "packages/docs/docs/page.md", "![alt](/img/screenshots/a.png)\n")
    errors, warnings = _GATE.find_problems(repo, today=_TODAY)
    assert errors == [] and warnings == []


def test_unlisted_screenshot_is_an_error(tmp_path: Path) -> None:
    repo = _repo(tmp_path, _row("a.png", "ok"), shots=("a.png", "b.png"))
    errors, _ = _GATE.find_problems(repo, today=_TODAY)
    assert any("b.png" in e and "no manifest row" in e for e in errors)


def test_manifest_row_for_missing_file_is_an_error(tmp_path: Path) -> None:
    repo = _repo(tmp_path, _row("a.png", "ok") + _row("gone.png", "ok"))
    errors, _ = _GATE.find_problems(repo, today=_TODAY)
    assert any("gone.png" in e and "does not exist" in e for e in errors)


def test_broken_screenshot_referenced_by_docs_is_an_error(tmp_path: Path) -> None:
    repo = _repo(tmp_path, _row("a.png", 'broken: "service unavailable" error card'))
    _doc(repo, "packages/docs/docs/lexicon.md", "![alt](/img/screenshots/a.png)\n")
    errors, _ = _GATE.find_problems(repo, today=_TODAY)
    assert any("a.png" in e and "broken" in e and "lexicon.md" in e for e in errors)


def test_broken_screenshot_referenced_by_homepage_and_queue_is_an_error(tmp_path: Path) -> None:
    repo = _repo(tmp_path, _row("a.png", "broken: empty state"))
    _doc(repo, "packages/docs/src/pages/index.tsx", 'src="/img/screenshots/a.png"\n')
    queue = repo / "internal" / "company" / "outreach-queue"
    _doc(repo, str(queue.relative_to(repo) / "entry.md"), "- image: `.../screenshots/a.png`\n")
    errors, _ = _GATE.find_problems(repo, today=_TODAY)
    assert sum("a.png" in e and "broken" in e for e in errors) == 2


def test_broken_screenshot_unreferenced_is_not_an_error(tmp_path: Path) -> None:
    repo = _repo(tmp_path, _row("a.png", "broken: empty state"))
    errors, _ = _GATE.find_problems(repo, today=_TODAY)
    assert errors == []


def test_reference_to_nonexistent_screenshot_is_an_error(tmp_path: Path) -> None:
    repo = _repo(tmp_path, _row("a.png", "ok"))
    _doc(repo, "packages/docs/blog/post.md", "![alt](/img/screenshots/nope.png)\n")
    errors, _ = _GATE.find_problems(repo, today=_TODAY)
    assert any("nope.png" in e and "does not exist" in e and "post.md" in e for e in errors)


def test_manifest_itself_is_not_scanned_for_references(tmp_path: Path) -> None:
    repo = _repo(tmp_path, _row("a.png", "broken: error card"))
    manifest = repo / "internal" / "company" / "media-library.md"
    manifest.write_text(
        manifest.read_text(encoding="utf-8") + "\nSee screenshots/a.png above.\n", encoding="utf-8"
    )
    errors, _ = _GATE.find_problems(repo, today=_TODAY)
    assert errors == []


def test_stale_verified_date_is_a_warning_not_an_error(tmp_path: Path) -> None:
    repo = _repo(tmp_path, _row("a.png", "ok", verified="2026-01-01"))
    errors, warnings = _GATE.find_problems(repo, today=_TODAY)
    assert errors == []
    assert any("a.png" in w and "days" in w for w in warnings)


def test_docs_only_is_fine_in_docs_but_not_in_a_queue_entry(tmp_path: Path) -> None:
    repo = _repo(tmp_path, _row("a.png", "docs-only: clipped title"))
    _doc(repo, "packages/docs/docs/page.md", "![alt](/img/screenshots/a.png)\n")
    errors, _ = _GATE.find_problems(repo, today=_TODAY)
    assert errors == []
    queue = repo / "internal" / "company" / "outreach-queue"
    _doc(repo, str(queue.relative_to(repo) / "entry.md"), "- image: `.../screenshots/a.png`\n")
    errors, _ = _GATE.find_problems(repo, today=_TODAY)
    assert len(errors) == 1 and "docs-only" in errors[0] and "entry.md" in errors[0]


def test_only_the_file_table_is_parsed(tmp_path: Path) -> None:
    repo = _repo(tmp_path, _row("a.png", "ok"))
    manifest = repo / "internal" / "company" / "media-library.md"
    prose_table = "| verdict | meaning |\n|---|---|\n| ok | fine |\n| broken: <why> | bad |\n\n"
    manifest.write_text(
        prose_table + manifest.read_text(encoding="utf-8") + "\n| stray | row |\n",
        encoding="utf-8",
    )
    assert _GATE.parse_manifest(manifest.read_text(encoding="utf-8")) == {
        "a.png": ("ok", "2026-09-15")
    }
    errors, warnings = _GATE.find_problems(repo, today=_TODAY)
    assert errors == [] and warnings == []


def test_bad_verdict_word_is_an_error(tmp_path: Path) -> None:
    repo = _repo(tmp_path, _row("a.png", "fine"))
    errors, _ = _GATE.find_problems(repo, today=_TODAY)
    assert any("a.png" in e and "verdict" in e for e in errors)


def test_main_exit_codes(tmp_path: Path, capsys) -> None:
    repo = _repo(tmp_path, _row("a.png", "ok"), shots=("a.png", "b.png"))
    assert _GATE.main(["--root", str(repo)]) == 1
    assert "b.png" in capsys.readouterr().out
    (repo / "packages" / "docs" / "static" / "img" / "screenshots" / "b.png").unlink()
    assert _GATE.main(["--root", str(repo)]) == 0
