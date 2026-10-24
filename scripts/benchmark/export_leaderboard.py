# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Export probe-benchmark runs as the data file behind the /leaderboard page.

Usage (from the repo root):
    uv run python scripts/benchmark/export_leaderboard.py \
        --native <probes-native.json> ... --carrier <probes-carrier.json> ... \
        [--chat <chat-run.json> ...] \
        --out packages/docs/src/data/leaderboard.json
    uv run python scripts/benchmark/export_leaderboard.py \
        --check packages/docs/src/data/leaderboard.json

``--check`` exports nothing: it compares a committed board's ``provenance``
block with this checkout (the chat scorer's recipe and version, the chat
fixture's content) and exits 1 naming ``rebuild_board.sh`` when the board is
stale, or has no block (an export from before the block: unknown, never
clean). Any override flag also withholds every headline (``holds`` False).

Joins each model's rows with the model registry (VRAM, license) and keeps
the per-probe verdicts so the page can show which instructions failed, not
just how many. ``--chat`` adds the grounded-chat board: questions answered
from a fixed reference graph, thinking on. Only what was measured goes in: a
model missing from a run has that suite left out.

Several rows for one model in one suite pool (2026-10-01). A row over the
same tasks is a repeat: the board shows the mean, ``runs`` says how many,
and each verdict is the fraction of runs that passed the task. A row over
other tasks extends the suite. An identical row read twice is one run.
Every row also carries a 90% interval from resampling its tasks, so the
page can say which rows the board cannot tell apart. Questions the chat
fixture flags ``answerable: false`` leave every row's count and verdicts,
and so do the ``graph_advantage`` questions (``BOARD_BANDS``): the chat
column counts the five original bands only, so published chat scores do
not move when a run covers the retrieval comparison's band.

Chat rows carry the retrieval rung they were answered under
(``extras["retrieval"]``; a baseline's dataset id ends ``__retrieval_<mode>``).
Only ``graphrag`` rows feed the chat column. A model with rows under all
three rungs joins ``suites.chat.comparison`` ("Where the graph matters"):
per kind of ``graph_advantage`` question, the band as a whole and the
original 80, how many it answered right under each rung (every question
rescored from its answers with the ``answerable`` flags off, since a rung's
miss is the measurement), the lift of graphrag over each baseline in
questions with a paired-bootstrap 90% interval, citation accuracy per rung,
and up to three worked examples with each rung's trimmed context, answer and
verdict for the best local row and the best harness row.

A chat row that timed out, failed, or holds a question that errored is
refused with a message naming it (``--include-errored`` keeps the old
handling: a timed-out model shows "did not finish", a failed row is left out,
errored questions count as fails; ``--drop-errored-models`` leaves such a
row out of the board, naming it, so one errored question no longer blocks
the whole export). A rung whose rows lack retrieval pins is refused
(``--allow-unpinned``), and so is a rung whose rows are not one measurement
by the shared rule every comparison uses
(:func:`~chaoscypher_core.benchmark.results.comparability_findings`: run
settings recorded, pack, graph, build, temperature, seed, question file and
its content, dataset and rung, thinking, context window, output cap, loop
guard, and one model's pins; not what scored the stored verdicts, since the
board rescores every row) or that pools one model's
repeats under two lever sets (``--allow-mixed``): the board's chat column
once pooled unpinned rows of six builds (audit H4, H8 of 2026-10-06), and
until wave 3 the exporter kept its own shorter list, so rows sampled at 0.3
with no seed passed as a board. Rows served oracle pins (a ceiling
run, ``retrieval_pins.oracle``) are refused (``--allow-oracle``), and so are
rows that asked a question file other than their dataset's own, or a rung
whose rows asked different files (``--allow-mixed``): the board scores the
dataset fixture, and a holdout row on it is a different measurement (audit
wave 2). A question whose prompt plus answer
overflowed the model's context window is counted on the model's chat object
(``context_overflows``).

Each corpus is reported on its own (2026-10-22): chat rows are split by the
dataset their questions belong to (:func:`~chaoscypher_core.benchmark.results.row_corpus_id`)
before anything is checked, scored or counted, every check, headline, bar,
interval and count is computed over one corpus, and :func:`one_corpus` stops
the export if rows of two corpora ever reach one statistic. The board's
``corpora`` lists each corpus with the label its manifest gives it; the board
corpus (:data:`BOARD_CORPUS` when measured) keeps ``suites.chat``, which the
table's chat column and Overall rest on, and every other corpus's chat suite,
comparison, per-model chat objects and fixture hash are in its own entry
(:data:`BOARD_FORMAT` 2).

Where a full hand review of a corpus's board answers exists, the board shows
the hand-graded numbers (2026-10-23; the first rule of that day only checked
each headline on the review). ``--reviews CORPUS=PATH`` (repeatable; the file
archived in :data:`REVIEWS_DIR`) overlays a review's verdicts on the corpus's
rescored comparison answers, matched by key and answer start
(:mod:`chaoscypher_core.benchmark.reviews`, the rule ``review_check`` applies).
The review must match every comparison answer (every complete model's answer
under every rung to every question), or the export stops naming the answers
it misses. The corpus's whole comparison block (the per-model rows, their
lifts, the value block, the headlines and the worked examples) is then
computed on the reviewers' verdicts by the same code a plain export runs, so
no headline disagrees with the table under it; only citation accuracy stays
the scorer's (the reviewers grade the verdict, not the citation). The
scorer's block is kept beside it as ``comparison.scorer`` (``models`` and
``value``), and each headline records the scorer's reading in
``values.scorer`` (its ``holds`` and its values). ``holds`` is the hand-graded
reading's alone; one that holds on the scorer and not hand-graded carries
``held_back`` naming the hand-graded numbers. Every corpus entry carries
``review``: null, or the review's coverage, file name and ``file_sha256``.
"""

import argparse
import functools
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from typing import Any

import numpy as np

from chaoscypher_cli.benchmark.discovery import discover_datasets
from chaoscypher_cli.benchmark.models_registry import load_registry
from chaoscypher_cli.benchmark.probe_dataset import ProbeDataset
from chaoscypher_cli.benchmark.results import format_harness_settings, load_results
from chaoscypher_core.benchmark.chat_run import lever_signature
from chaoscypher_core.benchmark.queries import BOARD_BANDS, KINDS
from chaoscypher_core.benchmark.results import (
    RUNG_SPECIFIC_FIELDS,
    comparability_findings,
    context_overflows,
    row_active_latency_ms,
    row_corpus_id,
    row_problems,
    row_retrieval_mode,
    row_timed_out,
)
from chaoscypher_core.benchmark.retrieval import DEFAULT_RETRIEVAL_MODE, RETRIEVAL_MODES
from chaoscypher_core.benchmark.reviews import Review, expected, load_reviews, review_key
from chaoscypher_core.benchmark.scorers.chat_probes import ChatProbeScorer, holds_an_answer_term
from chaoscypher_core.benchmark.types import RawOutput


SUITES = {"native": lambda section: section != "H", "carrier": lambda section: section == "H"}
SUITE_NAMES = {"native": "isolated", "carrier": "in chunks", "chat": "chat"}
VRAM_HEADROOM_GB = 4
# The one place the leaderboard's blended scores are defined; the page, the
# homepage teaser and the interface pickers all read the numbers this writes.
# Extraction: in chunks counts twice (it is what transfers to real data),
# isolated once (the diagnostic behind it). Overall: extraction 0.6, chat 0.4.
# Speed is left out because it depends on the GPU.
EXTRACTION_WEIGHTS = {"carrier": 2 / 3, "native": 1 / 3}
OVERALL_WEIGHTS = {"extraction": 0.6, "chat": 0.4}
# The interval: resample a row's tasks with replacement (a task's verdict is
# then one of its runs' verdicts at random), recompute the blends, take the
# 5th and 95th percentiles. Seeded, so an export is reproducible.
BOOTSTRAP_DRAWS = 2000
BOOTSTRAP_SEED = 0
INTERVAL = 0.90
# The comparison's worked examples: how many, and lines of context kept
# around a line that holds an answer term.
EXAMPLE_COUNT = 3

BOARD_FORMAT = 2
"""The board JSON's layout (``board_format``), the only one written or read.

2 (2026-10-22): every corpus on its own, ``corpora`` listing each (``id``,
``label``, ``board``, ``last_run``); the board corpus's suite is ``suites.chat``
and every other corpus's entry carries its ``chat`` suite, its per-model chat
objects (``models``) and its ``fixture_sha256``. A board in any other layout,
format 1 (no key: one corpus) included, is unknown: :func:`provenance_problems`
names it, ``--check`` fails, and the page (``packages/docs/src/lib/boardShape.ts``)
shows it as provisional with no corpora.
"""

BOARD_CORPUS = "war_and_peace_book1"
"""The corpus the table's chat column and Overall rest on, when it was measured.

Book One of *War and Peace*, the board's corpus since the chat column began.
A board without it takes the first corpus its ``--chat`` rows name.
"""

# The value block's headline bars (2026-10-02): a headline is written only when
# the measure clears it, so the page and the homepage never claim more than the
# runs show. Median errors removed on the band against plain RAG; questions no
# model answers without the graph; the VRAM ratio of a size-substitution pair.
HEADLINE_ERRORS_REMOVED_PCT = 25.0
HEADLINE_ONLY_WITH_GRAPH = 5
HEADLINE_SIZE_RATIO = 1.8
# A headline holds over the population, not the best row (2026-10-03): the errors-removed bar
# must be cleared by a majority of the models with all three rungs, against plain RAG and
# against hybrid search, and the size pair must beat the big model on hybrid too. The count
# goes in the sentence so a reader sees how many models the claim rests on.


def majority(n: int) -> int:
    """The smallest count that is more than half of ``n`` (0 models: nothing can hold)."""
    return n // 2 + 1 if n else 1


EXAMPLE_CONTEXT_MARGIN = 2


def one_corpus(rows: Any, where: str) -> str:
    """The one corpus ``rows`` (chat rows) belong to; refuses rows of two corpora.

    Each corpus is reported on its own (2026-10-22): a pooled count, rate,
    interval or headline across corpora is never a number the board shows,
    and an override flag does not lift this (``--allow-mixed`` once let rows
    of two datasets through as one rung).

    Raises:
        SystemExit: Naming ``where`` and every corpus the rows come from.
    """
    corpora = sorted({row_corpus_id(row) for row in rows})
    if len(corpora) != 1:
        msg = (
            f"{where}: rows from {len(corpora)} corpora ({', '.join(corpora)}) would make one "
            "statistic; each corpus is reported on its own, never pooled"
        )
        raise SystemExit(msg)
    return corpora[0]


def corpus_label(corpus: str) -> str:
    """How the board names ``corpus``: its dataset manifest's ``label``.

    Raises:
        SystemExit: When the dataset cannot be loaded or its manifest has no label.
    """
    from chaoscypher_cli.benchmark.discovery import load_dataset_bundle

    try:
        bundle = load_dataset_bundle(corpus)
    except (FileNotFoundError, ValueError) as exc:
        msg = f"{corpus}: the board names every corpus by its dataset's label ({exc})"
        raise SystemExit(msg) from exc
    if not bundle.label:
        manifest = bundle.corpus_path.parent / "manifest.yaml"
        msg = (
            f"{corpus}: its dataset has no label ({manifest}); the board names the corpus "
            "every number is about, so add `label:` to the manifest"
        )
        raise SystemExit(msg)
    return bundle.label


def _pass_rate(suite: dict[str, Any]) -> float:
    """Passed over total; a run that did not finish has no passes and counts as 0."""
    total = suite.get("total")
    return (suite.get("passed") or 0) / total if total else 0.0


def _scores(model: dict[str, Any]) -> dict[str, Any]:
    """Extraction, chat and overall as percentages with their intervals; None where a board was not run."""
    native, carrier, chat = model.get("native"), model.get("carrier"), model.get("chat")
    extraction = (
        100
        * (
            EXTRACTION_WEIGHTS["carrier"] * _pass_rate(carrier)
            + EXTRACTION_WEIGHTS["native"] * _pass_rate(native)
        )
        if native and carrier
        else None
    )
    # 100 * passed / total, not (passed / total) * 100: the same arithmetic as the
    # per-suite pct fields, so the two never disagree in the last decimal.
    chat_pct = (
        (100 * (chat.get("passed") or 0) / chat["total"] if chat.get("total") else 0.0)
        if chat
        else None
    )
    overall = (
        OVERALL_WEIGHTS["extraction"] * extraction + OVERALL_WEIGHTS["chat"] * chat_pct
        if extraction is not None and chat_pct is not None
        else None
    )
    return {
        "extraction": None if extraction is None else round(extraction, 1),
        "chat": None if chat_pct is None else round(chat_pct, 1),
        "overall": None if overall is None else round(overall, 1),
        **_intervals(model),
    }


def _intervals(model: dict[str, Any]) -> dict[str, list[float] | None]:
    """90% intervals of the three blends from resampling the row's tasks (see BOOTSTRAP_DRAWS)."""
    rng = np.random.default_rng(BOOTSTRAP_SEED)

    def draws(suite: dict[str, Any] | None) -> Any:
        """One resampled pass rate per draw, or None when the suite was not scored."""
        fractions = (suite or {}).get("verdicts") or {}
        if not fractions or (suite or {}).get("passed") is None:
            return None
        p = np.fromiter(fractions.values(), dtype=float)
        idx = rng.integers(0, len(p), size=(BOOTSTRAP_DRAWS, len(p)))
        return (rng.random((BOOTSTRAP_DRAWS, len(p))) < p[idx]).mean(axis=1)

    def interval(values: Any) -> list[float] | None:
        """The percentile pair, rounded as the scores are."""
        if values is None:
            return None
        lo, hi = np.quantile(values, [(1 - INTERVAL) / 2, 1 - (1 - INTERVAL) / 2])
        return [round(float(lo), 1), round(float(hi), 1)]

    native, carrier = draws(model.get("native")), draws(model.get("carrier"))
    chat_obj = model.get("chat")
    chat = draws(chat_obj)
    extraction = (
        100 * (EXTRACTION_WEIGHTS["carrier"] * carrier + EXTRACTION_WEIGHTS["native"] * native)
        if native is not None and carrier is not None
        else None
    )
    if chat is not None:
        chat_pct = 100 * chat
    elif chat_obj and chat_obj.get("timed_out"):
        chat_pct = np.zeros(BOOTSTRAP_DRAWS)  # a run that did not finish counts as 0
    else:
        chat_pct = None
    overall = (
        OVERALL_WEIGHTS["extraction"] * extraction + OVERALL_WEIGHTS["chat"] * chat_pct
        if extraction is not None and chat_pct is not None
        else None
    )
    return {
        "extraction_ci90": interval(extraction),
        "chat_ci90": interval(100 * chat) if chat is not None else None,
        "overall_ci90": interval(overall),
    }


