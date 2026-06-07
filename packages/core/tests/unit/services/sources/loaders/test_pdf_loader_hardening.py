# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression tests for Phase 5b PDF loader hardening.

Covers all four tasks:
  - Task 1: per-page try/except — a corrupt page does not kill the whole doc;
    ``loader_pdf_pages_failed`` metadata key is set correctly.
  - Task 2: encrypted PDF detection — ``EncryptedPDFError`` is raised before
    page iteration; HTTP 422 mapping applies.
  - Task 3: image-only PDF detection — ``needs_vision`` flag is set when
    every page produced empty text; a ``loader_warnings`` entry is appended.
  - Task 4: ``pdf_max_pages`` setting — page count is capped and a
    ``loader_warnings`` entry describes the truncation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from chaoscypher_core.exceptions import EncryptedPDFError
from chaoscypher_core.services.sources.loaders.pdf_loader import PdfLoader
from chaoscypher_core.settings import EngineSettings, LoaderSettings, PathSettings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_settings(
    *,
    pdf_max_pages: int | None = None,
    data_dir: Path | None = None,
) -> EngineSettings:
    kwargs: dict[str, Any] = {}
    if pdf_max_pages is not None:
        kwargs["loader"] = LoaderSettings(pdf_max_pages=pdf_max_pages)
    if data_dir is not None:
        kwargs["paths"] = PathSettings(data_dir=str(data_dir))
    return EngineSettings(**kwargs)


def _make_page(text: str) -> MagicMock:
    """Return a fake pypdf Page object whose ``extract_text`` returns ``text``."""
    page = MagicMock()
    page.extract_text.return_value = text
    return page


def _make_raising_page(exc: Exception) -> MagicMock:
    """Return a fake pypdf Page object whose ``extract_text`` raises ``exc``."""
    page = MagicMock()
    page.extract_text.side_effect = exc
    return page


def _make_reader(
    pages: list[MagicMock],
    *,
    is_encrypted: bool = False,
    decrypt_returns: int | Exception = 0,
) -> MagicMock:
    """Return a fake ``PdfReader`` with the given pages.

    ``decrypt_returns`` is consulted only when ``is_encrypted=True``: pass an
    integer to control the simulated ``reader.decrypt("")`` return value
    (0 = NOT_DECRYPTED, 1 = USER_PASSWORD, 2 = OWNER_PASSWORD per pypdf), or
    pass an Exception instance to simulate ``decrypt`` raising.
    """
    reader = MagicMock()
    reader.is_encrypted = is_encrypted
    reader.pages = pages
    reader.metadata = None
    if isinstance(decrypt_returns, Exception):
        reader.decrypt.side_effect = decrypt_returns
    else:
        reader.decrypt.return_value = decrypt_returns
    return reader


# ---------------------------------------------------------------------------
# Task 1: per-page try/except + loader_pdf_pages_failed counter
# ---------------------------------------------------------------------------


