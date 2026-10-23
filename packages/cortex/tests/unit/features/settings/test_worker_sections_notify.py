# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""A settings write reaches the running workers when it changes a section they read.

The Neuron worker registers the chat handler with the settings it loaded; a
``graphrag`` change (the chat-context switch, ``include_unevidenced_edges``)
reached it only after a restart because the settings PATCH notified the
workers for ``llm``/``search``/``embedding`` alone. One list,
``worker_notify.WORKER_SETTINGS_SECTIONS``, now gates the settings PATCH and
the Cache section alike.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from chaoscypher_core.app_config import (
    get_config_manager,
    get_settings,
    reload_settings,
    set_settings,
)
from chaoscypher_cortex.shared import worker_notify
from chaoscypher_cortex.shared.auth.dependencies import get_current_username


@pytest.fixture
def notifications(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    sent: list[str] = []

    async def record(reason: str) -> None:
        sent.append(reason)

    monkeypatch.setattr(worker_notify, "publish_settings_change", record)
    return sent


@pytest.fixture
def client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, notifications: list[str]
) -> Iterator[TestClient]:
    del notifications
    original = get_settings()
    directory = tmp_path / "data"
    directory.mkdir()
    monkeypatch.setenv("CHAOSCYPHER_DATA_DIR", str(directory))
    get_config_manager.cache_clear()
    reload_settings()
    from chaoscypher_cortex.app_factory import create_app

    app = create_app()
    app.dependency_overrides[get_current_username] = lambda: "test-user"
    try:
        yield TestClient(app, base_url="http://localhost")
    finally:
        get_config_manager.cache_clear()
        set_settings(original)


def test_the_worker_sections_include_graphrag_and_the_cache() -> None:
    assert {
        "llm",
        "search",
        "embedding",
        "cache",
        "graphrag",
    } <= worker_notify.WORKER_SETTINGS_SECTIONS


@pytest.mark.asyncio
async def test_notify_publishes_only_for_a_worker_section(notifications: list[str]) -> None:
    assert await worker_notify.notify_workers_of({"graphrag"}) is True
    assert await worker_notify.notify_workers_of({"dark_mode", "security"}) is False
    assert await worker_notify.notify_workers_of({"cache"}, "v1:cache_settings_updated") is True
    assert notifications == ["v1:settings_updated:graphrag", "v1:cache_settings_updated"]


def test_a_graphrag_patch_persists_and_publishes(
    client: TestClient, notifications: list[str]
) -> None:
    response = client.patch(
        "/api/v1/settings", json={"graphrag": {"include_unevidenced_edges": True}}
    )

    assert response.status_code == 200
    assert response.json()["settings"]["graphrag"]["include_unevidenced_edges"] is True
    assert notifications == ["v1:settings_updated:graphrag"]
    # persisted: a fresh read of the settings file sees it (what a reloading worker loads)
    get_config_manager().invalidate_cache()
    assert get_config_manager().get_settings().graphrag.include_unevidenced_edges is True


def test_a_patch_the_workers_do_not_read_publishes_nothing(
    client: TestClient, notifications: list[str]
) -> None:
    response = client.patch("/api/v1/settings", json={"dark_mode": False})

    assert response.status_code == 200
    assert notifications == []