def _run_meta(row: Any) -> dict[str, Any]:
    """The harness run's own record on a row (sessions, refusals), or empty."""
    meta = (row.extras or {}).get("run")
    return meta if isinstance(meta, dict) else {}


def _sessions(row: Any, task_ids: set[str] | None = None) -> tuple[int, int]:
    """(extra client sessions, tasks that needed one) on a harness row, for ``task_ids`` or all.

    The bridge records ``sessions`` as ``{task_id: count}`` for tasks that took
    more than one session (answered in text without submitting; re-run until
    it submitted). A ``probes-all`` row holds both probe suites, so each
    suite counts its own tasks.
    """
    sessions = _run_meta(row).get("sessions") or {}
    kept = {t: int(n) for t, n in sessions.items() if task_ids is None or t in task_ids}
    return sum(n - 1 for n in kept.values()), len(kept)


def _fractions(samples: dict[str, list[bool]]) -> dict[str, float]:
    """Per task, the fraction of runs that passed it."""
    return {tid: round(sum(v) / len(v), 2) for tid, v in samples.items()}


def _passed(fractions: dict[str, float], runs: int) -> float | int:
    """The mean pass count: a whole number for one run, one decimal for several."""
    total = sum(fractions.values())
    return round(total, 1) if runs > 1 else round(total)


def _suite_rows(paths: list[Path] | None, keep: Any) -> dict[str, dict[str, Any]]:
    """One summary per model id for the probes of one suite, pooled over its runs.

    Several files may feed one suite: a main sweep, an addition pass for
    models added later, a run over probes added later, a repeat. Rows for
    one model pool per task (see the module docstring).
    """
    runs: dict[str, list[tuple[Any, dict[str, bool]]]] = {}
    seen: set[tuple[str, str, str]] = set()
    for row in (r for path in paths or [] for r in load_results(path)):
        # A probe the run attempted but produced no record is a fail, never a
        # smaller denominator: a run that stops early must not outrank a
        # complete one. A run that failed outright has no verdicts and is
        # left out ("not run").
        verdicts = {
            v["id"]: bool(v["passed"])
            for v in row.metrics.get("verdicts", [])
            if keep(v.get("section"))
        }
        if not verdicts or not row.success:
            continue
        key = (row.model_id, row.timestamp.isoformat(), json.dumps(verdicts, sort_keys=True))
        if key in seen:
            continue
        seen.add(key)
        runs.setdefault(row.model_id, []).append((row, verdicts))
    return {mid: _pool_probe_runs(items) for mid, items in runs.items()}


def _pool_probe_runs(items: list[tuple[Any, dict[str, bool]]]) -> dict[str, Any]:
    """Pool one model's probe rows: per-task fractions, means of the run-level numbers."""
    samples: dict[str, list[bool]] = {}
    for _row, verdicts in items:
        for tid, ok in verdicts.items():
            samples.setdefault(tid, []).append(ok)
    rows = [r for r, _ in items]
    latest = max(rows, key=lambda r: r.timestamp)
    fractions = _fractions(samples)
    n_runs = max(len(v) for v in samples.values())
    newest_first = sorted(rows, key=lambda r: r.timestamp, reverse=True)
    return {
        "passed": _passed(fractions, n_runs),
        "total": len(samples),
        "runs": n_runs,
        "p50_ms": next(
            (r.latency_ms_per_chunk_p50 for r in newest_first if r.latency_ms_per_chunk_p50), 0
        ),
        # the model's time, not the harness's retry backoff (audit wave 5)
        "minutes": round(mean(row_active_latency_ms(r) for r in rows) / 60000, 1),
        "output_tokens": int(mean(r.output_tokens for r in rows)),
        "truncated": max(r.chunks_truncated for r in rows),
        "aborted": max(r.chunks_aborted_by_loop for r in rows),
        "thinking_honoured": latest.thinking_honoured,
        "pins_applied": all(r.pins_applied for r in rows),
        "harness": latest.harness,
        "harness_settings": latest.harness_settings,
        "app_version": latest.app_version,
        "app_versions": sorted({r.app_version for r in rows if r.app_version}),
        "model_label": latest.model_label,
        # Harness rows: client sessions beyond one per task, and the tasks it took.
        "extra_sessions": sum(_sessions(r, set(v))[0] for r, v in items),
        "retried_tasks": sum(_sessions(r, set(v))[1] for r, v in items),
        "verdicts": fractions,
        # internal: the newest run's time, for the board's last_run (stripped from the page)
        "_last_run": latest.timestamp,
    }


@functools.cache
def _excluded_ids(fixture: str) -> frozenset[str]:
    """The fixture's questions flagged ``answerable: false``; empty when it cannot be loaded."""
    from chaoscypher_cli.benchmark.discovery import load_dataset_bundle

    try:
        queries = load_dataset_bundle(fixture).queries
    except FileNotFoundError, ValueError:
        return frozenset()
    if queries is None:
        return frozenset()
    return frozenset(q.id for q in queries.queries if not q.answerable)


ChatRun = tuple[Any, list[dict[str, Any]], bool]
"""A chat row, its verdicts (scored here, never the stored ones), and whether it timed out."""

OVERRIDE_FLAGS = (
    "--allow-unpinned",
    "--allow-mixed",
    "--allow-oracle",
    "--include-errored",
    "--drop-errored-models",
    "--drop-timed-out-models",
)
"""The flags that let a board through a refusal; the output's ``provenance.overrides`` names
each one a run passed (audit wave 4: ``rebuild_board.sh`` published with two of them and the
JSON said nothing)."""

REBUILD_COMMAND = "internal/benchmarks/tools/rebuild_board.sh"
"""What rebuilds the committed board; every refusal of a stale board names it."""

REVIEWS_DIR = Path(__file__).resolve().parents[2] / "internal" / "benchmarks" / "runs"
"""Where a hand review the board reads is archived; the board names it by file name alone."""


UNMATCHED_SHOWN = 5
"""How many answers a review does not cover the refusal names (:func:`_require_full_review`)."""


class ReviewOverlay:
    """One corpus's hand review, laid over its comparison answers: the reviewers' verdict for each.

    Counts every answer it is asked about (``answers``) and those a review
    covers (``matched``), keeping the keys of the first answers it does not
    (``unmatched``); a review short of any answer stops the export
    (:func:`_require_full_review`).
    """

    def __init__(self, path: Path, corpus: str = "") -> None:
        """Load the review at ``path`` of ``corpus``'s board answers, nothing counted yet."""
        self.path = path
        self.corpus = corpus
        self.reviews: dict[str, list[Review]]
        self.reviews, self.overrides = load_reviews([path])
        self.answers = 0
        self.matched = 0
        self.unmatched: list[str] = []

    def passed(self, row: Any, verdict: dict[str, Any], record: dict[str, Any]) -> bool:
        """The reviewers' verdict on one scored answer, or the scorer's when no review covers it."""
        self.answers += 1
        key = review_key(verdict["id"], row)
        want = expected(key, str(record.get("answer") or ""), self.reviews, self.overrides)
        if want is None:
            if len(self.unmatched) < UNMATCHED_SHOWN:
                self.unmatched.append(key)
            return bool(verdict["passed"])
        self.matched += 1
        return want[0] == "PASS"

    @property
    def full(self) -> bool:
        """Every comparison answer matched (and there were some): only then is the review applied."""
        return self.answers > 0 and self.matched == self.answers


STORED_SCORER_FIELDS: frozenset[str] = frozenset(
    {"scorer_version", "scorer_recipe", "rescored_queries_sha256"}
)
"""Comparability fields that say what scored a row's *stored* verdicts (audit wave 5).

The board never reads those verdicts: :func:`score_board` scores every row's
answers with today's scorer, so two rows a different scorer once scored are
one measurement here. Exempt from the board's checks (and only the board's:
``variant_compare`` still holds them).
"""


def _board_queries(row: Any) -> Any:
    """The question set the board scores a chat row with: the file it was asked (``row_questions``).

    Raises:
        SystemExit: When the row's question file is gone.
    """
    from chaoscypher_cli.benchmark.chat_dataset import row_questions

    try:
        return row_questions(row)[0]
    except FileNotFoundError as exc:
        msg = f"{_row_name(row)}: {exc}"
        raise SystemExit(msg) from exc


def _score_row(row: Any, queries: Any, *, ignore_answerable: bool = False) -> list[dict[str, Any]]:
    """One chat row's verdicts as the shipped scorer gives them on its stored answers.

    The one scoring path of the board and the comparison (audit wave 4: the
    board read the verdicts stored on the row, scored by whatever scorer and
    fixture wrote the file, and checked neither). ``ignore_answerable`` is
    the comparison's (a rung's miss is the measurement).

    Raises:
        SystemExit: When none of the row's answers is a question of ``queries``.
    """
    from chaoscypher_core.benchmark.results import unanswered_message

    refusal = unanswered_message(row, {q.id for q in queries.queries}, "the board's questions")
    if refusal:
        raise SystemExit(refusal)
    raw = RawOutput(
        entities=[],
        relationships=[],
        latency_ms=0,
        input_tokens=0,
        output_tokens=0,
        error=None,
        extras={"per_query": (row.extras or {}).get("per_query") or []},
    )
    scorer = ChatProbeScorer(ignore_answerable=ignore_answerable)
    asked = {r.get("query_id") for r in raw.extras["per_query"]}
    # only the questions the row asked: a run over a subset says nothing about the rest
    return [v for v in scorer.score(raw, queries).metrics["verdicts"] if v["id"] in asked]


def _retrieval_mode(row: Any) -> str:
    """The rung a chat row was answered under (the shared :func:`row_retrieval_mode`)."""
    return row_retrieval_mode(row)


def _row_name(row: Any) -> str:
    """How a refusal or a drop names a row: model, dataset and timestamp."""
    return f"{row.model_label} ({row.dataset_id}, {row.timestamp.isoformat()})"


def refuse_errored(where: Any, row: Any, problems: list[str]) -> None:
    """Stop with a message naming a row that is not a finished, fully answered run."""
    msg = (
        f"{where}: {_row_name(row)} {'; '.join(problems)}; it would score as a finished run. "
        "Re-run it, pass --drop-errored-models (an errored or failed row) or "
        "--drop-timed-out-models (a timed-out row) to leave the row out (named), or "
        "--include-errored to pool it as before (timed out: did not finish; errored "
        "questions: fails)"
    )
    raise SystemExit(msg)


