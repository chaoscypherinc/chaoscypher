# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""``source add`` gives the document its tags and summary, on a new document and on a re-add.

``CLISourceProcessingService.upload_file`` and ``upload_url`` apply them through
Core's ``apply_source_labels`` on a real adapter; the URL fetch is faked the way
``test_upload_url_uses_fetch_page.py`` fakes it.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlmodel import SQLModel

from chaoscypher_cli.sources.service import CLISourceProcessingService
from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import evict_engine, get_engine
from chaoscypher_core.services.sources.tags import TagRuleError, source_tag_names
from chaoscypher_core.services.sources.web import FetchedPage
from chaoscypher_core.settings import SourceProcessingSettings


URL = "https://example.com/page"
BODY = "A page body that is comfortably longer than the minimum length."


@pytest.fixture
def service(mock_cli_context: MagicMock, tmp_path: Path) -> Iterator[CLISourceProcessingService]:
    db_path = tmp_path / "labels.db"
    SQLModel.metadata.create_all(get_engine(str(db_path)), checkfirst=True)
    adapter = SqliteAdapter(str(db_path), database_name=mock_cli_context.database_name)
    adapter.connect()
    mock_cli_context.storage_adapter = adapter
    mock_cli_context.settings.source_processing = SourceProcessingSettings()
    built = CLISourceProcessingService(mock_cli_context)
    yield built
    built.close()
    adapter.disconnect()
    evict_engine(db_path)


def _upload_file(service: CLISourceProcessingService, doc: Path, **kwargs: object) -> str:
    result = service.upload_file(doc, **kwargs)  # type: ignore[arg-type]
    return result["id"] if isinstance(result, dict) else result


def _upload_url(service: CLISourceProcessingService, **kwargs: object) -> str:
    page = FetchedPage(
        url=URL,
        final_url=URL,
        content_type="text/html",
        is_binary=False,
        content=BODY,
        data=None,
        title="A Page",
    )
    with patch("chaoscypher_cli.sources.service.fetch_page", AsyncMock(return_value=page)):
        result, _title = service.upload_url(URL, **kwargs)  # type: ignore[arg-type]
    return result["id"] if isinstance(result, dict) else result


def _summary(service: CLISourceProcessingService, source_id: str) -> str | None:
    row = service.ctx.storage_adapter.get_source(source_id, service.ctx.database_name)
    assert row is not None
    return row["summary"]


@pytest.fixture
def doc(tmp_path: Path) -> Path:
    path = tmp_path / "doc-a.txt"
    path.write_text("A document about alpha, long enough to read.\n")
    return path


def test_upload_file_applies_tags_and_summary(
    service: CLISourceProcessingService, doc: Path
) -> None:
    source_id = _upload_file(service, doc, tags=["Alpha", "beta"], summary="A short note.")

    assert source_tag_names(service.ctx.storage_adapter, source_id) == ["alpha", "beta"]
    assert _summary(service, source_id) == "A short note."


def test_upload_file_readd_adds_tags_to_the_existing_document(
    service: CLISourceProcessingService, doc: Path
) -> None:
    first = _upload_file(service, doc, tags=["alpha"])
    again = service.upload_file(doc, skip_duplicates=True, tags=["gamma"], summary="Later.")

    assert isinstance(again, dict) and again["id"] == first
    assert again["tags"] == ["alpha", "gamma"]
    assert source_tag_names(service.ctx.storage_adapter, first) == ["alpha", "gamma"]
    assert _summary(service, first) == "Later."


def test_upload_file_refuses_a_bad_tag_before_any_row(
    service: CLISourceProcessingService, doc: Path
) -> None:
    with pytest.raises(TagRuleError):
        _upload_file(service, doc, tags=["Work Docs"])

    assert service.ctx.storage_adapter.count_sources(database_name=service.ctx.database_name) == 0


def test_upload_url_applies_tags_and_summary(service: CLISourceProcessingService) -> None:
    source_id = _upload_url(service, tags=["Alpha", "beta"], summary="A short note.")

    assert source_tag_names(service.ctx.storage_adapter, source_id) == ["alpha", "beta"]
    assert _summary(service, source_id) == "A short note."


def test_upload_url_readd_adds_tags_to_the_existing_document(
    service: CLISourceProcessingService,
) -> None:
    first = _upload_url(service, tags=["alpha"])
    again = _upload_url(service, skip_duplicates=True, tags=["gamma"])

    assert again == first
    assert source_tag_names(service.ctx.storage_adapter, first) == ["alpha", "gamma"]


def test_a_labels_failure_after_the_row_exists_marks_it_failed(
    service: CLISourceProcessingService, doc: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chaoscypher_core.exceptions import ConflictError

    def lost_race(*_args: object, **_kwargs: object) -> list[str]:
        msg = 'another writer created tag "alpha" at the same time; try again'
        raise ConflictError(msg)

    monkeypatch.setattr("chaoscypher_core.services.sources.labels.apply_source_labels", lost_race)

    with pytest.raises(ConflictError):
        _upload_file(service, doc, tags=["alpha"])

    rows, total = service.ctx.storage_adapter.list_sources()
    assert total == 1
    row = service.ctx.storage_adapter.get_source(rows[0]["id"], service.ctx.database_name)
    assert row is not None
    assert row["status"] == "error"
    assert row["error_stage"] == "indexing"
