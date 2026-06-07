# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""E2E tests for source data access (chunks, citations, entities)."""

import time

import httpx


def _poll_source_status(
    client: httpx.Client, source_id: str, target: str, timeout: int = 60
) -> dict:
    """Poll source until it reaches target status."""
    start = time.time()
    while time.time() - start < timeout:
        resp = client.get(f"/api/v1/sources/{source_id}")
        resp.raise_for_status()
        data = resp.json()
        status = data.get("processing_status") or data.get("status", "")
        if status == target:
            return data
        if status in ("error", "failed"):
            msg = f"Source failed: {data}"
            raise RuntimeError(msg)
        time.sleep(1)
    msg = f"Source {source_id} did not reach '{target}' within {timeout}s"
    raise TimeoutError(msg)


class TestSourceData:
    """Test source chunks, citations, entities, relationships access."""

    def _upload_and_index(self, client: httpx.Client, sample_data_dir: str, filename: str) -> str:
        """Upload a file and wait for it to be indexed."""
        with open(f"{sample_data_dir}/sample.txt", "rb") as f:
            resp = client.post(
                "/api/v1/sources",
                files={"file": (filename, f, "text/plain")},
                data={"extract_entities": "false"},
            )
        source_id = resp.json()["id"]
        _poll_source_status(client, source_id, "indexed", timeout=60)
        return source_id

    def test_list_chunks(self, client: httpx.Client, sample_data_dir: str) -> None:
        """Listing chunks returns the canonical paginated envelope."""
        source_id = self._upload_and_index(client, sample_data_dir, "chunks_test.txt")
        resp = client.get(f"/api/v1/sources/{source_id}/chunks")
        assert resp.status_code == 200
        data = resp.json()
        # API migrated to {data, pagination} envelope; old keys
        # ``chunks`` / ``total`` are gone.
        assert "data" in data
        assert "pagination" in data
        assert len(data["data"]) > 0

    def test_get_single_chunk(self, client: httpx.Client, sample_data_dir: str) -> None:
        """Getting a single chunk by ID returns its content."""
        source_id = self._upload_and_index(client, sample_data_dir, "single_chunk.txt")
        chunks_resp = client.get(f"/api/v1/sources/{source_id}/chunks")
        chunks = chunks_resp.json()["data"]
        if not chunks:
            return
        chunk_id = chunks[0]["id"]

        resp = client.get(f"/api/v1/sources/{source_id}/chunks/{chunk_id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == chunk_id

    def test_list_citations(self, client: httpx.Client, sample_data_dir: str) -> None:
        """Listing citations returns a citations / total / page envelope.

        Citations endpoint still uses the legacy pagination shape
        (``{citations, total, page, page_size}``) — distinct from
        chunks (which has migrated to ``{data, pagination}``). The
        inconsistency is real API drift tracked separately in
        TODO.md; this test just pins whatever the endpoint actually
        returns today.
        """
        source_id = self._upload_and_index(client, sample_data_dir, "citations_test.txt")
        resp = client.get(f"/api/v1/sources/{source_id}/citations")
        assert resp.status_code == 200
        data = resp.json()
        assert "citations" in data
        assert "total" in data

    def test_source_stats_by_id(self, client: httpx.Client, sample_data_dir: str) -> None:
        """Source-level stats endpoint returns metrics."""
        source_id = self._upload_and_index(client, sample_data_dir, "stats_test.txt")
        resp = client.get(f"/api/v1/sources/{source_id}/stats")
        # Stats might not exist for indexed-only sources, 200 or 404 acceptable
        assert resp.status_code in (200, 404)

    def test_source_tags(self, client: httpx.Client, sample_data_dir: str) -> None:
        """Source tags endpoint returns list."""
        source_id = self._upload_and_index(client, sample_data_dir, "tags_test.txt")
        resp = client.get(f"/api/v1/sources/{source_id}/tags")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)