def _chat_runs(
    paths: list[Path] | None,
    *,
    include_errored: bool = False,
    drop_errored: bool = False,
    drop_timed_out: bool = False,
    dropped: list[dict[str, str | None]] | None = None,
) -> dict[str, dict[tuple[str, str], list[ChatRun]]]:
    """Every grounded-chat run by corpus, then by model id and retrieval rung, not yet scored.

    The corpus is the dataset the row's questions belong to
    (:func:`~chaoscypher_core.benchmark.results.row_corpus_id`), in the order
    the rows first name it: each corpus is reported on its own, so nothing
    downstream sees two corpora's rows under one key.
    Only ``dataset_kind == "chat"`` rows count: a chat result file also holds
    the extraction and embedding rows that built the reference graph. One
    run is one (corpus, model, rung, timestamp): a file and its rescored copy are the
    same run (until audit wave 4 the key held the verdicts, so a rescore that
    moved one counted the run twice). The stored verdicts are never read: a
    run's verdicts are empty here and :func:`score_board` scores its stored
    answers (a row with stored verdicts and no answers to score stops the
    export). A row with :func:`row_problems` stops it too
    unless ``include_errored`` (pooled as before), or it is left out and
    named on stderr (and in ``dropped``): a timed-out row by
    ``drop_timed_out`` only, any other by ``drop_errored`` (wave 4: the one
    flag also dropped timed-out rows without saying so). Of several copies
    of one run the newest file's is kept (:func:`_newest_copies`), the others
    left out before any of the above is judged (wave 6), and a row
    left out for any other reason (judged, failed, answered nothing) is named
    on stderr and in ``dropped`` with no flag (wave 5: both were silent).
    """
    runs: dict[str, dict[tuple[str, str], list[ChatRun]]] = {}
    loaded = [(p, load_results(p)) for p in paths or []]
    keep = _newest_copies(loaded)
    for at, (path, row) in enumerate((p, r) for p, rows in loaded for r in rows):
        if row.dataset_kind != "chat":
            continue
        # a superseded copy is not a run of the board: dropped before its problems are judged
        # (audit wave 6: an errored older copy was refused or listed beside its newer copy)
        if at not in keep:
            continue
        problems = row_problems(row)
        timed_out = row_timed_out(row)
        flag = "--drop-timed-out-models" if timed_out else "--drop-errored-models"
        if problems and (drop_timed_out if timed_out else drop_errored) and not include_errored:
            print(
                f"DROPPED {path}: {_row_name(row)} {'; '.join(problems)} ({flag})", file=sys.stderr
            )
            if dropped is not None:
                dropped.append(
                    {
                        "model": row.model_id,
                        "corpus": row_corpus_id(row),
                        "file": str(path),
                        "why": "; ".join(problems),
                        "flag": flag,
                    }
                )
            continue
        if problems and not include_errored:
            refuse_errored(path, row, problems)
        mode = _retrieval_mode(row)
        extras = row.extras or {}
        if not timed_out and (
            not (row.success and extras.get("per_query")) or extras.get("judge_model")
        ):
            if (
                row.success
                and (row.metrics or {}).get("verdicts")
                and not extras.get("judge_model")
            ):
                msg = (
                    f"{path}: {_row_name(row)} stores verdicts but no answers to score; "
                    "the board scores every row itself"
                )
                raise SystemExit(msg)
            # Failed for another reason, judged, or answered nothing: "not run", named.
            why = (
                f"judged by {extras['judge_model']} (the board scores the probe checks)"
                if extras.get("judge_model")
                else "failed"
                if not row.success
                else "answered no question"
            )
            print(f"DROPPED {path}: {_row_name(row)} {why}", file=sys.stderr)
            if dropped is not None:
                dropped.append(
                    {
                        "model": row.model_id,
                        "corpus": row_corpus_id(row),
                        "file": str(path),
                        "why": why,
                        "flag": None,
                    }
                )
            continue
        corpus = runs.setdefault(row_corpus_id(row), {})
        corpus.setdefault((row.model_id, mode), []).append((row, [], timed_out))
    return runs


def _newest_copies(loaded: list[tuple[Path, list[Any]]]) -> set[int]:
    """The flat positions of the chat rows :func:`_chat_runs` keeps: one per (corpus, model, rung, timestamp).

    A file and its rescored copy are one run; the copy kept is the newest
    file's (its modification time, a later position on the command line on a
    tie), never simply the first one named (audit wave 5). One model's rows on
    two corpora at one timestamp are two runs (2026-10-22: the key without the
    corpus kept one and dropped the other without a word).
    """
    best: dict[tuple[str, str, str, str], tuple[tuple[float, int], int]] = {}
    at = 0
    for order, (path, rows) in enumerate(loaded):
        mtime = path.stat().st_mtime if path.exists() else 0.0
        for row in rows:
            if row.dataset_kind == "chat":
                key = (
                    row_corpus_id(row),
                    row.model_id,
                    _retrieval_mode(row),
                    row.timestamp.isoformat(),
                )
                age = (mtime, order)
                if key not in best or age >= best[key][0]:
                    best[key] = (age, at)
            at += 1
    return {position for _, position in best.values()}


def score_board(runs: dict[tuple[str, str], list[ChatRun]]) -> dict[tuple[str, str], list[ChatRun]]:
    """The runs with every row's verdicts scored by :func:`_score_row` (a timed-out row has none).

    One scorer for every row of the board, on the question file each was
    asked (:func:`_board_queries`); run after the provenance checks, so a row
    those refuse is never scored.
    """
    return {
        key: [
            (row, [] if timed_out else _score_row(row, _board_queries(row)), timed_out)
            for row, _, timed_out in items
        ]
        for key, items in runs.items()
    }


def _pins_of(row: Any) -> dict[str, Any]:
    """The retrieval pins a chat row was served (``extras["retrieval_pins"]``), or an empty dict."""
    return (row.extras or {}).get("retrieval_pins") or {}


def _question_file(row: Any) -> str | None:
    """The question file a chat row was asked when it is not its dataset's own, else None."""
    from chaoscypher_cli.benchmark.chat_dataset import dataset_queries_path
    from chaoscypher_core.benchmark.results import (
        recorded_queries_file,
        resolve_queries_file,
        row_corpus_id,
    )

    recorded = recorded_queries_file(row)
    own = dataset_queries_path(row_corpus_id(row))
    try:
        path = resolve_queries_file(recorded, own)
    except FileNotFoundError:
        return f"{recorded} (missing)"
    return str(path) if path is not None else None


def check_rung_provenance(
    runs: dict[tuple[str, str], list[ChatRun]],
    *,
    allow_unpinned: bool = False,
    allow_mixed: bool = False,
    allow_oracle: bool = False,
) -> list[str]:
    """Refuse a rung that is not one measurement: unpinned or oracle rows, mixed graphs, pins, builds.

    Per rung: every row must name the pins it was served (``allow_unpinned``
    lifts it); no row may have been served oracle pins, a ceiling
    (``allow_oracle``); and the rows must be one measurement
    (:func:`_mixed_findings`: the shared comparability rule, the dataset's own
    question file, one lever set per model's repeats; ``allow_mixed`` lifts
    it). Models may differ in fingerprint (a profile gets its own pins).
    Returns the lifted findings as warnings.

    Raises:
        SystemExit: Naming the rung and what differs.
    """
    if runs:
        one_corpus((row for items in runs.values() for row, _, _ in items), "the rung checks")
    warnings: list[str] = []
    rungs: dict[str, list[tuple[str, Any]]] = {}
    for (mid, mode), items in runs.items():
        rungs.setdefault(mode, []).extend((mid, row) for row, _, _ in items)
    for mode, rows in sorted(rungs.items()):
        unpinned = sorted({row.model_label for _, row in rows if not _pins_of(row)})
        if unpinned:
            finding = f"{mode}: rows without retrieval pins ({', '.join(unpinned)})"
            if not allow_unpinned:
                msg = f"{finding}; pin the rung, or pass --allow-unpinned"
                raise SystemExit(msg)
            warnings.append(finding)
        oracle = sorted({row.model_label for _, row in rows if _pins_of(row).get("oracle")})
        if oracle:
            finding = f"{mode}: rows served oracle pins, a ceiling ({', '.join(oracle)})"
            if not allow_oracle:
                msg = f"{finding}; a ceiling is not a board score, or pass --allow-oracle"
                raise SystemExit(msg)
            warnings.append(finding)
        mixed = _mixed_findings(rows)
        if mixed:
            finding = f"{mode}: mixes {'; '.join(mixed)}"
            if not allow_mixed:
                msg = f"{finding}; export one measurement, or pass --allow-mixed"
                raise SystemExit(msg)
            warnings.append(finding)
    across = _cross_rung_findings(runs)
    if across:
        finding = f"across the rungs: {'; '.join(across)}"
        if not allow_mixed:
            msg = (
                f"{finding}; the comparison's rungs must be one measurement, or pass --allow-mixed"
            )
            raise SystemExit(msg)
        warnings.append(finding)
    return warnings


def _cross_rung_findings(runs: dict[tuple[str, str], list[ChatRun]]) -> list[str]:
    """Why the rows of a three-rung comparison are not one measurement across the rungs.

    The shared rule (:func:`~chaoscypher_core.benchmark.results.comparability_findings`)
    over the rows of every model with rows under every rung, the rungs as
    labels, with :data:`~chaoscypher_core.benchmark.results.RUNG_SPECIFIC_FIELDS`
    exempt (the rung and its dataset id; the pins are per rung already).
    Until audit wave 4 each rung was checked on its own, so a graphrag rung
    of one build beside naive rows of another passed.
    """
    complete = {
        mid
        for mid, _ in runs
        if all(any(not t for _, _, t in runs.get((mid, mode), [])) for mode in RETRIEVAL_MODES)
    }
    by_rung: dict[str, list[Any]] = {}
    for (mid, mode), items in sorted(runs.items()):
        if mid in complete:
            by_rung.setdefault(mode, []).extend(row for row, _, _ in items)
    if len(by_rung) < 2:
        return []
    return comparability_findings(by_rung, exempt=RUNG_SPECIFIC_FIELDS | STORED_SCORER_FIELDS)


def _mixed_findings(rows: list[tuple[str, Any]]) -> list[str]:
    """Why one rung's ``(model id, row)`` pairs are not one measurement; empty when they are.

    The shared rule (:func:`~chaoscypher_core.benchmark.results.comparability_findings`,
    the one ``variant_compare`` applies: every comparability field one value,
    run settings recorded, one seed, one model's pins per lever set; the
    fields of the stored verdicts exempt, :data:`STORED_SCORER_FIELDS`), plus
    what only a board asks: every row asked the dataset's own question file,
    and one model's repeats ran with one lever set.
    """
    per_model: dict[str, list[Any]] = {}
    for mid, row in rows:
        per_model.setdefault(mid, []).append(row)
    mixed = comparability_findings(per_model, exempt=STORED_SCORER_FIELDS)
    files = {_question_file(row) for _, row in rows}
    if files - {None}:
        # the board scores the dataset fixture: a holdout row is another measurement
        mixed.append(
            f"question files {sorted(str(f or 'the dataset fixture') for f in files)}"
            " (the board is the dataset fixture)"
        )
    for mid, model_rows in sorted(per_model.items()):
        levers = {
            lever_signature(((r.extras or {}).get("run_settings") or {}).get("levers"))
            for r in model_rows
        }
        if len(levers) > 1:
            mixed.append(f"{mid}'s repeats ({len(levers)} lever sets)")
    return mixed


def _chat_rows(runs: dict[tuple[str, str], list[ChatRun]]) -> dict[str, dict[str, Any]]:
    """One grounded-chat summary per model, keyed by model id, pooled over its graphrag runs.

    The chat column is the product's retrieval: rows under a baseline rung
    belong to the comparison only. A harness row (a model answering the same
    reference graph through an MCP client) counts like any other and joins
    that model's probe rows by model id. Questions the fixture flags
    unanswerable leave every row, whether or not the row was rescored on a
    scorer that already skips them. Internal fields (``_fixture``, ``_row``)
    are stripped before the object reaches the page; ``verdicts`` stays, one
    pass fraction per question.
    """
    return {
        mid: _pool_chat_runs(items)
        for (mid, mode), items in runs.items()
        if mode == DEFAULT_RETRIEVAL_MODE
    }


def _pool_chat_runs(items: list[tuple[Any, list[dict[str, Any]], bool]]) -> dict[str, Any]:
    """Pool one model's chat rows of one corpus; a model whose every run timed out is "did not finish"."""
    rows = [r for r, _, _ in items]
    latest = max(rows, key=lambda r: r.timestamp)
    fixture = one_corpus(rows, f"{latest.model_label}'s chat column")
    excluded = _excluded_ids(fixture)
    scored = [(r, v) for r, v, timed_out in items if not timed_out]
    base = {
        # the model's time, not the harness's retry backoff (audit wave 5)
        "minutes": round(mean(row_active_latency_ms(r) for r in rows) / 60000, 1),
        "truncated": max(r.chunks_truncated for r in rows),
        # A harness row ran on the client's own thinking setting: unknown.
        "thinking": None if latest.harness else bool(latest.thinking),
        "extra_sessions": sum(_sessions(r)[0] for r in rows),
        "retried_tasks": sum(_sessions(r)[1] for r in rows),
        # questions whose prompt plus answer overflowed the context window (audit H9)
        "context_overflows": max(len(context_overflows(r)) for r in rows),
        "_fixture": fixture,
        "_row": latest,
    }
    if not scored:
        return {
            "passed": None,
            "total": None,
            "pct": None,
            "runs": len(items),
            "timed_out": True,
            "unsupported_names": None,
            "citation_accuracy": None,
            "verdicts": {},
            **base,
        }
    samples: dict[str, list[bool]] = {}
    unsupported: list[int] = []
    cited = correct = 0
    for _row, verdicts in scored:
        kept = [v for v in verdicts if v["id"] not in excluded and v.get("section") in BOARD_BANDS]
        cited += sum(1 for v in kept if v.get("citation") in ("correct", "wrong"))
        correct += sum(1 for v in kept if v.get("citation") == "correct")
        # The answer text is not on the row and no verdict detail says
        # whether an answer declined, so false declines are not counted.
        unsupported.append(
            sum(
                any(str(d).startswith("FAIL no_unsupported_names") for d in v.get("details", []))
                for v in kept
            )
        )
        for v in kept:
            samples.setdefault(v["id"], []).append(bool(v["passed"]))
    fractions = _fractions(samples)
    n_runs = max((len(v) for v in samples.values()), default=1)
    passed = _passed(fractions, n_runs)
    total = len(samples)
    return {
        "passed": passed,
        "total": total,
        "pct": round(100 * passed / total, 1) if total else None,
        "runs": n_runs,
        "timed_out": False,
        "unsupported_names": round(mean(unsupported)),
        # Cited-correctly over cited (the scorer's reported citation check);
        # None on rows scored before the check existed or that cite nothing.
        "citation_accuracy": _accuracy(correct, cited),
        # One verdict per question, for the page's per-question strip.
        "verdicts": fractions,
        **base,
    }


