# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Leaderboard aggregation and Markdown rendering."""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from chaoscypher_cli.benchmark.results import BenchmarkResult


@dataclass(frozen=True)
class ModelAggregate:
    """Aggregated stats for one model across the datasets it ran on.

    Attributes:
        model_id: ``provider/model`` identifier.
        model_label: Human-readable name.
        mean_score: Mean of headline_score across succeeded datasets, or
            None when every dataset failed.
        median_latency_ms_per_chunk_p50: Median of per-dataset p50
            latencies across succeeded datasets.
        total_cost_usd: Sum of cost across all datasets (succeeded + failed).
        succeeded_datasets: Dataset ids the model ran successfully on.
        failed_datasets: Dataset ids the model failed on.
    """

    model_id: str
    model_label: str
    mean_score: float | None
    median_latency_ms_per_chunk_p50: int
    total_cost_usd: float
    succeeded_datasets: list[str] = field(default_factory=list)
    failed_datasets: list[str] = field(default_factory=list)


def aggregate_by_model(rows: list[BenchmarkResult]) -> list[ModelAggregate]:
    """Group rows by model and compute mean/median/totals."""
    by_model: dict[str, list[BenchmarkResult]] = {}
    for r in rows:
        by_model.setdefault(r.model_id, []).append(r)

    out: list[ModelAggregate] = []
    for model_id, model_rows in by_model.items():
        succeeded = [r for r in model_rows if r.success]
        failed = [r for r in model_rows if not r.success]
        mean = statistics.mean(r.headline_score for r in succeeded) if succeeded else None
        med_lat = (
            int(statistics.median(r.latency_ms_per_chunk_p50 for r in succeeded))
            if succeeded
            else 0
        )
        total_cost = sum(r.cost_usd for r in model_rows)
        out.append(
            ModelAggregate(
                model_id=model_id,
                model_label=model_rows[0].model_label,
                mean_score=mean,
                median_latency_ms_per_chunk_p50=med_lat,
                total_cost_usd=total_cost,
                succeeded_datasets=[r.dataset_id for r in succeeded],
                failed_datasets=[r.dataset_id for r in failed],
            )
        )
    return out


def render_leaderboard(rows: list[BenchmarkResult]) -> str:
    """Render rows as a Markdown leaderboard.

    Groups result rows by ``dataset_kind`` and emits one section per kind
    that is present (extraction, embedding, chat).  A shared run-metadata
    preamble appears at the top.

    Layout: header with run metadata (and config name when present);
    per-kind sections (extraction → embedding → chat);
    each extraction section contains a ranked headline table, per-dataset
    drill-down, and "did not complete" subsection.
    """
    if not rows:
        return "# ChaosCypher Extraction Benchmark\n\nNo results.\n"

    preamble_lines = _build_header_section(rows)
    preamble = "\n".join(preamble_lines)

    by_kind: dict[str, list[BenchmarkResult]] = {}
    for r in rows:
        by_kind.setdefault(r.dataset_kind, []).append(r)

    sections: list[str] = []
    if "extraction" in by_kind:
        sections.append(_render_extraction_section(by_kind["extraction"]))
    if "embedding" in by_kind:
        sections.append(_render_embedding_section(by_kind["embedding"]))
    if "chat" in by_kind:
        sections.append(_render_chat_section(by_kind["chat"]))

    body = "\n\n".join(sections)
    return preamble + body


def _build_header_section(rows: list[BenchmarkResult]) -> list[str]:
    """Build the title, run-metadata blurb, and any heterogeneity warnings."""
    dataset_ids = sorted({r.dataset_id for r in rows})
    model_ids = sorted({r.model_id for r in rows})
    seeds = sorted({r.seed for r in rows})
    temps = sorted({r.temperature for r in rows})
    benchmark_versions = sorted({r.benchmark_version for r in rows})
    scorer_versions = sorted({r.scorer_version for r in rows})
    config_names = sorted({r.config_name for r in rows if r.config_name})
    user_datasets = sorted({r.dataset_id for r in rows if r.dataset_source == "user"})
    run_date = max(r.timestamp for r in rows).strftime("%Y-%m-%d")

    title_suffix = ""
    if config_names:
        title_suffix = " (config: " + ", ".join(config_names) + ")"

    lines: list[str] = [
        f"# ChaosCypher Extraction Benchmark - {run_date}{title_suffix}",
        "",
        (
            f"Benchmark v{','.join(benchmark_versions)} . "
            f"Scorer v{','.join(str(v) for v in scorer_versions)} . "
            f"{len(dataset_ids)} datasets . {len(model_ids)} models . "
            f"single shot . temp={','.join(str(t) for t in temps)} . "
            f"seed={','.join(str(s) for s in seeds)}"
        ),
        "",
    ]

    # Heterogeneous-version warnings.
    dataset_versions: dict[str, set[str]] = {}
    for r in rows:
        dataset_versions.setdefault(r.dataset_id, set()).add(r.dataset_version)
    mixed = [did for did, vs in dataset_versions.items() if len(vs) > 1]
    if mixed:
        lines.append(
            "> WARNING heterogeneous run: dataset_version mismatch on " + ", ".join(sorted(mixed))
        )
        lines.append("")
    if len(scorer_versions) > 1:
        lines.append(f"> WARNING heterogeneous run: scorer_version mismatch ({scorer_versions})")
        lines.append("")
    if user_datasets:
        lines.append(
            "> NOTE includes user-overlay datasets (not reproducible from pip alone): "
            + ", ".join(user_datasets)
        )
        lines.append("")
    return lines