class TestPerPageTryExcept:
    """A corrupt page does not abort the whole PDF."""

    def test_good_pages_survive_when_middle_page_fails(self, tmp_path: Path) -> None:
        """Pages before and after a corrupt page are still included in output."""
        dummy_pdf = tmp_path / "test.pdf"
        dummy_pdf.write_bytes(b"%PDF-1.4 stub")

        pages = [
            _make_page("First page text"),
            _make_raising_page(RuntimeError("corrupt page")),
            _make_page("Third page text"),
        ]
        reader = _make_reader(pages)

        loader = PdfLoader()

        with patch(
            "pypdf.PdfReader",
            return_value=reader,
        ):
            docs = loader.load_document(str(dummy_pdf))

        assert len(docs) == 1
        content = docs[0]["content"]
        # Both good pages should appear in the output
        assert "First page text" in content
        assert "Third page text" in content

    def test_loader_pdf_pages_failed_reflects_failure_count(self, tmp_path: Path) -> None:
        """``loader_pdf_pages_failed`` in metadata equals number of failed pages."""
        dummy_pdf = tmp_path / "test.pdf"
        dummy_pdf.write_bytes(b"%PDF-1.4 stub")

        pages = [
            _make_page("OK page"),
            _make_raising_page(ValueError("bad data")),
            _make_raising_page(OSError("io error")),
        ]
        reader = _make_reader(pages)

        loader = PdfLoader()

        with patch(
            "pypdf.PdfReader",
            return_value=reader,
        ):
            docs = loader.load_document(str(dummy_pdf))

        assert docs[0]["metadata"]["loader_pdf_pages_failed"] == 2

    def test_zero_failed_pages_when_all_succeed(self, tmp_path: Path) -> None:
        """``loader_pdf_pages_failed`` is 0 when no pages raise."""
        dummy_pdf = tmp_path / "clean.pdf"
        dummy_pdf.write_bytes(b"%PDF-1.4 stub")

        pages = [_make_page("page one"), _make_page("page two")]
        reader = _make_reader(pages)

        loader = PdfLoader()

        with patch(
            "pypdf.PdfReader",
            return_value=reader,
        ):
            docs = loader.load_document(str(dummy_pdf))

        assert docs[0]["metadata"]["loader_pdf_pages_failed"] == 0

    def test_failed_page_adds_loader_warning(self, tmp_path: Path) -> None:
        """A per-page failure generates a ``loader_warnings`` entry."""
        dummy_pdf = tmp_path / "warn.pdf"
        dummy_pdf.write_bytes(b"%PDF-1.4 stub")

        pages = [_make_raising_page(RuntimeError("bad page"))]
        reader = _make_reader(pages)

        loader = PdfLoader()

        with patch(
            "pypdf.PdfReader",
            return_value=reader,
        ):
            docs = loader.load_document(str(dummy_pdf))

        warnings = docs[0]["metadata"].get("loader_warnings", [])
        assert any("failed text extraction" in w for w in warnings)


# ---------------------------------------------------------------------------
# Task 2: encrypted PDF detection
# ---------------------------------------------------------------------------


