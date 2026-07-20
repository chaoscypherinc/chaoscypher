# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Contract tests for the loader file-size guard.

The guard pre-checks ``path.stat().st_size`` against
``settings.loader.max_disk_bytes`` BEFORE the heavyweight parser is
invoked. This prevents a multi-GB upload from OOM-killing the worker
when pypdf / python-docx / full-text read would otherwise materialise
the file at 5-10x disk size into RAM.

Covered behaviours:
- Helper raises LoaderFileTooLargeError when over cap.
- Helper no-ops when settings is None.
- Helper no-ops when ``max_disk_bytes`` is None (opt-out).
- Each integrating loader (PDF, text, CSV, DOCX) calls the helper.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chaoscypher_core.exceptions import LoaderFileTooLargeError
from chaoscypher_core.services.sources.loaders.base import check_loader_file_size
from chaoscypher_core.services.sources.loaders.csv_loader import CSVLoader
from chaoscypher_core.services.sources.loaders.docx_loader import DOCXLoader
from chaoscypher_core.services.sources.loaders.pdf_loader import PdfLoader
from chaoscypher_core.services.sources.loaders.text_loader import TextLoader
from chaoscypher_core.settings import EngineSettings, LoaderSettings


def _make_settings(max_disk_bytes: int | None) -> EngineSettings:
    return EngineSettings(loader=LoaderSettings(max_disk_bytes=max_disk_bytes))


# ---------------------------------------------------------------------------
# Helper contract
# ---------------------------------------------------------------------------


def test_loader_settings_default_max_disk_bytes_is_500_mib() -> None:
    """Default cap matches the 500 MiB pre-launch contract."""
    s = LoaderSettings()
    assert s.max_disk_bytes == 500 * 1024 * 1024


def test_helper_no_op_when_settings_is_none(tmp_path: Path) -> None:
    """Helper does not raise when no settings provided (standalone / CLI flows)."""
    f = tmp_path / "anything.txt"
    f.write_bytes(b"x" * 100)
    check_loader_file_size(f, None)  # Should not raise


def test_helper_no_op_when_cap_disabled(tmp_path: Path) -> None:
    """Helper does not raise when settings.loader.max_disk_bytes is None."""
    f = tmp_path / "anything.txt"
    f.write_bytes(b"x" * 1000)
    settings = _make_settings(max_disk_bytes=None)
    check_loader_file_size(f, settings)  # Should not raise


def test_helper_no_op_when_file_under_cap(tmp_path: Path) -> None:
    """Helper does not raise when file fits under the cap."""
    f = tmp_path / "small.txt"
    f.write_bytes(b"x" * 100)
    settings = _make_settings(max_disk_bytes=1024)
    check_loader_file_size(f, settings)  # Should not raise


def test_helper_raises_when_file_over_cap(tmp_path: Path) -> None:
    """Helper raises LoaderFileTooLargeError when file exceeds cap."""
    f = tmp_path / "big.txt"
    f.write_bytes(b"x" * 2048)
    settings = _make_settings(max_disk_bytes=1024)

    with pytest.raises(LoaderFileTooLargeError) as excinfo:
        check_loader_file_size(f, settings)

    assert excinfo.value.actual_bytes == 2048
    assert excinfo.value.max_bytes == 1024
    assert excinfo.value.filename == "big.txt"
    assert excinfo.value.code == "LOADER_FILE_TOO_LARGE"


# ---------------------------------------------------------------------------
# Loader integration: each guarded loader raises before its heavyweight parser
# ---------------------------------------------------------------------------


def test_pdf_loader_raises_before_pypdf_when_over_cap(tmp_path: Path) -> None:
    """PdfLoader rejects oversized files before invoking pypdf."""
    f = tmp_path / "huge.pdf"
    # Write garbage — if the size check fails, pypdf would explode parsing
    # garbage. Either way we expect LoaderFileTooLargeError first.
    f.write_bytes(b"X" * 4096)
    loader = PdfLoader(settings=_make_settings(max_disk_bytes=1024))
    with pytest.raises(LoaderFileTooLargeError):
        loader.load_document(str(f))


