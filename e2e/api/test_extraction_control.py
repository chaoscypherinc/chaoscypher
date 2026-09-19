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
        # SourceService.cancel_extraction raises ValueError("No active
        # extraction job for this source") whenever current_extraction
        # _job_id is unset (service.py:903-906), which is guaranteed
        # here since the source was uploaded with extract_entities=false
        # and never entered extraction. extraction_api.py:327-328 maps
        # that ValueError to 404 unconditionally; 204 is unreachable.
        assert resp.status_code == 404

    def test_extraction_charts_endpoint(self, client: httpx.Client, sample_data_dir: str) -> None:
        """Extraction charts endpoint returns a list (possibly empty)."""
        source_id = self._upload_indexed(client, sample_data_dir, "charts_test.txt")
        resp = client.get(f"/api/v1/sources/{source_id}/extraction/charts")
        # This route has no 404 wiring at all — no lookup, no
        # raise_if_not_found, and NOT_FOUND_RESPONSE absent from its
        # `responses` — so the list assertion runs unconditionally.
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_abort_processing(self, client: httpx.Client, sample_data_dir: str) -> None:
        """Abort processing endpoint responds to indexed source."""
        source_id = self._upload_indexed(client, sample_data_dir, "abort_test.txt")
        resp = client.delete(f"/api/v1/sources/{source_id}/processing")
        # The source is already "indexed" (not one of the processing
        # statuses SourceService.abort_processing checks for), so it
        # always raises RuntimeError("Source is not currently
        # processing...") (service.py:935-989), which extraction_api.py
        # :559-567 maps to 400. 204 (success) requires an active
        # processing status this test never reaches; 404 requires a
        # missing source, which this test never creates.
        assert resp.status_code == 400


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
        # A text source has no images dir: the route returns 200 with an
        # empty list (404 is only raised for structurally invalid ids).
        assert resp.status_code == 200
        assert resp.json() == []
