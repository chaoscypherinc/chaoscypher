# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The cache endpoints on the real Cortex app with a temporary data directory.

The stores live where the autouse ``llm_cache_in_tmp`` fixture points them;
``settings.yaml`` lives in a per-test data directory, so a PATCH is read
back from the file the next provider build loads.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chaoscypher_core.app_config import (
    get_config_manager,
    get_settings,
    reload_settings,
    set_settings,
)
from chaoscypher_core.app_config.engine_factory import build_engine_settings
from chaoscypher_core.cache import CacheEntry, SqliteCacheStore, encode_value, wiring
from chaoscypher_core.settings import CacheStoreSettings
from chaoscypher_cortex.shared.auth.dependencies import get_current_username


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A fresh data directory the settings singletons load from, restored afterwards."""
    original = get_settings()
    directory = tmp_path / "data"
    directory.mkdir()
    monkeypatch.setenv("CHAOSCYPHER_DATA_DIR", str(directory))
    get_config_manager.cache_clear()
    reload_settings()
    try:
        yield directory
    finally:
        get_config_manager.cache_clear()
        set_settings(original)


@pytest.fixture
def notifications(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Worker notifications the PATCH publishes (recorded instead of sent to Valkey)."""
    sent: list[str] = []

    async def record(reason: str) -> None:
        """Record ``reason``."""
        sent.append(reason)

    from chaoscypher_cortex.shared import worker_notify

    monkeypatch.setattr(worker_notify, "publish_settings_change", record)
    return sent


@pytest.fixture
def app(data_dir: Path, notifications: list[str]) -> FastAPI:
    """The real Cortex app, auth bypassed as nginx would."""
    del data_dir, notifications
    from chaoscypher_cortex.app_factory import create_app

    application = create_app()
    application.dependency_overrides[get_current_username] = lambda: "test-user"
    return application


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    """A client on an allowed host."""
    return TestClient(app, base_url="http://localhost")


def _cache(body: dict[str, Any], name: str) -> dict[str, Any]:
    """The overview's entry for cache ``name``."""
    (cache,) = [c for c in body["caches"] if c["name"] == name]
    return cache


def _write_one_entry(name: wiring.CacheName) -> None:
    """One entry, one hit and one miss in cache ``name``'s store file, written directly."""
    path = wiring.store_path(build_engine_settings(get_settings()), name)
    store = SqliteCacheStore(path, max_bytes=CacheStoreSettings().max_bytes)
    value = encode_value({"content": "Paris"})
    store.put(
        name,
        "key-1",
        CacheEntry(
            value=value,
            model_build="ollama:m:sha256:abc",
            call_site="chat",
            seeded=True,
            latency_ms=1200,
            bytes=len(value),
        ),
    )
    store.record(name, "chat", hit=True, latency_saved_ms=1200)
    store.record(name, "chat", hit=False)
    store.close()


def test_get_shows_both_caches_enabled_and_empty(
    client: TestClient, llm_cache_in_tmp: Path
) -> None:
    """A fresh install: completions then embeddings, on, nothing stored, no history."""
    response = client.get("/api/v1/cache")

    assert response.status_code == 200
    body = response.json()
    assert body["dir"] == str(llm_cache_in_tmp)
    assert [c["name"] for c in body["caches"]] == ["completions", "embeddings"]
    defaults = CacheStoreSettings()
    for cache in body["caches"]:
        assert cache["enabled"] is True
        assert cache["require_hit"] is False
        assert cache["max_bytes"] == defaults.max_bytes
        for field in ("entries", "bytes", "hits", "misses", "bypasses", "latency_saved_ms"):
            assert cache[field] == 0, field
        assert cache["hit_rate"] == 0.0
        assert cache["by_call_site"] == {}


def test_patch_turns_a_cache_off_in_settings_yaml_and_for_the_next_provider_build(
    client: TestClient, data_dir: Path, notifications: list[str]
) -> None:
    """The flag lands in settings.yaml, GET shows it, and a new embedding provider is unwrapped."""
    response = client.patch(
        "/api/v1/cache",
        json={"completions": {"enabled": False}, "embeddings": {"enabled": False}},
    )

    assert response.status_code == 200
    assert _cache(response.json(), "completions")["enabled"] is False
    after = client.get("/api/v1/cache").json()
    assert _cache(after, "completions")["enabled"] is False
    assert _cache(after, "embeddings")["enabled"] is False
    on_disk = yaml.safe_load((data_dir / "settings.yaml").read_text(encoding="utf-8"))
    assert on_disk["cache"]["completions"]["enabled"] is False
    assert notifications == ["v1:cache_settings_updated"]

    class _Provider:
        """The smallest embedding provider the wrapper looks at."""

        provider_type = "ollama"
        model_name = "m"

    provider: Any = _Provider()
    assert wiring.wrap_embedding(provider, build_engine_settings(get_settings())) is provider


