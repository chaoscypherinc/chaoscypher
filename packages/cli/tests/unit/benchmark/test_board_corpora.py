# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""The page reads each corpus of a board on its own (2026-10-22).

``packages/docs/src/lib/boardCorpora.ts`` turns a format-2 board into its corpora;
any other layout (format 1 included: no legacy reading) is no corpora and a
provisional board, so it makes no claim. The homepage
hero (``heroStats.ts``) shows a corpus's figures only when its own headline holds,
naming the corpus. Both run under node on the committed board and on copies of it
given a second corpus; nothing is pooled across corpora.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import re
from pathlib import Path
from typing import Any

from chaoscypher_cli.benchmark.discovery import load_dataset_bundle
from chaoscypher_core.testing.docs_modules import judge_boards
from chaoscypher_core.testing.repo_files import repo_file_or_skip, repo_root


BOARD = "packages/docs/src/data/leaderboard.json"
CORPORA_TS = "packages/docs/src/lib/boardCorpora.ts"
SHAPE_TS = "packages/docs/src/lib/boardShape.ts"
PROVENANCE_TS = "packages/docs/src/lib/leaderboardProvenance.ts"
BOOK = "war_and_peace_book1"
BOOK_LABEL = "War and Peace, Book One"
DOCS = "ollama_docs"
DOCS_LABEL = "the Ollama docs"


def _board() -> dict[str, Any]:
    return json.loads(repo_file_or_skip(BOARD).read_text(encoding="utf-8"))


def _v1(board: dict[str, Any]) -> dict[str, Any]:
    """The board as a format-1 export wrote it: one chat suite, no ``corpora`` (no longer read)."""
    out = copy.deepcopy(board)
    out.pop("board_format", None)
    out.pop("corpora", None)
    return out


def _docs_entry(board: dict[str, Any], holds: dict[str, bool], models: int) -> dict[str, Any]:
    """A second corpus made from the board's own suite, with its own holds and model count."""
    suite = copy.deepcopy(board["suites"]["chat"])
    suite["fixture"] = DOCS
    value = suite["comparison"]["value"]
    value["models"] = models
    for h in value["headlines"]:
        h["holds"] = holds.get(h["key"], False)
        if "models" in h["values"]:
            h["values"]["models"] = models
    return {
        "id": DOCS,
        "label": DOCS_LABEL,
        "board": False,
        "chat": suite,
        "models": {},
        "fixture_sha256": "0" * 64,
    }


def _v2(board: dict[str, Any], *others: dict[str, Any]) -> dict[str, Any]:
    """The board as format 2 writes it, Book One the board corpus, then ``others``."""
    out = _v1(board)
    out["board_format"] = 2
    out["corpora"] = [{"id": BOOK, "label": BOOK_LABEL, "board": True}, *others]
    return out


CORPORA = (
    "boardCorpora(board).map((c) => ({ id: c.id, label: c.label, board: c.board,"
    " fixture: c.suite ? c.suite.fixture : null,"
    " headlines: c.suite && c.suite.comparison ? c.suite.comparison.value.headlines.length : 0 }))"
)