def _accuracy(correct: int, cited: int) -> float | None:
    """Cited-correctly over cited as a percentage; None when nothing was cited."""
    return round(100.0 * correct / cited, 1) if cited else None


def _chat_suite(rows: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Size, fixture, questions, exclusions and retrieval floor of the grounded-chat board.

    ``excluded_ids`` are the fixture's unanswerable questions (the audit found
    the retrieved context cannot answer them): on no row's count. The floor
    is the number of scored questions every scored model failed: on a fixed
    reference graph those are, in practice, retrieval misses the audit has
    not yet flagged. One model alone says nothing about retrieval, so the
    floor needs at least two scored rows. ``questions`` lists the fixture's
    questions in order (id, section, tier, text, answerable; never the
    answer terms) so the page can draw one cell per question.
    """
    scored = [r["verdicts"] for r in rows.values() if r["passed"] is not None]
    floor_ids: list[str] = []
    if len(scored) >= 2:
        ids = set.intersection(*(set(v) for v in scored))
        floor_ids = sorted(qid for qid in ids if all(v[qid] == 0 for v in scored))
    # one corpus's suite: until 2026-10-22 two fixtures were joined into one name and pooled
    fixture = one_corpus((r["_row"] for r in rows.values()), "the chat suite")
    excluded = sorted(_excluded_ids(fixture))
    return {
        "total": max((r["total"] or 0 for r in rows.values()), default=0),
        "fixture": fixture,
        "retrieval_floor": len(floor_ids) if len(scored) >= 2 else None,
        "retrieval_floor_ids": floor_ids,
        "excluded_ids": excluded,
        "questions": _chat_questions(fixture),
    }


def _fixture_queries(fixture: str) -> Any:
    """The fixture's parsed queries, or None when it cannot be loaded."""
    from chaoscypher_cli.benchmark.discovery import load_dataset_bundle

    try:
        return load_dataset_bundle(fixture).queries
    except FileNotFoundError, ValueError:
        return None


def _rescore(
    items: list[ChatRun],
    fixture: Any,
    *,
    include_errored: bool = False,
    review: ReviewOverlay | None = None,
) -> dict[str, dict[str, Any]]:
    """Every question's pooled verdict under one rung, scored with the ``answerable`` flags off.

    Returns ``{id: {"fraction", "citation", "record"}}``: the fraction of runs
    that passed, the newest run's citation state and its per-question record
    (answer and context, for the examples). Only questions a run asked are
    scored for it (a run over a subset says nothing about the rest); a
    question one repeat asked and another did not counts as a fail there.
    With ``review``, each answer's pass is the reviewers' verdict
    (:meth:`ReviewOverlay.passed`) instead of the scorer's.
    """
    if items:  # no rows is no statistic (a panel label with nothing on file scores nothing)
        one_corpus((row for row, _, _ in items), "a rung's rescore")
    samples: dict[str, list[bool]] = {}
    latest: dict[str, tuple[Any, dict[str, Any], dict[str, Any]]] = {}
    for row, _verdicts, timed_out in items:
        problems = row_problems(row)
        if problems and not include_errored:
            refuse_errored("rescore", row, problems)
        if timed_out:
            continue
        per_query = (row.extras or {}).get("per_query") or []
        records = {r.get("query_id"): r for r in per_query}
        for v in _score_row(row, fixture, ignore_answerable=True):
            record = records.get(v["id"]) or {}
            passed = review.passed(row, v, record) if review else bool(v["passed"])
            samples.setdefault(v["id"], []).append(passed)
            if v["id"] not in latest or row.timestamp > latest[v["id"]][0].timestamp:
                latest[v["id"]] = (row, v, records.get(v["id"]) or {})
    n_runs = max((len(s) for s in samples.values()), default=1)
    return {
        qid: {
            "fraction": sum(passes) / n_runs,  # a run without the question failed it
            "citation": latest[qid][1].get("citation"),
            "record": latest[qid][2],
        }
        for qid, passes in samples.items()
    }


def _paired_lift(graphrag: list[float], baseline: list[float]) -> dict[str, Any]:
    """The graphrag rung minus a baseline, in questions, with a paired-bootstrap 90% interval.

    The questions are resampled with replacement (the same draw for both
    rungs), each rung's verdict on a drawn question is one of its runs' at
    random, and the difference in passes is taken; the board's draws and
    seed.
    """
    g, b = np.asarray(graphrag, dtype=float), np.asarray(baseline, dtype=float)
    if not len(g):
        return {"value": 0, "ci90": [0, 0]}
    value = round(float(g.sum() - b.sum()), 1)
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    idx = rng.integers(0, len(g), size=(BOOTSTRAP_DRAWS, len(g)))
    passed_g = (rng.random((BOOTSTRAP_DRAWS, len(g))) < g[idx]).sum(axis=1)
    passed_b = (rng.random((BOOTSTRAP_DRAWS, len(g))) < b[idx]).sum(axis=1)
    lo, hi = np.quantile(passed_g - passed_b, [(1 - INTERVAL) / 2, 1 - (1 - INTERVAL) / 2])
    whole = value == int(value)
    return {
        "value": int(value) if whole else value,
        "ci90": (
            [round(float(lo)), round(float(hi))]
            if whole
            else [round(float(lo), 1), round(float(hi), 1)]
        ),
    }


def _trim_context(context_text: str, q: Any) -> str:
    """The lines of a rung's context that hold an answer term, with two lines each side.

    Gaps are marked with an ellipsis line; the untrusted-document fences are
    dropped. A context holding no answer term shows its first lines and an
    ellipsis, so a miss reads as one.
    """
    lines = [
        ln for ln in context_text.splitlines() if not ln.startswith(("<untrusted", "</untrusted"))
    ]
    hits = [i for i, ln in enumerate(lines) if holds_an_answer_term(ln, q)]
    if not hits:
        head = lines[: EXAMPLE_CONTEXT_MARGIN + 1]
        return "\n".join([*head, "\u2026"]) if len(lines) > len(head) else "\n".join(head)
    keep: set[int] = set()
    for i in hits:
        keep.update(
            range(
                max(0, i - EXAMPLE_CONTEXT_MARGIN),
                min(len(lines), i + EXAMPLE_CONTEXT_MARGIN + 1),
            )
        )
    out: list[str] = []
    last = -1
    for i in sorted(keep):
        if i != last + 1:
            out.append("\u2026")
        out.append(lines[i])
        last = i
    if last < len(lines) - 1:
        out.append("\u2026")
    return "\n".join(out)


def _comparison(
    runs: dict[tuple[str, str], list[ChatRun]],
    fixture: str,
    models: list[dict[str, Any]],
    *,
    include_errored: bool = False,
    review: ReviewOverlay | None = None,
) -> dict[str, Any] | None:
    """``suites.chat.comparison``: the three-rung comparison, or None when no model has all three.

    Groups are the four kinds, the whole ``graph_advantage`` band and the
    original (board-band) questions; every count, lift and example comes
    from rescoring the runs' own answers with the ``answerable`` flags off.
    One corpus's comparison: every row must be ``fixture``'s (:func:`one_corpus`),
    and ``models`` carry that corpus's scores (the examples' best model).
    With ``review`` the block is computed on the reviewers' verdicts, the
    scorer's kept as ``scorer`` (``models`` and ``value``) and its reading of
    each headline recorded on it (:func:`_apply_review`).

    Raises:
        SystemExit: When the runs hold rows of another corpus than ``fixture``.
    """
    rows = [row for items in runs.values() for row, _, _ in items]
    if rows and one_corpus(rows, "the comparison") != fixture:
        msg = f"the comparison of {fixture}: its rows are {row_corpus_id(rows[0])}'s"
        raise SystemExit(msg)
    queries = _fixture_queries(fixture)
    complete = sorted(
        mid
        for mid in {m for m, _ in runs}
        if all(
            any(not timed_out for _, _, timed_out in runs.get((mid, mode), []))
            for mode in RETRIEVAL_MODES
        )
    )
    if queries is None or not complete:
        return None
    by_id = {q.id: q for q in queries.queries}
    groups: dict[str, list[str]] = {kind: [] for kind in KINDS}
    groups["graph_advantage"] = []
    groups["original"] = []
    for q in queries.queries:
        if q.band == "graph_advantage":
            groups[q.kind or ""].append(q.id)
            groups["graph_advantage"].append(q.id)
        elif q.band in BOARD_BANDS:
            groups["original"].append(q.id)
    scored = {
        (mid, mode): _rescore(runs[(mid, mode)], queries, include_errored=include_errored)
        for mid in complete
        for mode in RETRIEVAL_MODES
    }
    info = {m["id"]: m for m in models}
    shown = scored
    if review is not None:
        shown = {
            key: _rescore(runs[key], queries, include_errored=include_errored, review=review)
            for key in scored
        }
        _require_full_review(review)
    block = _comparison_block(complete, shown, groups, info, by_id)
    if review is not None:
        theirs = _comparison_block(complete, scored, groups, info, by_id, examples=False)
        _apply_review(block["value"], theirs["value"])
        block["scorer"] = {"models": theirs["models"], "value": theirs["value"]}
    return {
        "kinds": list(KINDS),
        "groups": list(groups),
        "models": block["models"],
        "value": block["value"],
        "examples": block["examples"],
        "questions": [
            {"id": qid, "kind": by_id[qid].kind, "question": by_id[qid].question}
            for qid in groups["graph_advantage"]
        ],
    } | ({"scorer": block["scorer"]} if "scorer" in block else {})


def _comparison_block(
    complete: list[str],
    scored: dict[tuple[str, str], dict[str, dict[str, Any]]],
    groups: dict[str, list[str]],
    info: dict[str, dict[str, Any]],
    by_id: dict[str, Any],
    *,
    examples: bool = True,
) -> dict[str, Any]:
    """What one set of verdicts (``scored``) says: the per-model rows, the value block, the examples.

    The one computation a comparison runs, on the scorer's verdicts or on a
    review's (:func:`_comparison`). Per model: each rung's passes per group and
    its citation accuracy, and the lift of graphrag over each baseline.
    """
    entries: list[dict[str, Any]] = []
    question_lift: dict[str, float] = {}
    for mid in complete:
        common = set.intersection(*(set(scored[(mid, mode)]) for mode in RETRIEVAL_MODES))
        modes: dict[str, dict[str, Any]] = {}
        for mode in RETRIEVAL_MODES:
            verdicts = scored[(mid, mode)]
            cited = sum(1 for v in verdicts.values() if v["citation"] in ("correct", "wrong"))
            correct = sum(1 for v in verdicts.values() if v["citation"] == "correct")
            modes[mode] = {"citation_accuracy": _accuracy(correct, cited)}
            for group, ids in groups.items():
                members = [qid for qid in ids if qid in common]
                total = sum(verdicts[qid]["fraction"] for qid in members)
                modes[mode][group] = {
                    "passed": int(total) if total == int(total) else round(total, 1),
                    "total": len(members),
                }
        lift: dict[str, dict[str, Any]] = {}
        for group, ids in groups.items():
            members = [qid for qid in ids if qid in common]
            g = [scored[(mid, "graphrag")][qid]["fraction"] for qid in members]
            lift[group] = {
                baseline: _paired_lift(
                    g, [scored[(mid, baseline)][qid]["fraction"] for qid in members]
                )
                for baseline in ("naive", "hybrid")
            }
        for qid in groups["graph_advantage"]:
            if qid in common:
                question_lift[qid] = question_lift.get(qid, 0.0) + (
                    scored[(mid, "graphrag")][qid]["fraction"]
                    - scored[(mid, "naive")][qid]["fraction"]
                )
        entries.append(
            {
                "id": mid,
                "label": info.get(mid, {}).get("label", mid),
                "harness": info.get(mid, {}).get("harness"),
                "modes": modes,
                "lift": lift,
            }
        )
    return {
        "models": entries,
        "value": _value(complete, scored, groups, info),
        "examples": _examples(question_lift, by_id, scored, entries, info) if examples else [],
    }


def _rate_on(scored_mode: dict[str, dict[str, Any]], ids: list[str]) -> float | None:
    """Passed over asked as a percentage on ``ids``; None when none were asked."""
    asked = [qid for qid in ids if qid in scored_mode]
    if not asked:
        return None
    return round(100.0 * float(sum(scored_mode[qid]["fraction"] for qid in asked)) / len(asked), 1)


def _wrong_on(scored_mode: dict[str, dict[str, Any]], ids: list[str]) -> float:
    """Wrong answers over ``ids`` under one rung: the summed failed fractions."""
    return float(sum(1.0 - scored_mode[qid]["fraction"] for qid in ids if qid in scored_mode))


def _median_of(values: list[float]) -> float | None:
    """The median of ``values``, or None when there are none."""
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    return round(ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2, 1)


