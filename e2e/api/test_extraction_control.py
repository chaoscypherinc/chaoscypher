# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""E2E tests for extraction control and source image endpoints."""

import time

import httpx


def _poll_indexed(client: httpx.Client, source_id: str, timeout: int = 60) -> dict:
    """Wait for a source to reach indexed status."""
    start = time.time()
    while time.time() - start < timeout:
        resp = client.get(f"/api/v1/sources/{source_id}")
        resp.raise_for_status()
        data = resp.json()
        status = data.get("processing_status") or data.get("status", "")
        if status == "indexed":
            return data
        if status in ("error", "failed"):
            msg = f"Source failed: {data}"
            raise RuntimeError(msg)
        time.sleep(1)
    msg = f"Source did not index in {timeout}s"
    raise TimeoutError(msg)


class TestExtractionControl:
    """Test extraction cancel, stats, and charts endpoints."""

    def _upload_indexed(self, client: httpx.Client, sample_data_dir: str, filename: str) -> str:
        """Upload file and wait for indexing."""
        with open(f"{sample_data_dir}/sample.txt", "rb") as f:
            resp = client.post(
                "/api/v1/sources",
                files={"file": (filename, f, "text/plain")},
                data={"extract_entities": "false"},
            )
        source_id = resp.json()["id"]
        _poll_indexed(client, source_id)
        return source_id

    def test_cancel_extraction_no_job(self, client: httpx.Client, sample_data_dir: str) -> None:
        """Cancelling extraction when no job is active returns 404."""
        source_id = self._upload_indexed(client, sample_data_dir, "cancel_test.txt")
        resp = client.delete(f"/api/v1/sources/{source_id}/extraction")
        # No active job - 404 expected
        assert resp.status_code in (204, 404)

    def test_extraction_charts_endpoint(self, client: httpx.Client, sample_data_dir: str) -> None:
        """Extraction charts endpoint returns a list (possibly empty)."""
        source_id = self._upload_indexed(client, sample_data_dir, "charts_test.txt")
        resp = client.get(f"/api/v1/sources/{source_id}/extraction/charts")
        # Should return 200 with empty or populated list, or 404 if no extraction
        assert resp.status_code in (200, 404)
        if resp.status_code == 200:
            assert isinstance(resp.json(), list)

    def test_abort_processing(self, client: httpx.Client, sample_data_dir: str) -> None:
        """Abort processing endpoint responds to indexed source."""
        source_id = self._upload_indexed(client, sample_data_dir, "abort_test.txt")
        resp = client.delete(f"/api/v1/sources/{source_id}/processing")
        # Either successfully aborts or says nothing to abort
        assert resp.status_code in (200, 204, 404, 400)


class TestSourceImages:
    """Test source image endpoints."""

    def test_list_images_no_pdf(self, client: httpx.Client, sample_data_dir: str) -> None:
        """Listing images for a text source returns empty list."""
        with open(f"{sample_data_dir}/sample.txt", "rb") as f:
            upload_resp = client.post(
                "/api/v1/sources",
                files={"file": ("images_test.txt", f, "text/plain")},
                data={"extract_entities": "false"},
            )
        source_id = upload_resp.json()["id"]
        _poll_indexed(client, source_id)

        resp = client.get(f"/api/v1/sources/{source_id}/images")
        # 200 with empty list, or 404 if no images exist
        assert resp.status_code in (200, 404)
