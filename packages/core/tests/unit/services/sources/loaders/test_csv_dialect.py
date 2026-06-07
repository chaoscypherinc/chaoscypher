# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""CSV loader sniffs delimiter; handles BOM and explicit encoding.

Workstream 6 (2026-05-07): the previous loader hardcoded LangChain's
``CSVLoader`` which uses the comma delimiter and the platform default
encoding. TSV-saved-as-csv and EU semicolon-delimited files came out as
single-cell rows; cp1252 exports got mojibake. The new loader sniffs
the dialect and routes through :func:`detect_encoding`.
"""

from __future__ import annotations

from pathlib import Path

from chaoscypher_core.services.sources.loaders.csv_loader import CSVLoader


def test_csv_loader_handles_tsv_with_csv_extension(tmp_path: Path) -> None:
    p = tmp_path / "data.csv"
    p.write_text("a\tb\tc\n1\t2\t3\n4\t5\t6\n", encoding="utf-8")
    loader = CSVLoader()
    docs = loader.load_document(str(p))
    # First row of body = data row "1\t2\t3" — verify each cell is parsed
    # as its own column rather than a single bonded value.
    first_row = docs[0]["content"]
    assert "1" in first_row and "2" in first_row and "3" in first_row
    # Headers used: "a", "b", "c" should appear
    assert "a" in first_row
    # Dialect was tab-delimited.
    assert docs[0]["metadata"]["dialect"] == "\t"


def test_csv_loader_handles_european_semicolon_csv(tmp_path: Path) -> None:
    p = tmp_path / "europe.csv"
    p.write_text("a;b;c\n1;2;3\n4;5;6\n", encoding="utf-8")
    loader = CSVLoader()
    docs = loader.load_document(str(p))
    first_row = docs[0]["content"]
    assert "1" in first_row and "2" in first_row and "3" in first_row
    assert docs[0]["metadata"]["dialect"] == ";"


def test_csv_loader_handles_standard_comma(tmp_path: Path) -> None:
    p = tmp_path / "std.csv"
    p.write_text("a,b,c\n1,2,3\n4,5,6\n", encoding="utf-8")
    loader = CSVLoader()
    docs = loader.load_document(str(p))
    assert len(docs) == 2  # body rows
    assert docs[0]["metadata"]["dialect"] == ","


def test_csv_loader_strips_utf8_bom(tmp_path: Path) -> None:
    p = tmp_path / "bom.csv"
    p.write_bytes(b"\xef\xbb\xbfa,b,c\n1,2,3\n")
    loader = CSVLoader()
    docs = loader.load_document(str(p))
    # First cell of header should not include the zero-width no-break space.
    assert "﻿" not in docs[0]["content"]
    assert "bom" in docs[0]["metadata"]["encoding_used"].lower()


def test_csv_loader_records_encoding_used(tmp_path: Path) -> None:
    p = tmp_path / "u.csv"
    p.write_text("a,b\n1,2\n", encoding="utf-8")
    loader = CSVLoader()
    docs = loader.load_document(str(p))
    assert docs[0]["metadata"]["encoding_used"] == "utf-8"


def test_csv_loader_handles_cp1252_csv(tmp_path: Path) -> None:
    p = tmp_path / "cp.csv"
    p.write_bytes(
        ("name,note,country\nrésumé,café,France\nnaïve,élan,Belgium\nZoë,piñata,Spain\n").encode(
            "cp1252"
        )
    )
    loader = CSVLoader()
    docs = loader.load_document(str(p))
    rendered = " ".join(d["content"] for d in docs)
    assert "résumé" in rendered
    assert "café" in rendered
    assert "naïve" in rendered
    assert "Zoë" in rendered
    assert docs[0]["metadata"]["encoding_used"] == "cp1252"
