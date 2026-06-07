# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for archive handler loader_files_skipped counter parity.

Verifies that MarkdownHandler, SphinxHTMLHandler, and OpenAPIHandler all
attach ``loader_files_skipped`` to ``documents[0]["metadata"]`` when files
are silently dropped, and emit a synthetic empty-content document (with
``loader_warnings``) when every file in the archive is skipped.

Also verifies that OpenAPIHandler attaches a ``loader_warnings`` entry
when jsonref is unavailable (the _resolve_refs soft-dep fallback path).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

from chaoscypher_core.services.sources.loaders.archive.handlers.markdown_handler import (
    MarkdownHandler,
)
from chaoscypher_core.services.sources.loaders.archive.handlers.openapi_handler import (
    OpenAPIHandler,
)
from chaoscypher_core.services.sources.loaders.archive.handlers.sphinx_handler import (
    SphinxHTMLHandler,
)
from chaoscypher_core.settings import EngineSettings, PathSettings


def _make_settings(data_dir: Path) -> EngineSettings:
    """Build minimal EngineSettings pointing at *data_dir*."""
    return EngineSettings(paths=PathSettings(data_dir=str(data_dir)))


# ---------------------------------------------------------------------------
# MarkdownHandler
# ---------------------------------------------------------------------------


class TestMarkdownHandlerCounterParity:
    """loader_files_skipped parity for MarkdownHandler."""

    def test_partial_skip_attaches_counter_to_first_doc(self, tmp_path: Path) -> None:
        """When some files fail and some succeed, counter appears on first doc."""
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()

        # One valid file that will succeed
        (docs_dir / "valid.md").write_text("# Valid\n\nSome content here.")

        handler = MarkdownHandler()
        settings = _make_settings(tmp_path)

        # Patch _process_markdown_file so the second file raises
        original_process = handler._process_markdown_file

        call_count = 0

        def patched(md_path: Path, base_dir: Path) -> Any:
            nonlocal call_count
            call_count += 1
            return original_process(md_path, base_dir)

        # Add a file that will be processed as empty (no content)
        (docs_dir / "empty.md").write_text("")

        docs = handler.process(docs_dir, settings)

        # valid.md should produce a document; empty.md contributes a skip
        assert len(docs) >= 1
        assert docs[0]["metadata"].get("loader_files_skipped", 0) >= 1

    def test_exception_increments_skip_counter(self, tmp_path: Path) -> None:
        """Per-file exceptions increment files_skipped and appear on the first doc."""
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()

        # One file that will succeed
        (docs_dir / "good.md").write_text("# Good\n\nThis file is fine.")
        # One file that will raise on processing
        (docs_dir / "bad.md").write_text("# Bad\n\nThis will raise.")

        handler = MarkdownHandler()
        settings = _make_settings(tmp_path)

        original = handler._process_markdown_file

        def raise_on_bad(md_path: Path, base_dir: Path) -> Any:
            if md_path.name == "bad.md":
                raise RuntimeError("simulated parse failure")
            return original(md_path, base_dir)

        handler._process_markdown_file = raise_on_bad  # type: ignore[method-assign]
        docs = handler.process(docs_dir, settings)

        assert len(docs) >= 1
        assert docs[0]["metadata"].get("loader_files_skipped", 0) >= 1

    def test_all_files_skipped_returns_synthetic_doc(self, tmp_path: Path) -> None:
        """When every file fails, a synthetic empty-content doc is returned."""
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()

        (docs_dir / "a.md").write_text("# A\nsome content")
        (docs_dir / "b.md").write_text("# B\nsome content")

        handler = MarkdownHandler()
        settings = _make_settings(tmp_path)

        def always_raise(md_path: Path, base_dir: Path) -> Any:
            raise RuntimeError("forced failure")

        handler._process_markdown_file = always_raise  # type: ignore[method-assign]
        docs = handler.process(docs_dir, settings)

        assert len(docs) == 1
        meta = docs[0]["metadata"]
        # On case-insensitive filesystems (Windows) _find_markdown_files may
        # find the same files via both *.md and *.MD globs, so the skipped
        # count is >= the number of unique source files.
        assert meta["loader_files_skipped"] >= 2
        assert any("skipped" in w.lower() for w in meta.get("loader_warnings", []))
        assert docs[0]["content"] == ""

    def test_empty_content_files_counted_as_skipped(self, tmp_path: Path) -> None:
        """Files that produce empty content count as skipped."""
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()

        # All files have no real content (just whitespace)
        (docs_dir / "x.md").write_text("   \n  ")
        (docs_dir / "y.md").write_text("\t\n")

        handler = MarkdownHandler()
        settings = _make_settings(tmp_path)

        docs = handler.process(docs_dir, settings)

        # Both files produced empty content → synthetic doc
        assert len(docs) == 1
        meta = docs[0]["metadata"]
        # On case-insensitive filesystems the same files may be matched by
        # both *.md and *.MD globs, so skipped count is >= unique files.
        assert meta["loader_files_skipped"] >= 2
        assert meta.get("loader_warnings"), "loader_warnings should be non-empty"

    def test_no_skip_no_counter(self, tmp_path: Path) -> None:
        """When no files are skipped, loader_files_skipped is absent from metadata."""
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()

        (docs_dir / "page.md").write_text("# Page\n\nContent here.")

        handler = MarkdownHandler()
        settings = _make_settings(tmp_path)

        docs = handler.process(docs_dir, settings)

        assert len(docs) >= 1
        # Key should not be present when nothing was skipped
        assert "loader_files_skipped" not in docs[0]["metadata"]


