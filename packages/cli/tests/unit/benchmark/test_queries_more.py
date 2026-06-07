# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Coverage for load_queries error branches not hit by test_queries.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from chaoscypher_cli.benchmark.queries import load_queries


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "queries.yaml"
    p.write_text(body, encoding="utf-8")
    return p


def test_top_level_not_mapping_raises_type_error(tmp_path: Path) -> None:
    p = _write(tmp_path, "- a\n- b\n")
    with pytest.raises(TypeError, match="must be a YAML mapping"):
        load_queries(p)


def test_missing_top_level_keys_raises(tmp_path: Path) -> None:
    p = _write(tmp_path, "version: '1.0'\n")
    with pytest.raises(ValueError, match="missing required top-level keys"):
        load_queries(p)


def test_query_entry_not_mapping_raises_type_error(tmp_path: Path) -> None:
    p = _write(tmp_path, "version: '1.0'\nqueries:\n  - just-a-string\n")
    with pytest.raises(TypeError, match=r"queries\[0\] is not a mapping"):
        load_queries(p)


def test_query_missing_required_field_raises(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "version: '1.0'\nqueries:\n  - {id: q1, band: out_of_scope}\n",
    )
    with pytest.raises(ValueError, match="missing required field 'question'"):
        load_queries(p)


def test_out_of_scope_rejects_gold_answer(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "version: '1.0'\n"
        "queries:\n"
        "  - id: q1\n"
        "    band: out_of_scope\n"
        "    question: q?\n"
        "    gold_answer: nope\n"
        "    expect_refusal: true\n",
    )
    with pytest.raises(ValueError, match="out_of_scope must not set gold_answer"):
        load_queries(p)


def test_out_of_scope_requires_expect_refusal(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "version: '1.0'\nqueries:\n  - id: q1\n    band: out_of_scope\n    question: q?\n",
    )
    with pytest.raises(ValueError, match="must set expect_refusal: true"):
        load_queries(p)


def test_in_scope_rejects_expect_refusal(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "version: '1.0'\n"
        "queries:\n"
        "  - id: q1\n"
        "    band: factual_single_hop\n"
        "    question: q?\n"
        "    gold_entities: [X]\n"
        "    gold_answer: a\n"
        "    expect_refusal: true\n",
    )
    with pytest.raises(ValueError, match="expect_refusal only allowed on out_of_scope"):
        load_queries(p)