def test_every_board_format_reads_as_its_corpora(tmp_path: Path) -> None:
    board = _board()
    docs = _docs_entry(board, {"failures_ordinary": True}, models=5)
    no_chat = _v1(board)
    del no_chat["suites"]["chat"]
    newer = _v2(board)
    newer["board_format"] = 3
    as_text = _v2(board)
    as_text["board_format"] = "2"
    two_boards = _v2(board, {**docs, "board": True})
    wrong_board = _v2(board)
    wrong_board["corpora"][0]["id"] = DOCS
    twice = _v2(board, {**docs, "id": BOOK})
    no_board = _v2(board, docs)
    no_board["corpora"] = [docs]
    out = judge_boards(
        tmp_path,
        {"boardCorpora": ["boardCorpora"], "leaderboardProvenance": ["readProvenance"]},
        f"{{ corpora: {CORPORA}, provisional: readProvenance(board).provisional }}",
        {
            "v1": _v1(board),
            "v2": _v2(board),
            "two": _v2(board, docs),
            "no_chat": no_chat,
            "newer": newer,
            "as_text": as_text,
            "two_boards": two_boards,
            "wrong_board": wrong_board,
            "twice": twice,
            "no_board": no_board,
        },
    )
    book = {"id": BOOK, "label": BOOK_LABEL, "board": True, "fixture": BOOK, "headlines": 4}
    assert out["v2"]["corpora"] == [book]
    assert out["v2"]["provisional"] is False
    # an old or unknown layout is no corpora and a provisional board: no claim, no ranking
    for name in ("v1", "newer", "as_text"):
        assert out[name] == {"corpora": [], "provisional": True}, name
    # the exporter's layout rules (_corpora_problems): one board corpus, the chat suite's, once
    for name in ("two_boards", "wrong_board", "twice", "no_board"):
        assert out[name]["corpora"] == [], name
    out = {name: v["corpora"] for name, v in out.items()}
    assert out["two"] == [
        book,
        {"id": DOCS, "label": DOCS_LABEL, "board": False, "fixture": DOCS, "headlines": 4},
    ]
    assert out["no_chat"] == []
    # a layout this page does not know makes no claim
    assert out["newer"] == []


def test_the_page_knows_the_exporters_format_and_reads_records_one_way() -> None:
    """One format number, the exporter's; one ``isRecord``, shared by the page's modules."""
    text = repo_file_or_skip(SHAPE_TS).read_text(encoding="utf-8")
    spec = importlib.util.spec_from_file_location(
        "export_leaderboard_format", repo_root() / "scripts" / "benchmark" / "export_leaderboard.py"
    )
    assert spec is not None and spec.loader is not None
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    fmt = re.search(r"export const BOARD_FORMAT = (\d+);", text)
    assert fmt is not None and int(fmt.group(1)) == script.BOARD_FORMAT
    assert "export function isRecord" in text
    for path in (CORPORA_TS, PROVENANCE_TS):
        module = repo_file_or_skip(path).read_text(encoding="utf-8")
        assert "function isRecord" not in module, path
        assert re.search(r'import \{[^}]*isRecord[^}]*\} from "\./boardShape"', module), path
    assert "V1_CORPUS_LABELS" not in repo_file_or_skip(CORPORA_TS).read_text(encoding="utf-8")
    assert load_dataset_bundle(BOOK).label == BOOK_LABEL


def test_the_hero_names_each_corpus_and_shows_only_what_holds_on_it(tmp_path: Path) -> None:
    """A headline that holds on one corpus and not the other is shown for that corpus only."""
    board = _board()
    holding = {
        h["key"] for h in board["suites"]["chat"]["comparison"]["value"]["headlines"] if h["holds"]
    }
    # the hero's order: the outcome, the efficiency, then the mechanism
    book_holds = [
        k for k in ("failures_ordinary", "size_substitution", "errors_removed") if k in holding
    ]
    docs = _docs_entry(board, {"failures_ordinary": True}, models=5)
    nothing = _docs_entry(board, {}, models=5)
    unmeasured = _v2(board)
    unmeasured["suites"]["chat"]["comparison"].pop("questions")
    out = judge_boards(
        tmp_path,
        {"heroStats": ["heroSections"]},
        "heroSections(board)",
        {
            "two": _v2(board, docs),
            "docs_hold_nothing": _v2(board, nothing),
            "v1": _v1(board),
            "one": _v2(board),
            "unmeasured": unmeasured,
        },
    )
    book, other = out["two"]
    # with two corpora every section is headed by its corpus
    assert (book["heading"], other["heading"]) == (f"On {BOOK_LABEL}", f"On {DOCS_LABEL}")
    # one corpus: its method line names it; with no method line, a heading does
    (one,) = out["one"]
    assert one["heading"] is None and one["method"] is not None
    (alone,) = out["unmeasured"]
    assert alone["method"] is None and alone["heading"] == f"On {BOOK_LABEL}"
    assert (book["corpus"], book["label"]) == (BOOK, BOOK_LABEL)
    assert [s["key"] for s in book["stats"]] == book_holds
    assert (other["corpus"], other["label"]) == (DOCS, DOCS_LABEL)
    assert [s["key"] for s in other["stats"]] == ["failures_ordinary"]
    # each corpus's counts are its own: the docs' detail and method say 5 models, not Book One's
    assert "5 local models" in other["stats"][0]["detail"]
    assert other["method"]["models"] == 5
    assert book["method"]["models"] == board["suites"]["chat"]["comparison"]["value"]["models"]
    # a corpus where nothing holds is not shown at all
    assert [s["corpus"] for s in out["docs_hold_nothing"]] == [BOOK]
    # a format-1 board is not read: no figures
    assert out["v1"] == []


