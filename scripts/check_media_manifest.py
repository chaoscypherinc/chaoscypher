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

``--json`` (2026-09-29) is a metering mode for the metrics collector's media
row: it prints the manifest's row counts as one JSON object and always exits
0 instead of running the gate. Keys and order are the ``media_library`` block
the collector already publishes on its scoreboard (``rows_total``,
``not_ok``, ``broken``, ``docs_only``, ``stale_over_90d``); ``not_ok`` is every
row whose verdict is not exactly ``ok`` (so it also catches a malformed
verdict), and ``stale_over_90d`` is every row whose ``verified`` date is more
than 90 days before ``--now``, or that has no ``verified`` date -- the same two
cases the gate warns "view it again" on.

Usage:  python scripts/check_media_manifest.py [--root PATH]
        python scripts/check_media_manifest.py --json [--root PATH] [--now ISO-UTC]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
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
        except (OSError, UnicodeDecodeError):
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


def media_counts(repo_root: Path, today: dt.date) -> dict[str, object]:
    """Return the collector's ``media_library`` counts, keys in board order."""
    manifest = repo_root / _MANIFEST
    if not manifest.is_file():
        return {"unavailable": f"no manifest at {manifest.resolve()}"}
    rows = parse_manifest(manifest.read_text(encoding="utf-8"))
    verdicts = [verdict for verdict, _ in rows.values()]
    stale = 0
    for _, verified in rows.values():
        if verified is None or (today - dt.date.fromisoformat(verified)).days > _STALE_AFTER_DAYS:
            stale += 1
    return {
        "rows_total": len(rows),
        "not_ok": sum(1 for v in verdicts if v != "ok"),
        "broken": sum(1 for v in verdicts if v.startswith("broken")),
        "docs_only": sum(1 for v in verdicts if v.startswith("docs-only")),
        "stale_over_90d": stale,
    }


def _parse_now(value: str) -> dt.date:
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(dt.UTC).date()
    except ValueError as exc:
        msg = f"--now must be ISO-8601 UTC, got {value!r}"
        raise argparse.ArgumentTypeError(msg) from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parent.parent, help="repo root"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print the media_library counts as JSON and exit 0 (no gate)",
    )
    parser.add_argument(
        "--now",
        type=_parse_now,
        default=None,
        help="reference time for staleness, ISO-8601 UTC (default: now)",
    )
    args = parser.parse_args(argv)
    today = args.now or dt.datetime.now(tz=dt.UTC).date()
    if args.json:
        try:
            counts = media_counts(args.root, today)
        except (OSError, UnicodeDecodeError, ValueError) as exc:  # metering never raises
            counts = {"unavailable": f"{type(exc).__name__}: {exc}"}
        print(json.dumps({"media_library": counts}, indent=2))
        return 0
    errors, warnings = find_problems(args.root, today)
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
