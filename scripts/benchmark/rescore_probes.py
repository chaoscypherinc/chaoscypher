# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Re-score a saved probe-benchmark run against the current probe fixture.

Usage (from the repo root):
    uv run python scripts/benchmark/rescore_probes.py [--reparse] <results.json> <out.json> [SECTION]

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
from chaoscypher_cli.benchmark.scorers.probes import rescore


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
        strict_entity_types=probe.strict_types,
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
    args = [a for a in args if a != "--reparse"]
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
    for row in rows:
        extras = row.extras or {}
        if row.dataset_kind == "chat" and extras.get("per_query"):
            changed += _rescore_chat(row)
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
        row.chunks_truncated, row.chunks_aborted_by_loop = completion_counters(extras["probes"])
    dump_results(rows, out)
    print(f"rescored {len(rows)} rows ({changed} changed) -> {out}")


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


def _rescore_chat(row: Any) -> int:
    """Re-apply the chat probe checks to a saved chat row; 1 if a verdict flipped.

    Judge-scored rows are left untouched: probe metrics would silently
    replace the judge's.
    """
    if not _is_probe_scored(row):
        print(f"{row.model_label}: judge-scored chat row, not rescored")
        return 0
    from chaoscypher_cli.benchmark.dataset import RawOutput
    from chaoscypher_cli.benchmark.discovery import load_dataset_bundle
    from chaoscypher_cli.benchmark.scorers.chat_probes import ChatProbeScorer

    corpus_id = row.dataset_id.split("__chat__")[0]
    queries = load_dataset_bundle(corpus_id).queries
    if queries is None:
        return 0
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
    return 1 if flips else 0


if __name__ == "__main__":
    main()