def _render_extraction_section(rows: list[BenchmarkResult]) -> str:
    """Render the extraction leaderboard section as Markdown."""
    dataset_ids = sorted({r.dataset_id for r in rows})

    aggs = aggregate_by_model(rows)
    completed = [a for a in aggs if a.mean_score is not None]
    incomplete = [a for a in aggs if a.mean_score is None]
    completed.sort(
        key=lambda a: (
            -(a.mean_score or 0.0),
            a.median_latency_ms_per_chunk_p50,
            a.total_cost_usd,
        )
    )

    lines: list[str] = ["## Extraction Leaderboard", ""]
    lines.extend(_build_headline_table(completed))
    lines.extend(_build_per_dataset_section(completed, dataset_ids, rows))
    lines.extend(_build_incomplete_section(incomplete))
    return "\n".join(lines)


def _build_headline_table(completed: list[ModelAggregate]) -> list[str]:
    """Render the ranked headline quality/speed/cost table."""
    lines: list[str] = [
        "| Rank | Model | Quality | Speed (ms/chunk p50) | Cost (run) | Notes |",
        "|------|-------|---------|---------------------|-----------:|-------|",
    ]
    for rank, a in enumerate(completed, start=1):
        notes = ""
        if a.failed_datasets:
            notes = f"failed: {', '.join(a.failed_datasets)}"
        cost_str = f"${a.total_cost_usd:.2f}" if a.total_cost_usd > 0 else "$0.00"
        mean_score = a.mean_score
        assert mean_score is not None
        lines.append(
            f"| {rank} | {a.model_label} | {mean_score:.1f} | "
            f"{a.median_latency_ms_per_chunk_p50:,} | {cost_str} | {notes} |"
        )
    lines.append("")
    return lines


def _build_per_dataset_section(
    completed: list[ModelAggregate],
    dataset_ids: list[str],
    rows: list[BenchmarkResult],
) -> list[str]:
    """Render the per-dataset drill-down table; empty when no datasets."""
    if not dataset_ids:
        return []
    lines: list[str] = [
        "## Per-dataset scores",
        "",
        "| Model | " + " | ".join(dataset_ids) + " |",
        "|" + "------|" * (len(dataset_ids) + 1),
    ]
    scores_by_model_dataset: dict[tuple[str, str], BenchmarkResult] = {
        (r.model_id, r.dataset_id): r for r in rows
    }
    for a in completed:
        cells = [a.model_label]
        for did in dataset_ids:
            r = scores_by_model_dataset.get((a.model_id, did))
            if r is None:
                cells.append("-")
            elif not r.success:
                cells.append(f"failed: {r.error}")
            else:
                cells.append(f"{r.headline_score:.1f}")
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")
    return lines


def _build_incomplete_section(incomplete: list[ModelAggregate]) -> list[str]:
    """Render the "did not complete" section; empty when all models completed."""
    if not incomplete:
        return []
    lines: list[str] = ["## Did not complete", ""]
    lines.extend(
        f"- **{a.model_label}** - failed on all datasets: {', '.join(a.failed_datasets)}"
        for a in incomplete
    )
    lines.append("")
    return lines


def _render_embedding_section(rows: list[BenchmarkResult]) -> str:
    """Render the embedding leaderboard section as Markdown."""
    rows = sorted(rows, key=lambda r: -r.headline_score)
    lines = [
        "## Embedding Leaderboard",
        "",
        "| Rank | Model | MRR | R@1 | R@3 | Skipped | Cost (USD) |",
        "|---|---|---|---|---|---|---|",
    ]
    for i, r in enumerate(rows, start=1):
        m = r.metrics
        total_queries = m.get("queries_scored", 0) + m.get("queries_unresolved", 0)
        lines.append(
            f"| {i} | {r.model_label} | {m.get('mrr', 0):.3f} | "
            f"{m.get('recall_at_1', 0):.2f} | {m.get('recall_at_3', 0):.2f} | "
            f"{m.get('queries_unresolved', 0)} / {total_queries} | "
            f"{r.cost_usd:.4f} |"
        )
    return "\n".join(lines)


def _render_chat_section(rows: list[BenchmarkResult]) -> str:
    """Render the chat leaderboard section as Markdown."""
    rows = sorted(rows, key=lambda r: -r.headline_score)
    lines = [
        "## Chat Leaderboard",
        "",
        "| Rank | Model | Faithfulness | Correctness | Refusal | Cost (USD) |",
        "|---|---|---|---|---|---|",
    ]
    judge_id = ""
    for i, r in enumerate(rows, start=1):
        m = r.metrics
        if not judge_id and m.get("judge_model"):
            judge_id = f"{m.get('judge_provider')}/{m.get('judge_model')}"
        lines.append(
            f"| {i} | {r.model_label} | {m.get('faithfulness_avg') or 0:.2f} | "
            f"{m.get('correctness_avg') or 0:.2f} | "
            f"{(m.get('refusal_correct_rate') or 0) * 100:.0f}% | "
            f"{r.cost_usd:.4f} |"
        )
    if judge_id:
        lines.append("")
        lines.append(f"**Judge:** {judge_id}")
    return "\n".join(lines)


__all__ = ["ModelAggregate", "aggregate_by_model", "render_leaderboard"]
