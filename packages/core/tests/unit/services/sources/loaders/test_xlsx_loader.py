# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""XLSXLoader walks each sheet and flattens rows to text.

Workstream 7 (2026-05-07): standalone .xlsx uploads had no loader.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chaoscypher_core.exceptions import ValidationError
from chaoscypher_core.services.sources.loaders.xlsx_loader import XLSXLoader
from chaoscypher_core.settings import EngineSettings, LoaderSettings, PathSettings


FIXTURE = Path(__file__).parent.parent.parent.parent.parent / "fixtures" / "loaders" / "sample.xlsx"


def test_xlsx_loader_extracts_rows() -> None:
    assert FIXTURE.exists(), f"fixture missing: {FIXTURE}"
    loader = XLSXLoader()
    docs = loader.load_document(str(FIXTURE))
    text = docs[0]["content"]
    # Header row.
    assert "Name | Age | City" in text
    # Body rows (cells are stringified).
    assert "Alice | 30 | NYC" in text
    assert "Bob | 25 | LA" in text
    assert "Carol | 28 | SF" in text


def test_xlsx_loader_includes_sheet_names() -> None:
    loader = XLSXLoader()
    docs = loader.load_document(str(FIXTURE))
    text = docs[0]["content"]
    assert "[Sheet: Sheet1]" in text
    assert "[Sheet: Notes]" in text
    assert "Top-line note" in text


def test_xlsx_loader_skips_blank_rows() -> None:
    """All-None rows must not appear as a stray ' |  | ' line."""
    loader = XLSXLoader()
    docs = loader.load_document(str(FIXTURE))
    # Sheet1 has 4 non-empty rows (header + 3 names).
    sheets = docs[0]["metadata"]["sheets"]
    sheet1 = next(s for s in sheets if s["name"] == "Sheet1")
    assert sheet1["rows"] == 4


def test_xlsx_loader_supported_extensions() -> None:
    loader = XLSXLoader()
    assert ".xlsx" in loader.supported_extensions
    assert loader.metadata.plugin_id == "xlsx"


def test_xlsx_loader_wraps_invalid_file_in_validation_error(tmp_path: Path) -> None:
    """A file with .xlsx extension but wrong magic bytes raises ValidationError.

    Without the wrapper, openpyxl surfaces ``InvalidFileException`` or
    ``zipfile.BadZipFile`` — both bypass the API exception envelope.
    """
    bogus = tmp_path / "fake.xlsx"
    bogus.write_bytes(b"not a real xlsx")

    loader = XLSXLoader()
    with pytest.raises(ValidationError) as exc_info:
        loader.load_document(str(bogus))

    err = exc_info.value
    assert err.code == "VALIDATION_ERROR"
    assert err.field == "content"
    assert "fake.xlsx" in err.message
    assert "not a valid" in err.message
    assert exc_info.value.__cause__ is not None


def _shared_string_workbook(path: Path, *, rows: int, cols: int, cell: str) -> None:
    """Write a workbook whose every cell holds the same (shared) string."""
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    assert ws is not None
    for _ in range(rows):
        ws.append([cell] * cols)
    wb.save(path)


def _loader_settings(data_dir: Path, **loader_kwargs: object) -> EngineSettings:
    return EngineSettings(
        paths=PathSettings(data_dir=str(data_dir)), loader=LoaderSettings(**loader_kwargs)
    )


def test_xlsx_loader_rejects_shared_string_amplification(tmp_path: Path) -> None:
    """Shared strings amplify past the on-disk size check; the char cap stops it.

    2 rows x 50 cells of one 1,000-char string is a few KB on disk but
    flattens to ~100,000 characters. The cap fires mid-row (per cell),
    before the whole row is joined.
    """
    workbook = tmp_path / "amplified.xlsx"
    _shared_string_workbook(workbook, rows=2, cols=50, cell="A" * 1_000)
    loader = XLSXLoader(_loader_settings(tmp_path, xlsx_max_content_chars=10_000))

    with pytest.raises(ValidationError) as exc_info:
        loader.load_document(str(workbook))

    assert exc_info.value.field == "content"
    assert "amplified.xlsx" in exc_info.value.message
    assert "xlsx_max_content_chars" in exc_info.value.message


def test_xlsx_loader_loads_workbook_under_char_cap(tmp_path: Path) -> None:
    workbook = tmp_path / "small.xlsx"
    _shared_string_workbook(workbook, rows=2, cols=5, cell="cell")
    loader = XLSXLoader(_loader_settings(tmp_path, xlsx_max_content_chars=10_000))

    docs = loader.load_document(str(workbook))

    assert "cell | cell | cell | cell | cell" in docs[0]["content"]


def test_xlsx_loader_char_cap_disabled_by_none(tmp_path: Path) -> None:
    workbook = tmp_path / "big.xlsx"
    _shared_string_workbook(workbook, rows=2, cols=50, cell="A" * 1_000)
    loader = XLSXLoader(_loader_settings(tmp_path, xlsx_max_content_chars=None))

    docs = loader.load_document(str(workbook))

    assert len(docs[0]["content"]) > 100_000
