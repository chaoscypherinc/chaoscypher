# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Regression: _resume_extraction_job clears stale error fields.

Audit fix #H/core (resume keeps stale error). start_extraction does
this on the fresh path; resume must do it too or the UI shows
'extraction failed' while extraction is actively running.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from chaoscypher_core.operations.importing.import_service import (
    _resume_extraction_job,
)
from chaoscypher_core.utils.id import generate_id


def _make_existing_job(
    source_id: str,
    job_id: str,
    *,
    detected_domain: str | None = None,
    forced_domain: str | None = None,
    completed_chunks: int = 0,
    total_chunks: int = 3,
) -> dict:
    """Return a minimal job dict mirroring what the adapter returns."""
    return {
        "id": job_id,
        "source_id": source_id,
        "database_name": "default",
        "detected_domain": detected_domain,
        "forced_domain": forced_domain,
        "completed_chunks": completed_chunks,
        "total_chunks": total_chunks,
    }


def _make_chunk(source_id: str, index: int = 0) -> dict:
    """Return a minimal chunk dict."""
    return {
        "id": generate_id(prefix="chk"),
        "source_id": source_id,
        "chunk_index": index,
        "content": "some text",
        "database_name": "default",
    }


class TestResumeExtractionClearsStaleError:
    """_resume_extraction_job must clear error_message and error_stage on entry."""

    def test_update_file_called_with_none_error_fields(self) -> None:
        """Verify update_file is called to clear both error fields."""
        source_id = generate_id(prefix="src")
        job_id = generate_id(prefix="job")
        chunk = _make_chunk(source_id)

        adapter = MagicMock()
        adapter.get_chunks_for_extraction.return_value = [chunk]

        registry = MagicMock()
        registry.get_domain.return_value = None

        existing_job = _make_existing_job(source_id, job_id)

        _resume_extraction_job(
            adapter=adapter,
            existing_job=existing_job,
            file_id=source_id,
            registry=registry,
            database_name="default",
        )

        adapter.update_file.assert_called_once_with(
            source_id=source_id,
            database_name="default",
            updates={
                "error_message": None,
                "error_stage": None,
            },
        )

    def test_update_file_called_before_chunks_fetched(self) -> None:
        """Error fields are cleared before chunk work begins (ordering check)."""
        source_id = generate_id(prefix="src")
        job_id = generate_id(prefix="job")
        chunk = _make_chunk(source_id)

        call_order: list[str] = []

        adapter = MagicMock()
        adapter.update_file.side_effect = lambda **_kw: call_order.append("update_file")
        adapter.get_chunks_for_extraction.side_effect = lambda **_kw: (
            call_order.append("get_chunks") or [chunk]
        )

        registry = MagicMock()
        registry.get_domain.return_value = None

        existing_job = _make_existing_job(source_id, job_id)

        _resume_extraction_job(
            adapter=adapter,
            existing_job=existing_job,
            file_id=source_id,
            registry=registry,
            database_name="default",
        )

        assert call_order == ["update_file", "get_chunks"], (
            f"error fields must be cleared before chunk fetching; actual order: {call_order}"
        )

    def test_return_value_contains_job_id_domain_and_chunks(self) -> None:
        """_resume_extraction_job returns (job_id, domain, chunks)."""
        source_id = generate_id(prefix="src")
        job_id = generate_id(prefix="job")
        chunks = [_make_chunk(source_id, i) for i in range(2)]

        adapter = MagicMock()
        adapter.get_chunks_for_extraction.return_value = chunks

        fake_domain = MagicMock()
        registry = MagicMock()
        registry.get_domain.return_value = fake_domain

        existing_job = _make_existing_job(
            source_id,
            job_id,
            detected_domain="technical",
        )

        returned_job_id, returned_domain, returned_chunks = _resume_extraction_job(
            adapter=adapter,
            existing_job=existing_job,
            file_id=source_id,
            registry=registry,
            database_name="default",
        )

        assert returned_job_id == job_id
        assert returned_domain is fake_domain
        assert returned_chunks == chunks
