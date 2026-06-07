# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for GenericHandler synthetic-doc emission when all files are skipped.

Verifies Phase 7 (2026-05-09 audit) fix: when every file in the archive is
skipped via the extension / path skip lists, GenericHandler emits a synthetic
empty-content document carrying ``loader_files_skipped`` and ``loader_warnings``
so the indexing-handler rollup surfaces 'all N files were skipped' rather than
a generic empty-content failure.

Audit finding: P0 #4 (archive handlers).
"""

from __future__ import annotations

from pathlib import Path

from chaoscypher_core.services.sources.loaders.archive.handlers.generic_handler import (
    GenericHandler,
)
from chaoscypher_core.settings import EngineSettings, PathSettings


def _make_settings(data_dir: Path) -> EngineSettings:
    """Build minimal EngineSettings pointing at *data_dir*."""
    return EngineSettings(paths=PathSettings(data_dir=str(data_dir)))


class TestGenericHandlerSyntheticDoc:
    """GenericHandler emits a synthetic doc when every file is skipped."""

    def test_emits_synthetic_doc_when_all_files_skipped(self, tmp_path: Path) -> None:
        """When every file in the archive is skipped, emit a synthetic empty-content
        doc carrying loader_files_skipped and loader_warnings so the
        indexing-handler rollup surfaces 'all N files were skipped' rather than
        a generic empty-content failure.

        .exe, .dll, .so are all in GenericHandler.SKIP_EXTENSIONS (default).
        """
        archive_dir = tmp_path / "all-skipped"
        archive_dir.mkdir()
        # Three files that all match SKIP_EXTENSIONS defaults.
        (archive_dir / "tool.exe").write_bytes(b"binary")
        (archive_dir / "lib.dll").write_bytes(b"binary")
        (archive_dir / "module.so").write_bytes(b"binary")

        handler = GenericHandler()
        settings = _make_settings(tmp_path)
        docs = handler.process(archive_dir, settings)

        assert len(docs) == 1, "expected one synthetic doc when all files skipped"
        assert docs[0]["content"] == ""
        assert docs[0]["metadata"].get("loader_files_skipped") == 3
        warnings = docs[0]["metadata"].get("loader_warnings") or []
        assert any("all 3 files were skipped" in w.lower() for w in warnings), (
            f"expected 'all 3 files were skipped' in loader_warnings, got: {warnings}"
        )

    def test_no_synthetic_doc_when_documents_present(self, tmp_path: Path) -> None:
        """When at least one document is produced, the new branch does not fire.

        Uses a .txt file (supported by TextLoader) to ensure a real document is
        produced, then checks the synthetic-doc branch is inactive.
        """
        archive_dir = tmp_path / "mixed"
        archive_dir.mkdir()
        # One supported file that will produce a real document.
        (archive_dir / "readme.txt").write_text("This is real content for the test.")
        # One skipped binary.
        (archive_dir / "lib.dll").write_bytes(b"binary")

        handler = GenericHandler()
        settings = _make_settings(tmp_path)
        docs = handler.process(archive_dir, settings)

        # At least one real document exists — synthetic doc must NOT be added.
        assert len(docs) >= 1
        assert all(d["content"] != "" for d in docs), (
            "synthetic empty-content doc should not appear when real docs exist"
        )

    def test_empty_archive_returns_empty_list(self, tmp_path: Path) -> None:
        """An archive with no files at all returns [] (files_skipped == 0).

        The synthetic-doc branch is gated on ``files_skipped > 0`` so an
        empty archive must still return [].
        """
        archive_dir = tmp_path / "empty"
        archive_dir.mkdir()

        handler = GenericHandler()
        settings = _make_settings(tmp_path)
        docs = handler.process(archive_dir, settings)

        assert docs == [], f"expected empty list for empty archive, got: {docs}"

    def test_synthetic_doc_includes_doc_type(self, tmp_path: Path) -> None:
        """Synthetic doc metadata includes doc_type == 'generic' (handler name)."""
        archive_dir = tmp_path / "binaries"
        archive_dir.mkdir()
        (archive_dir / "a.pyc").write_bytes(b"\x00" * 16)
        (archive_dir / "b.pyc").write_bytes(b"\x00" * 16)

        handler = GenericHandler()
        settings = _make_settings(tmp_path)
        docs = handler.process(archive_dir, settings)

        assert len(docs) == 1
        assert docs[0]["metadata"].get("doc_type") == "generic"
