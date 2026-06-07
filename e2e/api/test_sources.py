# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""E2E tests for source upload and management."""

import time

import httpx


def poll_source_status(
    client: httpx.Client,
    source_id: str,
    target_status: str = "indexed",
    timeout: int = 60,
) -> dict:
    """Poll a source until it reaches the target status or times out."""
    start = time.time()
    while time.time() - start < timeout:
        resp = client.get(f"/api/v1/sources/{source_id}")
        resp.raise_for_status()
        data = resp.json()
        status = data.get("processing_status") or data.get("status", "")
        if status == target_status:
            return data
        if status in ("error", "failed"):
            msg = f"Source {source_id} failed: {data}"
            raise RuntimeError(msg)
        time.sleep(1)
    msg = f"Source {source_id} did not reach '{target_status}' within {timeout}s"
    raise TimeoutError(msg)


class TestSources:
    """Test source upload, indexing, listing, and deletion."""

    def test_upload_text_file(self, client: httpx.Client, sample_data_dir: str) -> None:
        """Uploading a text file returns 202 with source ID."""
        with open(f"{sample_data_dir}/sample.txt", "rb") as f:
            resp = client.post(
                "/api/v1/sources",
                files={"file": ("sample.txt", f, "text/plain")},
                data={"extract_entities": "false"},
            )
        assert resp.status_code == 202, f"Upload failed: {resp.text}"
        assert "id" in resp.json()

    def test_upload_and_poll_indexing(self, client: httpx.Client, sample_data_dir: str) -> None:
        """Uploaded file eventually reaches 'indexed' status."""
        with open(f"{sample_data_dir}/sample.txt", "rb") as f:
            resp = client.post(
                "/api/v1/sources",
                files={"file": ("poll_test.txt", f, "text/plain")},
                data={"extract_entities": "false"},
            )
        source_id = resp.json()["id"]
        result = poll_source_status(client, source_id, "indexed", timeout=60)
        assert result is not None

    def test_list_sources(self, client: httpx.Client) -> None:
        """List sources returns uploaded files."""
        resp = client.get("/api/v1/sources")
        assert resp.status_code == 200
        data = resp.json()
        assert "data" in data
        assert "pagination" in data

    def test_get_source_detail(self, client: httpx.Client, sample_data_dir: str) -> None:
        """Get source by ID returns full metadata."""
        with open(f"{sample_data_dir}/sample.pdf", "rb") as f:
            upload_resp = client.post(
                "/api/v1/sources",
                files={"file": ("detail_test.pdf", f, "application/pdf")},
                data={"extract_entities": "false"},
            )
        source_id = upload_resp.json()["id"]

        resp = client.get(f"/api/v1/sources/{source_id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == source_id

    def test_delete_source(self, client: httpx.Client, sample_data_dir: str) -> None:
        """Deleting a source removes it."""
        with open(f"{sample_data_dir}/sample.txt", "rb") as f:
            upload_resp = client.post(
                "/api/v1/sources",
                files={"file": ("delete_test.txt", f, "text/plain")},
                data={"extract_entities": "false"},
            )
        source_id = upload_resp.json()["id"]

        del_resp = client.delete(f"/api/v1/sources/{source_id}")
        assert del_resp.status_code == 204

        get_resp = client.get(f"/api/v1/sources/{source_id}")
        assert get_resp.status_code == 404