PAGE_TSX = "packages/docs/src/pages/leaderboard.tsx"
TEASER_TSX = "packages/docs/src/components/LeaderboardTeaser.tsx"
HERO_TSX = "packages/docs/src/components/HeroStats.tsx"
METHODS_MD = "packages/docs/docs/reference/extraction-benchmark.md"


def test_every_surface_names_the_corpus_its_numbers_are_about() -> None:
    """The page's comparison, the teaser's line and the hero's method line each name their corpus."""
    page = repo_file_or_skip(PAGE_TSX).read_text(encoding="utf-8")
    teaser = repo_file_or_skip(TEASER_TSX).read_text(encoding="utf-8")
    hero = repo_file_or_skip(HERO_TSX).read_text(encoding="utf-8")
    # one reading of the board's corpora, the shared module's
    for name, text in (("page", page), ("teaser", teaser)):
        assert re.search(r'import \{[^}]*boardCorpora[^}]*\} from "\.\./lib/boardCorpora"', text), (
            name
        )
    lead = re.search(r"function GraphAdvantage\(.*?\n\}\n", page, re.S)
    assert lead is not None and "On {label}:" in lead.group(0)
    line = re.search(r"function graphAdvantageLine\(.*?\n\}\n", teaser, re.S)
    assert line is not None
    assert line.group(0).count("On ${label}") == 2
    assert "questions on {section.label}" in hero
    assert "section.heading" in hero
    # no corpus is hard-coded where the board names it
    assert "War and Peace" not in page + teaser + hero
    # the copy rules
    for name, text in (("page", page), ("teaser", teaser), ("hero", hero)):
        assert "wrong answers" not in text, name
        assert "brain" not in text.lower(), name
        # the rungs as the hero names them; a verdict is passed or failed, never right or wrong
        assert "GraphRAG" not in text and "naive RAG" not in text, name
        assert not re.search(r'["\']wrong["\']|\?\s*["\']right["\']', text), name
    labels = re.search(r"RUNG_LABEL[^=]*= \{(.*?)\};", page, re.S)
    assert labels is not None
    assert dict(re.findall(r'(\w+): "([^"]+)"', labels.group(1))) == {
        "naive": "plain vector RAG",
        "hybrid": "hybrid search",
        "graphrag": "graph + vector",
    }
    assert '? "passed" : "failed"' in page
    # Overall's chat part is the board corpus's only, and says so wherever Overall is explained
    tip = re.search(r"`Overall \$\{pct\(value\)\}[^`]*`", page)
    assert tip is not None and "BOARD_LABEL" in tip.group(0)
    header = re.search(r"overall: \[(.*?)\],\n", page, re.S)
    assert header is not None and "BOARD_LABEL" in header.group(1)
    blurb = re.search(r"Overall is\{\" \"\}.*?grounded chat(.*?)\.", teaser, re.S)
    assert blurb is not None and "BOARD_LABEL" in blurb.group(1)
    # the date shown is when the runs were measured, never when the file was exported
    for name, text in (("page", page), ("teaser", teaser)):
        assert "data.generated" not in text, name
        assert "data.last_run" in text, name


def test_the_methods_page_states_the_per_corpus_rule() -> None:
    text = repo_file_or_skip(METHODS_MD).read_text(encoding="utf-8")
    para = text.split("**The headlines and their bars.**", 1)[1].split("**Citation accuracy.**")[0]
    para = " ".join(para.split())
    assert "Each corpus is reported on its own" in para
    assert "nothing is pooled across corpora" in para