def test_text_loader_raises_before_read_when_over_cap(tmp_path: Path) -> None:
    """TextLoader rejects oversized files before reading bytes."""
    f = tmp_path / "huge.txt"
    f.write_bytes(b"X" * 4096)
    loader = TextLoader(settings=_make_settings(max_disk_bytes=1024))
    with pytest.raises(LoaderFileTooLargeError):
        loader.load_document(str(f))


def test_csv_loader_raises_before_sniff_when_over_cap(tmp_path: Path) -> None:
    """CSVLoader rejects oversized files before dialect sniffing."""
    f = tmp_path / "huge.csv"
    f.write_bytes(b"a,b\n" + b"X" * 4096)
    loader = CSVLoader(settings=_make_settings(max_disk_bytes=1024))
    with pytest.raises(LoaderFileTooLargeError):
        loader.load_document(str(f))


def test_docx_loader_raises_before_parse_when_over_cap(tmp_path: Path) -> None:
    """DOCXLoader rejects oversized files before python-docx parse."""
    f = tmp_path / "huge.docx"
    # Garbage bytes — guard fires before python-docx tries to parse the zip.
    f.write_bytes(b"X" * 4096)
    loader = DOCXLoader(settings=_make_settings(max_disk_bytes=1024))
    with pytest.raises(LoaderFileTooLargeError):
        loader.load_document(str(f))


# ---------------------------------------------------------------------------
# Parity: the remaining full-file loaders (JSON, HTML, RST, EPUB, XLSX, PPTX)
# enforce the same guard. These read the whole file into RAM (detect_encoding
# / zipfile / openpyxl) but historically skipped the pre-parse size check, so
# a multi-GB upload of one of these formats could still OOM the worker.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("loader_module", "loader_cls", "suffix", "payload"),
    [
        ("json_loader", "JSONLoader", ".json", b"X" * 4096),
        ("html_loader", "HTMLLoader", ".html", b"X" * 4096),
        ("rst_loader", "RSTLoader", ".rst", b"X" * 4096),
        ("epub_loader", "EPUBLoader", ".epub", b"X" * 4096),
        ("xlsx_loader", "XLSXLoader", ".xlsx", b"X" * 4096),
        ("pptx_loader", "PPTXLoader", ".pptx", b"X" * 4096),
    ],
)
def test_remaining_loaders_raise_before_parse_when_over_cap(
    tmp_path: Path,
    loader_module: str,
    loader_cls: str,
    suffix: str,
    payload: bytes,
) -> None:
    """Each full-file loader rejects oversized input before its parser runs.

    Garbage bytes over the cap: if the guard didn't fire first, the parser
    would raise a format error (or materialise the bytes into RAM) instead of
    ``LoaderFileTooLargeError``.
    """
    import importlib

    module = importlib.import_module(f"chaoscypher_core.services.sources.loaders.{loader_module}")
    cls = getattr(module, loader_cls)
    f = tmp_path / f"huge{suffix}"
    f.write_bytes(payload)
    loader = cls(settings=_make_settings(max_disk_bytes=1024))
    with pytest.raises(LoaderFileTooLargeError):
        loader.load_document(str(f))


# ---------------------------------------------------------------------------
# Error message carries enough detail to be operator-actionable
# ---------------------------------------------------------------------------


def test_error_message_contains_filename_size_and_cap(tmp_path: Path) -> None:
    """The raised error names the offending file, its size, and the cap.

    Surfaces verbatim through the FastAPI exception handler (413 envelope),
    so the operator can fix the upload or the setting without reading logs.
    """
    f = tmp_path / "oversize.bin"
    f.write_bytes(b"X" * 2048)
    settings = _make_settings(max_disk_bytes=1024)

    with pytest.raises(LoaderFileTooLargeError) as excinfo:
        check_loader_file_size(f, settings)

    msg = excinfo.value.message
    assert "oversize.bin" in msg
    assert "MiB" in msg
    # Both the actual and the cap appear in the message.
    assert "0.0 MiB" in msg  # 2 KiB and 1 KiB both round to 0.0 MiB
    # Detail dict carries the raw byte counts for downstream consumers.
    assert excinfo.value.details["actual_bytes"] == 2048
    assert excinfo.value.details["max_bytes"] == 1024
    assert excinfo.value.details["filename"] == "oversize.bin"


# ---------------------------------------------------------------------------
# Positive-path: sub-cap files load normally (no false positives)
# ---------------------------------------------------------------------------


