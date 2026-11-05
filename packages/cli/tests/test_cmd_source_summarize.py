# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""``chaoscypher source summarize [--missing]`` and the CLI's import-time summary.

The command runs on a real adapter behind a patched ``get_context``; the model
is a fake provider, standing in for ``summary_llm_provider``, whose ``chat``
replies with a fixed line. ``index_file`` is driven the way
``test_source_add_labels.py`` drives ``upload_file``.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import yaml
from click.testing import CliRunner
from sqlmodel import SQLModel

from chaoscypher_cli.commands.source.summarize import summarize
from chaoscypher_cli.sources.service import CLISourceProcessingService
from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import evict_engine, get_engine
from chaoscypher_core.adapters.sqlite.models import SourceRow
from chaoscypher_core.settings import EngineSettings, LLMSettings


DB = "default"
SUMMARY_PROVIDER = "chaoscypher_core.adapters.llm.summary.summary_llm_provider"
WRITTEN = "A model-written line."
NO_MODEL = (
    'Error: no language model is configured, so no summaries can be written. Run "chaoscypher '
    'setup" or set llm.chat_provider.'
)


class FakeProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> SimpleNamespace:
        self.calls += 1
        return SimpleNamespace(content=WRITTEN)


def _settings(data_dir: Path, *, configured: bool) -> EngineSettings:
    if configured:
        (data_dir / "settings.yaml").write_text(
            yaml.safe_dump({"setup_completed": True}), encoding="utf-8"
        )
    settings = EngineSettings(llm=LLMSettings(chat_provider="ollama"), current_database=DB)
    settings.paths.data_dir = str(data_dir)
    return settings


@pytest.fixture
def adapter(tmp_path: Path) -> Iterator[SqliteAdapter]:
    db_path = tmp_path / "summaries.db"
    SQLModel.metadata.create_all(get_engine(str(db_path)), checkfirst=True)
    built = SqliteAdapter(str(db_path), database_name=DB)
    built.connect()
    yield built
    built.disconnect()
    evict_engine(db_path)


def _add(adapter: SqliteAdapter, source_id: str, *, summary: str | None = None) -> None:
    session = adapter.session
    assert session is not None
    session.add(
        SourceRow(
            id=source_id,
            database_name=DB,
            filename=f"{source_id}.txt",
            filepath="",
            source_type="text",
            status="indexed",
            full_text=f"The text of {source_id}.",
            summary=summary,
            chunk_count=1,
        )
    )
    session.commit()


def _summary(adapter: SqliteAdapter, source_id: str) -> str | None:
    row = adapter.get_source(source_id, DB)
    assert row is not None
    return row["summary"]


def _run(adapter: SqliteAdapter, settings: EngineSettings, provider: Any, *args: str) -> Any:
    ctx = MagicMock()
    ctx.storage_adapter = adapter
    ctx.database_name = DB
    ctx.settings = settings
    ctx.llm_provider = None  # the summary builds its own provider (no client retries)
    with (
        patch("chaoscypher_cli.commands.source.summarize.get_context", return_value=ctx),
        patch(SUMMARY_PROVIDER, return_value=provider),
    ):
        return CliRunner().invoke(summarize, list(args))


@pytest.fixture
def two_documents(adapter: SqliteAdapter) -> SqliteAdapter:
    _add(adapter, "doc-a", summary="Typed earlier.")
    _add(adapter, "doc-b")
    return adapter


def test_missing_writes_only_for_documents_without_one(
    two_documents: SqliteAdapter, isolated_settings: Path
) -> None:
    provider = FakeProvider()
    result = _run(
        two_documents, _settings(isolated_settings, configured=True), provider, "--missing"
    )

    assert result.exit_code == 0, result.output
    assert "Summaries: 1 written, 0 failed, 0 skipped." in result.output
    assert provider.calls == 1
    assert _summary(two_documents, "doc-a") == "Typed earlier."
    assert _summary(two_documents, "doc-b") == WRITTEN


def test_without_missing_rewrites_every_summary(
    two_documents: SqliteAdapter, isolated_settings: Path
) -> None:
    provider = FakeProvider()
    result = _run(two_documents, _settings(isolated_settings, configured=True), provider)

    assert result.exit_code == 0, result.output
    assert "Summaries: 2 written, 0 failed, 0 skipped." in result.output
    assert _summary(two_documents, "doc-a") == WRITTEN
    assert _summary(two_documents, "doc-b") == WRITTEN


def test_no_model_configured_is_refused(
    two_documents: SqliteAdapter, isolated_settings: Path
) -> None:
    provider = FakeProvider()
    result = _run(two_documents, _settings(isolated_settings, configured=False), provider)

    assert result.exit_code == 1
    assert " ".join(result.output.split()) == NO_MODEL
    assert provider.calls == 0
    assert _summary(two_documents, "doc-b") is None


def test_index_file_writes_a_summary(
    mock_cli_context: MagicMock, adapter: SqliteAdapter, isolated_settings: Path, tmp_path: Path
) -> None:
    mock_cli_context.storage_adapter = adapter
    mock_cli_context.database_name = DB
    mock_cli_context.settings = _settings(isolated_settings, configured=True)
    mock_cli_context.embedding_service = None
    provider = FakeProvider()
    mock_cli_context.llm_provider = None  # the summary builds its own provider
    doc = tmp_path / "doc-a.txt"
    doc.write_text("A document about falcons, long enough to read.\n")
    service = CLISourceProcessingService(mock_cli_context)
    try:
        upload = service.upload_file(doc)
        source_id = upload["id"] if isinstance(upload, dict) else upload
        with patch(SUMMARY_PROVIDER, return_value=provider):
            service.index_file(source_id, skip_embeddings=True, enable_vision=False)
    finally:
        service.close()

    row = adapter.get_source(source_id, DB)
    assert row is not None
    assert row["status"] == "indexed"
    assert row["summary"] == WRITTEN
    assert provider.calls == 1
