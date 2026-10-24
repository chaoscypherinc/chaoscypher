# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""The one rule that matches a scored chat answer to its hand review (``benchmark.reviews``).

``review_check`` (the gate on every scorer or fixture change) and the leaderboard exporter
(a headline holds only if it holds on the reviewers' verdicts) both read reviews through it.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from chaoscypher_core.benchmark.reviews import (
    expected,
    known_misses_path,
    load_known_misses,
    load_reviews,
    review_key,
)


def _write(path: Path, data: object) -> Path:
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_an_answer_meets_its_review_by_key_and_start_and_an_override_wins(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "r-verdicts.json",
        {
            "verdicts": [
                {"key": "q1|M|graphrag", "answer_start": "Yes, it is.", "should_be": "PASS"},
                {"key": "q1|M|graphrag", "answer_start": "No.", "should_be": "FAIL"},
                {"key": "q2|M|naive", "answer_start": "", "should_be": "FAIL", "reason": "r"},
            ],
            "overrides": {"q1|M|graphrag||No.  Never": "PASS"},
        },
    )
    reviews, overrides = load_reviews([path])
    assert expected("q1|M|graphrag", "Yes,\n it is. More.", reviews, overrides) == ("PASS", "")
    assert expected("q1|M|graphrag", "\n\nNo. Never.", reviews, overrides) == ("PASS", "override")
    # a key with one review and no recorded start covers any answer
    assert expected("q2|M|naive", "anything", reviews, overrides) == ("FAIL", "r")
    assert expected("q3|M|naive", "anything", reviews, overrides) is None


def test_the_key_names_the_question_the_models_label_and_the_rung() -> None:
    row = SimpleNamespace(
        model_label="M (local)", dataset_id="d__chat__x__y__retrieval_hybrid", extras={}
    )
    # the rung from the dataset id when the row does not record it (the shared row_retrieval_mode)
    assert review_key("q7", row) == "q7|M (local)|hybrid"
    row.extras = {"retrieval": "naive"}
    assert review_key("q7", row) == "q7|M (local)|naive"


def test_a_reviews_known_misses_sit_beside_it_and_load_by_key(tmp_path: Path) -> None:
    verdicts = tmp_path / "2026-10-23-x-review-verdicts.json"
    assert known_misses_path(verdicts) == tmp_path / "2026-10-23-x-review-known-misses.json"
    assert load_known_misses(verdicts) == {}  # none on file: every disagreement is new
    _write(
        known_misses_path(verdicts),
        {"misses": [{"key": "q1|M|graphrag", "scorer": "PASS", "reviewer": "FAIL", "fault": "f"}]},
    )
    misses = load_known_misses(verdicts)
    assert set(misses) == {"q1|M|graphrag"}
    assert misses["q1|M|graphrag"]["fault"] == "f"
