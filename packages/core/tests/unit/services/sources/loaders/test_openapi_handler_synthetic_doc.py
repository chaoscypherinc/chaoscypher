# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for OpenAPI handler synthetic-doc emission when no spec is found.

Verifies Phase 7 (2026-05-09 audit) fix: when _find_spec_files returns nothing,
OpenApiHandler emits a synthetic empty-content document carrying loader_warnings
so the indexing-handler rollup surfaces 'no OpenAPI spec detected' rather than
a generic empty-content failure.

Audit finding: P0 #5 (archive handlers).
"""

from __future__ import annotations

from pathlib import Path

from chaoscypher_core.services.sources.loaders.archive.handlers.openapi_handler import (
    OpenAPIHandler,
)
from chaoscypher_core.settings import EngineSettings, PathSettings


def _make_settings(data_dir: Path) -> EngineSettings:
    """Build minimal EngineSettings pointing at *data_dir*."""
    return EngineSettings(paths=PathSettings(data_dir=str(data_dir)))


class TestOpenAPIHandlerSyntheticDoc:
    """OpenAPIHandler emits a synthetic doc when no spec files are found."""

    def test_emits_synthetic_doc_when_no_spec_found(self, tmp_path: Path) -> None:
        """If _find_spec_files returns nothing, emit a synthetic empty-content doc
        rather than [] so the user sees an actionable warning.
        """
        archive_dir = tmp_path / "no-spec"
        archive_dir.mkdir()
        (archive_dir / "README.md").write_text("# Just a readme, no OpenAPI spec")
        (archive_dir / "logo.png").write_bytes(b"fake-png")

        handler = OpenAPIHandler()
        settings = _make_settings(tmp_path)
        docs = handler.process(archive_dir, settings)

        assert len(docs) == 1, "expected one synthetic doc when no spec found"
        assert docs[0]["content"] == ""
        warnings = docs[0]["metadata"].get("loader_warnings") or []
        assert isinstance(warnings, list)
        assert any(
            "openapi" in w.lower()
            and (
                "no spec" in w.lower()
                or "not detected" in w.lower()
                or "no openapi" in w.lower()
                or "detected" in w.lower()
            )
            for w in warnings
        ), f"expected actionable OpenAPI warning in loader_warnings, got: {warnings}"

    def test_synthetic_doc_includes_doc_type(self, tmp_path: Path) -> None:
        """Synthetic doc metadata includes doc_type == 'openapi' (handler name)."""
        archive_dir = tmp_path / "no-spec-doctype"
        archive_dir.mkdir()
        (archive_dir / "notes.txt").write_text("No spec here.")

        handler = OpenAPIHandler()
        settings = _make_settings(tmp_path)
        docs = handler.process(archive_dir, settings)

        assert len(docs) == 1
        assert docs[0]["metadata"].get("doc_type") == "openapi"

    def test_loader_warnings_is_a_list(self, tmp_path: Path) -> None:
        """loader_warnings must be a list, not a bare string."""
        archive_dir = tmp_path / "no-spec-list"
        archive_dir.mkdir()

        handler = OpenAPIHandler()
        settings = _make_settings(tmp_path)
        docs = handler.process(archive_dir, settings)

        assert len(docs) == 1
        warnings = docs[0]["metadata"].get("loader_warnings")
        assert isinstance(warnings, list), (
            f"loader_warnings must be a list, got {type(warnings)}: {warnings!r}"
        )
        assert len(warnings) >= 1, "loader_warnings must not be empty"

    def test_existing_return_path_unaffected_when_spec_present(self, tmp_path: Path) -> None:
        """When a valid spec is present, the synthetic-doc branch must NOT fire.

        Uses a minimal but valid OpenAPI 3.0 spec so _find_spec_files returns
        a result and the normal processing path runs.
        """
        import json

        archive_dir = tmp_path / "with-spec"
        archive_dir.mkdir()
        spec = {
            "openapi": "3.0.0",
            "info": {"title": "Test API", "version": "1.0.0"},
            "paths": {
                "/ping": {
                    "get": {
                        "operationId": "ping",
                        "summary": "Health check",
                        "responses": {"200": {"description": "OK"}},
                    }
                }
            },
        }
        (archive_dir / "openapi.json").write_text(json.dumps(spec))

        handler = OpenAPIHandler()
        settings = _make_settings(tmp_path)
        docs = handler.process(archive_dir, settings)

        # At least one real document should be produced (the /ping operation).
        assert len(docs) >= 1, "expected real docs when valid spec is present"
        # None of the real docs should be the synthetic empty-content placeholder.
        assert all(
            d["content"] != "" or d["metadata"].get("loader_warnings") is None for d in docs
        ), "synthetic empty-content doc should not appear when spec is present"
