#!/usr/bin/env python3
# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Fail if a committed screenshot is unaudited, or a broken one is still referenced.

Every file under ``packages/docs/static/img/screenshots/`` must have a row in
the media-library manifest (kept in the private company tree — the path is
assembled below so this file never carries the private literal) with a
verdict a human or a routine wrote after *looking at the image*: ``ok``, or
``broken: <why>``. A ``broken`` screenshot may stay in the tree, but nothing
that ships or posts may reference it: the docs site, the blog, the homepage,
the README, or a queued marketing entry. A ``docs-only: <why>`` screenshot
(a cosmetic capture flaw — clipped title, stray tooltip, overlapping labels)
may keep its place in the docs and homepage until re-captured, but no
marketing entry under the company tree may attach it.

Why: on 2026-09-11 a Bluesky post went out with ``lexicon-hub.png`` attached
— a capture of the "Lexicon service unavailable" error card that the
screenshot-refresh procedure had documented as uncapturable locally, that the
docs and homepage had shipped for months, and that the marketing picking
table pointed every Lexicon post at. Nothing had ever looked at the file.
This gate makes "someone looked" a recorded fact and blocks the reference
path mechanically. Wired into the ``lint-internal-refs`` CI step so the
routines' own sweep runs it; pre-commit alone is not enough in the sandbox.

No-ops when the manifest is absent: the private tree is stripped from the
public export, where this script still ships.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from collections.abc import Iterator
from pathlib import Path


_SCREENSHOTS = Path("packages") / "docs" / "static" / "img" / "screenshots"
_MANIFEST = Path("internal") / "company" / "media-library.md"
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
_REF = re.compile(r"screenshots/([A-Za-z0-9_.-]+\.(?:png|jpe?g|gif|webp))\b")
_TEXT_SUFFIXES = {".md", ".mdx", ".tsx", ".ts", ".jsx", ".js", ".html"}
_STALE_AFTER_DAYS = 90
_VERDICT = re.compile(r"^(ok|docs-only(?::.+)?|broken(?::.+)?)$")


def _company_dir(repo_root: Path) -> Path:
    return repo_root / "internal" / "company"


def _scan_roots(repo_root: Path) -> list[Path]:
    """Trees whose image references must resolve to an ``ok`` screenshot."""
    docs = repo_root / "packages" / "docs"
    return [
        docs / "docs",
        docs / "blog",
        docs / "src",
        repo_root / "README.md",
        _company_dir(repo_root),
    ]


def parse_manifest(text: str) -> dict[str, tuple[str, str | None]]:
    """Return ``{file: (verdict, verified-date-or-None)}`` from the manifest table."""
    rows: dict[str, tuple[str, str | None]] = {}
    in_table = False  # only the table whose header's first cell is `file` holds rows
    for line in text.splitlines():
        if not line.startswith("|"):
            in_table = False
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if cells and cells[0] == "file":
            in_table = True
            continue
        if not in_table or len(cells) < 2 or set(cells[0]) <= set("-: "):
            continue
        name = cells[0].strip("`")
        verified = cells[-1] if re.fullmatch(r"\d{4}-\d{2}-\d{2}", cells[-1]) else None
        rows[name] = (cells[1], verified)
    return rows


def _iter_reference_files(repo_root: Path, manifest: Path) -> Iterator[Path]:
    for root in _scan_roots(repo_root):
        if root.is_file():
            yield root
            continue
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if path.is_file() and path.suffix in _TEXT_SUFFIXES and path != manifest:
                yield path


def find_problems(repo_root: Path, today: dt.date | None = None) -> tuple[list[str], list[str]]:
    """Return ``(errors, warnings)``; errors are gate failures."""
    today = today or dt.datetime.now(tz=dt.UTC).date()
    manifest = repo_root / _MANIFEST
    if not manifest.is_file():
        return [], []
    rows = parse_manifest(manifest.read_text(encoding="utf-8"))
    shots_dir = repo_root / _SCREENSHOTS
    on_disk: set[str] = set()
    if shots_dir.is_dir():
        on_disk = {
            p.name for p in shots_dir.iterdir() if p.is_file() and p.suffix in _IMAGE_SUFFIXES
        }

    errors: list[str] = []
    warnings: list[str] = []
    errors.extend(
        f"{name}: no manifest row — view it and record a verdict"
        for name in sorted(on_disk - rows.keys())
    )
    for name, (verdict, verified) in sorted(rows.items()):
        if name not in on_disk:
            errors.append(f"{name}: manifest row but the file does not exist")
        if not _VERDICT.match(verdict):
            errors.append(
                f"{name}: verdict must be `ok`, `docs-only: <why>` or `broken: <why>`, "
                f"got `{verdict}`"
            )
        if verified is None:
            warnings.append(f"{name}: no verified date")
        else:
            age = (today - dt.date.fromisoformat(verified)).days
            if age > _STALE_AFTER_DAYS:
                warnings.append(f"{name}: verified {age} days ago — view it again")

    for path in _iter_reference_files(repo_root, manifest):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError, UnicodeDecodeError:
            continue
        rel = path.relative_to(repo_root).as_posix()
        for name in sorted(set(_REF.findall(text))):
            if name not in on_disk:
                errors.append(f"{rel}: references screenshots/{name}, which does not exist")
                continue
            verdict = rows.get(name, ("", None))[0]
            if verdict.startswith("broken"):
                errors.append(f"{rel}: references screenshots/{name}, which is broken ({verdict})")
            elif verdict.startswith("docs-only") and path.is_relative_to(_company_dir(repo_root)):
                errors.append(
                    f"{rel}: references screenshots/{name}, which is docs-only — "
                    f"not attachable to a post ({verdict})"
                )
    return errors, warnings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parent.parent, help="repo root"
    )
    args = parser.parse_args(argv)
    errors, warnings = find_problems(args.root)
    for w in warnings:
        print(f"check_media_manifest: warning: {w}")
    for e in errors:
        print(f"check_media_manifest: ERROR: {e}")
    if errors:
        print(f"check_media_manifest: FAIL ({len(errors)} problem(s))")
        return 1
    print("check_media_manifest: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
