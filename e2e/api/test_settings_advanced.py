# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""E2E tests for advanced settings endpoints.

Covers presets, embedding models, cloud models, ollama verify, TLS.
"""

import httpx


class TestPresets:
    """Test VRAM preset endpoints."""

    def test_list_presets(self, client: httpx.Client) -> None:
        """Listing presets returns available VRAM presets."""
        resp = client.get("/api/v1/settings/presets")
        assert resp.status_code == 200
        data = resp.json()
        assert "presets" in data

    def test_get_nonexistent_preset(self, client: httpx.Client) -> None:
        """Getting a nonexistent preset returns 404."""
        resp = client.get("/api/v1/settings/presets/nonexistent-preset-xyz")
        assert resp.status_code == 404


class TestEmbeddingModels:
    """Test embedding model endpoints."""

    def test_list_embedding_models(self, client: httpx.Client) -> None:
        """Listing embedding models returns curated + cloud options."""
        resp = client.get("/api/v1/settings/embedding/models")
        assert resp.status_code == 200
        data = resp.json()
        # EmbeddingModelsResponse always serializes both keys, populated
        # from the static registry — pin both, non-empty.
        assert "curated" in data
        assert "cloud" in data
        assert isinstance(data["curated"], list)
        assert data["curated"]
        assert isinstance(data["cloud"], dict)
        assert data["cloud"]
        assert all("name" in m and "dimensions" in m for m in data["curated"])

    def test_list_local_embedding_models(self, client: httpx.Client) -> None:
        """Listing local embedding models returns list."""
        resp = client.get("/api/v1/settings/embedding/local/models")
        assert resp.status_code == 200
        data = resp.json()
        assert "models" in data


class TestCloudModels:
    """Test cloud model listing endpoints."""

    def test_list_cloud_models(self, client: httpx.Client) -> None:
        """Listing cloud models returns all providers."""
        resp = client.get("/api/v1/settings/cloudmodels")
        assert resp.status_code == 200
        data = resp.json()
        # CloudModelsResponse: {"providers": {name: {display_name, models}}},
        # always non-empty (packaged model registry data).
        assert "providers" in data
        providers = data["providers"]
        assert isinstance(providers, dict)
        assert providers
        for info in providers.values():
            assert "display_name" in info
            assert isinstance(info["models"], list)

    def test_nonexistent_provider(self, client: httpx.Client) -> None:
        """Nonexistent cloud provider returns 404."""
        resp = client.get("/api/v1/settings/cloudmodels/nonexistent-provider")
        assert resp.status_code == 404