class TestEncryptedPDFDetection:
    """Encrypted PDFs: empty-password decrypt is attempted before refusing.

    ``is_encrypted=True`` alone is not sufficient evidence that a PDF is
    actually password-protected — Adobe Acrobat, journal-article tooling,
    and OCR'd-scan workflows all routinely emit "encrypted" PDFs whose
    cryptographic envelope is empty and whose ``decrypt("")`` returns
    ``USER_PASSWORD``.  The loader tries empty-password decryption first
    and only raises ``EncryptedPDFError`` when that fails, so these
    real-world false-positives load normally.
    """

    def test_truly_password_protected_pdf_raises_typed_error(self, tmp_path: Path) -> None:
        """``EncryptedPDFError`` is raised when empty-password decrypt fails."""
        dummy_pdf = tmp_path / "secret.pdf"
        dummy_pdf.write_bytes(b"%PDF-1.4 stub")

        # decrypt_returns=0 simulates pypdf's NOT_DECRYPTED — i.e. the
        # empty password did not unlock the file.
        reader = _make_reader([], is_encrypted=True, decrypt_returns=0)

        loader = PdfLoader()

        with (
            patch(
                "pypdf.PdfReader",
                return_value=reader,
            ),
            pytest.raises(EncryptedPDFError) as exc_info,
        ):
            loader.load_document(str(dummy_pdf))

        err = exc_info.value
        assert err.code == "ENCRYPTED_PDF"
        assert "secret.pdf" in err.message
        assert "encrypted" in err.message.lower()
        assert "Decrypt" in err.message

    def test_encrypted_pdf_error_carries_filename(self, tmp_path: Path) -> None:
        """``EncryptedPDFError.filename`` matches the PDF's basename."""
        dummy_pdf = tmp_path / "locked.pdf"
        dummy_pdf.write_bytes(b"%PDF-1.4 stub")

        reader = _make_reader([], is_encrypted=True, decrypt_returns=0)

        loader = PdfLoader()

        with (
            patch(
                "pypdf.PdfReader",
                return_value=reader,
            ),
            pytest.raises(EncryptedPDFError) as exc_info,
        ):
            loader.load_document(str(dummy_pdf))

        assert exc_info.value.filename == "locked.pdf"

    def test_non_encrypted_pdf_does_not_raise(self, tmp_path: Path) -> None:
        """Non-encrypted PDFs load normally without touching ``decrypt``."""
        dummy_pdf = tmp_path / "plain.pdf"
        dummy_pdf.write_bytes(b"%PDF-1.4 stub")

        reader = _make_reader([_make_page("hello world")], is_encrypted=False)

        loader = PdfLoader()

        with patch(
            "pypdf.PdfReader",
            return_value=reader,
        ):
            docs = loader.load_document(str(dummy_pdf))

        assert "hello world" in docs[0]["content"]
        # ``decrypt`` should not have been called for a non-encrypted file.
        reader.decrypt.assert_not_called()

    def test_restriction_only_encryption_loads_with_empty_password(self, tmp_path: Path) -> None:
        """PDFs with empty-password "encryption" (Adobe Acrobat, journals, OCR
        scans) load normally — ``decrypt("")`` returns ``USER_PASSWORD`` (1)
        or ``OWNER_PASSWORD`` (2) per pypdf's PasswordType enum.

        Regression for a 2026-05-09 production failure: a 184-page scanned
        PDF was being rejected as "password-protected" even though
        ``reader.decrypt("")`` succeeded and all 184 pages were readable.
        """
        dummy_pdf = tmp_path / "restricted.pdf"
        dummy_pdf.write_bytes(b"%PDF-1.4 stub")

        # USER_PASSWORD (1): empty password unlocks the user-level view.
        # Common pattern for "permissions-only" encryption.
        reader = _make_reader(
            [_make_page("body text"), _make_page("more body text")],
            is_encrypted=True,
            decrypt_returns=1,
        )

        loader = PdfLoader()

        with patch(
            "pypdf.PdfReader",
            return_value=reader,
        ):
            docs = loader.load_document(str(dummy_pdf))

        # decrypt was attempted with the empty string — only.
        reader.decrypt.assert_called_once_with("")
        # Both pages made it into the output.
        assert "body text" in docs[0]["content"]
        assert "more body text" in docs[0]["content"]

    def test_owner_only_encryption_also_loads(self, tmp_path: Path) -> None:
        """``decrypt("")`` returning OWNER_PASSWORD (2) is also a successful
        unlock — owner access through an empty password is rare but
        legitimate (some tools emit it for marker documents).
        """
        dummy_pdf = tmp_path / "owner.pdf"
        dummy_pdf.write_bytes(b"%PDF-1.4 stub")

        reader = _make_reader(
            [_make_page("owner content")],
            is_encrypted=True,
            decrypt_returns=2,  # OWNER_PASSWORD
        )

        loader = PdfLoader()

        with patch(
            "pypdf.PdfReader",
            return_value=reader,
        ):
            docs = loader.load_document(str(dummy_pdf))

        assert "owner content" in docs[0]["content"]

    def test_decrypt_raising_treated_as_failure(self, tmp_path: Path) -> None:
        """If ``decrypt("")`` itself raises, treat it as NOT_DECRYPTED.

        Some pypdf versions raise rather than returning 0 on a bad password
        — the loader must not propagate the raw pypdf exception type;
        callers expect ``EncryptedPDFError``.
        """
        dummy_pdf = tmp_path / "broken.pdf"
        dummy_pdf.write_bytes(b"%PDF-1.4 stub")

        reader = _make_reader(
            [],
            is_encrypted=True,
            decrypt_returns=RuntimeError("decrypt failed: AES-256 not supported"),
        )

        loader = PdfLoader()

        with (
            patch(
                "pypdf.PdfReader",
                return_value=reader,
            ),
            pytest.raises(EncryptedPDFError),
        ):
            loader.load_document(str(dummy_pdf))

    def test_encrypted_pdf_error_inherits_validation_error(self) -> None:
        """``EncryptedPDFError`` is a subclass of ``ValidationError``."""
        from chaoscypher_core.exceptions import ValidationError

        err = EncryptedPDFError("doc.pdf")
        assert isinstance(err, ValidationError)


# ---------------------------------------------------------------------------
# Task 3: image-only PDF detection (needs_vision flag)
# ---------------------------------------------------------------------------


