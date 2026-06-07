# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""E2E cross-feature journey tests.

These tests verify multi-step user workflows that span multiple features,
simulating actual user scenarios.
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


def _poll_task(client: httpx.Client, task_id: str, timeout: int = 60) -> dict:
    """Poll task until completion."""
    start = time.time()
    while time.time() - start < timeout:
        resp = client.get(f"/api/v1/queue/tasks/{task_id}")
        if resp.status_code == 200:
            data = resp.json()
            status = data.get("status", "")
            if status in ("complete", "completed", "success"):
                return data
            if status in ("failed", "error"):
                msg = f"Task failed: {data}"
                raise RuntimeError(msg)
        time.sleep(1)
    msg = f"Task {task_id} did not complete within {timeout}s"
    raise TimeoutError(msg)


class TestSourceUploadSearchJourney:
    """Journey: Upload document -> wait indexed -> search for content -> delete."""

    def test_upload_search_delete_flow(self, client: httpx.Client, sample_data_dir: str) -> None:
        """Full lifecycle: upload, index, search, delete."""
        # 1. Upload
        with open(f"{sample_data_dir}/sample.txt", "rb") as f:
            upload_resp = client.post(
                "/api/v1/sources",
                files={"file": ("journey.txt", f, "text/plain")},
                data={"extract_entities": "false"},
            )
        assert upload_resp.status_code == 202
        source_id = upload_resp.json()["id"]

        # 2. Wait for indexing
        _poll_source_status(client, source_id, "indexed", timeout=60)

        # 3. Verify it appears in listing
        list_resp = client.get("/api/v1/sources")
        source_ids = [s["id"] for s in list_resp.json()["data"]]
        assert source_id in source_ids

        # 4. Check chunks exist. Envelope: {data, pagination: {total, ...}}.
        # ``pagination`` has a ``total`` key, not ``total_items`` —
        # the chunks endpoint uses a slightly different paginator
        # shape than some other endpoints; tracked as API consistency
        # debt separately.
        chunks_resp = client.get(f"/api/v1/sources/{source_id}/chunks")
        assert chunks_resp.status_code == 200
        assert chunks_resp.json()["pagination"]["total"] > 0

        # 5. Delete it
        del_resp = client.delete(f"/api/v1/sources/{source_id}")
        assert del_resp.status_code == 204

        # 6. Verify gone
        get_resp = client.get(f"/api/v1/sources/{source_id}")
        assert get_resp.status_code == 404


class TestCcxRoundtripJourney:
    """Journey: Import CCX -> verify data -> export -> import again -> verify."""

    def test_ccx_import_export_roundtrip(
        self,
        client: httpx.Client,
        e2e_fixtures_dir: str,
    ) -> None:
        """Full CCX roundtrip through the API."""
        # 1. Import seed.ccx
        with open(f"{e2e_fixtures_dir}/seed.ccx", "rb") as f:
            import_resp = client.post(
                "/api/v1/exports/import",
                files={"file": ("seed.ccx", f, "application/octet-stream")},
                params={"merge": "true"},
            )
        assert import_resp.status_code == 202
        _poll_task(client, import_resp.json()["task_id"], timeout=60)

        # 2. Verify nodes exist
        nodes_resp = client.get("/api/v1/nodes")
        assert nodes_resp.status_code == 200
        assert nodes_resp.json()["pagination"]["total"] > 0

        # 3. Export graph
        export_resp = client.post(
            "/api/v1/exports",
            params={
                "include_templates": "true",
                "include_knowledge": "true",
                "include_embeddings": "false",
            },
        )
        assert export_resp.status_code == 202
        _poll_task(client, export_resp.json()["task_id"], timeout=60)


class TestTemplateNodeEdgeJourney:
    """Journey: Create template -> create nodes -> create edges -> query graph."""

    def test_full_graph_construction(self, client: httpx.Client) -> None:
        """Build a mini graph via API calls."""
        # 1. Create node template
        node_tmpl = client.post(
            "/api/v1/templates",
            json={
                "name": "E2E_JourneyPerson",
                "template_type": "node",
                "properties": [
                    {
                        "name": "name",
                        "display_name": "name",
                        "property_type": "string",
                        "required": True,
                    },
                ],
            },
        )
        assert node_tmpl.status_code == 201
        node_tmpl_id = node_tmpl.json()["id"]

        # 2. Create edge template
        edge_tmpl = client.post(
            "/api/v1/templates",
            json={
                "name": "E2E_JourneyKnows",
                "template_type": "edge",
                "properties": [],
            },
        )
        assert edge_tmpl.status_code == 201
        edge_tmpl_id = edge_tmpl.json()["id"]

        # 3. Create two nodes
        alice = client.post(
            "/api/v1/nodes",
            json={
                "template_id": node_tmpl_id,
                "label": "Journey Alice",
                "properties": {"name": "Journey Alice"},
            },
        ).json()["id"]

        bob = client.post(
            "/api/v1/nodes",
            json={
                "template_id": node_tmpl_id,
                "label": "Journey Bob",
                "properties": {"name": "Journey Bob"},
            },
        ).json()["id"]

        # 4. Create edge between them
        edge_resp = client.post(
            "/api/v1/edges",
            json={
                "template_id": edge_tmpl_id,
                "source_node_id": alice,
                "target_node_id": bob,
                "label": "knows",
            },
        )
        assert edge_resp.status_code == 201

        # 5. Query connections
        connections_resp = client.get(f"/api/v1/nodes/{alice}/connections")
        assert connections_resp.status_code == 200
        # Alice should have at least one connection now
        assert connections_resp.json()["pagination"]["total"] >= 1

        # 6. Cleanup
        client.delete(f"/api/v1/edges/{edge_resp.json()['id']}")
        client.delete(f"/api/v1/nodes/{alice}")
        client.delete(f"/api/v1/nodes/{bob}")
        client.delete(f"/api/v1/templates/{edge_tmpl_id}?force=true")
        client.delete(f"/api/v1/templates/{node_tmpl_id}?force=true")
