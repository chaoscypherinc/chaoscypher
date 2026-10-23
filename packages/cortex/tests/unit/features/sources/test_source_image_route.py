# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Path safety of the source-image routes (``/sources/{id}/images[/{file}]``).

Two layers keep a request inside ``databases/<db>/images/<source_id>/``:
the token regexes, and a containment check anchored on the fixed images
root. Each layer is pinned on its own, so dropping either one fails here.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from chaoscypher_cortex.features.sources import api as sources_api


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    db_dir = tmp_path / "databases" / "default"
    (db_dir / "images" / "src_1").mkdir(parents=True)
    (db_dir / "images" / "src_1" / "page_001.png").write_bytes(b"\x89PNG")
    # Files a traversal would reach: the database itself, and a .png
    # beside it that satisfies the filename regex.
    (db_dir / "app.db").write_bytes(b"SQLite format 3\x00")
    (db_dir / "leak.png").write_bytes(b"not an image")
    return tmp_path


def _settings(data_dir: Path) -> Any:
    return SimpleNamespace(data_dir=data_dir, current_database="default")


async def _get(data_dir: Path, source_id: str, filename: str) -> Any:
    return await sources_api.get_source_image(
        "admin", source_id, filename, settings=_settings(data_dir)
    )


@pytest.mark.asyncio
async def test_serves_an_image_inside_the_source_directory(data_dir: Path) -> None:
    response = await _get(data_dir, "src_1", "page_001.png")
    assert (
        Path(response.path)
        == (data_dir / "databases" / "default" / "images" / "src_1" / "page_001.png").resolve()
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source_id", "filename"),
    [
        ("..", "app.db"),
        ("..", "leak.png"),
        ("src_1", "../../app.db"),
        ("src_1", "page_001.PNG.db"),
        ("src.1", "page_001.png"),
        ("a" * 65, "page_001.png"),
    ],
)
async def test_validators_reject_unsafe_tokens(
    data_dir: Path, source_id: str, filename: str
) -> None:
    with pytest.raises(HTTPException) as exc_info:
        await _get(data_dir, source_id, filename)
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize(("source_id", "filename"), [("..", "leak.png"), (".", "page_001.png")])
async def test_containment_holds_without_the_validators(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch, source_id: str, filename: str
) -> None:
    """The defence-in-depth check alone stops a ``..`` source id.

    A check anchored on a directory built from ``source_id`` cannot: with
    ``source_id=".."`` that directory is the database dir itself.
    """
    monkeypatch.setattr(sources_api, "_validate_source_id", lambda _sid: None)
    monkeypatch.setattr(sources_api, "_validate_image_filename", lambda _name: None)
    with pytest.raises(HTTPException) as exc_info:
        await _get(data_dir, source_id, filename)
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_list_images_rejects_a_traversal_source_id(data_dir: Path) -> None:
    with pytest.raises(HTTPException) as exc_info:
        await sources_api.list_source_images("admin", "..", settings=_settings(data_dir))
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_list_images_lists_the_source_directory(data_dir: Path) -> None:
    images = await sources_api.list_source_images("admin", "src_1", settings=_settings(data_dir))
    assert [i.filename for i in images] == ["page_001.png"]
    assert images[0].url == "/sources/src_1/images/page_001.png"
