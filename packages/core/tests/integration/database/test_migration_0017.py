# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Roundtrip test for migration 0017 — a source and an extraction job carry a list of packs.

The upgrade adds ``sources.forced_packs`` / ``extraction_packs`` and
``chunk_extraction_jobs.forced_packs`` / ``detected_packs`` (nullable JSON,
the model's shape) and backfills the source lists from the old single-name
columns by the rule the live code reads them with
(:func:`~chaoscypher_core.services.sources.engine.extraction.domains.packs.pack_columns`);
the downgrade drops the four columns and leaves the old ones.
"""

from __future__ import annotations

import importlib
import json
import sqlite3
from typing import TYPE_CHECKING

import chaoscypher_core.adapters.sqlite.models  # noqa: F401 — register metadata
from chaoscypher_core.adapters.sqlite.engine import evict_engine
from chaoscypher_core.adapters.sqlite.models import ChunkExtractionJob, SourceRow
from chaoscypher_core.database.migrations.runner import downgrade_to, upgrade_to
from chaoscypher_core.services.sources.engine.extraction.domain_resolver import source_packs
from chaoscypher_core.services.sources.engine.extraction.domains.packs import pack_columns

from ._schema import table_columns


if TYPE_CHECKING:
    from pathlib import Path

_MIGRATION = importlib.import_module(
    "chaoscypher_core.database.migrations.versions.0017_domain_packs"
)
COLUMNS: dict[str, tuple[str, ...]] = _MIGRATION.COLUMNS

_ROWS: dict[str, tuple[str | None, str | None]] = {
    # id: (forced_domain, extraction_domain)
    "literary": (None, "literary"),
    "forced": ("legal", "legal"),
    "generic": (None, "generic"),
    "forced_floor": ("generic", "generic"),
    "never_extracted": (None, None),
    "forced_not_extracted": ("medical", None),
    "auto_sentinel": ("__auto__", None),  # an MCP client stored the sentinel raw
    "auto_choice": ("auto", "news"),
}


def _seed(db: Path, source_id: str, forced: str | None, extracted: str | None) -> None:
    """Insert a ``sources`` row at 0016: every NOT NULL column without a default gets a filler."""
    conn = sqlite3.connect(str(db))
    try:
        info = conn.execute("PRAGMA table_info(sources)").fetchall()
        values: dict[str, object] = {}
        for _cid, name, col_type, notnull, default, _pk in info:
            if notnull and default is None:
                kind = str(col_type).upper()
                values[name] = 0 if ("INT" in kind or "BOOL" in kind or "FLOAT" in kind) else "x"
        values.update(
            {
                "id": source_id,
                "database_name": "default",
                "created_at": "2026-10-01T00:00:00",
                "forced_domain": forced,
                "extraction_domain": extracted,
            }
        )
        cols = ", ".join(values)
        marks = ", ".join("?" for _ in values)
        conn.execute(f"INSERT INTO sources ({cols}) VALUES ({marks})", tuple(values.values()))
        conn.commit()
    finally:
        conn.close()


def _lists(db: Path) -> dict[str, tuple[object, object]]:
    conn = sqlite3.connect(str(db))
    try:
        rows = conn.execute("SELECT id, forced_packs, extraction_packs FROM sources").fetchall()
    finally:
        conn.close()
    return {
        sid: (json.loads(f) if f is not None else None, json.loads(e) if e is not None else None)
        for sid, f, e in rows
    }


def test_the_columns_are_the_model_fields() -> None:
    """One list: every column the migration adds is a model field on its table."""
    assert set(COLUMNS["sources"]) <= set(SourceRow.model_fields)
    assert set(COLUMNS["chunk_extraction_jobs"]) <= set(ChunkExtractionJob.model_fields)


def test_0017_adds_the_columns_in_the_model_shape(tmp_path: Path) -> None:
    db = tmp_path / "app.db"
    sqlite3.connect(str(db)).close()
    try:
        upgrade_to(db, "0017")
        for table, names in COLUMNS.items():
            columns = table_columns(db, table)
            for name in names:
                assert columns[name].type == "JSON"
                assert not columns[name].notnull
                assert columns[name].default is None
            assert "forced_domain" in columns  # the old columns stay for one release
    finally:
        evict_engine(db)


def test_0017_backfills_the_source_lists_as_the_live_code_derives_them(tmp_path: Path) -> None:
    """``extraction_packs``: ``[name]``, ``[]`` for generic or none; ``forced_packs``: NULL for none."""
    db = tmp_path / "app.db"
    sqlite3.connect(str(db)).close()
    try:
        upgrade_to(db, "0016")
        for sid, (forced, extracted) in _ROWS.items():
            _seed(db, sid, forced, extracted)
        upgrade_to(db, "0017")
        lists = _lists(db)
    finally:
        evict_engine(db)
    assert lists == {
        "literary": (None, ["literary"]),
        "forced": (["legal"], ["legal"]),
        "generic": (None, []),
        "forced_floor": ([], []),
        "never_extracted": (None, []),
        "forced_not_extracted": (["medical"], []),
        "auto_sentinel": (None, []),  # detect, never a forced "__auto__" pack
        "auto_choice": (None, ["news"]),
    }
    for sid, (forced, extracted) in _ROWS.items():
        derived = pack_columns({"forced_domain": forced, "extraction_domain": extracted})
        assert lists[sid][0] == derived["forced_packs"], sid
        assert lists[sid][1] == (derived["extraction_packs"] or []), sid
        # the packs a backfilled row extracts with are the ones its old columns named
        backfilled = {"forced_packs": lists[sid][0], "extraction_packs": lists[sid][1]}
        old = {"forced_domain": forced, "extraction_domain": extracted}
        assert source_packs(backfilled) == source_packs(old), sid


def test_0017_downgrade_removes_the_lists(tmp_path: Path) -> None:
    db = tmp_path / "app.db"
    sqlite3.connect(str(db)).close()
    try:
        upgrade_to(db, "0017")
        downgrade_to(db, "0016")
        for table, names in COLUMNS.items():
            columns = table_columns(db, table)
            for name in names:
                assert name not in columns
            assert "forced_domain" in columns
        assert "extraction_domain" in table_columns(db, "sources")
    finally:
        evict_engine(db)