# ---------------------------------------------------------------------------
# SphinxHTMLHandler
# ---------------------------------------------------------------------------


class TestSphinxHandlerCounterParity:
    """loader_files_skipped parity for SphinxHTMLHandler."""

    def _make_html_file(self, path: Path, title: str = "Page", body: str = "Content") -> None:
        """Write a minimal Sphinx-style HTML file."""
        path.write_text(
            f"<html><head><title>{title}</title></head>"
            f"<body><div class='body'><p>{body}</p></div></body></html>"
        )

    def test_skipped_pattern_files_increment_counter(self, tmp_path: Path) -> None:
        """Files matching SKIP_PATTERNS increment files_skipped."""
        sphinx_dir = tmp_path / "sphinx"
        sphinx_dir.mkdir()

        # One content file + two files that match skip patterns
        self._make_html_file(sphinx_dir / "index.html")
        (sphinx_dir / "genindex.html").write_text("<html><body>Gen Index</body></html>")
        (sphinx_dir / "search.html").write_text("<html><body>Search</body></html>")

        handler = SphinxHTMLHandler()
        settings = _make_settings(tmp_path)

        docs = handler.process(sphinx_dir, settings)

        assert len(docs) >= 1
        # 2 files match skip patterns
        assert docs[0]["metadata"].get("loader_files_skipped", 0) >= 2

    def test_exception_increments_counter(self, tmp_path: Path) -> None:
        """Per-file exceptions during content extraction increment the counter."""
        sphinx_dir = tmp_path / "sphinx"
        sphinx_dir.mkdir()

        self._make_html_file(sphinx_dir / "good.html", body="Good content here")
        self._make_html_file(sphinx_dir / "bad.html", body="Will raise")

        handler = SphinxHTMLHandler()
        settings = _make_settings(tmp_path)

        original = handler._extract_html_content

        def raise_on_bad(html_path: Path, base_dir: Path) -> Any:
            if html_path.name == "bad.html":
                raise RuntimeError("simulated extraction failure")
            return original(html_path, base_dir)

        handler._extract_html_content = raise_on_bad  # type: ignore[method-assign]
        docs = handler.process(sphinx_dir, settings)

        assert len(docs) >= 1
        assert docs[0]["metadata"].get("loader_files_skipped", 0) >= 1

    def test_all_files_skipped_returns_synthetic_doc(self, tmp_path: Path) -> None:
        """When every HTML file fails, a synthetic empty-content doc is emitted."""
        sphinx_dir = tmp_path / "sphinx"
        sphinx_dir.mkdir()

        self._make_html_file(sphinx_dir / "a.html")
        self._make_html_file(sphinx_dir / "b.html")

        handler = SphinxHTMLHandler()
        settings = _make_settings(tmp_path)

        def always_raise(html_path: Path, base_dir: Path) -> Any:
            raise RuntimeError("forced failure")

        handler._extract_html_content = always_raise  # type: ignore[method-assign]
        docs = handler.process(sphinx_dir, settings)

        assert len(docs) == 1
        meta = docs[0]["metadata"]
        assert meta["loader_files_skipped"] == 2
        assert any("skipped" in w.lower() for w in meta.get("loader_warnings", []))
        assert docs[0]["content"] == ""

    def test_empty_html_body_counted_as_skipped(self, tmp_path: Path) -> None:
        """HTML files that produce no extractable content count as skipped."""
        sphinx_dir = tmp_path / "sphinx"
        sphinx_dir.mkdir()

        # Files whose body content extracts as empty / whitespace only
        (sphinx_dir / "empty.html").write_text(
            "<html><body><div class='body'>   </div></body></html>"
        )

        handler = SphinxHTMLHandler()
        settings = _make_settings(tmp_path)

        docs = handler.process(sphinx_dir, settings)

        assert len(docs) == 1
        meta = docs[0]["metadata"]
        assert meta["loader_files_skipped"] == 1
        assert any("1" in w for w in meta.get("loader_warnings", []))

    def test_no_skip_no_counter(self, tmp_path: Path) -> None:
        """When no files are skipped, loader_files_skipped is absent."""
        sphinx_dir = tmp_path / "sphinx"
        sphinx_dir.mkdir()

        self._make_html_file(sphinx_dir / "page.html", body="Useful page content")

        handler = SphinxHTMLHandler()
        settings = _make_settings(tmp_path)

        docs = handler.process(sphinx_dir, settings)

        assert len(docs) >= 1
        assert "loader_files_skipped" not in docs[0]["metadata"]


