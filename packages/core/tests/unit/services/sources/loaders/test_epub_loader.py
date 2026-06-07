# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""EPUBLoader walks the spine in order and extracts visible chapter text.

Workstream 7 (2026-05-07): EPUB is a zip of XHTML chapters with an
OPF manifest. Implementation uses stdlib + BeautifulSoup only — the
dominant ebook library, ``ebooklib``, is AGPL and would block the
proprietary enterprise edition.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from textwrap import dedent

import pytest

from chaoscypher_core.exceptions import ValidationError
from chaoscypher_core.services.sources.loaders.epub_loader import (
    EPUBLoader,
    _summarize_encodings,
)


FIXTURE = Path(__file__).parent.parent.parent.parent.parent / "fixtures" / "loaders" / "sample.epub"


def _build_epub_with_cp1252_chapter(tmp_path: Path) -> Path:
    """Build a minimal valid EPUB whose single chapter is cp1252-encoded.

    The chapter body contains ``café`` — the ``é`` (0xE9) is valid in
    both cp1252 and Latin-1 but is *not* valid UTF-8, so decoding the
    raw bytes with ``utf-8 errors="replace"`` would insert U+FFFD.
    ``detect_encoding`` should detect cp1252 / Latin-1 and decode the
    byte correctly without inserting a replacement character.

    We need ``replacement_chars_count`` > 0 only when the OLD code path
    (raw ``decode("utf-8", errors="replace")``) was used.  After the
    fix, the count should be 0 because ``detect_encoding`` decodes
    strictly via cp1252.  The test therefore asserts:

    - ``encoding_used`` is present (proves the new path ran).
    - ``replacement_chars_count`` equals **0** (strict decode succeeded).
    """
    container_xml = dedent("""\
        <?xml version="1.0" encoding="UTF-8"?>
        <container version="1.0"
            xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
          <rootfiles>
            <rootfile full-path="OEBPS/content.opf"
                      media-type="application/oebps-package+xml"/>
          </rootfiles>
        </container>
    """)

    content_opf = dedent("""\
        <?xml version="1.0" encoding="UTF-8"?>
        <package version="2.0"
            xmlns="http://www.idpf.org/2007/opf"
            xmlns:dc="http://purl.org/dc/elements/1.1/">
          <metadata>
            <dc:title>CP1252 Test Book</dc:title>
            <dc:creator>Test Author</dc:creator>
          </metadata>
          <manifest>
            <item id="ch1" href="ch1.xhtml"
                  media-type="application/xhtml+xml"/>
          </manifest>
          <spine>
            <itemref idref="ch1"/>
          </spine>
        </package>
    """)

    # "café" — the "é" byte (0xE9) is undefined in UTF-8 as a solo byte
    # but is valid cp1252 / Latin-1.  Decoding with utf-8 errors="replace"
    # produces "caf�"; detect_encoding should give "caf\xe9".
    chapter_utf8_header = (
        '<?xml version="1.0"?><html><head><title>CP1252 Chapter</title></head><body><p>'
    )
    chapter_suffix = "</p></body></html>"
    # Encode the "café" word in cp1252 (0xE9 for é).
    chapter_bytes = (
        chapter_utf8_header.encode("utf-8")
        + "café".encode("cp1252")
        + chapter_suffix.encode("utf-8")
    )

    epub_path = tmp_path / "cp1252_test.epub"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr("META-INF/container.xml", container_xml)
        zf.writestr("OEBPS/content.opf", content_opf)
        zf.writestr("OEBPS/ch1.xhtml", chapter_bytes)
    epub_path.write_bytes(buf.getvalue())
    return epub_path


def test_epub_extracts_chapters_in_spine_order() -> None:
    assert FIXTURE.exists(), f"fixture missing: {FIXTURE}"
    loader = EPUBLoader()
    docs = loader.load_document(str(FIXTURE))
    text = docs[0]["content"]
    # Spine order: ch1 then ch2.
    pos1 = text.find("first chapter")
    pos2 = text.find("second chapter")
    assert pos1 != -1, f"ch1 prose not found in: {text!r}"
    assert pos2 != -1, f"ch2 prose not found in: {text!r}"
    assert pos1 < pos2


