# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Re-score a saved probe-benchmark run against the current probe fixture.

Usage (from the repo root):
    uv run python scripts/benchmark/rescore_probes.py [--reparse] [--queries FILE] [--allow-mixed] <results.json> <out.json> [SECTION]

A sweep loads the probe fixture once at launch, so a checker fix or a re-tier
made while it runs is not reflected in its verdicts. Rows carry the raw
entities and relationships per probe, so the current checkers can be
re-applied in seconds instead of re-running the models for hours. With
SECTION (e.g. ``H``) only that section is scored; otherwise the probes the
run selected (``extras["selected_ids"]``), or all of them.

With ``--reparse`` each extraction-probe record is first re-derived from its
saved ``raw_llm_response`` with the current line parser and entity filters
(``AIEntityExtractor.parse_harvest_outputs``), so a parser fix is reflected
without re-running the models. The raw text is the two passes joined by
``format_raw_harvest_response``; a record whose text does not split back
into exactly two passes keeps its saved entities.

Every rescored row is stamped with the scorer's ``scorer_recipe`` (a hash of
its verdicts on fixed samples) next to ``scorer_version``, and the script
prints the version and recipe of the chat scorer it rescored with, so a
rescore names the behaviour it applied rather than only the hand-bumped
number (scorer v5 drifted under one number; 2026-10-06 audit, H3).

A chat row is rescored against the question file it was asked: the row's
``extras["queries_file"]`` (a holdout set), else its dataset's own fixture.
Rows of one file that asked different question files are refused, and so is
a row none of whose answers is a question of the file it is rescored with:
until 2026-10-06 (audit wave 2) a holdout row was rescored against the main
fixture, every verdict read "missing" and the script printed "0 changed".
``--queries FILE`` names the file for holdout rows from before the field.