def _size_pairs(
    complete: list[str],
    scored: dict[tuple[str, str], dict[str, dict[str, Any]]],
    original: list[str],
    info: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """The size pairs on the ordinary questions, smallest model first (``_value`` item 4).

    The big model is the best local model on plain RAG over ``original`` (ties go to the
    bigger one); each pair is a local model the graph lifts to that score, at least 1.5x
    smaller, carrying how many models reach the score with the graph.
    """
    pairs: list[dict[str, Any]] = []
    local = [
        mid
        for mid in complete
        if not info.get(mid, {}).get("harness") and info.get(mid, {}).get("vram_gb")
    ]

    def ordinary(mid: str, mode: str) -> float | None:
        return _rate_on(scored[(mid, mode)], original)

    rated = [mid for mid in local if ordinary(mid, "naive") is not None]
    best_plain = max(
        rated, key=lambda mid: (ordinary(mid, "naive"), info[mid]["vram_gb"]), default=None
    )
    models_matching = 0
    big_n = ordinary(best_plain, "naive") if best_plain is not None else None
    if best_plain is not None and big_n is not None:
        big = best_plain
        big_h = ordinary(big, "hybrid")
        models_matching = sum(1 for mid in complete if (ordinary(mid, "graphrag") or 0.0) >= big_n)
        for small in local:
            ratio = info[big]["vram_gb"] / info[small]["vram_gb"]
            small_g, small_n = ordinary(small, "graphrag"), ordinary(small, "naive")
            if ratio < 1.5 or small_g is None or small_n is None:
                continue
            # the graph is what lifts it: at or above the best plain-RAG score, and above its own
            if small_g < big_n or small_g <= small_n:
                continue
            pairs.append(
                {
                    "small": small,
                    "small_label": info[small].get("label", small),
                    "small_vram_gb": info[small]["vram_gb"],
                    "big": big,
                    "big_label": info[big].get("label", big),
                    "big_vram_gb": info[big]["vram_gb"],
                    "ratio": round(ratio, 1),
                    "small_graphrag_pct": small_g,
                    "small_naive_pct": small_n,
                    "big_naive_pct": big_n,
                    "big_hybrid_pct": big_h,
                    "beats_hybrid_too": big_h is not None and small_g >= big_h,
                    "models_matching": models_matching,
                }
            )
    # the smallest model first: the claim is how small the graph lets you go
    pairs.sort(key=lambda p: (-p["ratio"], -(p["small_graphrag_pct"] - p["big_naive_pct"])))
    return pairs


def _value(
    complete: list[str],
    scored: dict[tuple[str, str], dict[str, dict[str, Any]]],
    groups: dict[str, list[str]],
    info: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """``comparison.value``: what the graph is worth, four ways, over the models with all three rungs.

    1. ``only_with_graph``: band questions no model answers on naive or hybrid
       and at least half answer on graphrag (``unanswered_on_naive`` relaxes
       the hybrid half). A capability, not an accuracy bump.
    2. ``lift``: pass-rate points graphrag minus each baseline, per model, on
       the band and on the original questions, with the mean, how many models
       improved, and the ``differential`` (band minus original): the ordinary
       questions are the control, so a lift that is the same on both is better
       retrieval, not the graph.
    3. ``errors_removed``: failed answers removed by graphrag as a share of the
       baseline's, per model, on the band, on the ordinary questions and on
       every scored question, with the pooled failure rates before and after.
       Most removed misses are incomplete answers and wrongful declines, not
       wrong facts (the 2.1 campaign: wrong facts 11 -> 6 of 250 -> 172 misses
       on the ordinary questions), so the copy says "failed", never "wrong".
    4. ``size_substitution``: the best local model on plain RAG over the
       ordinary questions (the control set; ties go to the bigger model)
       against the smallest local model the graph lifts to that score
       (graphrag at or above it, and above its own plain RAG), with how many
       models reach it with the graph (``models_matching``).
    ``headlines`` carries one line per measure with ``holds`` against the bars
    above; the page shows only those that hold. A headline holds over the
    population (2026-10-03): the errors-removed bar by a majority of models
    (against both baselines on the band, against plain RAG on the ordinary
    questions); the size pair by a majority of models matching the best
    plain-RAG score with the graph (2026-10-21; it replaced the two-model pair
    with the largest size ratio, which picked whichever big model scored worst).
    """

    def asked(ids: list[str]) -> list[str]:
        """The questions every complete model was asked under every rung."""
        return [
            qid
            for qid in ids
            if all(qid in scored[(mid, mode)] for mid in complete for mode in RETRIEVAL_MODES)
        ]

    band, original = asked(groups["graph_advantage"]), asked(groups["original"])
    everything = band + original
    n = len(complete)
    half = (n + 1) // 2

    def passes(mode: str, qid: str) -> int:
        return sum(
            1 for mid in complete if scored[(mid, mode)].get(qid, {}).get("fraction", 0) >= 1
        )

    only_with_graph = [
        qid
        for qid in band
        if passes("naive", qid) == 0
        and passes("hybrid", qid) == 0
        and passes("graphrag", qid) >= half
    ]
    unanswered_on_naive = [
        qid for qid in band if passes("naive", qid) == 0 and passes("graphrag", qid) >= half
    ]

    lift: dict[str, Any] = {}
    for group, ids in (("graph_advantage", band), ("original", original)):
        lift[group] = {}
        for baseline in ("naive", "hybrid"):
            per_model: dict[str, float] = {}
            for mid in complete:
                g = _rate_on(scored[(mid, "graphrag")], ids)
                b = _rate_on(scored[(mid, baseline)], ids)
                if g is not None and b is not None:
                    per_model[mid] = round(g - b, 1)
            values = list(per_model.values())
            lift[group][baseline] = {
                "per_model": per_model,
                "mean": round(mean(values), 1) if values else None,
                "models_improved": sum(1 for v in values if v > 0),
                "models": len(values),
            }
    lift["differential"] = {
        baseline: (
            round(lift["graph_advantage"][baseline]["mean"] - lift["original"][baseline]["mean"], 1)
            if lift["graph_advantage"][baseline]["mean"] is not None
            and lift["original"][baseline]["mean"] is not None
            else None
        )
        for baseline in ("naive", "hybrid")
    }

    errors_removed: dict[str, Any] = {}
    for group, ids in (("graph_advantage", band), ("original", original), ("all", everything)):
        errors_removed[group] = {}
        asked_total = len(complete) * len(ids)
        for baseline in ("naive", "hybrid"):
            per_model = {}
            pooled_b = pooled_g = 0.0
            for mid in complete:
                wrong_b = _wrong_on(scored[(mid, baseline)], ids)
                wrong_g = _wrong_on(scored[(mid, "graphrag")], ids)
                pooled_b, pooled_g = pooled_b + wrong_b, pooled_g + wrong_g
                if wrong_b > 0:
                    per_model[mid] = round(100.0 * (wrong_b - wrong_g) / wrong_b, 1)
            errors_removed[group][baseline] = {
                "per_model": per_model,
                "median_pct": _median_of(list(per_model.values())),
                "models_above_bar": sum(
                    1 for v in per_model.values() if v >= HEADLINE_ERRORS_REMOVED_PCT
                ),
                "bar_pct": HEADLINE_ERRORS_REMOVED_PCT,
                "fail_pct_baseline": (
                    round(100.0 * pooled_b / asked_total, 1) if asked_total else None
                ),
                "fail_pct_graphrag": (
                    round(100.0 * pooled_g / asked_total, 1) if asked_total else None
                ),
            }

    pairs = _size_pairs(complete, scored, original, info)
    best = pairs[0] if pairs else None

    band_removed = errors_removed["graph_advantage"]
    median_removed = band_removed["naive"]["median_pct"]
    above_naive = band_removed["naive"]["models_above_bar"]
    above_hybrid = band_removed["hybrid"]["models_above_bar"]
    needed = majority(n)
    plain = errors_removed["original"]["naive"]
    fewer_plain = sum(1 for v in plain["per_model"].values() if v > 0)
    headlines = [
        {
            "key": "failures_ordinary",
            # the median clears the bar AND most models clear it, against plain RAG, on the
            # questions not written for the graph (the control set)
            "holds": (
                plain["median_pct"] is not None
                and plain["median_pct"] >= HEADLINE_ERRORS_REMOVED_PCT
                and plain["models_above_bar"] >= needed
            ),
            "text": (
                f"{plain['median_pct']:.0f}% fewer failed answers than plain vector RAG on "
                f"ordinary questions (fewer on {fewer_plain} of {n} models; failed answers "
                f"{plain['fail_pct_baseline']:.0f}% → {plain['fail_pct_graphrag']:.0f}%)"
                if plain["median_pct"] is not None
                and plain["fail_pct_baseline"] is not None
                and plain["fail_pct_graphrag"] is not None
                else ""
            ),
            "values": {
                "median_pct": plain["median_pct"],
                "models": n,
                "models_above_bar": plain["models_above_bar"],
                "models_fewer": fewer_plain,
                "majority": needed,
                "questions": len(original),
                "fail_pct_baseline": plain["fail_pct_baseline"],
                "fail_pct_graphrag": plain["fail_pct_graphrag"],
            },
        },
        {
            "key": "errors_removed",
            # the median clears the bar AND most models clear it, against both baselines
            "holds": (
                median_removed is not None
                and median_removed >= HEADLINE_ERRORS_REMOVED_PCT
                and above_naive >= needed
                and above_hybrid >= needed
            ),
            "text": (
                f"{median_removed:.0f}% fewer failed answers on graph-shaped questions than plain RAG "
                f"(median over {n} models; {min(above_naive, above_hybrid)} of {n} remove at least "
                f"{HEADLINE_ERRORS_REMOVED_PCT:.0f}% against plain RAG and against hybrid search)"
                if median_removed is not None
                else ""
            ),
            "values": {
                "median_pct": median_removed,
                "models": n,
                "models_above_bar_naive": above_naive,
                "models_above_bar_hybrid": above_hybrid,
                "majority": needed,
            },
        },
        {
            "key": "only_with_graph",
            "holds": len(only_with_graph) >= HEADLINE_ONLY_WITH_GRAPH,
            "text": (
                f"{len(only_with_graph)} of {len(band)} graph-shaped questions no model answered "
                "without the graph"
            ),
            "values": {"count": len(only_with_graph), "of": len(band)},
        },
        {
            "key": "size_substitution",
            # a population claim: most models reach the best plain-RAG score with the graph,
            # and the smallest of them is far smaller than the model that holds it
            "holds": (
                best is not None
                and best["ratio"] >= HEADLINE_SIZE_RATIO
                and best["models_matching"] >= needed
            ),
            "text": (
                f"{best['small_label']} with the graph answers "
                f"{'better than' if best['small_graphrag_pct'] > best['big_naive_pct'] else 'as well as'} "
                f"{best['big_label']}, the best model on plain vector RAG "
                f"({best['small_graphrag_pct']:.0f}% vs {best['big_naive_pct']:.0f}% on ordinary "
                f"questions); {best['models_matching']} of {n} models match it with the graph"
                if best
                else ""
            ),
            "values": best or {},
        },
    ]
    return {
        "models": n,
        "only_with_graph": {"ids": only_with_graph, "count": len(only_with_graph), "of": len(band)},
        "unanswered_on_naive": {
            "ids": unanswered_on_naive,
            "count": len(unanswered_on_naive),
            "of": len(band),
        },
        "lift": lift,
        "errors_removed": errors_removed,
        "size_substitution": {"pairs": pairs, "best": best},
        "headlines": headlines,
    }


def _apply_review(value: dict[str, Any], scorer: dict[str, Any]) -> None:
    """Record the scorer's reading (``scorer``, its value block) on each hand-graded headline of ``value``.

    Every headline of ``value`` (computed on the reviewers' verdicts: its text,
    values and ``holds`` are the board's) gains ``values.scorer``, the
    scorer's ``holds`` and values. One the scorer's reading would show and the
    hand-graded one does not carries ``held_back`` naming the hand-graded
    numbers. ``holds`` follows the hand-graded reading alone.
    """
    theirs = {h["key"]: h for h in scorer["headlines"]}
    for headline in value["headlines"]:
        other = theirs[headline["key"]]
        # a copy: a headline's values can be the value block's own (the size pair is ``best``)
        reading = {"holds": bool(other["holds"]), **other["values"]}
        headline["values"] = {**headline["values"], "scorer": reading}
        if other["holds"] and not headline["holds"]:
            headline["held_back"] = _held_back(headline["key"], headline["values"], value["models"])


def _held_back(key: str, values: dict[str, Any], models: int) -> str:
    """Why a headline the scorer would show is held back: its bar on the hand-graded numbers."""
    bar = f"{HEADLINE_ERRORS_REMOVED_PCT:.0f}%"
    needed = majority(models)
    where = "on the hand-graded answers, the numbers this board shows,"
    if key == "errors_removed":
        return (
            f"{where} the median is {values.get('median_pct')}% and "
            f"{values.get('models_above_bar_naive')} of {models} models remove at least {bar} "
            f"against plain RAG, {values.get('models_above_bar_hybrid')} of {models} against "
            f"hybrid search ({needed} needed)"
        )
    if key == "failures_ordinary":
        return (
            f"{where} the median is {values.get('median_pct')}% and "
            f"{values.get('models_above_bar')} of {models} models remove at least {bar} against "
            f"plain RAG on ordinary questions ({needed} needed)"
        )
    if key == "only_with_graph":
        return (
            f"{where} {values.get('count')} of {values.get('of')} graph-shaped questions no model "
            f"answered without the graph ({HEADLINE_ONLY_WITH_GRAPH} needed)"
        )
    if not values:
        return f"{where} no smaller model the graph lifts to the best plain-RAG score"
    return (
        f"{where} the size ratio is {values.get('ratio')}x ({HEADLINE_SIZE_RATIO}x needed) and "
        f"{values.get('models_matching')} of {models} models match the best plain-RAG score "
        f"({needed} needed)"
    )


def _reference_models(entries: list[dict[str, Any]], info: dict[str, dict[str, Any]]) -> list[str]:
    """The best local row and the best harness row by Overall (chat score when there is none)."""

    def rank(mid: str) -> tuple[float, float]:
        scores = info.get(mid, {}).get("scores") or {}
        return (scores.get("overall") or -1.0, scores.get("chat") or -1.0)

    local = [e["id"] for e in entries if not e["harness"]]
    harness = [e["id"] for e in entries if e["harness"]]
    return [max(group, key=rank) for group in (local, harness) if group]


def _examples(
    question_lift: dict[str, float],
    by_id: dict[str, Any],
    scored: dict[tuple[str, str], dict[str, dict[str, Any]]],
    entries: list[dict[str, Any]],
    info: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Up to three band questions with the largest graphrag minus naive lift, distinct kinds first."""
    ranked = sorted(
        (qid for qid, lift in question_lift.items() if lift > 0),
        key=lambda qid: (-question_lift[qid], qid),
    )
    chosen: list[str] = []
    kinds_used: set[str] = set()
    for qid in ranked:
        kind = by_id[qid].kind or ""
        if kind not in kinds_used and len(chosen) < EXAMPLE_COUNT:
            chosen.append(qid)
            kinds_used.add(kind)
    for qid in ranked:
        if len(chosen) >= EXAMPLE_COUNT:
            break
        if qid not in chosen:
            chosen.append(qid)
    references = _reference_models(entries, info)
    examples: list[dict[str, Any]] = []
    for qid in chosen:
        q = by_id[qid]
        models_out: list[dict[str, Any]] = []
        for mid in references:
            rungs: dict[str, dict[str, Any]] = {}
            for mode in RETRIEVAL_MODES:
                verdict = scored[(mid, mode)].get(qid)
                if verdict is None:
                    continue
                record = verdict["record"]
                rungs[mode] = {
                    "context": _trim_context(record.get("context_text") or "", q),
                    "answer": record.get("answer") or "",
                    "passed": verdict["fraction"] >= 1,
                    "citation": verdict["citation"],
                }
            models_out.append(
                {"id": mid, "label": info.get(mid, {}).get("label", mid), "rungs": rungs}
            )
        examples.append(
            {
                "id": qid,
                "kind": q.kind,
                "question": q.question,
                "gold_answer": (q.gold_answer or "").strip(),
                "models": models_out,
            }
        )
    return examples


def _chat_questions(fixture: str) -> list[dict[str, Any]]:
    """The chat column's questions as the page shows them: id, section, tier, text, answerable.

    The five board bands only; the ``graph_advantage`` band belongs to the
    comparison section.
    """
    from chaoscypher_core.benchmark.scorers.chat_probes import BAND_TIER

    queries = _fixture_queries(fixture)
    if queries is None:
        return []
    return [
        {
            "id": q.id,
            "section": q.band,
            "tier": BAND_TIER.get(q.band, ""),
            "question": q.question,
            "answerable": q.answerable,
        }
        for q in queries.queries
        if q.band in BOARD_BANDS
    ]


def _parse_args() -> argparse.Namespace:
    """The command line (see the module docstring)."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--native", type=Path, nargs="+")
    ap.add_argument("--carrier", type=Path, nargs="+")
    ap.add_argument(
        "--chat",
        type=Path,
        nargs="+",
        help="Grounded-chat run(s); rows for one model pool as repeats",
    )
    ap.add_argument("--out", type=Path)
    ap.add_argument(
        "--check",
        type=Path,
        metavar="BOARD_JSON",
        help=(
            "Compare a committed board's provenance with this checkout (scorer recipe and "
            "version, the chat fixture's content) and exit 1 naming the rebuild command when "
            "it is stale or unrecorded; exports nothing"
        ),
    )
    ap.add_argument("--registry", type=Path, help="Model registry YAML (default: the built-in one)")
    ap.add_argument(
        "--interface-out",
        type=Path,
        help="Slim copy (no verdicts) for the interface model pickers",
    )
    ap.add_argument(
        "--include-errored",
        action="store_true",
        help="Pool timed-out, failed and errored chat rows instead of refusing them",
    )
    ap.add_argument(
        "--drop-errored-models",
        action="store_true",
        help="Leave out (and name) a chat row that failed or holds an errored question",
    )
    ap.add_argument(
        "--drop-timed-out-models",
        action="store_true",
        help="Leave out (and name) a chat row that timed out (never dropped as errored)",
    )
    ap.add_argument(
        "--allow-unpinned",
        action="store_true",
        help="Accept chat rows served no retrieval pins (rows from before pins; a warning each)",
    )
    ap.add_argument(
        "--allow-mixed",
        action="store_true",
        help="Accept a rung whose rows are not one measurement (graphs, builds, decoding, files, ...)",
    )
    ap.add_argument(
        "--allow-oracle",
        action="store_true",
        help="Accept chat rows served oracle pins (a ceiling run; a warning each)",
    )
    ap.add_argument(
        "--reviews",
        type=_review_spec,
        action="append",
        default=[],
        metavar="CORPUS=PATH",
        help=(
            "A full hand review of CORPUS's board answers (archived in internal/benchmarks/runs); "
            "the corpus's comparison is then computed on the reviewers' verdicts, and the export "
            "stops unless the review matches every comparison answer. Repeatable"
        ),
    )
    args = ap.parse_args()
    if args.check is None and args.out is None:
        ap.error("--out is required (or --check BOARD_JSON)")
    return args


def _review_spec(spec: str) -> tuple[str, Path]:
    """``CORPUS=PATH`` as ``(corpus, path)``.

    Raises:
        argparse.ArgumentTypeError: When it is not of that shape.
    """
    corpus, sep, path = spec.partition("=")
    if not sep or not corpus or not path:
        msg = f"--reviews takes CORPUS=PATH, not {spec!r}"
        raise argparse.ArgumentTypeError(msg)
    return corpus, Path(path)


def _review_overlays(specs: list[tuple[str, Path]], corpora: Any) -> dict[str, ReviewOverlay]:
    """One :class:`ReviewOverlay` per ``--reviews`` corpus.

    Raises:
        SystemExit: When a review names a corpus the board did not measure, names one corpus
            twice, or is not archived in :data:`REVIEWS_DIR` (the board names it by file name).
    """
    overlays: dict[str, ReviewOverlay] = {}
    measured = set(corpora)
    for corpus, path in specs:
        if corpus not in measured:
            msg = f"--reviews {corpus}: no chat rows of {corpus} (measured: {', '.join(sorted(measured))})"
            raise SystemExit(msg)
        if corpus in overlays:
            msg = f"--reviews {corpus}: given twice; a corpus has one review of its board answers"
            raise SystemExit(msg)
        if path.resolve().parent != REVIEWS_DIR.resolve() or not path.is_file():
            msg = f"--reviews {corpus}: {path} is not a file archived in {REVIEWS_DIR}"
            raise SystemExit(msg)
        overlays[corpus] = ReviewOverlay(path, corpus)
    return overlays


def main() -> None:
    """Write the leaderboard data file, or with ``--check`` judge a committed one (exit 1 when stale)."""
    args = _parse_args()
    if args.check is not None:
        problems = check_board(json.loads(args.check.read_text(encoding="utf-8")))
        for problem in problems:
            print(f"STALE {args.check}: {problem}", file=sys.stderr)
        if problems:
            print(f"rebuild it with {REBUILD_COMMAND}", file=sys.stderr)
            raise SystemExit(1)
        print(f"{args.check}: provenance matches this checkout")
        return

    dataset = next(d for d in discover_datasets() if isinstance(d, ProbeDataset))
    probes = [
        {"id": p.id, "section": p.section, "tier": p.tier, "instruction": p.instruction}
        for p in dataset.probes
    ]
    registry = load_registry(path=args.registry)
    suites = {name: _suite_rows(getattr(args, name), keep) for name, keep in SUITES.items()}
    dropped: list[dict[str, str | None]] = []
    # each corpus on its own from here on: checked, scored and counted apart (2026-10-22)
    by_corpus = _chat_runs(
        args.chat,
        include_errored=args.include_errored,
        drop_errored=args.drop_errored_models,
        drop_timed_out=args.drop_timed_out_models,
        dropped=dropped,
    )
    lifted = [
        finding
        for corpus, runs in by_corpus.items()
        for finding in _check_corpus(corpus, runs, args)
    ]
    for warning in lifted:
        print(f"WARNING {warning}")
    reviews = _review_overlays(args.reviews, by_corpus)
    by_corpus = _board_order({corpus: score_board(runs) for corpus, runs in by_corpus.items()})
    for corpus, runs in by_corpus.items():
        _warn_context_overflows(corpus, runs)
    corpus_chat = {corpus: _chat_rows(runs) for corpus, runs in by_corpus.items()}
    board_corpus = next(iter(by_corpus), None)
    chat = corpus_chat[board_corpus] if board_corpus is not None else {}
    chat_suite = _chat_suite(chat) if chat else None
    # The suite size is what the runs attempted, not what the fixture holds
    # today: the page header must match the rows it labels. A row short of it
    # (a model not yet run on probes added later) says so in its notes.
    suite_meta = {
        name: {
            "total": max((r["total"] for r in rows.values()), default=0),
            "fixture_total": sum(1 for p in dataset.probes if SUITES[name](p.section)),
        }
        for name, rows in suites.items()
    }
    # the table's rows: the probe suites and the board corpus, never another corpus's models
    board = _Board(registry, suites, suite_meta, args)
    models = board.models(chat, chat_suite)
    corpora = [
        {
            "id": corpus,
            "label": corpus_label(corpus),
            "board": corpus == board_corpus,
            "last_run": _last_run(row.timestamp for items in runs.values() for row, _, _ in items),
        }
        | (
            _board_corpus_entry(chat_suite, runs, models, args, reviews.get(corpus))
            if corpus == board_corpus
            else _corpus_entry(corpus_chat[corpus], runs, board, reviews.get(corpus))
        )
        for corpus, runs in by_corpus.items()
    ]
    if chat_suite:
        suite_meta["chat"] = chat_suite
    payload = {
        # when this file was exported; a re-export is not a new run
        "generated": datetime.now(tz=UTC).strftime("%Y-%m-%d"),
        # when the table's runs were measured: the newest probe or board-corpus row
        "last_run": _last_run(
            [
                *(s["_last_run"] for rows in suites.values() for s in rows.values()),
                *(
                    row.timestamp
                    for items in by_corpus.get(board_corpus, {}).values()
                    for row, _, _ in items
                ),
            ]
        ),
        "thinking": "off",
        # The chat board is scored with thinking on: that is how chat runs.
        "chat_thinking": "on",
        # Weights alone are not the footprint: the context window needs room.
        # Both the page filter and the interface picker read this one number.
        "vram_headroom_gb": VRAM_HEADROOM_GB,
        # How the blended scores on every row were computed, so a page can say so.
        "score_weights": {"extraction": EXTRACTION_WEIGHTS, "overall": OVERALL_WEIGHTS},
        # How the intervals beside them were computed.
        "interval": {"level": INTERVAL, "draws": BOOTSTRAP_DRAWS, "seed": BOOTSTRAP_SEED},
        # the layout of this file (BOARD_FORMAT): each corpus on its own since 2
        "board_format": BOARD_FORMAT,
        # every corpus measured, the board corpus (suites.chat) first; never pooled
        "corpora": corpora,
        "suites": suite_meta,
        "probes": probes,
        "models": models,
        # what let this board through and what scored it (audit wave 4)
        "provenance": _provenance(args, lifted, dropped, chat_suite),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {args.out}: {len(models)} models, {len(probes)} probes")
    if args.interface_out:
        slim = {
            "generated": payload["generated"],
            "vram_headroom_gb": VRAM_HEADROOM_GB,
            "score_weights": {"extraction": EXTRACTION_WEIGHTS, "overall": OVERALL_WEIGHTS},
            # Harness-track rows are a model run inside an MCP client (Claude
            # Code, say) on the user's own subscription. They are not Ollama
            # models, so a local-model picker must never offer them.
            "models": [_slim(m) for m in models if not m["harness"]],
        }
        args.interface_out.parent.mkdir(parents=True, exist_ok=True)
        args.interface_out.write_text(json.dumps(slim, indent=1) + "\n", encoding="utf-8")
        print(f"wrote {args.interface_out}")


class _Board:
    """What every corpus's model rows are built from: the registry, the probe suites, the args."""

    def __init__(
        self,
        registry: Any,
        suites: dict[str, dict[str, dict[str, Any]]],
        suite_meta: dict[str, dict[str, Any]],
        args: argparse.Namespace,
    ) -> None:
        self.registry, self.suites, self.suite_meta, self.args = registry, suites, suite_meta, args

    def models(
        self, chat: dict[str, dict[str, Any]], chat_suite: dict[str, Any] | None
    ) -> list[dict[str, Any]]:
        """One corpus's model rows: the probe suites' models and that corpus's, nobody else's.

        A model's metadata (harness, pins, builds, label) comes from its probe
        rows and its chat rows on this corpus alone (2026-10-22: a model run on
        a second corpus only once joined the table as a row of dashes, and a
        docs row's build reached a Book One model's row); its scores blend
        this corpus's chat object.
        """
        registry, suites, suite_meta = self.registry, self.suites, self.suite_meta
        ids = sorted({mid for rows in (*suites.values(), chat) for mid in rows})
        models = []
        for mid in ids:
            entry = registry.get(mid)
            measured = [rows[mid] for rows in suites.values() if mid in rows]
            # how the model ran on this corpus (a harness row, its pins); never another corpus's
            measured.extend(
                {
                    "harness": row.harness,
                    "harness_settings": row.harness_settings,
                    "app_version": row.app_version,
                    "app_versions": [row.app_version] if row.app_version else [],
                    "pins_applied": row.pins_applied,
                    "model_label": row.model_label,
                }
                for row in ([chat[mid]["_row"]] if mid in chat else [])
            )
            harness = next((m["harness"] for m in measured if m["harness"]), None)
            # A harness-track model is not in the registry; its row carries the
            # label the client reported.
            fallback = measured[0]["model_label"] if harness else mid
            model: dict[str, Any] = {
                "id": mid,
                "label": (entry.label if entry else fallback).replace(" (local)", ""),
                "vram_gb": entry.vram_gb if entry else None,
                "license": entry.license if entry else None,
                "open_weight": entry.open_weight if entry else None,
                # From the Ollama manifest (``ollama show`` Capabilities). The app's
                # chat loop needs tool calling, so a False here means the model
                # cannot be the chat model whatever its chat score says.
                "tools": entry.tools if entry else None,
            }
            for name, rows in suites.items():
                if mid in rows:
                    model[name] = {
                        k: v
                        for k, v in rows[mid].items()
                        if k != "app_versions" and not k.startswith("_")
                    }
            if mid in chat:
                model["chat"] = _public_chat(chat[mid], chat_suite)
            # Harness-track rows (a model run inside an MCP client, on its own
            # settings) must stay distinguishable so the page can group them.
            model["pins_applied"] = all(m["pins_applied"] for m in measured)
            model["harness"] = harness
            # What the client said about its own effort/thinking/version: part of
            # the row's identity, since any of it can move the score.
            model["harness_settings"] = next(
                (m["harness_settings"] for m in measured if m["harness_settings"]), None
            )
            # The ChaosCypher builds the scores were measured on; empty for runs
            # from before builds were recorded.
            model["app_versions"] = sorted(
                {v for m in measured for v in m.get("app_versions") or []}
            )
            models.append(model)
        for model in models:
            model["notes"] = _notes(model, suite_meta)
            model["scores"] = _scores(model)
        return models


def _check_corpus(
    corpus: str, runs: dict[tuple[str, str], list[ChatRun]], args: argparse.Namespace
) -> list[str]:
    """:func:`check_rung_provenance` on one corpus's runs, every finding and refusal naming it."""
    try:
        findings = check_rung_provenance(
            runs,
            allow_unpinned=args.allow_unpinned,
            allow_mixed=args.allow_mixed,
            allow_oracle=args.allow_oracle,
        )
    except SystemExit as exc:
        msg = f"{corpus}: {exc.code}"
        raise SystemExit(msg) from exc
    return [f"{corpus}: {finding}" for finding in findings]


def _last_run(timestamps: Any) -> str | None:
    """The day (UTC) of the newest of ``timestamps``, the date the page shows; None when there are none."""
    newest = max(timestamps, default=None)
    return None if newest is None else newest.astimezone(UTC).strftime("%Y-%m-%d")


def _board_order(by_corpus: dict[str, Any]) -> dict[str, Any]:
    """The corpora in report order: :data:`BOARD_CORPUS` first when measured, then as the rows name them."""
    order = sorted(by_corpus, key=lambda corpus: corpus != BOARD_CORPUS)
    return {corpus: by_corpus[corpus] for corpus in order}


def _public_chat(pooled: dict[str, Any], suite: dict[str, Any] | None) -> dict[str, Any]:
    """A model's chat object as the page gets it: internal fields stripped, a timed-out total filled.

    A timed-out run may carry no counts: its denominator is its corpus's suite's.
    """
    chat = {k: v for k, v in pooled.items() if not k.startswith("_")}
    if chat["total"] is None and suite:
        chat["total"] = suite["total"]
    return chat


def _add_comparison(
    suite: dict[str, Any],
    runs: dict[tuple[str, str], list[ChatRun]],
    models: list[dict[str, Any]],
    args: argparse.Namespace,
    review: ReviewOverlay | None = None,
) -> None:
    """Put one corpus's three-rung comparison on its chat suite, headlines withheld under an override.

    With ``review`` the comparison is the hand-graded one, the scorer's kept beside it
    (:func:`_comparison`).
    """
    comparison = _comparison(
        runs, suite["fixture"], models, include_errored=args.include_errored, review=review
    )
    if comparison:
        _withhold_headlines(comparison, _overrides_passed(args))
        suite["comparison"] = comparison


def _require_full_review(review: ReviewOverlay) -> None:
    """Stop unless ``review`` matched every comparison answer it was asked about (and some were).

    Raises:
        SystemExit: Naming the coverage and the first answers the review does not cover.
    """
    if review.full:
        return
    if not review.answers:
        msg = (
            f"--reviews {review.corpus}: {review.path.name} has no comparison answers to grade "
            "(no model of that corpus has rows under all three rungs). Nothing exported."
        )
        raise SystemExit(msg)
    msg = (
        f"--reviews {review.corpus}: {review.path.name} matches {review.matched:,} of "
        f"{review.answers:,} comparison answers; a corpus's review must match every one (each "
        f"complete model's answer under every rung to every question). Not covered: "
        f"{', '.join(review.unmatched)}"
        f"{', ...' if review.answers - review.matched > len(review.unmatched) else ''}. "
        "Nothing exported."
    )
    raise SystemExit(msg)


def _review_summary(review: ReviewOverlay | None) -> dict[str, Any] | None:
    """A corpus entry's ``review``: null without one, else its coverage, file name and content hash.

    ``full`` is every comparison answer matched; the export stops on any other
    review (:func:`_require_full_review`), so a written board's is always full.

    Raises:
        SystemExit: When the review graded no comparison answer, or not every one.
    """
    if review is None:
        return None
    _require_full_review(review)
    return {
        "answers": review.answers,
        "matched": review.matched,
        "full": review.full,
        "file": review.path.name,
        "file_sha256": hashlib.sha256(review.path.read_bytes()).hexdigest(),
    }


def _board_corpus_entry(
    suite: dict[str, Any] | None,
    runs: dict[tuple[str, str], list[ChatRun]],
    models: list[dict[str, Any]],
    args: argparse.Namespace,
    review: ReviewOverlay | None = None,
) -> dict[str, Any]:
    """The board corpus: its comparison goes on its suite (``suites.chat``); its entry adds its review."""
    if suite:
        _add_comparison(suite, runs, models, args, review)
    return {"review": _review_summary(review)}


def _corpus_entry(
    rows: dict[str, dict[str, Any]],
    runs: dict[tuple[str, str], list[ChatRun]],
    board: _Board,
    review: ReviewOverlay | None = None,
) -> dict[str, Any]:
    """A corpus other than the board corpus: its chat suite, per-model chat objects and fixture hash.

    Computed from that corpus's rows alone. The comparison's models are that
    corpus's (:meth:`_Board.models`: its own metadata and scores), so its
    worked examples come from the best model on that corpus, never from
    another corpus's ranking, and a model run on it alone is in it.
    """
    suite = _chat_suite(rows) if rows else None
    public = {mid: _public_chat(pooled, suite) for mid, pooled in rows.items()}
    if suite:
        _add_comparison(suite, runs, board.models(rows, suite), board.args, review)
    queries = _fixture_queries(suite["fixture"]) if suite else None
    return {
        "chat": suite,
        "models": public,
        "fixture_sha256": getattr(queries, "sha256", None),
        "review": _review_summary(review),
    }


def _provenance(
    args: argparse.Namespace,
    findings: list[str],
    dropped: list[dict[str, str | None]],
    chat_suite: dict[str, Any] | None,
) -> dict[str, Any]:
    """The board's ``provenance`` block: the override flags passed, what they lifted, what scored it.

    ``overrides`` lists every :data:`OVERRIDE_FLAGS` flag the run passed,
    ``findings`` the refusals they lifted, ``dropped_models`` each row left
    out (model, file, why, flag), ``scorer_recipe``/``scorer_version`` the
    chat scorer every chat row was scored with (:func:`_score_row`), and
    ``fixture_sha256`` the content of the board's question file. The page
    shows a banner when ``overrides`` is not empty, and when the block is
    missing or unrecorded (:func:`provenance_problems`: unknown is not clean).
    """
    from chaoscypher_core.benchmark.scorers.chat_probe_recipe import scorer_recipe
    from chaoscypher_core.benchmark.scorers.chat_probes import CHAT_PROBE_SCORER_VERSION

    queries = _fixture_queries(chat_suite["fixture"]) if chat_suite else None
    return {
        "overrides": _overrides_passed(args),
        "findings": list(findings),
        "dropped_models": list(dropped),
        "scorer_version": CHAT_PROBE_SCORER_VERSION if chat_suite else None,
        "scorer_recipe": scorer_recipe() if chat_suite else None,
        "fixture_sha256": getattr(queries, "sha256", None),
    }


def _overrides_passed(args: argparse.Namespace) -> list[str]:
    """Every :data:`OVERRIDE_FLAGS` flag the run passed, in that order."""
    return [
        flag for flag in OVERRIDE_FLAGS if getattr(args, flag.removeprefix("--").replace("-", "_"))
    ]


def _withhold_headlines(comparison: dict[str, Any], overrides: list[str]) -> None:
    """No headline holds on a board an override let through (audit wave 5).

    A headline's bar is met over rows the exporter would have refused, so it
    is not a claim the board can make: ``holds`` turns False and
    ``withheld_by`` names the flags (the page and the teaser read ``holds``).
    The scorer's block beside a hand-graded one (``scorer``) is withheld too.
    """
    if not overrides:
        return
    for block in (comparison, comparison.get("scorer") or {}):
        for headline in (block.get("value") or {}).get("headlines") or []:
            if headline.get("holds"):
                headline["holds"] = False
                headline["withheld_by"] = list(overrides)


PROVENANCE_KEYS = (
    "overrides",
    "findings",
    "dropped_models",
    "scorer_version",
    "scorer_recipe",
    "fixture_sha256",
)
"""The keys of the board's ``provenance`` block (:func:`_provenance`)."""

CHAT_PROVENANCE_KEYS = ("scorer_version", "scorer_recipe", "fixture_sha256")
"""The :data:`PROVENANCE_KEYS` a board with a chat suite must record (what scored it)."""


UNRECORDED_PROVENANCE = (
    "the provenance block is unrecorded (every key null): exported before the exporter "
    "recorded what scored the board and what let it through"
)
"""What :func:`provenance_problems` says of an explicit unrecorded block (the page: unknown)."""


def provenance_problems(board: dict[str, Any]) -> list[str]:
    """Why a board's ``provenance`` block is missing, unrecorded or not self-consistent; empty when sound.

    Sound means: every :data:`PROVENANCE_KEYS` key present; ``overrides`` a
    list of :data:`OVERRIDE_FLAGS` (``null`` with every other key null is an
    export from before the block, :data:`UNRECORDED_PROVENANCE`); ``findings``
    and ``dropped_models`` lists; a lifted finding only beside an override;
    each dropped row's flag an override that was passed (or none: a row left
    out without one, such as a judged row); and a board with a chat suite
    names the scorer recipe, version and fixture hash that scored it
    (:data:`CHAT_PROVENANCE_KEYS`). The page applies the same rule
    (``packages/docs/src/lib/leaderboardProvenance.ts`` ``provenanceProblems``),
    and a parity test runs both on the same boards (audit wave 6: the page
    skipped the findings, flag and drop checks); it shows a banner unless the
    block is sound and has no override. Before any of it, the board must be in
    the one layout this exporter writes (:data:`BOARD_FORMAT`): an old or
    unknown layout is unknown, never clean.
    """
    fmt = board.get("board_format", 1)
    if type(fmt) is not int or fmt != BOARD_FORMAT:
        return [f"board format {json.dumps(fmt)}: the page reads format {BOARD_FORMAT} only"]
    return _block_problems(board)


def _block_problems(board: dict[str, Any]) -> list[str]:
    """:func:`provenance_problems` on a board in the current layout: the block's own checks."""
    block = board.get("provenance")
    if not isinstance(block, dict):
        return ["no provenance block: an export from before audit wave 4, scored by unknown code"]
    missing = [k for k in PROVENANCE_KEYS if k not in block]
    if missing:
        return [f"the provenance block lacks {', '.join(missing)}"]
    overrides = block["overrides"]
    if overrides is None:
        stray = [k for k in PROVENANCE_KEYS if block[k] is not None]
        if stray:
            return [f"an unrecorded provenance block (overrides null) records {', '.join(stray)}"]
        return [UNRECORDED_PROVENANCE]
    not_lists = [
        k for k in ("overrides", "findings", "dropped_models") if not isinstance(block[k], list)
    ]
    if not_lists:
        return [f"a recorded provenance block whose {', '.join(not_lists)} is not a list"]
    problems: list[str] = []
    unknown = [f for f in overrides if f not in OVERRIDE_FLAGS]
    if unknown:
        problems.append(
            f"overrides name flags the exporter has not got: {', '.join(map(str, unknown))}"
        )
    if block["findings"] and not overrides:
        problems.append("findings were lifted but no override is recorded")
    for drop in block["dropped_models"]:
        if not isinstance(drop, dict):
            problems.append(f"a dropped row that is not a record: {drop!r}")
        elif drop.get("flag") is not None and drop["flag"] not in overrides:
            problems.append(f"{drop.get('model')} was dropped by {drop['flag']}, not passed")
    if (board.get("suites") or {}).get("chat"):
        problems.extend(
            f"a chat board without {key}"
            for key in CHAT_PROVENANCE_KEYS
            if block[key] in (None, "")
        )
    return problems


def check_board(board: dict[str, Any]) -> list[str]:
    """Why a committed board is not what this checkout would score; empty when it is.

    :func:`provenance_problems`, then the recorded scorer recipe and version
    against today's (:func:`~chaoscypher_core.benchmark.scorers.chat_probe_recipe.scorer_recipe`)
    and the recorded fixture hash against the content of the chat fixture
    the board names, and every corpus's review fields (:func:`_review_problems`).
    The ``--check`` mode; ``rebuild_board.sh`` documents it.
    """
    from chaoscypher_core.benchmark.scorers.chat_probe_recipe import scorer_recipe
    from chaoscypher_core.benchmark.scorers.chat_probes import CHAT_PROBE_SCORER_VERSION

    problems = provenance_problems(board) or _corpora_problems(board)
    if problems:
        return problems
    problems.extend(_review_problems(board))
    block = board["provenance"]
    # every corpus other than the board corpus records what scored it in its own entry
    for entry in board.get("corpora") or []:
        if entry.get("board") or not entry.get("chat"):
            continue
        fixture = str(entry["chat"].get("fixture"))
        current = getattr(_fixture_queries(fixture), "sha256", None)
        if entry.get("fixture_sha256") != current:
            problems.append(
                f"{entry.get('id')}: scored on fixture {fixture} with sha256 "
                f"{entry.get('fixture_sha256')}; its content here hashes to {current}"
            )
    chat = (board.get("suites") or {}).get("chat")
    if not chat:
        return problems
    if block["scorer_recipe"] != scorer_recipe():
        problems.append(
            f"scored by chat scorer recipe {block['scorer_recipe']}; this checkout's is "
            f"{scorer_recipe()}"
        )
    if block["scorer_version"] != CHAT_PROBE_SCORER_VERSION:
        problems.append(
            f"scored by chat scorer v{block['scorer_version']}; this checkout's is "
            f"v{CHAT_PROBE_SCORER_VERSION}"
        )
    queries = _fixture_queries(str(chat.get("fixture")))
    current = getattr(queries, "sha256", None)
    if block["fixture_sha256"] != current:
        problems.append(
            f"scored on fixture {chat.get('fixture')} with sha256 {block['fixture_sha256']}; "
            f"its content here hashes to {current}"
        )
    return problems


REVIEW_KEYS = ("answers", "matched", "full", "file", "file_sha256")
"""The keys of a corpus entry's ``review`` record (:func:`_review_summary`)."""


def _review_problems(board: dict[str, Any]) -> list[str]:
    """Why a board's review fields are missing, stale or inconsistent with its headlines; empty when sound.

    Every corpus entry carries ``review`` (a board from before the review rule
    of 2026-10-23 does not). Null: the corpus's comparison is the scorer's, and
    nothing in it carries a second reading. A record: its file is archived in
    :data:`REVIEWS_DIR` with the content hash recorded, it matched every
    answer, and the comparison is the hand-graded one: the scorer's block
    beside it (``scorer``) and the scorer's reading on every headline
    (:func:`_one_review_problems`).
    """
    problems: list[str] = []
    chat = (board.get("suites") or {}).get("chat") or {}
    for entry in board.get("corpora") or []:
        corpus = entry.get("id")
        suite = chat if entry.get("board") else entry.get("chat") or {}
        comparison = suite.get("comparison") or {}
        headlines = (comparison.get("value") or {}).get("headlines") or []
        if "review" not in entry:
            problems.append(
                f"{corpus}: no review field (exported before the board showed the hand-graded "
                "numbers wherever a full review exists)"
            )
            continue
        review = entry["review"]
        if review is None:
            read = [
                str(h.get("key"))
                for h in headlines
                if {"scorer", "review"} & set(h.get("values") or {}) or "held_back" in h
            ]
            if read:
                problems.append(
                    f"{corpus}: no review, yet {', '.join(read)} carry a second reading"
                )
            if "scorer" in comparison:
                problems.append(f"{corpus}: no review, yet the comparison keeps a scorer block")
            continue
        problems.extend(
            f"{corpus}: {problem}" for problem in _one_review_problems(review, comparison)
        )
    return problems


def _one_review_problems(review: Any, comparison: dict[str, Any]) -> list[str]:
    """:func:`_review_problems` on one corpus's review record and its (hand-graded) comparison."""
    if not isinstance(review, dict) or any(k not in review for k in REVIEW_KEYS):
        return [f"a review record without {', '.join(REVIEW_KEYS)}: {review!r}"]
    problems: list[str] = []
    answers, matched, full = review["answers"], review["matched"], review["full"]
    if not (full is True and isinstance(answers, int) and answers > 0 and matched == answers):
        problems.append(
            f"the review matched {matched} of {answers} answers (marked "
            f"{'full' if full else 'partial'}); the export stops on a review short of every one"
        )
    path = REVIEWS_DIR / str(review["file"])
    if not path.is_file():
        problems.append(f"review {review['file']} is not archived in {REVIEWS_DIR}")
    else:
        current = hashlib.sha256(path.read_bytes()).hexdigest()
        if review["file_sha256"] != current:
            problems.append(
                f"read review {review['file']} with sha256 {review['file_sha256']}; its content "
                f"here hashes to {current}"
            )
    if not comparison:
        return problems
    scorer = comparison.get("scorer")
    if not isinstance(scorer, dict) or not {"models", "value"} <= set(scorer):
        problems.append("no scorer block (models, value) beside the hand-graded comparison")
        theirs: dict[str, Any] = {}
    else:
        theirs = {h.get("key"): h for h in (scorer["value"] or {}).get("headlines") or []}
    for headline in (comparison.get("value") or {}).get("headlines") or []:
        problems.extend(_headline_review_problems(headline, theirs))
    return problems


def _headline_review_problems(headline: dict[str, Any], theirs: dict[str, Any]) -> list[str]:
    """One hand-graded headline against the scorer's reading of it and the scorer block's headline."""
    key = headline.get("key")
    values = headline.get("values") or {}
    if "review" in values:
        return [
            f"{key}: a values.review reading (exported under the first rule of 2026-10-23, which "
            "kept the scorer's numbers)"
        ]
    reading = values.get("scorer")
    if not isinstance(reading, dict) or not isinstance(reading.get("holds"), bool):
        return [f"{key}: no scorer's reading beside the hand-graded numbers"]
    problems: list[str] = []
    other = theirs.get(key)
    if other is not None and {k: v for k, v in reading.items() if k != "holds"} != other.get(
        "values"
    ):
        problems.append(f"{key}: its scorer's reading differs from the scorer block's headline")
    held = "held_back" in headline
    if held and (headline.get("holds") or not reading["holds"]):
        problems.append(
            f"{key}: held back though it holds hand-graded or fails on the scorer already"
        )
    if (
        reading["holds"]
        and not headline.get("holds")
        and not held
        and "withheld_by" not in headline
    ):
        problems.append(f"{key}: holds on the scorer, not hand-graded, with no held_back line")
    return problems


def _corpora_problems(board: dict[str, Any]) -> list[str]:
    """Why a format-:data:`BOARD_FORMAT` board's corpora break the layout; empty when they do not.

    ``corpora`` names each corpus once, exactly one of them the board corpus,
    and that one is the corpus of ``suites.chat`` (the page's ``boardCorpora``
    applies the same rules). The format itself is :func:`provenance_problems`'s.
    """
    corpora = board.get("corpora")
    if not isinstance(corpora, list) or not all(isinstance(c, dict) for c in corpora):
        return ["a format-2 board whose corpora is not a list of records"]
    problems: list[str] = []
    ids = [c.get("id") for c in corpora]
    if len(set(ids)) != len(ids):
        problems.append(f"a corpus is listed twice: {ids}")
    boards = [c.get("id") for c in corpora if c.get("board")]
    chat = (board.get("suites") or {}).get("chat")
    if corpora and len(boards) != 1:
        problems.append(f"{len(boards)} corpora marked the board corpus ({boards}); one is")
    elif chat and boards and boards[0] != chat.get("fixture"):
        problems.append(
            f"the board corpus {boards[0]} is not the chat suite's {chat.get('fixture')}"
        )
    return problems


def _warn_context_overflows(corpus: str, chat_runs: dict[tuple[str, str], list[ChatRun]]) -> None:
    """Print each chat row of ``corpus`` whose prompt plus answer overflowed the context window (audit H9)."""
    for (_mid, mode), items in sorted(chat_runs.items()):
        for row, _, _ in items:
            over = context_overflows(row)
            if over:
                print(
                    f"WARNING {corpus} {mode}: {row.model_label} overflowed its context window on "
                    f"{len(over)} question(s): {', '.join(over[:10])}"
                )


def _slim(model: dict[str, Any]) -> dict[str, Any]:
    """What a model picker needs: one score per suite, no per-probe detail."""

    def suite(s: dict[str, Any] | None) -> dict[str, Any] | None:
        if not s:
            return None
        return {
            "passed": s["passed"],
            "total": s["total"],
            "pct": round(100 * s["passed"] / s["total"]),
        }

    return {
        "id": model["id"].removeprefix("ollama/"),
        "label": model["label"],
        "vram_gb": model["vram_gb"],
        "license": model["license"],
        "tools": model["tools"],
        "isolated": suite(model.get("native")),
        "chunks": suite(model.get("carrier")),
        # Grounded chat, thinking on: the whole-number headline for a picker,
        # and the counts behind it. None when the model was not measured.
        "chat": _slim_chat(model.get("chat")),
        "chat_pct": (model.get("chat") or {}).get("pct"),
        # The same blended numbers the leaderboard page shows.
        "scores": model["scores"],
        "notes": model["notes"],
        "pins_applied": model["pins_applied"],
        "harness": model["harness"],
        "app_versions": model["app_versions"],
    }


def _slim_chat(chat: dict[str, Any] | None) -> dict[str, Any] | None:
    """The chat counts a picker can show, without the per-check breakdown."""
    if not chat:
        return None
    return {k: chat[k] for k in ("passed", "total", "pct", "timed_out")}


def _plural(n: int, word: str) -> str:
    """'1 task', '3 tasks'."""
    return f"{n} {word}{'' if n == 1 else 's'}"


def _notes(model: dict[str, Any], suite_meta: dict[str, dict[str, Any]]) -> list[str]:
    """Repeats, short runs, completion problems and extra sessions worth a flag, worded once for every surface."""
    notes: list[str] = []
    if model["harness"]:
        notes.append(f"harness: {model['harness']}")
        notes.extend(format_harness_settings(model.get("harness_settings")))
    if not model["pins_applied"]:
        # No temperature/seed/thinking pins: the client ran on its own settings.
        notes.append("pins not applied")
    for key in ("native", "carrier"):
        s, name = model.get(key), SUITE_NAMES[key]
        if not s:
            continue
        if s["runs"] > 1:
            notes.append(f"{name}: mean of {s['runs']} runs")
        if s["total"] < suite_meta[key]["total"]:
            notes.append(f"{name}: {s['total']} of {suite_meta[key]['total']} probes run")
        if s["truncated"]:
            notes.append(f"{s['truncated']} cut off {name}")
        if s["aborted"]:
            notes.append(f"{s['aborted']} looped {name}")
        if s["thinking_honoured"] is False and "thought despite think=off" not in notes:
            notes.append("thought despite think=off")
    chat = model.get("chat")
    if chat and chat.get("passed") is not None and chat.get("runs", 1) > 1:
        notes.append(f"chat: mean of {chat['runs']} runs")
    extra = sum(
        int((s or {}).get("extra_sessions") or 0)
        for s in (model.get("native"), model.get("carrier"), chat)
    )
    tasks = sum(
        int((s or {}).get("retried_tasks") or 0)
        for s in (model.get("native"), model.get("carrier"), chat)
    )
    if extra:
        notes.append(
            f"{_plural(extra, 'extra session')} on {_plural(tasks, 'task')}: answered in text "
            "without submitting, re-run until it submitted"
        )
    return notes


if __name__ == "__main__":
    main()
