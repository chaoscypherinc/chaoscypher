# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""``chaoscypher memory export --scope S OUT.ccx``: a scope's memories as a pack ``package load`` takes."""

from __future__ import annotations

import importlib
from collections.abc import AsyncGenerator
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from chaoscypher_core import Engine
from chaoscypher_core.services.package.importer import CcxImporter, ImportOptions
from chaoscypher_core.settings import EngineSettings
from chaoscypher_core.utils.id import generate_hex_id


MEMORIES = (
    ("Pierre Bezukhov moved to Lyon.", "alice", "work", "2026-10-20"),
    ("Boris Drubetskoy walked through the garden.", "bob", "home", "2026-10-01"),
)


def _engine(path: Path) -> Engine:
    """An engine with no embedder (vector search off) and no extraction model involved."""
    settings = EngineSettings()
    settings.search.enable_vector_search = False
    return Engine(str(path / "databases" / "test"), settings=settings)


async def _remember(engine: Engine, text: str, by: str, scope: str, valid_from: str) -> None:
    """A memory row as ``remember`` writes it, its text indexed and not extracted."""
    await engine.process_document(
        text,
        source_id=generate_hex_id(),
        filename="memory.txt",
        auto_confirm=True,
        source_type="memory",
        source_fields={"memory_by": by, "memory_scope": scope, "valid_from": valid_from},
        extract=False,
    )


@pytest.fixture
async def origin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncGenerator[Engine]:
    engine = _engine(tmp_path / "origin")
    for memory in MEMORIES:
        await _remember(engine, *memory)
    # the module, not the ``export`` command the package re-exports under the same name
    cmd = importlib.import_module("chaoscypher_cli.commands.memory.export")
    monkeypatch.setattr(cmd, "get_context", lambda **_kw: SimpleNamespace(_engine=engine))
    yield engine
    engine.close()


async def test_memory_export_writes_a_pack_the_importer_accepts(
    origin: Engine, tmp_path: Path
) -> None:
    from chaoscypher_cli.commands.memory import memory

    out = tmp_path / "out.ccx"
    result = CliRunner().invoke(memory, ["export", "--scope", "work", str(out)])
    assert result.exit_code == 0, result.output
    assert "1 memories" in result.output

    target = _engine(tmp_path / "target")
    try:
        importer = CcxImporter(
            graph_repository=target.graph_repository,
            sources_repository=target.storage_adapter,
            settings=target.settings,
        )
        options = ImportOptions(database_name=target.settings.current_database)
        stats = await importer.import_from_path(out, options)
        assert not stats.errors, stats.errors
        rows, _ = target.storage_adapter.list_sources(source_type="memory", page_size=10)
        assert [(r["memory_by"], r["memory_scope"], r["valid_from"]) for r in rows] == [
            ("alice", "work", "2026-10-20")
        ]
    finally:
        target.close()


async def test_a_scope_with_no_memories_is_refused(origin: Engine, tmp_path: Path) -> None:
    from chaoscypher_cli.commands.memory import memory

    out = tmp_path / "empty.ccx"
    result = CliRunner().invoke(memory, ["export", "--scope", "travel", str(out)])
    assert result.exit_code == 1
    assert "No memories in scope travel" in result.output
    assert not out.exists()


def test_memory_is_a_registered_top_level_group() -> None:
    from chaoscypher_cli.__main__ import LAZY_COMMANDS

    assert LAZY_COMMANDS["memory"][0] == "chaoscypher_cli.commands.memory:memory"
