# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Export probe-benchmark runs as the data file behind the /leaderboard page.

Usage (from the repo root):
    uv run python scripts/benchmark/export_leaderboard.py \
        --native <probes-native.json> --carrier <probes-carrier.json> \
        [--chat <chat-run.json> ...] \
        --out packages/docs/src/data/leaderboard.json

Joins each model's rows with the model registry (VRAM, license) and keeps
the per-probe verdicts so the page can show which instructions failed, not
just how many. ``--chat`` adds the grounded-chat board: questions answered
from a fixed reference graph, thinking on. Only what was measured goes in: a model missing from a run
has that suite left out.
"""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from chaoscypher_cli.benchmark.discovery import discover_datasets
from chaoscypher_cli.benchmark.models_registry import load_registry
from chaoscypher_cli.benchmark.probe_dataset import ProbeDataset
from chaoscypher_cli.benchmark.results import format_harness_settings, load_results


SUITES = {"native": lambda section: section != "H", "carrier": lambda section: section == "H"}
VRAM_HEADROOM_GB = 4
# The one place the leaderboard's blended scores are defined; the page, the
# homepage teaser and the interface pickers all read the numbers this writes.
# Extraction: in chunks counts twice (it is what transfers to real data),
# isolated once (the diagnostic behind it). Overall: extraction 0.6, chat 0.4.
# Speed is left out because it depends on the GPU.
EXTRACTION_WEIGHTS = {"carrier": 2 / 3, "native": 1 / 3}
OVERALL_WEIGHTS = {"extraction": 0.6, "chat": 0.4}


def _pass_rate(suite: dict[str, Any]) -> float:
    """Passed over total; a run that did not finish has no passes and counts as 0."""
    total = suite.get("total")
    return (suite.get("passed") or 0) / total if total else 0.0


def _scores(model: dict[str, Any]) -> dict[str, float | None]:
    """Extraction, chat and overall as percentages; None where a board was not run."""
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
    }


def _suite_rows(paths: list[Path] | None, keep: Any) -> dict[str, dict[str, Any]]:
    """One summary per model id for the probes of one suite.

    Several files may feed one suite (a main sweep plus an addition pass for
    models added later); a later file's row replaces an earlier one.
    """
    out: dict[str, dict[str, Any]] = {}
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
        out[row.model_id] = {
            "passed": sum(verdicts.values()),
            "total": len(verdicts),
            "p50_ms": row.latency_ms_per_chunk_p50,
            "minutes": round(row.latency_ms_total / 60000, 1),
            "output_tokens": row.output_tokens,
            "truncated": row.chunks_truncated,
            "aborted": row.chunks_aborted_by_loop,
            "thinking_honoured": row.thinking_honoured,
            "pins_applied": row.pins_applied,
            "harness": row.harness,
            "harness_settings": row.harness_settings,
            "model_label": row.model_label,
            "verdicts": verdicts,
        }
    return out


def _chat_rows(paths: list[Path] | None) -> dict[str, dict[str, Any]]:
    """One grounded-chat summary per model, keyed by model id.

    Only ``dataset_kind == "chat"`` rows count: a chat result file also holds
    the extraction and embedding rows that built the reference graph. A later
    file's row replaces an earlier one, as for the probe suites. A harness row
    (a model answering the same reference graph through an MCP client) counts
    like any other and joins that model's probe rows by model id. Internal
    fields (``_fixture``, ``_row``) are stripped before the object reaches
    the page; ``verdicts`` stays, one boolean per question.
    """
    out: dict[str, dict[str, Any]] = {}
    for row in (r for path in paths or [] for r in load_results(path)):
        if row.dataset_kind != "chat":
            continue
        verdicts = row.metrics.get("verdicts", [])
        timed_out = not row.success and "Timeout" in (row.error or "")
        if timed_out:
            passed = None
        elif row.success and verdicts:
            passed = int(row.metrics.get("probes_passed", sum(bool(v["passed"]) for v in verdicts)))
        else:
            # Failed for another reason, or scored nothing: "not run".
            continue
        total = row.metrics.get("probes_total") or len(verdicts) or None
        out[row.model_id] = {
            "passed": passed,
            "total": total,
            "pct": round(100 * passed / total, 1) if passed is not None and total else None,
            "minutes": round(row.latency_ms_total / 60000, 1),
            "truncated": row.chunks_truncated,
            "timed_out": timed_out,
            # A harness row ran on the client's own thinking setting: unknown.
            "thinking": None if row.harness else bool(row.thinking),
            # The answer text is not on the row and no verdict detail says
            # whether an answer declined, so false declines are not counted.
            "unsupported_names": None
            if passed is None
            else sum(
                any(str(d).startswith("FAIL no_unsupported_names") for d in v.get("details", []))
                for v in verdicts
            ),
            # One verdict per question, for the page's per-question strip.
            "verdicts": {v["id"]: bool(v["passed"]) for v in verdicts}
            if passed is not None
            else {},
            "_fixture": row.dataset_id.split("__chat__")[0],
            "_row": row,
        }
    return out


def _chat_suite(rows: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Size, fixture, questions and retrieval floor of the grounded-chat board.

    The floor is the number of questions every scored model failed: on a
    fixed reference graph those are, in practice, retrieval misses (the fact
    never reached any model's context). One model alone says nothing about
    retrieval, so the floor needs at least two scored rows. ``questions``
    lists the fixture's questions in order (id, section, tier, text; never
    the answer terms) so the page can draw one cell per question.
    """
    scored = [r["verdicts"] for r in rows.values() if r["passed"] is not None]
    floor_ids: list[str] = []
    if len(scored) >= 2:
        ids = set.intersection(*(set(v) for v in scored))
        floor_ids = sorted(qid for qid in ids if all(not v[qid] for v in scored))
    fixtures = sorted({r["_fixture"] for r in rows.values()})
    fixture = fixtures[-1] if len(fixtures) == 1 else ", ".join(fixtures)
    return {
        "total": max((r["total"] or 0 for r in rows.values()), default=0),
        "fixture": fixture,
        "retrieval_floor": len(floor_ids) if len(scored) >= 2 else None,
        "retrieval_floor_ids": floor_ids,
        "questions": _chat_questions(fixture),
    }


