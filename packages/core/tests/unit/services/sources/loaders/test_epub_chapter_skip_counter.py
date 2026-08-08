# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""EPUB loader surfaces skipped chapters via ``loader_epub_chapters_skipped``.

Two silent-skip sites existed in ``EPUBLoader.load_document``:

1. a spine ``idref`` with no manifest entry (``if not href: continue``);
2. a manifest chapter missing from the zip (``except KeyError``).

Every sibling loader surfaces its drops through a quality counter
(loader_docx_paragraphs_skipped, loader_xlsx_rows_skipped, ...); EPUB
dropped whole chapters with only a log line. The loader must count both
sites into ``metadata["loader_epub_chapters_skipped"]`` so the indexing
handler's rollup can increment ``QualityCounter.LOADER_EPUB_CHAPTERS_SKIPPED``.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from textwrap import dedent

from chaoscypher_core.services.sources.loaders.epub_loader import EPUBLoader


def _chapter_xhtml(title: str, body: str) -> str:
    return (
        f'<?xml version="1.0"?><html><head><title>{title}</title></head>'
        f"<body><p>{body}</p></body></html>"
    )


def _build_epub_with_skips(tmp_path: Path) -> Path:
    """EPUB with one good chapter + both skip variants.

    Spine references three idrefs:
    - ``ch1``   — good chapter, present in manifest and zip.
    - ``ghost`` — in the spine but absent from the manifest (skip site 1).
    - ``ch2``   — in the manifest but its file is missing from the zip
      (skip site 2).
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
            <dc:title>Skip Test Book</dc:title>
            <dc:creator>Test Author</dc:creator>
          </metadata>
          <manifest>
            <item id="ch1" href="ch1.xhtml" media-type="application/xhtml+xml"/>
            <item id="ch2" href="ch2.xhtml" media-type="application/xhtml+xml"/>
          </manifest>
          <spine>
            <itemref idref="ch1"/>
            <itemref idref="ghost"/>
            <itemref idref="ch2"/>
          </spine>
        </package>
    """)

    epub_path = tmp_path / "skips.epub"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr("META-INF/container.xml", container_xml)
        zf.writestr("OEBPS/content.opf", content_opf)
        zf.writestr("OEBPS/ch1.xhtml", _chapter_xhtml("Chapter 1", "The good chapter."))
        # ch2.xhtml deliberately NOT written — missing-from-zip skip site.
    epub_path.write_bytes(buf.getvalue())
    return epub_path


def _build_clean_epub(tmp_path: Path) -> Path:
    """EPUB with a single well-formed chapter — nothing skipped."""
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
            <dc:title>Clean Book</dc:title>
            <dc:creator>Test Author</dc:creator>
          </metadata>
          <manifest>
            <item id="ch1" href="ch1.xhtml" media-type="application/xhtml+xml"/>
          </manifest>
          <spine>
            <itemref idref="ch1"/>
          </spine>
        </package>
    """)
    epub_path = tmp_path / "clean.epub"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr("META-INF/container.xml", container_xml)
        zf.writestr("OEBPS/content.opf", content_opf)
        zf.writestr("OEBPS/ch1.xhtml", _chapter_xhtml("Chapter 1", "All present."))
    epub_path.write_bytes(buf.getvalue())
    return epub_path


def test_epub_counts_both_skip_sites(tmp_path: Path) -> None:
    """Manifest-less spine idref + missing-from-zip chapter → count of 2."""
    epub_path = _build_epub_with_skips(tmp_path)

    docs = EPUBLoader().load_document(str(epub_path))

    md = docs[0]["metadata"]
    assert md.get("loader_epub_chapters_skipped") == 2, (
        f"expected both skip sites counted, metadata keys: {list(md.keys())}, "
        f"value: {md.get('loader_epub_chapters_skipped')!r}"
    )
    # The good chapter still loads.
    assert "The good chapter." in docs[0]["content"]
    assert md["chapter_count"] == 1


def test_epub_clean_book_reports_zero_skips(tmp_path: Path) -> None:
    """A well-formed EPUB reports 0 so the rollup writes nothing."""
    epub_path = _build_clean_epub(tmp_path)

    docs = EPUBLoader().load_document(str(epub_path))

    assert docs[0]["metadata"].get("loader_epub_chapters_skipped") == 0


def test_epub_counter_enum_member_present() -> None:
    """LOADER_EPUB_CHAPTERS_SKIPPED follows the sibling loader naming."""
    from chaoscypher_core.services.quality.counters import QualityCounter

    assert "LOADER_EPUB_CHAPTERS_SKIPPED" in {m.name for m in QualityCounter}
    assert QualityCounter.LOADER_EPUB_CHAPTERS_SKIPPED.value == "loader_epub_chapters_skipped"


def test_epub_counter_in_reset_defaults() -> None:
    """force_re_extract must zero the new counter like every sibling."""
    from chaoscypher_core.services.quality.counters import _RESET_DEFAULTS

    assert _RESET_DEFAULTS.get("loader_epub_chapters_skipped") == 0