class TestImageOnlyPDFDetection:
    """All-empty-text PDFs get needs_vision=True in metadata."""

    def test_all_empty_pages_sets_needs_vision(self, tmp_path: Path) -> None:
        """When every page returns empty text, ``needs_vision`` is True."""
        dummy_pdf = tmp_path / "scanned.pdf"
        dummy_pdf.write_bytes(b"%PDF-1.4 stub")

        pages = [_make_page(""), _make_page(""), _make_page("")]
        reader = _make_reader(pages)

        loader = PdfLoader()

        with patch(
            "pypdf.PdfReader",
            return_value=reader,
        ):
            docs = loader.load_document(str(dummy_pdf))

        assert docs[0]["metadata"].get("needs_vision") is True

    def test_all_empty_pages_adds_loader_warning(self, tmp_path: Path) -> None:
        """Image-only detection appends a descriptive ``loader_warnings`` entry."""
        dummy_pdf = tmp_path / "scanned.pdf"
        dummy_pdf.write_bytes(b"%PDF-1.4 stub")

        pages = [_make_page(""), _make_page("")]
        reader = _make_reader(pages)

        loader = PdfLoader()

        with patch(
            "pypdf.PdfReader",
            return_value=reader,
        ):
            docs = loader.load_document(str(dummy_pdf))

        warnings = docs[0]["metadata"].get("loader_warnings", [])
        assert any("vision processing required" in w for w in warnings)

    def test_mixed_pages_does_not_set_needs_vision(self, tmp_path: Path) -> None:
        """When at least one page has text, ``needs_vision`` is not set."""
        dummy_pdf = tmp_path / "mixed.pdf"
        dummy_pdf.write_bytes(b"%PDF-1.4 stub")

        pages = [_make_page(""), _make_page("has content"), _make_page("")]
        reader = _make_reader(pages)

        loader = PdfLoader()

        with patch(
            "pypdf.PdfReader",
            return_value=reader,
        ):
            docs = loader.load_document(str(dummy_pdf))

        assert not docs[0]["metadata"].get("needs_vision")

    def test_whitespace_only_pages_count_as_empty(self, tmp_path: Path) -> None:
        """Pages with only whitespace are treated as empty for vision detection."""
        dummy_pdf = tmp_path / "whitespace.pdf"
        dummy_pdf.write_bytes(b"%PDF-1.4 stub")

        pages = [_make_page("   \n\t  "), _make_page("\n")]
        reader = _make_reader(pages)

        loader = PdfLoader()

        with patch(
            "pypdf.PdfReader",
            return_value=reader,
        ):
            docs = loader.load_document(str(dummy_pdf))

        assert docs[0]["metadata"].get("needs_vision") is True


# ---------------------------------------------------------------------------
# Task 4: pdf_max_pages setting
# ---------------------------------------------------------------------------