def _chat_questions(fixture: str) -> list[dict[str, str]]:
    """The chat fixture's questions as the page shows them: id, section, tier and text."""
    from chaoscypher_cli.benchmark.discovery import load_dataset_bundle
    from chaoscypher_core.benchmark.scorers.chat_probes import BAND_TIER

    try:
        queries = load_dataset_bundle(fixture).queries
    except FileNotFoundError, ValueError:
        queries = None
    if queries is None:
        return []
    return [
        {"id": q.id, "section": q.band, "tier": BAND_TIER.get(q.band, ""), "question": q.question}
        for q in queries.queries
    ]


def main() -> None:
    """Write the leaderboard data file."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--native", type=Path, nargs="+")
    ap.add_argument("--carrier", type=Path, nargs="+")
    ap.add_argument(
        "--chat",
        type=Path,
        nargs="+",
        help="Grounded-chat run(s); a later file's row replaces an earlier one per model",
    )
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--registry", type=Path, help="Model registry YAML (default: the built-in one)")
    ap.add_argument(
        "--interface-out",
        type=Path,
        help="Slim copy (no verdicts) for the interface model pickers",
    )
    args = ap.parse_args()

    dataset = next(d for d in discover_datasets() if isinstance(d, ProbeDataset))
    probes = [
        {"id": p.id, "section": p.section, "tier": p.tier, "instruction": p.instruction}
        for p in dataset.probes
    ]
    registry = load_registry(path=args.registry)
    suites = {name: _suite_rows(getattr(args, name), keep) for name, keep in SUITES.items()}
    chat = _chat_rows(args.chat)
    chat_suite = _chat_suite(chat) if chat else None
    ids = sorted({mid for rows in (*suites.values(), chat) for mid in rows})
    models = []
    for mid in ids:
        entry = registry.get(mid)
        measured = [rows[mid] for rows in suites.values() if mid in rows]
        if mid in chat:
            row = chat[mid]["_row"]
            measured.append(
                {
                    "harness": row.harness,
                    "harness_settings": row.harness_settings,
                    "pins_applied": row.pins_applied,
                    "model_label": row.model_label,
                }
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
                model[name] = rows[mid]
        if mid in chat:
            model["chat"] = {k: v for k, v in chat[mid].items() if not k.startswith("_")}
            # A timed-out run may carry no counts: its denominator is the board's.
            if model["chat"]["total"] is None and chat_suite:
                model["chat"]["total"] = chat_suite["total"]
        # Harness-track rows (a model run inside an MCP client, on its own
        # settings) must stay distinguishable so the page can group them.
        model["pins_applied"] = all(m["pins_applied"] for m in measured)
        model["harness"] = harness
        # What the client said about its own effort/thinking/version: part of
        # the row's identity, since any of it can move the score.
        model["harness_settings"] = next(
            (m["harness_settings"] for m in measured if m["harness_settings"]), None
        )
        models.append(model)
    for model in models:
        model["notes"] = _notes(model)
        model["scores"] = _scores(model)
    suite_meta = {
        name: {"total": max((r["total"] for r in rows.values()), default=0)}
        for name, rows in suites.items()
    }
    if chat_suite:
        suite_meta["chat"] = chat_suite
    payload = {
        "generated": datetime.now(tz=UTC).strftime("%Y-%m-%d"),
        "thinking": "off",
        # The chat board is scored with thinking on: that is how chat runs.
        "chat_thinking": "on",
        # Weights alone are not the footprint: the context window needs room.
        # Both the page filter and the interface picker read this one number.
        "vram_headroom_gb": VRAM_HEADROOM_GB,
        # How the blended scores on every row were computed, so a page can say so.
        "score_weights": {"extraction": EXTRACTION_WEIGHTS, "overall": OVERALL_WEIGHTS},
        # The suite size is what the runs attempted, not what the fixture holds
        # today: the page header must match the rows it labels.
        "suites": suite_meta,
        "probes": probes,
        "models": models,
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
    }


def _slim_chat(chat: dict[str, Any] | None) -> dict[str, Any] | None:
    """The chat counts a picker can show, without the per-check breakdown."""
    if not chat:
        return None
    return {k: chat[k] for k in ("passed", "total", "pct", "timed_out")}


def _notes(model: dict[str, Any]) -> list[str]:
    """Completion problems worth a flag, worded once for every surface."""
    notes: list[str] = []
    if model["harness"]:
        notes.append(f"harness: {model['harness']}")
        notes.extend(format_harness_settings(model.get("harness_settings")))
    if not model["pins_applied"]:
        # No temperature/seed/thinking pins: the client ran on its own settings.
        notes.append("pins not applied")
    for name, s in (("isolated", model.get("native")), ("in chunks", model.get("carrier"))):
        if not s:
            continue
        if s["truncated"]:
            notes.append(f"{s['truncated']} cut off {name}")
        if s["aborted"]:
            notes.append(f"{s['aborted']} looped {name}")
        if s["thinking_honoured"] is False and "thought despite think=off" not in notes:
            notes.append("thought despite think=off")
    return notes


if __name__ == "__main__":
    main()