Rows of one file that record different question-file contents
(``extras["queries_sha256"]``, audit wave 3: the file's name alone hid a
fixture edit between two runs) are refused unless ``--allow-mixed``; a
rescored chat row records the content it was rescored with as
``extras["rescored_queries_sha256"]`` next to the one it was asked.
"""

import asyncio
import logging
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import structlog

from chaoscypher_cli.benchmark.discovery import discover_datasets
from chaoscypher_cli.benchmark.probe_dataset import ProbeDataset, completion_counters
from chaoscypher_cli.benchmark.results import dump_results, load_results
from chaoscypher_cli.benchmark.scorers.probes import PROBE_SCORER_VERSION, rescore


_PASS1_HEADER = "=== PASS 1 (Entities) ===\n"
_PASS2_SEPARATOR = "\n\n=== PASS 2 (Relationships) ===\n"


def split_raw_harvest_response(raw: str) -> tuple[str, str] | None:
    """Split a saved ``raw_llm_response`` back into its pass-1 and pass-2 answers.

    Inverse of ``format_raw_harvest_response``. Returns None when the text
    does not start with the pass-1 header or carries the pass-2 separator
    other than exactly once (a model echoing the separator would make the
    split ambiguous).
    """
    if not raw.startswith(_PASS1_HEADER) or raw.count(_PASS2_SEPARATOR) != 1:
        return None
    pass1, _, pass2 = raw[len(_PASS1_HEADER) :].partition(_PASS2_SEPARATOR)
    return pass1, pass2


async def reparse_record(rec: dict[str, Any], probe: Any, extractor: Any, limits: Any) -> bool:
    """Re-derive a probe record's parsed fields from its raw response, in place.

    Updates ``entities``, ``relationships``, ``parser_lines_dropped`` and
    ``invalid_relationship_count`` with the probe's own exclusions and type
    constraints, as the probe runner passes them. Returns False (record
    untouched) when the record has no usable raw text.
    """
    split = split_raw_harvest_response(str(rec.get("raw_llm_response") or ""))
    sentences = rec.get("sentences")
    if split is None or not isinstance(sentences, list):
        return False
    from chaoscypher_core.services.sources.engine.extraction.domains.config_schema import (
        ExclusionRule,
    )

    parsed = await extractor.parse_harvest_outputs(
        split[0],
        split[1],
        sentences=sentences,
        chunk_content=" ".join(sentences),
        numbered_text="",
        edge_templates_formatted="",
        limits=limits,
        entity_exclusions=[ExclusionRule(**e) for e in probe.entity_exclusions] or None,
        valid_entity_type_names=set(probe.entity_types) or None,
    )
    rec["entities"] = parsed.entities
    rec["relationships"] = parsed.relationships
    rec["parser_lines_dropped"] = parsed.parser_lines_dropped
    rec["invalid_relationship_count"] = parsed.invalid_relationship_count
    return True


async def reparse_rows(rows: list[Any], probes_by_id: dict[str, Any]) -> None:
    """Reparse every extraction-probe record and print per-model entity-count changes."""
    from chaoscypher_core.services.sources.engine.extraction.utils.ai_entities import (
        AIEntityExtractor,
    )
    from chaoscypher_core.settings import EngineSettings

    extractor = AIEntityExtractor(settings=EngineSettings())
    limits = extractor.resolve_harvest_limits()
    for row in rows:
        records = (row.extras or {}).get("probes") or []
        tally: Counter[str] = Counter()
        for rec in records:
            probe = probes_by_id.get(str(rec.get("id")))
            if probe is None or "raw_llm_response" not in rec:
                continue
            before = len(rec.get("entities") or [])
            if not await reparse_record(rec, probe, extractor, limits):
                tally["unsplittable"] += 1
                continue
            tally["reparsed"] += 1
            after = len(rec["entities"])
            if after != before:
                tally["changed"] += 1
                tally["gained" if after > before else "lost"] += after - before
        if records:
            print(
                f"{row.model_label}: reparsed {tally['reparsed']}, entity count changed in "
                f"{tally['changed']} (+{tally['gained']} / {tally['lost']} entities), "
                f"unsplittable {tally['unsplittable']}"
            )


def main() -> None:
    """Rescore every row of the results file and write the updated copy."""
    args = sys.argv[1:]
    do_reparse = "--reparse" in args
    allow_mixed = "--allow-mixed" in args
    args = [a for a in args if a not in ("--reparse", "--allow-mixed")]
    override: Path | None = None
    if "--queries" in args:
        at = args.index("--queries")
        override = Path(args[at + 1])
        args = args[:at] + args[at + 2 :]
    src, out = Path(args[0]), Path(args[1])
    section = args[2] if len(args) > 2 else None
    dataset = next(d for d in discover_datasets() if isinstance(d, ProbeDataset))
    rows = load_results(src)
    if do_reparse:
        # The parser and filters log every line and drop; thousands of
        # records would bury the per-model summary.
        structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.ERROR))
        asyncio.run(reparse_rows(rows, {p.id: p for p in dataset.probes}))
    changed = 0
    chat_recipes: set[str] = set()
    chat_rows = [r for r in rows if r.dataset_kind == "chat" and (r.extras or {}).get("per_query")]
    mixed = mixed_question_contents(chat_rows)
    if mixed:
        if not allow_mixed:
            msg = f"{mixed}; rescore each set on its own, or pass --allow-mixed"
            raise SystemExit(msg)
        print(f"WARNING (--allow-mixed) {mixed}")
    chat_questions = _chat_questions(chat_rows, override)
    for row in rows:
        extras = row.extras or {}
        if row.dataset_kind == "chat" and extras.get("per_query"):
            changed += _rescore_chat(row, *chat_questions(row))
            if _is_probe_scored(row):
                chat_recipes.add(f"v{row.scorer_version} recipe {row.scorer_recipe}")
            continue
        if not extras.get("probes"):
            continue
        selected = set(extras.get("selected_ids") or [])
        probes = [
            p
            for p in dataset.probes
            if (section is None or p.section == section) and (not selected or p.id in selected)
        ]
        before = {v["id"]: v["passed"] for v in row.metrics.get("verdicts", [])}
        result = rescore(row, probes)
        flips = [
            f"{v['id']}:{'P' if before[v['id']] else 'F'}->{'P' if v['passed'] else 'F'}"
            for v in result.metrics["verdicts"]
            if v["id"] in before and before[v["id"]] != v["passed"]
        ]
        if flips:
            changed += 1
            print(f"{row.model_label}: {', '.join(flips)}")
        row.metrics = result.metrics
        row.headline_score = result.headline_score
        row.scorer_version = PROBE_SCORER_VERSION
        row.scorer_recipe = result.metrics.get("scorer_recipe")
        row.chunks_truncated, row.chunks_aborted_by_loop = completion_counters(extras["probes"])
    dump_results(rows, out)
    print(f"rescored {len(rows)} rows ({changed} changed) -> {out}")
    if chat_recipes:
        # the behaviour that produced the new verdicts, not only the hand-bumped number
        print(f"chat probe scorer: {', '.join(sorted(chat_recipes))}")


def mixed_question_contents(rows: list[Any]) -> str | None:
    """Why the chat rows were asked different question-file contents; None when they were not.

    Compares the recorded ``extras["queries_sha256"]`` of the rows that record
    one (:func:`~chaoscypher_core.benchmark.results.recorded_queries_sha256`).
    """
    from chaoscypher_core.benchmark.results import recorded_queries_sha256

    seen: dict[str, list[str]] = {}
    for row in rows:
        digest = recorded_queries_sha256(row)
        if digest is not None:
            seen.setdefault(digest, []).append(row.model_label)
    if len(seen) < 2:
        return None
    detail = "; ".join(f"{d[:12]}: {len(m)} row(s)" for d, m in sorted(seen.items()))
    return f"rows asked different question-file contents ({detail})"


def _is_probe_scored(row: Any) -> bool:
    """Whether a chat row was scored by the chat probes rather than a judge.

    Probe-scored rows carry ``verdicts``; a failed or older probe run is
    recognised by having no judge and per-query records that kept the
    context the model saw (the probes need it; judged runs never stored it).
    """
    if "verdicts" in (row.metrics or {}):
        return True
    extras = row.extras or {}
    per_query = extras.get("per_query") or []
    return extras.get("judge_model") is None and any(
        isinstance(e, dict) and "context_text" in e for e in per_query
    )


def _chat_questions(rows: list[Any], override: Path | None) -> Any:
    """``row -> (questions, source, path)`` for the chat rows: the one question file they were asked.

    The file is each row's ``extras["queries_file"]`` when it is not the
    dataset's own (a holdout set), ``override`` for rows that record none,
    else the dataset fixture.

    Raises:
        SystemExit: When the rows asked different question files.
    """
    from chaoscypher_cli.benchmark.chat_dataset import dataset_queries_path
    from chaoscypher_cli.benchmark.discovery import load_dataset_bundle
    from chaoscypher_core.benchmark.queries import load_queries
    from chaoscypher_core.benchmark.results import (
        QuestionFileMismatchError,
        common_queries_file,
        row_corpus_id,
    )

    try:
        shared = common_queries_file(rows, dataset_queries_path, override=override)
    except QuestionFileMismatchError as exc:
        raise SystemExit(str(exc)) from exc
    loaded = load_queries(shared) if shared is not None else None

    def questions(row: Any) -> tuple[Any, str, Path | None]:
        """The question set, its name and its file for one row."""
        if loaded is not None:
            return loaded, str(shared), shared
        corpus_id = row_corpus_id(row)
        bundle = load_dataset_bundle(corpus_id)
        return bundle.queries, f"the {corpus_id} fixture", getattr(bundle, "queries_path", None)

    return questions


def _rescore_chat(
    row: Any,
    queries: Any = None,
    source: str = "the dataset fixture",
    queries_path: Path | None = None,
) -> int:
    """Re-apply the chat probe checks to a saved chat row; 1 if a verdict flipped.

    Judge-scored rows are left untouched: probe metrics would silently
    replace the judge's. ``queries`` is the question set the row was asked
    (:func:`_chat_questions`); without it, the dataset's own. ``queries_path``
    is its file, whose content hash the row records as ``rescored_queries_sha256``.

    Raises:
        SystemExit: When none of the row's answers is a question of ``queries``.
    """
    if not _is_probe_scored(row):
        print(f"{row.model_label}: judge-scored chat row, not rescored")
        return 0
    from chaoscypher_cli.benchmark.dataset import RawOutput
    from chaoscypher_cli.benchmark.discovery import load_dataset_bundle
    from chaoscypher_cli.benchmark.scorers.chat_probes import (
        CHAT_PROBE_SCORER_VERSION,
        ChatProbeScorer,
    )
    from chaoscypher_core.benchmark.chat_run import queries_file_sha256
    from chaoscypher_core.benchmark.results import unanswered_message

    if queries is None:
        queries = load_dataset_bundle(row.dataset_id.split("__chat__")[0]).queries
    if queries is None:
        return 0
    refusal = unanswered_message(row, {q.id for q in queries.queries}, source)
    if refusal:
        raise SystemExit(refusal)
    raw = RawOutput(
        entities=[],
        relationships=[],
        latency_ms=0,
        input_tokens=0,
        output_tokens=0,
        error=None,
        extras=row.extras,
    )
    before = {v["id"]: v["passed"] for v in row.metrics.get("verdicts", [])}
    result = ChatProbeScorer().score(raw, queries)
    flips = [
        f"{v['id']}:{'P' if before[v['id']] else 'F'}->{'P' if v['passed'] else 'F'}"
        for v in result.metrics["verdicts"]
        if v["id"] in before and before[v["id"]] != v["passed"]
    ]
    if flips:
        print(f"{row.model_label}: {', '.join(flips)}")
    row.metrics = result.metrics
    row.headline_score = result.headline_score
    row.scorer_version = CHAT_PROBE_SCORER_VERSION
    row.scorer_recipe = result.metrics.get("scorer_recipe")
    # the content the verdicts were scored with: hashed when it was parsed (audit wave 4)
    rescored_with = getattr(queries, "sha256", None) or (
        queries_file_sha256(queries_path) if queries_path is not None else None
    )
    if rescored_with is not None:
        row.extras = {**(row.extras or {}), "rescored_queries_sha256": rescored_with}
    return 1 if flips else 0


if __name__ == "__main__":
    main()
