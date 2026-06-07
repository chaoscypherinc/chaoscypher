# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

r"""EPUB e-book loader (pure stdlib + BeautifulSoup).

EPUB is a zip archive containing XHTML chapter files and an OPF
manifest that lists every resource and prescribes a reading order
(the *spine*).

Implementation strategy:

1. Open the .epub as a zip.
2. Parse ``META-INF/container.xml`` to find the OPF rootfile path.
3. Parse the OPF for ``<dc:title>`` / ``<dc:creator>``, the manifest
   (``id`` -> ``href``), and the spine (ordered ``idref``\ s).
4. Walk the spine, dereference each ``idref`` through the manifest,
   read the chapter XHTML, and extract visible text via BeautifulSoup
   (same drop-tag list as the standalone HTML loader).

No third-party EPUB library is used — the dominant one,
``ebooklib``, is AGPL-only, which would force the proprietary
enterprise edition to inherit AGPL terms. Sticking to stdlib +
BeautifulSoup keeps Core compatible with the dual-license model.
"""

from __future__ import annotations

import tempfile
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, Any
from xml.etree import ElementTree

import structlog
from bs4 import BeautifulSoup

from chaoscypher_core.exceptions import ValidationError
from chaoscypher_core.plugins import PluginMetadata


if TYPE_CHECKING:
    from chaoscypher_core.settings import EngineSettings
    from chaoscypher_core.utils.chunk import LocationBoundary


logger = structlog.get_logger(__name__)

_NS = {
    "container": "urn:oasis:names:tc:opendocument:xmlns:container",
    "opf": "http://www.idpf.org/2007/opf",
    "dc": "http://purl.org/dc/elements/1.1/",
}

_DROP_TAGS = ("script", "style")


def _summarize_encodings(seen: list[str]) -> str:
    """Return a single label summarising one or more per-chapter encoding labels.

    If every chapter decoded with the same encoding, return that label.
    Otherwise return ``"mixed:<enc1>,<enc2>,…"`` with the distinct labels
    joined in encounter order (preserving first-seen ordering via dict).
    An empty list returns the empty string.
    """
    if not seen:
        return ""
    unique: list[str] = list(dict.fromkeys(seen))
    if len(unique) == 1:
        return unique[0]
    return "mixed:" + ",".join(unique)


