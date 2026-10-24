# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Hand reviews of grounded-chat answers: the verdict a reviewer says each answer should get.

The one matching rule every reader of a review applies: ``review_check``
(``internal/benchmarks/tools/review_check.py``, the gate on every scorer or
fixture change) and the leaderboard exporter
(``scripts/benchmark/export_leaderboard.py``, which shows a corpus's
hand-graded numbers wherever a full review of its board answers exists,
2026-10-23).

A review file holds ``verdicts`` (``key`` = :func:`review_key`, ``answer_start``,
``should_be``, ``reason``) and optionally ``overrides`` (``key||first 90
characters of the answer`` -> verdict: decisions taken against a reviewer).
An answer meets its review by key and by the start of its text, whitespace
folded (:func:`expected`). A review's known scorer misses (the answers the
scorer and the reviewers still disagree on, each with its fault) sit beside
it in ``<name>-known-misses.json`` for a review named ``<name>-verdicts.json``
(:func:`known_misses_path`).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from chaoscypher_core.benchmark.results import row_retrieval_mode


if TYPE_CHECKING:
    from pathlib import Path

PREFIX_CHARS = 80
"""How much of a recorded answer start must open the answer for the two to match."""

VERDICTS_SUFFIX = "-verdicts.json"
KNOWN_MISSES_SUFFIX = "-known-misses.json"


def norm(text: str) -> str:
    """Whitespace folded, so a re-serialised answer still matches its review."""
    return " ".join((text or "").split())


@dataclass(frozen=True)
class Review:
    """One reviewed answer: what the scorer must say about it."""

    key: str
    answer_start: str
    should_be: str
    reason: str


Overrides = dict[str, list[tuple[str, str]]]


def review_key(qid: str, row: Any) -> str:
    """How a review names one answer: ``question id|model label|rung``."""
    return f"{qid}|{row.model_label}|{row_retrieval_mode(row)}"


def load_reviews(paths: list[Path]) -> tuple[dict[str, list[Review]], Overrides]:
    """Reviews by key (several answers can share a key across repeats) and the overrides by key.

    An override is stored as ``key||first 90 characters of the answer``; it is kept as
    ``(normalised answer prefix, verdict)`` so an answer that starts with blank lines still
    matches (its first 90 characters hold fewer than 90 of text).
    """
    reviews: dict[str, list[Review]] = {}
    overrides: Overrides = {}
    for path in paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        for v in data["verdicts"]:
            reviews.setdefault(v["key"], []).append(
                Review(
                    v["key"], norm(v.get("answer_start", "")), v["should_be"], v.get("reason", "")
                )
            )
        for stored, verdict in (data.get("overrides") or {}).items():
            key, _, prefix = stored.partition("||")
            overrides.setdefault(key, []).append((norm(prefix), verdict))
    return reviews, overrides


def expected(
    key: str, answer: str, reviews: dict[str, list[Review]], overrides: Overrides
) -> tuple[str, str] | None:
    """``(verdict, why)`` the reviews require for this answer, or None when none covers it."""
    text = norm(answer)
    for prefix, verdict in overrides.get(key, []):
        if prefix and text.startswith(prefix[:PREFIX_CHARS]):
            return verdict, "override"
    for review in reviews.get(key, []):
        if review.answer_start and text.startswith(review.answer_start[:PREFIX_CHARS]):
            return review.should_be, review.reason
    candidates = reviews.get(key, [])
    if len(candidates) == 1 and not candidates[0].answer_start:
        return candidates[0].should_be, candidates[0].reason
    return None


def known_misses_path(verdicts: Path) -> Path:
    """Where a review's known scorer misses are kept: ``<name>-known-misses.json`` beside it."""
    name = verdicts.name.removesuffix(VERDICTS_SUFFIX).removesuffix(".json")
    return verdicts.with_name(name + KNOWN_MISSES_SUFFIX)


def load_known_misses(verdicts: Path) -> dict[str, dict[str, Any]]:
    """A review's known misses by key (``misses[].key``); empty when it has none on file."""
    path = known_misses_path(verdicts)
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {m["key"]: m for m in data.get("misses") or []}
