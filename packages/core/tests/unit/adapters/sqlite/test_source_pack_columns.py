# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""A source and an extraction job carry a list of packs; the old single-name columns follow.

Every write chokepoint of the SQLite adapter (``upload_source``,
``create_source``, ``update_file``, ``update_source``,
``update_source_columns``, ``create_extraction_job``,
``complete_extraction``) keeps the JSON list columns and the old columns in
step through ``domains.packs.pack_columns``: ``forced_domain`` /
``detected_domain`` hold the first pack or ``generic``, ``extraction_domain``
the packs' label. ``source_packs`` reads the result back.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.models import SourceStatus
from chaoscypher_core.services.sources.engine.extraction.domain_resolver import (
    source_domain_name,
    source_packs,
)
from chaoscypher_core.services.sources.engine.extraction.domains.packs import job_packs


@pytest.fixture
def adapter(tmp_path: Path) -> Generator[SqliteAdapter]:
    db_path = tmp_path / "test.db"
    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)
    adp = SqliteAdapter(str(db_path), database_name="default")
    adp.connect()
    yield adp
    adp.disconnect()


def _upload(adapter: SqliteAdapter, tmp_path: Path, **kwargs: Any) -> dict[str, Any]:
    adapter.upload_source(
        source_id="src1",
        database_name="default",
        filename="x.txt",
        file_content=b"x",
        staging_dir=str(tmp_path),
        **kwargs,
    )
    row = adapter.get_source("src1", database_name="default")
    assert row is not None
    return row


@pytest.mark.parametrize(
    ("kwargs", "forced_packs", "forced_domain"),
    [
        ({"forced_packs": ["literary", "historical"]}, ["literary", "historical"], "literary"),
        ({"forced_packs": []}, [], "generic"),  # the floor alone, chosen
        ({"forced_domain": "legal"}, ["legal"], "legal"),  # the old one-name caller
        ({"forced_domain": "literary+historical"}, ["literary", "historical"], "literary"),
        ({}, None, None),  # detect
    ],
)
def test_an_upload_writes_the_list_and_the_first(
    adapter: SqliteAdapter,
    tmp_path: Path,
    kwargs: dict[str, Any],
    forced_packs: list[str] | None,
    forced_domain: str | None,
) -> None:
    row = _upload(adapter, tmp_path, **kwargs)
    assert row["forced_packs"] == forced_packs
    assert row["forced_domain"] == forced_domain
    assert source_packs(row) == tuple(forced_packs or ())


def test_an_old_name_update_keeps_the_list_in_step(adapter: SqliteAdapter, tmp_path: Path) -> None:
    """``update_file({'forced_domain': ...})`` (an older caller) writes the list too."""
    _upload(adapter, tmp_path, forced_packs=["legal"])
    adapter.update_file("src1", "default", {"forced_domain": "medical"})
    row = adapter.get_source("src1", database_name="default")
    assert row is not None
    assert (row["forced_packs"], row["forced_domain"]) == (["medical"], "medical")

    adapter.update_file("src1", "default", {"forced_packs": None})
    row = adapter.get_source("src1", database_name="default")
    assert row is not None
    assert (row["forced_packs"], row["forced_domain"]) == (None, None)

    adapter.update_source_columns(
        source_id="src1", database_name="default", updates={"forced_packs": ["news", "legal"]}
    )
    adapter.update_source("src1", {"extraction_packs": ["news"]})
    row = adapter.get_source("src1", database_name="default")
    assert row is not None
    assert (row["forced_packs"], row["forced_domain"]) == (["news", "legal"], "news")
    assert (row["extraction_packs"], row["extraction_domain"]) == (["news"], "news")


def test_create_source_derives_the_list(adapter: SqliteAdapter) -> None:
    adapter.create_source(
        {
            "id": "src2",
            "database_name": "default",
            "filename": "y.txt",
            "filepath": "/tmp/y.txt",
            "status": SourceStatus.PENDING,
            "forced_domain": "historical+literary",  # the MCP index-only path's label
        }
    )
    row = adapter.get_source("src2", database_name="default")
    assert row is not None
    assert row["forced_packs"] == ["historical", "literary"]
    assert row["forced_domain"] == "historical"


@pytest.mark.parametrize(
    ("finalize", "packs", "label", "auto"),
    [
        (
            {"detected_packs": ["literary", "historical"]},
            ["literary", "historical"],  # the stored (score) order
            "historical+literary",  # the label sorts
            True,
        ),
        ({"forced_packs": [], "detected_packs": ["literary"]}, [], "generic", False),
        ({"forced_packs": ["legal"], "detected_packs": ["news"]}, ["legal"], "legal", False),
        # a finalize written before the lists: the one-name columns
        ({"detected_domain": "technical"}, ["technical"], "technical", True),
        ({"forced_domain": "generic", "detected_domain": "technical"}, [], "generic", False),
    ],
)
def test_complete_extraction_records_the_packs_and_their_label(
    adapter: SqliteAdapter,
    tmp_path: Path,
    finalize: dict[str, Any],
    packs: list[str],
    label: str,
    auto: bool,
) -> None:
    """``extraction_packs`` is the forced packs, else the detected; ``extraction_domain`` their label."""
    _upload(adapter, tmp_path)
    adapter.update_file("src1", "default", {"status": SourceStatus.EXTRACTING})
    adapter.complete_extraction(source_id="src1", entities=[], relationships=[], **finalize)
    row = adapter.get_source("src1", database_name="default")
    assert row is not None
    assert row["extraction_packs"] == packs
    assert row["extraction_domain"] == label
    assert row["extraction_domain_auto"] is auto
    assert source_packs(row) == tuple(packs)


def test_a_job_carries_its_packs(adapter: SqliteAdapter, tmp_path: Path) -> None:
    _upload(adapter, tmp_path)
    job = adapter.create_extraction_job(
        job_id="job1",
        source_id="src1",
        database_name="default",
        forced_packs=None,
        detected_packs=["literary", "historical"],
    )
    assert job["forced_packs"] is None
    assert job["detected_packs"] == ["literary", "historical"]
    assert job["detected_domain"] == "literary"  # the first, for one release
    assert job["forced_domain"] is None
    stored = adapter.get_extraction_job("job1")
    assert stored is not None
    assert job_packs(stored) == ("literary", "historical")

    old = adapter.create_extraction_job(
        job_id="job2", source_id="src1", database_name="default", detected_domain="news"
    )
    assert old["detected_packs"] == ["news"]  # an old caller's one name, the list derived


def test_the_one_name_shim_reads_the_first_pack(adapter: SqliteAdapter, tmp_path: Path) -> None:
    row = _upload(adapter, tmp_path, forced_packs=["literary", "historical"])
    assert source_domain_name(row) == "literary"  # the first in the stored order
    assert source_domain_name({"extraction_packs": [], "forced_packs": None}) is None
    assert source_domain_name({"extraction_domain": "generic"}) == "generic"