class EPUBLoader:
    """Loader for ``.epub`` e-book files."""

    @property
    def supported_extensions(self) -> list[str]:
        """File extensions this loader supports."""
        return [".epub", ".EPUB"]

    @property
    def metadata(self) -> PluginMetadata:
        """Plugin metadata."""
        return PluginMetadata(
            plugin_id="epub",
            name="EPUB Loader",
            description="Loads .epub e-book files (stdlib + BeautifulSoup).",
            version="1.0.0",
            author="ChaosCypher",
            category="loader",
            builtin=True,
            origin="builtin",
            tags=["document", "ebook"],
        )

    def __init__(self, settings: EngineSettings | None = None) -> None:
        """Initialize EPUB loader.

        Args:
            settings: Settings instance (not currently used).
        """
        self.settings = settings

    def load_document(self, filepath: str) -> list[dict[str, Any]]:
        """Load an EPUB file.

        Args:
            filepath: Path to a .epub file.

        Returns:
            A single-item list with concatenated chapter text and
            metadata listing title, author, chapter count and titles.

        Raises:
            ValidationError: If the file is not a valid EPUB (corrupt
                zip container, missing required ``META-INF/container.xml``
                rootfile, etc.).
        """
        from chaoscypher_core.utils.encoding import detect_encoding

        path = Path(filepath)
        try:
            with zipfile.ZipFile(path, "r") as zf:
                opf_path = self._find_opf_path(zf)
                opf_doc = ElementTree.fromstring(zf.read(opf_path))  # noqa: S314 - local user-uploaded EPUB; ET disables external entities in Py 3.7.1+

                title, author = self._read_metadata(opf_doc)
                spine_ids = self._read_spine(opf_doc)
                manifest = self._read_manifest(opf_doc)

                opf_dir = str(Path(opf_path).parent).replace("\\", "/")
                if opf_dir == ".":
                    opf_dir = ""

                chapters: list[str] = []
                chapter_titles: list[str] = []
                chapter_replacement_total: int = 0
                chapter_encoding_seen: list[str] = []

                for item_id in spine_ids:
                    href = manifest.get(item_id)
                    if not href:
                        continue
                    chapter_path = (f"{opf_dir}/{href}" if opf_dir else href).lstrip("/")
                    try:
                        raw_bytes = zf.read(chapter_path)
                    except KeyError:
                        logger.warning(
                            "epub_chapter_missing",
                            chapter_path=chapter_path,
                            idref=item_id,
                        )
                        continue
                    # Route through the canonical detect_encoding helper so
                    # cp1252 / Latin-1 EPUB chapters decode strictly (no
                    # silent U+FFFD substitution) and the replacement-char
                    # counter is populated for the data-quality rollup.
                    # detect_encoding reads from a Path, so we write the
                    # chapter bytes to a temporary file — same pattern used
                    # by the web adapter for in-memory bytes.
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".xhtml") as _tmp:
                        _tmp.write(raw_bytes)
                        _tmp_path = Path(_tmp.name)
                    try:
                        encoding_used, raw, replacement_count = detect_encoding(_tmp_path)
                    finally:
                        _tmp_path.unlink(missing_ok=True)
                    chapter_replacement_total += replacement_count
                    chapter_encoding_seen.append(encoding_used)
                    ch_text, ch_title = self._extract_chapter_text(raw)
                    if ch_text.strip():
                        chapters.append(ch_text)
                        chapter_titles.append(ch_title)
        except ValidationError:
            # Already a structured error from ``_find_opf_path`` etc.;
            # don't double-wrap.
            raise
        except (zipfile.BadZipFile, KeyError, ElementTree.ParseError) as exc:
            msg = f"File '{path.name}' is not a valid EPUB: {exc}"
            raise ValidationError(msg, field="content") from exc

        content = "\n\n".join(chapters)

        encoding_used = _summarize_encodings(chapter_encoding_seen)

        # Build location_index for the chunker. Each entry maps a char
        # range in the joined content to its chapter title (section
        # field). EPUB has chapters, not pages — page_number is None.
        location_index: list[LocationBoundary] = []
        chapter_offset = 0
        for i, chapter_text in enumerate(chapters):
            chapter_len = len(chapter_text)
            location_index.append(
                {
                    "start_char": chapter_offset,
                    "end_char": chapter_offset + chapter_len,
                    "page_number": None,
                    "section": chapter_titles[i],
                }
            )
            chapter_offset += chapter_len
            if i < len(chapters) - 1:
                chapter_offset += 2  # len("\n\n")

        logger.info(
            "epub_loaded",
            filepath=str(path),
            title=title,
            author=author,
            chapter_count=len(chapters),
            character_count=len(content),
            encoding_used=encoding_used,
            replacement_chars_count=chapter_replacement_total,
        )

        return [
            {
                "content": content,
                "metadata": {
                    "source": str(path),
                    "extraction_method": "epub",
                    "title": title,
                    "author": author,
                    "chapter_count": len(chapters),
                    "chapter_titles": chapter_titles,
                    "content_type": "application/epub+zip",
                    "encoding_used": encoding_used,
                    "replacement_chars_count": chapter_replacement_total,
                    "location_index": location_index,
                },
            }
        ]

    def supports_ocr(self) -> bool:
        """EPUB files don't need OCR."""
        return False

    @staticmethod
    def _find_opf_path(zf: zipfile.ZipFile) -> str:
        """Resolve the OPF rootfile path via ``META-INF/container.xml``."""
        container = ElementTree.fromstring(zf.read("META-INF/container.xml"))  # noqa: S314 - local user-uploaded EPUB; ET disables external entities in Py 3.7.1+
        rootfile = container.find("container:rootfiles/container:rootfile", _NS)
        if rootfile is None:
            msg = "EPUB container missing rootfile"
            raise ValidationError(msg, field="content")
        full_path = rootfile.attrib.get("full-path", "")
        if not full_path:
            msg = "EPUB rootfile missing full-path attribute"
            raise ValidationError(msg, field="content")
        return full_path

    @staticmethod
    def _read_metadata(opf: ElementTree.Element) -> tuple[str, str]:
        """Pull ``<dc:title>`` / ``<dc:creator>`` from the OPF metadata."""
        meta = opf.find("opf:metadata", _NS)
        title = ""
        author = ""
        if meta is not None:
            t = meta.find("dc:title", _NS)
            if t is not None and t.text:
                title = t.text.strip()
            a = meta.find("dc:creator", _NS)
            if a is not None and a.text:
                author = a.text.strip()
        return title, author

    @staticmethod
    def _read_spine(opf: ElementTree.Element) -> list[str]:
        """Return the ordered list of spine ``idref`` values."""
        spine = opf.find("opf:spine", _NS)
        if spine is None:
            return []
        return [
            ref.attrib.get("idref", "")
            for ref in spine.findall("opf:itemref", _NS)
            if ref.attrib.get("idref")
        ]

    @staticmethod
    def _read_manifest(opf: ElementTree.Element) -> dict[str, str]:
        """Build the ``id -> href`` map from the OPF manifest."""
        manifest = opf.find("opf:manifest", _NS)
        if manifest is None:
            return {}
        return {
            item.attrib["id"]: item.attrib["href"]
            for item in manifest.findall("opf:item", _NS)
            if "id" in item.attrib and "href" in item.attrib
        }

    @staticmethod
    def _extract_chapter_text(raw: str) -> tuple[str, str]:
        """Extract visible text and ``<title>`` from a chapter XHTML."""
        soup = BeautifulSoup(raw, "html.parser")
        for tag_name in _DROP_TAGS:
            for tag in soup.find_all(tag_name):
                tag.decompose()
        title = ""
        if soup.title and soup.title.string:
            title = soup.title.string.strip()
        text = soup.get_text(separator="\n", strip=True)
        return text, title


__all__ = ["EPUBLoader"]
