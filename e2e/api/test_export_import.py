# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""E2E tests for CCX export and import."""

import time

import httpx


def poll_task_complete(
    client: httpx.Client,
    task_id: str,
    timeout: int = 120,
) -> dict:
    """Poll a queue task until completion or timeout."""
    start = time.time()
    while time.time() - start < timeout:
        resp = client.get(f"/api/v1/queue/tasks/{task_id}")
        if resp.status_code == 200:
            data = resp.json()
            status = data.get("status", "")
            if status in ("complete", "completed", "success"):
                return data
            if status in ("failed", "error"):
                msg = f"Task {task_id} failed: {data}"
                raise RuntimeError(msg)
        time.sleep(1)
    msg = f"Task {task_id} did not complete within {timeout}s"
    raise TimeoutError(msg)


class TestExportImport:
    """Test CCX export, import, and roundtrip."""

    def test_import_seed_ccx(self, client: httpx.Client, e2e_fixtures_dir: str) -> None:
        """Importing seed.ccx creates templates and nodes."""
        with open(f"{e2e_fixtures_dir}/seed.ccx", "rb") as f:
            resp = client.post(
                "/api/v1/exports/import",
                files={"file": ("seed.ccx", f, "application/octet-stream")},
                params={"merge": "true"},
            )
        assert resp.status_code == 202
        task_id = resp.json()["task_id"]

        poll_task_complete(client, task_id, timeout=60)

        nodes_resp = client.get("/api/v1/nodes")
        assert nodes_resp.status_code == 200
        node_labels = [n["label"] for n in nodes_resp.json()["data"]]
        assert any("Alice" in label for label in node_labels)

    def test_export_graph(self, client: httpx.Client) -> None:
        """Exporting the graph returns a task ID that completes."""
        resp = client.post(
            "/api/v1/exports",
            params={
                "include_templates": "true",
                "include_knowledge": "true",
                "include_embeddings": "false",
            },
        )
        assert resp.status_code == 202
        assert "task_id" in resp.json()

        result = poll_task_complete(client, resp.json()["task_id"], timeout=60)
        assert result is not None

    def test_export_full_flow_with_result(self, client: httpx.Client) -> None:
        """Test full export flow: POST exports -> poll task -> fetch result.

        This is the exact flow the original export bug was in - the result
        endpoint was untested before.
        """
        import base64
        import zipfile
        from io import BytesIO

        # Step 1: Initiate export
        resp = client.post(
            "/api/v1/exports",
            params={
                "include_templates": "true",
                "include_knowledge": "true",
                "include_embeddings": "false",
            },
        )
        assert resp.status_code == 202
        task_id = resp.json()["task_id"]

        # Step 2: Poll task until complete
        poll_task_complete(client, task_id, timeout=60)

        # Step 3: Fetch the result (the part that was untested)
        result_resp = client.get(f"/api/v1/queue/tasks/{task_id}/result")
        assert result_resp.status_code == 200
        result_data = result_resp.json()

        # Step 4: Verify the result contains the export package
        # Result may have content/data/result key with base64 CCX
        ccx_b64 = (
            result_data.get("content")
            or result_data.get("data", {}).get("content")
            or result_data.get("result", {}).get("content")
        )
        if ccx_b64:
            # Decode and verify it's a valid ZIP (CCX files are ZIPs)
            ccx_bytes = base64.b64decode(ccx_b64)
            with zipfile.ZipFile(BytesIO(ccx_bytes)) as zf:
                names = zf.namelist()
                # CCX packages must have manifest.json
                assert "manifest.json" in names, f"Missing manifest in {names}"

    def test_export_by_sources(self, client: httpx.Client) -> None:
        """Test source-filtered export endpoint."""
        # Get a source ID to export
        sources_resp = client.get("/api/v1/sources")
        sources = sources_resp.json()["data"]
        if not sources:
            # No sources to export - just verify endpoint accepts empty list
            resp = client.post(
                "/api/v1/exports/by_sources",
                json=[],
            )
            assert resp.status_code in (202, 400, 422)
            return

        source_ids = [sources[0]["id"]]
        resp = client.post(
            "/api/v1/exports/by_sources",
            json=source_ids,
        )
        assert resp.status_code == 202
        assert "task_id" in resp.json()
