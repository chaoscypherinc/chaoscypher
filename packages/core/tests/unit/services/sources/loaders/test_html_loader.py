# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""HTMLLoader extracts text from a single .html file.

Workstream 7 (2026-05-07): the archive Sphinx handler already strips
script/style/nav/aside on HTML embedded in .zip / .tar.gz uploads, but
a single ``.html`` file uploaded standalone has no loader. This test
covers the new standalone HTML loader.
"""

from __future__ import annotations

from pathlib import Path

from chaoscypher_core.services.sources.loaders.html_loader import HTMLLoader


def test_html_loader_extracts_visible_text(tmp_path: Path) -> None:
    p = tmp_path / "page.html"
    p.write_text(
        "<html><head><title>Sample</title><script>x=1</script>"
        "<style>body{color:red}</style></head>"
        "<body><h1>Hello</h1><p>World content here.</p>"
        "<aside>Side note</aside></body></html>",
        encoding="utf-8",
    )
    loader = HTMLLoader()
    docs = loader.load_document(str(p))
    text = docs[0]["content"]
    # Visible content survives.
    assert "Hello" in text
    assert "World content here" in text
    # Script/style content removed.
    assert "x=1" not in text
    assert "color:red" not in text
    # Aside/footer/nav stripped.
    assert "Side note" not in text
    # Title captured in metadata.
    assert docs[0]["metadata"]["title"] == "Sample"


def test_html_loader_handles_cp1252_encoded(tmp_path: Path) -> None:
    p = tmp_path / "cp.html"
    p.write_bytes("<html><body><p>café résumé</p></body></html>".encode("cp1252"))
    loader = HTMLLoader()
    docs = loader.load_document(str(p))
    assert "café" in docs[0]["content"]
    assert "résumé" in docs[0]["content"]


def test_html_loader_supports_html_htm_xhtml() -> None:
    loader = HTMLLoader()
    exts = loader.supported_extensions
    assert ".html" in exts
    assert ".htm" in exts
    assert ".xhtml" in exts


def test_html_loader_metadata_plugin_id() -> None:
    loader = HTMLLoader()
    assert loader.metadata.plugin_id == "html"
    assert loader.metadata.category == "loader"


def test_html_loader_emits_per_tag_dict(tmp_path: Path) -> None:
    """HTMLLoader records stripped-tag counts as a per-tag dict for the
    indexing-handler dict-merge rollup (Phase 7 audit-remediation 2026-05-09).
    """
    p = tmp_path / "test.html"
    p.write_bytes(
        b"<html><head></head><body><script>x</script><nav>y</nav><nav>z</nav><div>main</div></body></html>"
    )
    loader = HTMLLoader()
    docs = loader.load_document(str(p))

    dropped = docs[0]["metadata"].get("loader_html_dropped_tags")
    assert isinstance(dropped, dict), f"expected dict, got {type(dropped)}: {dropped!r}"
    assert dropped.get("script") == 1
    assert dropped.get("nav") == 2


def test_html_loader_emits_empty_dict_when_no_tags_stripped(tmp_path: Path) -> None:
    """HTMLLoader emits {} (not None or 0) when nothing is stripped."""
    p = tmp_path / "clean.html"
    p.write_text("<html><body><p>Plain text only.</p></body></html>", encoding="utf-8")
    loader = HTMLLoader()
    docs = loader.load_document(str(p))
    dropped = docs[0]["metadata"].get("loader_html_dropped_tags")
    assert dropped == {}, f"expected empty dict, got {dropped!r}"


def test_html_loader_does_not_duplicate_title_in_body(tmp_path: Path) -> None:
    """``<title>`` text appears in metadata exactly once and not in body.

    BeautifulSoup's ``get_text()`` walks every descendant including
    ``<head>``. Without ``soup.title.decompose()`` the title text is
    surfaced both in metadata and concatenated into the body content,
    duplicating it through the rest of the pipeline (chunking,
    embedding, search).
    """
    unique_title = "ZZZUniqueTitleNotInBodyZZZ"
    p = tmp_path / "titled.html"
    p.write_text(
        f"<html><head><title>{unique_title}</title></head>"
        "<body><h1>Article heading</h1>"
        "<p>Article body without the title text.</p></body></html>",
        encoding="utf-8",
    )
    loader = HTMLLoader()
    docs = loader.load_document(str(p))

    # Title captured in metadata exactly once.
    assert docs[0]["metadata"]["title"] == unique_title

    # Title text NOT present in extracted content.
    content = docs[0]["content"]
    assert unique_title not in content, f"title text {unique_title!r} leaked into body: {content!r}"
    # Sanity: body content still extracted.
    assert "Article heading" in content
    assert "Article body without the title text." in content
