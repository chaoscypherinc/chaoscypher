# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""The homepage hero's figures come from the board's holding headlines and nowhere else.

``packages/docs/src/lib/heroStats.ts`` is run under node on the committed board and on
altered copies of it (:func:`~chaoscypher_core.testing.docs_modules.judge_boards`): the
hero shows a figure only when the leaderboard page would show its headline, one section
per corpus, each naming its corpus (``test_board_corpora.py`` has the second corpus).
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from chaoscypher_core.testing.docs_modules import judge_boards
from chaoscypher_core.testing.repo_files import repo_file_or_skip


BOARD = "packages/docs/src/data/leaderboard.json"


def _run(tmp_path: Path, boards: dict[str, Any]) -> dict[str, Any]:
    """``heroSections`` for each board, by node on the page's own modules."""
    return judge_boards(tmp_path, {"heroStats": ["heroSections"]}, "heroSections(board)", boards)


def _headlines(board: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {h["key"]: h for h in board["suites"]["chat"]["comparison"]["value"]["headlines"]}


def test_the_hero_shows_the_three_holding_headlines_in_order(tmp_path: Path) -> None:
    board = json.loads(repo_file_or_skip(BOARD).read_text(encoding="utf-8"))
    heads = _headlines(board)
    (section,) = _run(tmp_path, {"board": board})["board"]
    assert (section["corpus"], section["label"]) == (
        "war_and_peace_book1",
        "War and Peace, Book One",
    )
    keys = [s["key"] for s in section["stats"]]
    want = [
        k for k in ("failures_ordinary", "size_substitution", "errors_removed") if heads[k]["holds"]
    ]
    assert keys == want
    stats = {s["key"]: s for s in section["stats"]}
    # each figure is the exporter's own number, rounded the way the headline text rounds it
    for key in ("failures_ordinary", "errors_removed"):
        if key in stats:
            figure = stats[key]["figure"]
            assert figure == f"{round(heads[key]['values']['median_pct'])}%"
            assert heads[key]["text"].startswith(figure)
    if "size_substitution" in stats:
        v = heads["size_substitution"]["values"]
        assert stats["size_substitution"]["figure"] == f"{v['ratio']}\u00d7"
        assert v["small_label"] in stats["size_substitution"]["detail"]
        assert v["big_label"] in stats["size_substitution"]["detail"]
    # every label names its baseline
    assert all("plain vector RAG" in s["label"] + s["detail"] for s in section["stats"])
    cmp = board["suites"]["chat"]["comparison"]
    assert section["method"] == {
        "models": cmp["value"]["models"],
        "questions": len(cmp["questions"]) + heads["failures_ordinary"]["values"]["questions"],
    }


def test_a_headline_that_does_not_hold_or_a_provisional_board_shows_nothing(
    tmp_path: Path,
) -> None:
    board = json.loads(repo_file_or_skip(BOARD).read_text(encoding="utf-8"))
    failing = copy.deepcopy(board)
    for h in failing["suites"]["chat"]["comparison"]["value"]["headlines"]:
        if h["key"] == "size_substitution":
            h["holds"] = False
    provisional = copy.deepcopy(board)
    provisional["provenance"]["overrides"] = ["--allow-unpinned"]
    out = _run(tmp_path, {"failing": failing, "provisional": provisional})
    (section,) = out["failing"]
    assert "size_substitution" not in [s["key"] for s in section["stats"]]
    assert out["provisional"] == []