def test_text_loader_loads_normally_under_cap(tmp_path: Path) -> None:
    """A sub-cap text file loads normally; guard does not interfere."""
    f = tmp_path / "small.txt"
    f.write_bytes(b"hello world\nline two\n")
    loader = TextLoader(settings=_make_settings(max_disk_bytes=1024))
    docs = loader.load_document(str(f))
    assert len(docs) == 1
    assert "hello world" in docs[0]["content"]


def test_csv_loader_loads_normally_under_cap(tmp_path: Path) -> None:
    """A sub-cap CSV loads normally; guard does not interfere."""
    f = tmp_path / "small.csv"
    f.write_text("col_a,col_b\nval1,val2\n", encoding="utf-8")
    loader = CSVLoader(settings=_make_settings(max_disk_bytes=1024))
    docs = loader.load_document(str(f))
    assert len(docs) == 1
    assert "col_a: val1" in docs[0]["content"]
    assert "col_b: val2" in docs[0]["content"]


def test_docx_loader_loads_normally_under_cap(tmp_path: Path) -> None:
    """A sub-cap DOCX loads normally; guard does not interfere.

    Synthesises a minimal DOCX rather than relying on a checked-in
    fixture so the test stays self-contained.
    """
    from docx import Document as _Document

    doc = _Document()
    doc.add_paragraph("hello docx")
    docx_path = tmp_path / "small.docx"
    doc.save(str(docx_path))

    # Cap well above the synthesized docx (~36 KiB).
    loader = DOCXLoader(settings=_make_settings(max_disk_bytes=10 * 1024 * 1024))
    docs = loader.load_document(str(docx_path))
    assert len(docs) == 1
    assert docs[0]["metadata"]["extraction_method"] == "docx"
    assert "hello docx" in docs[0]["content"]


def test_pdf_loader_loads_normally_under_cap(tmp_path: Path) -> None:
    """A sub-cap PDF loads normally.

    Synthesises a minimal valid PDF with pypdf (already a Core dep) so
    we don't depend on a checked-in fixture and the test stays under
    the 1-MiB cap. The pypdf-emitted blank page has no extractable text,
    so we only assert load completes with metadata — not on the
    content string.
    """
    from pypdf import PdfWriter

    f = tmp_path / "small.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    with f.open("wb") as fh:
        writer.write(fh)
    assert f.stat().st_size < 1024 * 1024  # well under 1 MiB

    loader = PdfLoader(settings=_make_settings(max_disk_bytes=1024 * 1024))
    docs = loader.load_document(str(f))
    assert len(docs) == 1
    assert docs[0]["metadata"]["extraction_method"] == "pypdf"
    assert docs[0]["metadata"]["total_pages"] == 1


# ---------------------------------------------------------------------------
# Encoding-level guard: detect_encoding called directly (rst/html/json
# loaders, web search adapter, …) is covered too.
# ---------------------------------------------------------------------------


def test_detect_encoding_raises_when_file_over_cap(tmp_path: Path) -> None:
    """detect_encoding refuses to materialise a multi-GB file into RAM.

    Mirrors the helper guard so any caller bypassing the per-loader
    ``check_loader_file_size`` (rst / html / json loaders, web search
    adapter, …) is still protected.
    """
    from chaoscypher_core.settings import LoaderSettings
    from chaoscypher_core.utils.encoding import detect_encoding

    f = tmp_path / "huge.txt"
    f.write_bytes(b"X" * 4096)
    settings = LoaderSettings(max_disk_bytes=1024)

    with pytest.raises(LoaderFileTooLargeError) as excinfo:
        detect_encoding(f, settings=settings)

    assert excinfo.value.filename == "huge.txt"
    assert excinfo.value.actual_bytes == 4096
    assert excinfo.value.max_bytes == 1024


def test_detect_encoding_no_op_when_cap_disabled(tmp_path: Path) -> None:
    """detect_encoding does not raise when settings.max_disk_bytes is None."""
    from chaoscypher_core.settings import LoaderSettings
    from chaoscypher_core.utils.encoding import detect_encoding

    f = tmp_path / "any.txt"
    f.write_text("hi", encoding="utf-8")
    settings = LoaderSettings(max_disk_bytes=None)
    encoding_used, text, _ = detect_encoding(f, settings=settings)
    assert text == "hi"
    assert encoding_used == "utf-8"
