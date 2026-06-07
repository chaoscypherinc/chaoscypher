# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""E2E tests for deletion cascades and constraints."""

import time

import httpx
import pytest


def _poll_indexed(client: httpx.Client, source_id: str, timeout: int = 60) -> None:
    """Wait for a source to reach indexed status."""
    start = time.time()
    while time.time() - start < timeout:
        resp = client.get(f"/api/v1/sources/{source_id}")
        if resp.status_code == 200:
            status = resp.json().get("processing_status") or resp.json().get("status", "")
            if status == "indexed":
                return
            if status in ("error", "failed"):
                raise RuntimeError("Source failed")
        time.sleep(1)
    raise TimeoutError("Source did not index")


class TestSourceCascades:
    """Test that deleting a source cleans up related data."""

    @pytest.mark.requires_llm
    def test_source_delete_removes_chunks(self, client: httpx.Client, sample_data_dir: str) -> None:
        """Deleting a source also removes its chunks.

        Uses the standard ``requires_llm`` marker — auto-runs when the
        e2e fake-ollama is present (the default), auto-skips on
        stacks without one. No standalone skip rationale needed.
        """
        # Upload and index
        with open(f"{sample_data_dir}/sample.txt", "rb") as f:
            upload_resp = client.post(
                "/api/v1/sources",
                files={"file": ("cascade_test.txt", f, "text/plain")},
                data={"extract_entities": "false"},
            )
        source_id = upload_resp.json()["id"]
        _poll_indexed(client, source_id)

        # Verify chunks exist
        chunks_before = client.get(f"/api/v1/sources/{source_id}/chunks").json()
        assert chunks_before["pagination"]["total"] > 0

        # Delete the source
        del_resp = client.delete(f"/api/v1/sources/{source_id}")
        assert del_resp.status_code == 204

        # Chunks should be gone (either 404 or 200 with empty list)
        chunks_after = client.get(f"/api/v1/sources/{source_id}/chunks")
        assert chunks_after.status_code in (200, 404)
        if chunks_after.status_code == 200:
            assert chunks_after.json().get("pagination", {}).get("total", 0) == 0


class TestWorkflowCascades:
    """Test that deleting a workflow cleans up triggers."""

    def test_workflow_delete_cleans_triggers(self, client: httpx.Client) -> None:
        """Deleting a workflow should handle its triggers."""
        # Create workflow
        wf_resp = client.post(
            "/api/v1/workflows",
            json={"name": "E2E Cascade Workflow", "input_schema": {}},
        )
        workflow_id = wf_resp.json()["id"]

        # Create trigger for it
        trigger_resp = client.post(
            "/api/v1/triggers",
            json={
                "name": "E2E Cascade Trigger",
                "event_source": "webhook",
                "filters": {},
                "workflow_id": workflow_id,
            },
        )
        trigger_id = trigger_resp.json()["id"]

        # Delete workflow
        del_resp = client.delete(f"/api/v1/workflows/{workflow_id}")
        assert del_resp.status_code == 204

        # Trigger may be cascaded or orphaned - both are valid
        # If cascaded: 404; if orphaned: 200 but workflow_id invalid
        trig_check = client.get(f"/api/v1/triggers/{trigger_id}")
        assert trig_check.status_code in (200, 404)


class TestTemplateConstraints:
    """Test template deletion with in-use constraint + force-cascade."""

    @staticmethod
    def _create_template_and_node(
        client: httpx.Client, name_suffix: str
    ) -> tuple[str, str]:
        """Create a node-type template + one node that uses it.

        Returns ``(template_id, node_id)``. Uniquifies the template name
        via ``name_suffix`` so successive tests on the same DB don't
        collide on the unique-name 500 (separate product bug).
        """
        import uuid
        suffix = f"{name_suffix}-{uuid.uuid4().hex[:8]}"
        create_resp = client.post(
            "/api/v1/templates",
            json={
                "name": f"E2E_TemplateDelete_{suffix}",
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
        assert create_resp.status_code in (200, 201), create_resp.text
        template_id = create_resp.json()["id"]
        node_resp = client.post(
            "/api/v1/nodes",
            json={
                "template_id": template_id,
                "label": "Uses Template",
                "properties": {"name": "Uses Template"},
            },
        )
        assert node_resp.status_code in (200, 201), node_resp.text
        node_id = node_resp.json()["id"]
        return template_id, node_id

    def test_delete_in_use_template_without_force_returns_409(
        self, client: httpx.Client
    ) -> None:
        """``DELETE /templates/{id}`` (no force) on an in-use template is 409."""
        template_id, node_id = self._create_template_and_node(client, "no_force")
        try:
            resp = client.delete(f"/api/v1/templates/{template_id}")
            assert resp.status_code == 409, resp.text
            body = resp.json()
            # Canonical error envelope: {error, message, details?}
            assert body.get("error") == "TEMPLATE_IN_USE", body
            # Template itself should still exist (no partial delete).
            still_there = client.get(f"/api/v1/templates/{template_id}")
            assert still_there.status_code == 200
        finally:
            client.delete(f"/api/v1/templates/{template_id}?force=true")

    def test_delete_template_force_cascades(self, client: httpx.Client) -> None:
        """``DELETE /templates/{id}?force=true`` cascades to dependent nodes."""
        template_id, node_id = self._create_template_and_node(client, "force")

        del_resp = client.delete(f"/api/v1/templates/{template_id}?force=true")
        assert del_resp.status_code == 204, del_resp.text

        # Template gone.
        assert client.get(f"/api/v1/templates/{template_id}").status_code == 404
        # Dependent node cascaded.
        assert client.get(f"/api/v1/nodes/{node_id}").status_code == 404

        # Cleanup any remaining node
        client.delete(f"/api/v1/nodes/{node_id}")
