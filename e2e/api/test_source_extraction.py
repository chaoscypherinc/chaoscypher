# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""E2E tests for source extraction endpoints.

These tests verify API contract for extraction endpoints without requiring
an actual LLM to complete extraction (they check the endpoint returns
expected status codes and response shapes).
"""

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


class TestSourceExtraction:
    """Test manual extraction trigger and status endpoints."""

    def _upload_indexed_source(
        self, client: httpx.Client, sample_data_dir: str, filename: str
    ) -> str:
        """Upload a source and wait for it to be indexed."""
        with open(f"{sample_data_dir}/sample.txt", "rb") as f:
            resp = client.post(
                "/api/v1/sources",
                files={"file": (filename, f, "text/plain")},
                data={"extract_entities": "false"},
            )
        source_id = resp.json()["id"]
        _poll_source_status(client, source_id, "indexed", timeout=60)
        return source_id

    def test_get_extraction_status_no_job(self, client: httpx.Client, sample_data_dir: str) -> None:
        """Getting extraction status for an indexed source works."""
        source_id = self._upload_indexed_source(client, sample_data_dir, "extraction_status.txt")
        resp = client.get(f"/api/v1/sources/{source_id}/extraction")
        # Either returns 200 with state info, or 404 if no job yet
        assert resp.status_code in (200, 404)

    def test_extraction_tasks_endpoint(self, client: httpx.Client, sample_data_dir: str) -> None:
        """Extraction tasks endpoint returns paginated list."""
        source_id = self._upload_indexed_source(client, sample_data_dir, "extraction_tasks.txt")
        resp = client.get(f"/api/v1/sources/{source_id}/extraction/tasks")
        assert resp.status_code == 200
        data = resp.json()
        assert "tasks" in data
        assert "total" in data


class TestSourceBatch:
    """Test batch source upload."""

    def test_batch_upload(self, client: httpx.Client, sample_data_dir: str) -> None:
        """Batch upload of multiple files returns a summary."""
        with (
            open(f"{sample_data_dir}/sample.txt", "rb") as f1,
            open(f"{sample_data_dir}/sample.pdf", "rb") as f2,
        ):
            resp = client.post(
                "/api/v1/sources/batch",
                files=[
                    ("files", ("batch1.txt", f1.read(), "text/plain")),
                    ("files", ("batch2.pdf", f2.read(), "application/pdf")),
                ],
                data={"extract_entities": "false"},
            )
        assert resp.status_code == 202
        data = resp.json()
        assert "uploaded" in data
        assert "files" in data


class TestSourceMetadata:
    """Test source metadata endpoints (domains, stats)."""

    def test_list_domains(self, client: httpx.Client) -> None:
        """Listing extraction domains returns available domains."""
        resp = client.get("/api/v1/sources/domains")
        assert resp.status_code == 200
        data = resp.json()
        assert "domains" in data
        assert isinstance(data["domains"], list)

    def test_source_stats(self, client: httpx.Client) -> None:
        """Source stats endpoint returns processing metrics."""
        resp = client.get("/api/v1/sources/stats")
        assert resp.status_code == 200
        data = resp.json()
        # Expected shape: {"total_files": N, "by_status": {...}, "total_size_bytes": N}
        assert "total_files" in data
        assert "by_status" in data
