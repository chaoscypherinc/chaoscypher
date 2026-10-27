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


def _comparisons(board: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Each corpus's comparison by corpus id: the board corpus's on ``suites.chat``, the rest in ``corpora``."""
    return {
        c["id"]: (board["suites"]["chat"] if c["board"] else c["chat"])["comparison"]
        for c in board["corpora"]
    }


def _headlines(cmp: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {h["key"]: h for h in cmp["value"]["headlines"]}


def test_the_hero_shows_each_corpuss_holding_headlines_in_order(tmp_path: Path) -> None:
    board = json.loads(repo_file_or_skip(BOARD).read_text(encoding="utf-8"))
    comparisons = _comparisons(board)
    sections = _run(tmp_path, {"board": board})["board"]
    labels = {c["id"]: c["label"] for c in board["corpora"]}
    # the board corpus first; a corpus is shown when any of the hero's headlines holds on it
    shown = [
        cid
        for cid, cmp in comparisons.items()
        if any(h["holds"] for k, h in _headlines(cmp).items() if k in HERO_ORDER)
    ]
    assert [s["corpus"] for s in sections] == shown
    assert sections[0]["corpus"] == "war_and_peace_book1"
    for section in sections:
        cmp = comparisons[section["corpus"]]
        heads = _headlines(cmp)
        assert section["label"] == labels[section["corpus"]]
        keys = [s["key"] for s in section["stats"]]
        # a headline held back (on the reviewers' verdicts, say) is not shown, whatever its text
        assert keys == [k for k in HERO_ORDER if heads[k]["holds"]]
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
            # The house rule: a hero detail says how many models the figure holds on.
            assert (
                f"{v['models_matching']} of {cmp['value']['models']}"
                in stats["size_substitution"]["detail"]
            )
        # every label names its baseline
        assert all("plain vector RAG" in s["label"] + s["detail"] for s in section["stats"])
        # a corpus is hand-graded exactly when its entry records a full review
        entry = next(c for c in board["corpora"] if c["id"] == section["corpus"])
        assert section["handGraded"] is bool((entry.get("review") or {}).get("full"))
        assert section["method"] == {
            "models": cmp["value"]["models"],
            "questions": len(cmp["questions"]) + heads["failures_ordinary"]["values"]["questions"],
        }


HERO_ORDER = ("failures_ordinary", "size_substitution", "errors_removed")


def test_a_headline_held_back_by_the_review_leaves_the_rest_of_its_corpus_shown(
    tmp_path: Path,
) -> None:
    """2026-10-23: errors_removed fails hand-graded; the other figures stay, on the hand-graded numbers."""
    board = json.loads(repo_file_or_skip(BOARD).read_text(encoding="utf-8"))
    held = copy.deepcopy(board)
    held["corpora"][0]["review"] = {
        "answers": 7920,
        "matched": 7920,
        "full": True,
        "file": "r-verdicts.json",
        "file_sha256": "0" * 64,
    }
    for h in held["suites"]["chat"]["comparison"]["value"]["headlines"]:
        # the scorer's reading moves aside; the headline's values are the hand-graded ones
        h["values"] = {**h["values"], "scorer": {**h["values"], "holds": True}}
        h["holds"] = h["key"] not in ("only_with_graph", "errors_removed")
        if h["key"] == "failures_ordinary":
            h["values"]["median_pct"] = 36.4
        if h["key"] == "errors_removed":
            h["held_back"] = "on the hand-graded answers, the numbers this board shows, 12 of 22"
    section = _run(tmp_path, {"held": held})["held"][0]
    assert section["corpus"] == "war_and_peace_book1"
    assert section["handGraded"] is True
    assert [s["key"] for s in section["stats"]] == ["failures_ordinary", "size_substitution"]
    # the figure is the hand-graded number, never the scorer's beside it
    assert section["stats"][0]["figure"] == "36%"


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
    section = out["failing"][0]
    assert section["corpus"] == "war_and_peace_book1"
    assert "size_substitution" not in [s["key"] for s in section["stats"]]
    assert out["provisional"] == []
