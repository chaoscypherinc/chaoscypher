# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""JSONL files are parsed line-by-line; per-line errors are isolated.

Workstream 6 (2026-05-07): the previous loader fed ``.jsonl`` files
through ``json.load`` (single-document mode) which always raised, then
swallowed the exception and returned ``[]``. JSONL imports therefore
silently became zero-content sources. The new loader branches on
extension and runs a strict line-by-line decoder.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chaoscypher_core.exceptions import ValidationError
from chaoscypher_core.services.sources.loaders.json_loader import JSONLoader


def test_jsonl_parses_line_by_line(tmp_path: Path) -> None:
    p = tmp_path / "data.jsonl"
    p.write_text(
        '{"id": 1, "text": "first"}\n{"id": 2, "text": "second"}\n{"id": 3, "text": "third"}\n',
        encoding="utf-8",
    )
    loader = JSONLoader()
    docs = loader.load_document(str(p))
    assert len(docs) == 3
    contents = [d["content"] for d in docs]
    assert "first" in contents[0]
    assert "second" in contents[1]
    assert "third" in contents[2]
    # Per-line metadata records the line number.
    assert docs[0]["metadata"]["line_number"] == 1
    assert docs[2]["metadata"]["line_number"] == 3
    # Encoding metadata is recorded for the quality counter.
    assert docs[0]["metadata"]["encoding_used"] == "utf-8"


def test_jsonl_isolates_per_line_errors(tmp_path: Path) -> None:
    p = tmp_path / "broken.jsonl"
    p.write_text(
        '{"id": 1, "text": "fine"}\n'
        '{"id": 2, "text": "also fine"}\n'
        "this line is not json\n"
        '{"id": 4, "text": "still ok"}\n',
        encoding="utf-8",
    )
    loader = JSONLoader()
    docs = loader.load_document(str(p))
    # Three lines load; one line errors.
    assert len(docs) == 3
    # Loader warnings are attached to the first surviving doc.
    warnings = docs[0]["metadata"].get("loader_warnings", [])
    assert any("line 3" in w.lower() for w in warnings)


def test_jsonl_skips_blank_lines(tmp_path: Path) -> None:
    p = tmp_path / "blanks.jsonl"
    p.write_text(
        '{"id": 1}\n\n   \n{"id": 2}\n',
        encoding="utf-8",
    )
    loader = JSONLoader()
    docs = loader.load_document(str(p))
    assert len(docs) == 2


def test_jsonl_raises_when_every_line_malformed(tmp_path: Path) -> None:
    p = tmp_path / "all_broken.jsonl"
    p.write_text("not json\nstill not json\nnope\n", encoding="utf-8")
    loader = JSONLoader()
    with pytest.raises(ValidationError):
        loader.load_document(str(p))


def test_ndjson_extension_treated_as_jsonl(tmp_path: Path) -> None:
    p = tmp_path / "data.ndjson"
    p.write_text('{"a": 1}\n{"a": 2}\n', encoding="utf-8")
    loader = JSONLoader()
    docs = loader.load_document(str(p))
    assert len(docs) == 2


def test_json_single_document_path_still_works(tmp_path: Path) -> None:
    p = tmp_path / "doc.json"
    p.write_text('{"name": "alice", "age": 30}', encoding="utf-8")
    loader = JSONLoader()
    docs = loader.load_document(str(p))
    assert len(docs) == 1
    assert "alice" in docs[0]["content"]
    assert docs[0]["metadata"]["encoding_used"] == "utf-8"