# ---------------------------------------------------------------------------
# OpenAPIHandler
# ---------------------------------------------------------------------------

_MINIMAL_OPENAPI_SPEC: dict[str, Any] = {
    "openapi": "3.0.0",
    "info": {"title": "Test API", "version": "1.0.0"},
    "paths": {
        "/items": {
            "get": {
                "summary": "List items",
                "operationId": "listItems",
                "responses": {"200": {"description": "OK"}},
            }
        }
    },
}


class TestOpenAPIHandlerCounterParity:
    """loader_files_skipped / loader_warnings parity for OpenAPIHandler."""

    def _write_spec(self, path: Path, spec: dict[str, Any] | None = None) -> None:
        """Write an OpenAPI JSON spec to *path*."""
        path.write_text(json.dumps(spec or _MINIMAL_OPENAPI_SPEC))

    def test_successful_processing_has_no_files_skipped(self, tmp_path: Path) -> None:
        """A successfully processed spec does not inject loader_files_skipped."""
        self._write_spec(tmp_path / "openapi.json")

        handler = OpenAPIHandler()
        settings = _make_settings(tmp_path)

        docs = handler.process(tmp_path, settings)

        assert len(docs) >= 1
        assert "loader_files_skipped" not in docs[0]["metadata"]

    def test_spec_parse_failure_returns_synthetic_doc(self, tmp_path: Path) -> None:
        """A broken spec file triggers the synthetic error doc path."""
        (tmp_path / "openapi.json").write_text("{invalid json{{{{")

        handler = OpenAPIHandler()
        settings = _make_settings(tmp_path)

        docs = handler.process(tmp_path, settings)

        assert len(docs) == 1
        meta = docs[0]["metadata"]
        assert meta.get("loader_files_skipped") == 1
        assert docs[0]["content"] == ""
        # loader_warnings should describe the failure
        assert len(meta.get("loader_warnings", [])) >= 1

    def test_jsonref_unavailable_returns_synthetic_error_doc(self, tmp_path: Path) -> None:
        """When jsonref is not installed, process() raises OperationError internally
        and returns a synthetic error doc with loader_files_skipped=1 and
        loader_warnings describing the missing dependency.

        Phase 5c: jsonref is now hard-required — a missing import fails loudly
        rather than silently emitting unresolved $ref placeholders.
        """
        self._write_spec(tmp_path / "openapi.json")

        handler = OpenAPIHandler()
        settings = _make_settings(tmp_path)

        # Simulate jsonref being absent by patching the import inside _resolve_refs.
        import builtins

        real_import = builtins.__import__

        def mock_import(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "jsonref":
                raise ImportError("No module named 'jsonref'")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            docs = handler.process(tmp_path, settings)

        # Synthetic error doc is returned (OperationError caught by process())
        assert len(docs) == 1
        meta = docs[0]["metadata"]
        assert meta.get("loader_files_skipped") == 1
        assert docs[0]["content"] == ""
        warnings = meta.get("loader_warnings", [])
        assert any("jsonref" in w.lower() for w in warnings)

    def test_jsonref_ref_failure_returns_synthetic_error_doc(self, tmp_path: Path) -> None:
        """When $ref resolution raises an exception, process() returns a synthetic
        error doc rather than silently emitting broken content.

        Phase 5c: ref-resolution failures are hard errors.
        """
        self._write_spec(tmp_path / "openapi.json")

        handler = OpenAPIHandler()
        settings = _make_settings(tmp_path)

        # Patch _resolve_refs directly to raise an OperationError
        from chaoscypher_core.exceptions import OperationError

        def raise_on_resolve(spec: dict[str, Any]) -> dict[str, Any]:
            raise OperationError("Failed to resolve $ref references", operation="archive_load")

        handler._resolve_refs = raise_on_resolve  # type: ignore[method-assign]
        docs = handler.process(tmp_path, settings)

        assert len(docs) == 1
        meta = docs[0]["metadata"]
        assert meta.get("loader_files_skipped") == 1
        assert docs[0]["content"] == ""
        assert len(meta.get("loader_warnings", [])) >= 1