def test_patch_changes_a_cap_and_keeps_the_other_fields(client: TestClient, data_dir: Path) -> None:
    """Only the given field changes; the cap shows in GET and on disk."""
    cap = 64 * 1024**2
    response = client.patch("/api/v1/cache", json={"embeddings": {"max_bytes": cap}})

    assert response.status_code == 200
    embeddings = _cache(client.get("/api/v1/cache").json(), "embeddings")
    assert embeddings["max_bytes"] == cap
    assert embeddings["enabled"] is True
    on_disk = yaml.safe_load((data_dir / "settings.yaml").read_text(encoding="utf-8"))
    assert on_disk["cache"]["embeddings"]["max_bytes"] == cap


@pytest.mark.parametrize(
    "body",
    [
        {"completions": {"max_bytes": 1}},
        {"completions": {"require_hit": True}},
        {"bench": {"enabled": False}},
    ],
)
def test_patch_rejects_what_the_settings_section_would(
    client: TestClient, data_dir: Path, body: dict[str, Any]
) -> None:
    """A cap under the settings minimum or an unknown field is a 422 envelope; nothing is written."""
    response = client.patch("/api/v1/cache", json=body)

    assert response.status_code == 422
    assert response.json()["error"] == "VALIDATION_FAILED"
    settings_file = data_dir / "settings.yaml"
    if settings_file.exists():
        assert "cache" not in (yaml.safe_load(settings_file.read_text(encoding="utf-8")) or {})


def test_a_stored_entry_shows_in_the_overview(client: TestClient) -> None:
    """Entries, bytes, hits, misses, time saved and the call-site split come from the store."""
    _write_one_entry("completions")

    completions = _cache(client.get("/api/v1/cache").json(), "completions")

    assert completions["entries"] == 1
    assert completions["bytes"] > 0
    assert completions["hits"] == 1
    assert completions["misses"] == 1
    assert completions["hit_rate"] == 0.5
    assert completions["latency_saved_ms"] == 1200
    assert completions["by_call_site"] == {
        "chat": {"hits": 1, "misses": 1, "bypasses": 0, "latency_saved_ms": 1200}
    }
    assert _cache(client.get("/api/v1/cache").json(), "embeddings")["entries"] == 0


def test_clear_removes_the_entries_and_keeps_the_history(client: TestClient) -> None:
    """Clear answers how many went; the overview shows none left and the hits still counted."""
    _write_one_entry("completions")

    response = client.post("/api/v1/cache/completions/clear")

    assert response.status_code == 200
    assert response.json() == {"name": "completions", "entries_removed": 1}
    completions = _cache(client.get("/api/v1/cache").json(), "completions")
    assert completions["entries"] == 0
    assert completions["bytes"] == 0
    assert completions["hits"] == 1
    assert completions["latency_saved_ms"] == 1200


def test_clearing_an_unknown_cache_is_a_404_envelope(client: TestClient) -> None:
    """Only the product caches can be cleared; anything else is the not-found envelope."""
    response = client.post("/api/v1/cache/bench/clear")

    assert response.status_code == 404
    body = response.json()
    assert body["error"] == "NOT_FOUND"
    assert "bench" in body["message"]


def test_the_responses_match_their_openapi_schemas(app: FastAPI, client: TestClient) -> None:
    """Each endpoint names its model in the schema, and the JSON carries exactly its fields."""
    _write_one_entry("embeddings")
    schema = app.openapi()
    components = schema["components"]["schemas"]

    def response_ref(path: str, method: str) -> str:
        """The component name of ``method path``'s 200 response."""
        ref = schema["paths"][path][method]["responses"]["200"]["content"]["application/json"][
            "schema"
        ]["$ref"]
        return str(ref).rsplit("/", 1)[-1]

    assert response_ref("/api/v1/cache", "get") == "CacheOverviewResponse"
    assert response_ref("/api/v1/cache", "patch") == "CacheOverviewResponse"
    assert response_ref("/api/v1/cache/{name}/clear", "post") == "CacheClearResponse"
    request = schema["paths"]["/api/v1/cache"]["patch"]["requestBody"]["content"][
        "application/json"
    ]["schema"]["$ref"]
    assert request.endswith("/CacheUpdateRequest")
    minimum = CacheStoreSettings.model_fields["max_bytes"].metadata[0].ge
    max_bytes = components["CacheStoreUpdate"]["properties"]["max_bytes"]["anyOf"][0]
    assert max_bytes["minimum"] == minimum

    overview = client.get("/api/v1/cache").json()
    assert set(overview) == set(components["CacheOverviewResponse"]["properties"])
    stats_fields = set(components["CacheStatsResponse"]["properties"])
    assert set(components["CacheStatsResponse"]["required"]) == stats_fields
    for cache in overview["caches"]:
        assert set(cache) == stats_fields
    site_fields = set(components["CallSiteStatsResponse"]["properties"])
    assert set(_cache(overview, "embeddings")["by_call_site"]["chat"]) == site_fields
    cleared = client.post("/api/v1/cache/embeddings/clear").json()
    assert set(cleared) == set(components["CacheClearResponse"]["properties"])