class TestPdfMaxPagesSetting:
    """pdf_max_pages caps page extraction and appends a warning."""

    def test_pages_capped_at_max_pages(self, tmp_path: Path) -> None:
        """Only ``pdf_max_pages`` pages are extracted when limit is exceeded."""
        dummy_pdf = tmp_path / "big.pdf"
        dummy_pdf.write_bytes(b"%PDF-1.4 stub")

        pages = [_make_page(f"page {i}") for i in range(5)]
        reader = _make_reader(pages)
        reader.pages = pages  # let total_pages be 5

        settings = _make_settings(pdf_max_pages=3)
        loader = PdfLoader(settings=settings)

        with patch(
            "pypdf.PdfReader",
            return_value=reader,
        ):
            docs = loader.load_document(str(dummy_pdf))

        # Only first 3 pages should appear
        content = docs[0]["content"]
        assert "page 0" in content
        assert "page 1" in content
        assert "page 2" in content
        assert "page 3" not in content
        assert "page 4" not in content

    def test_truncation_warning_is_attached(self, tmp_path: Path) -> None:
        """A ``loader_warnings`` entry describes the truncation."""
        dummy_pdf = tmp_path / "big.pdf"
        dummy_pdf.write_bytes(b"%PDF-1.4 stub")

        pages = [_make_page(f"page {i}") for i in range(10)]
        reader = _make_reader(pages)
        reader.pages = pages

        settings = _make_settings(pdf_max_pages=5)
        loader = PdfLoader(settings=settings)

        with patch(
            "pypdf.PdfReader",
            return_value=reader,
        ):
            docs = loader.load_document(str(dummy_pdf))

        warnings = docs[0]["metadata"].get("loader_warnings", [])
        assert any("truncated" in w and "5" in w and "10" in w for w in warnings)

    def test_no_cap_processes_all_pages(self, tmp_path: Path) -> None:
        """When ``pdf_max_pages`` is None (default), all pages are processed."""
        dummy_pdf = tmp_path / "normal.pdf"
        dummy_pdf.write_bytes(b"%PDF-1.4 stub")

        pages = [_make_page(f"page {i}") for i in range(7)]
        reader = _make_reader(pages)
        reader.pages = pages

        loader = PdfLoader()  # no settings — no cap

        with patch(
            "pypdf.PdfReader",
            return_value=reader,
        ):
            docs = loader.load_document(str(dummy_pdf))

        content = docs[0]["content"]
        for i in range(7):
            assert f"page {i}" in content

    def test_cap_not_triggered_when_under_limit(self, tmp_path: Path) -> None:
        """When pages <= cap, no truncation warning is added."""
        dummy_pdf = tmp_path / "small.pdf"
        dummy_pdf.write_bytes(b"%PDF-1.4 stub")

        pages = [_make_page("p1"), _make_page("p2")]
        reader = _make_reader(pages)
        reader.pages = pages

        settings = _make_settings(pdf_max_pages=10)
        loader = PdfLoader(settings=settings)

        with patch(
            "pypdf.PdfReader",
            return_value=reader,
        ):
            docs = loader.load_document(str(dummy_pdf))

        warnings = docs[0]["metadata"].get("loader_warnings", [])
        assert not any("truncated" in w for w in warnings)


# ---------------------------------------------------------------------------
# LoaderSettings new field
# ---------------------------------------------------------------------------


class TestLoaderSettingsPdfMaxPages:
    """``LoaderSettings.pdf_max_pages`` defaults and validation."""

    def test_default_is_none(self) -> None:
        """Default value for ``pdf_max_pages`` is None (no cap)."""
        s = LoaderSettings()
        assert s.pdf_max_pages is None

    def test_positive_value_accepted(self) -> None:
        """A positive integer is accepted."""
        s = LoaderSettings(pdf_max_pages=100)
        assert s.pdf_max_pages == 100

    def test_zero_rejected(self) -> None:
        """``pdf_max_pages=0`` is rejected by the Field(ge=1) constraint."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            LoaderSettings(pdf_max_pages=0)

    def test_negative_rejected(self) -> None:
        """Negative values are rejected."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            LoaderSettings(pdf_max_pages=-5)


# ---------------------------------------------------------------------------
# 2026-05-18: location_index emitted alongside _page_texts
# ---------------------------------------------------------------------------


class TestLocationIndex:
    """PDF loader emits a location_index covering the joined full_text.

    Each entry maps a char range in the joined content to its 1-based
    page_number. _page_texts (the vision-pipeline contract) survives
    unchanged.
    """

    def test_location_index_covers_three_pages(self, tmp_path: Path) -> None:
        dummy_pdf = tmp_path / "test.pdf"
        dummy_pdf.write_bytes(b"%PDF-1.4 stub")

        pages = [
            _make_page("Page one body."),  # 14 chars
            _make_page("Second page."),  # 12 chars
            _make_page("Third page."),  # 11 chars
        ]
        reader = _make_reader(pages)

        loader = PdfLoader()
        with patch("pypdf.PdfReader", return_value=reader):
            docs = loader.load_document(str(dummy_pdf))

        metadata = docs[0]["metadata"]

        # _page_texts contract survives.
        assert metadata["_page_texts"] == ["Page one body.", "Second page.", "Third page."]

        # location_index: joined content has 2-char "\n\n" separators.
        # Page 1: [0, 14); Page 2: [16, 28); Page 3: [30, 41).
        assert metadata["location_index"] == [
            {"start_char": 0, "end_char": 14, "page_number": 1, "section": None},
            {"start_char": 16, "end_char": 28, "page_number": 2, "section": None},
            {"start_char": 30, "end_char": 41, "page_number": 3, "section": None},
        ]
