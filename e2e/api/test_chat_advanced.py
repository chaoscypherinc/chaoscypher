# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""E2E tests for advanced chat features (scope, streaming, title generation)."""

import httpx


class TestChatScope:
    """Test chat source scoping."""

    def test_set_chat_scope(self, client: httpx.Client) -> None:
        """Setting chat scope with source_ids succeeds."""
        chat_resp = client.post("/api/v1/chats", json={"title": "E2E Scope Test Chat"})
        chat_id = chat_resp.json()["id"]

        resp = client.patch(
            f"/api/v1/chats/{chat_id}/scope",
            json={"source_ids": [], "tag_ids": []},
        )
        assert resp.status_code == 200

    def test_clear_chat_scope(self, client: httpx.Client) -> None:
        """Clearing chat scope removes any source restrictions."""
        chat_resp = client.post("/api/v1/chats", json={"title": "E2E ClearScope Chat"})
        chat_id = chat_resp.json()["id"]

        resp = client.delete(f"/api/v1/chats/{chat_id}/scope")
        assert resp.status_code == 200


class TestChatStatus:
    """Test chat status updates."""

    def test_update_chat_status(self, client: httpx.Client) -> None:
        """Updating chat status changes it."""
        chat_resp = client.post("/api/v1/chats", json={"title": "E2E Status Chat"})
        chat_id = chat_resp.json()["id"]

        resp = client.patch(
            f"/api/v1/chats/{chat_id}/status",
            json={"status": "completed"},
        )
        assert resp.status_code == 200


class TestChatCounts:
    """Test chat count stats endpoint."""

    def test_chat_count(self, client: httpx.Client) -> None:
        """Chat count endpoint returns a count."""
        resp = client.get("/api/v1/chats/stats/count")
        assert resp.status_code == 200
        data = resp.json()
        assert "count" in data
