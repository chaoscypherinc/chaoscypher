# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

from chaoscypher_cli.benchmark.queries import LabeledQuery, LabeledQuerySet
from chaoscypher_cli.commands.benchmark.fixture import validate_fixture


def test_validate_reports_resolved(capsys):
    qs = LabeledQuerySet(
        version="1.0",
        queries=[
            LabeledQuery(
                id="q1",
                band="factual_single_hop",
                question="?",
                gold_entities=["ARPA"],
                gold_answer="a",
            )
        ],
    )
    rc = validate_fixture(queries=qs, entities=[{"id": "uuid-1", "name": "ARPA", "aliases": []}])
    captured = capsys.readouterr()
    assert rc == 0
    assert "1 / 1" in captured.out
    assert "all gold entities resolved" in captured.out.lower()


def test_validate_reports_unresolved(capsys):
    qs = LabeledQuerySet(
        version="1.0",
        queries=[
            LabeledQuery(
                id="q1",
                band="factual_single_hop",
                question="?",
                gold_entities=["ARPA", "MISSING"],
                gold_answer="a",
            )
        ],
    )
    rc = validate_fixture(queries=qs, entities=[{"id": "uuid-1", "name": "ARPA", "aliases": []}])
    captured = capsys.readouterr()
    assert rc == 1
    assert "MISSING" in captured.out
    assert "q1" in captured.out


def test_validate_skips_out_of_scope(capsys):
    qs = LabeledQuerySet(
        version="1.0",
        queries=[LabeledQuery(id="q1", band="out_of_scope", question="?", expect_refusal=True)],
    )
    rc = validate_fixture(queries=qs, entities=[])
    captured = capsys.readouterr()
    assert rc == 0
    assert "skipped" in captured.out.lower() or "out_of_scope" in captured.out.lower()