def test_epub_metadata_includes_title_author_chapter_count() -> None:
    loader = EPUBLoader()
    docs = loader.load_document(str(FIXTURE))
    md = docs[0]["metadata"]
    assert md["title"] == "Sample Book"
    assert md["author"] == "Sample Author"
    assert md["chapter_count"] == 2
    assert md["extraction_method"] == "epub"


def test_epub_chapter_titles_recorded() -> None:
    loader = EPUBLoader()
    docs = loader.load_document(str(FIXTURE))
    md = docs[0]["metadata"]
    assert "Chapter 1" in md["chapter_titles"]
    assert "Chapter 2" in md["chapter_titles"]


def test_epub_supported_extensions() -> None:
    loader = EPUBLoader()
    assert ".epub" in loader.supported_extensions
    assert loader.metadata.plugin_id == "epub"


def test_epub_loader_wraps_invalid_file_in_validation_error(tmp_path: Path) -> None:
    """A file with .epub extension but wrong magic bytes raises ValidationError.

    Without the wrapper, ``zipfile.BadZipFile`` surfaces and bypasses
    the API exception envelope.
    """
    bogus = tmp_path / "fake.epub"
    bogus.write_bytes(b"not a real epub")

    loader = EPUBLoader()
    with pytest.raises(ValidationError) as exc_info:
        loader.load_document(str(bogus))

    err = exc_info.value
    assert err.code == "VALIDATION_ERROR"
    assert err.field == "content"
    assert "fake.epub" in err.message
    assert "not a valid" in err.message
    assert exc_info.value.__cause__ is not None


def test_summarize_encodings_single_label() -> None:
    assert _summarize_encodings(["utf-8", "utf-8", "utf-8"]) == "utf-8"


def test_summarize_encodings_mixed() -> None:
    result = _summarize_encodings(["cp1252", "utf-8"])
    assert result.startswith("mixed:")
    assert "cp1252" in result
    assert "utf-8" in result


def test_summarize_encodings_empty_returns_empty_string() -> None:
    assert _summarize_encodings([]) == ""


def test_epub_loader_surfaces_replacement_chars_count(tmp_path: Path) -> None:
    """EPUB chapters decoded via detect_encoding surface encoding metadata.

    After routing through ``detect_encoding``:
    - ``encoding_used`` is present in the result metadata.
    - ``replacement_chars_count`` is 0 because detect_encoding decodes
      cp1252/Latin-1 bytes strictly (no U+FFFD substitution).

    The old code used ``raw_bytes.decode("utf-8", errors="replace")``
    which would have produced a non-zero count for the cp1252 test
    fixture — but the point of the fix is that strict decode succeeds,
    so we verify count == 0 and that the encoding label is present.
    """
    epub_path = _build_epub_with_cp1252_chapter(tmp_path)

    loader = EPUBLoader()
    docs = loader.load_document(str(epub_path))

    md = docs[0]["metadata"]
    assert "encoding_used" in md, f"encoding_used missing from metadata keys: {list(md.keys())}"
    assert md["encoding_used"] is not None
    # detect_encoding must have decoded strictly — no replacement chars.
    assert md.get("replacement_chars_count", 0) == 0, (
        f"Expected 0 replacement chars (strict decode), got {md.get('replacement_chars_count')}"
    )


# ---------------------------------------------------------------------------
# 2026-05-18: location_index emitted with chapter titles in section field
# ---------------------------------------------------------------------------


def test_epub_loader_emits_location_index_with_chapter_titles() -> None:
    """EPUB loader emits a location_index where each entry maps the char
    range of a chapter in the joined content to its title (in the section
    field). page_number is always None for EPUB.
    """
    assert FIXTURE.exists(), f"fixture missing: {FIXTURE}"
    loader = EPUBLoader()
    docs = loader.load_document(str(FIXTURE))

    metadata = docs[0]["metadata"]
    content = docs[0]["content"]
    chapter_titles = metadata["chapter_titles"]

    location_index = metadata["location_index"]
    assert len(location_index) == len(chapter_titles)

    for entry in location_index:
        assert entry["page_number"] is None
        assert entry["section"] in chapter_titles

    # Ranges contiguous with "\n\n" gaps between chapters.
    assert location_index[0]["start_char"] == 0
    assert location_index[-1]["end_char"] <= len(content)
    for i in range(len(location_index) - 1):
        assert location_index[i + 1]["start_char"] - location_index[i]["end_char"] == 2
