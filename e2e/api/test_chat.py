# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""E2E tests for chat endpoints.

Note: Chat message responses require an LLM provider. These tests
verify API contract and CRUD operations. Message sending tests
accept LLM-unavailable errors as valid responses.
"""

import httpx


class TestChat:
    """Test chat CRUD and message operations."""

    def test_create_chat(self, client: httpx.Client) -> None:
        """Creating a chat returns 201 with chat ID."""
        resp = client.post("/api/v1/chats", json={"title": "E2E Test Chat"})
        assert resp.status_code == 201
        data = resp.json()
        assert "id" in data
        assert data["title"] == "E2E Test Chat"

    def test_list_chats(self, client: httpx.Client) -> None:
        """Listing chats returns created chats."""
        client.post("/api/v1/chats", json={"title": "ListTest Chat"})
        resp = client.get("/api/v1/chats")
        assert resp.status_code == 200
        assert "data" in resp.json()

    def test_add_message(self, client: httpx.Client) -> None:
        """Adding a user message to a chat returns 201."""
        chat_resp = client.post("/api/v1/chats", json={"title": "Message Test Chat"})
        chat_id = chat_resp.json()["id"]

        msg_resp = client.post(
            f"/api/v1/chats/{chat_id}/messages",
            json={"role": "user", "content": "Hello from E2E test"},
        )
        assert msg_resp.status_code == 201

    def test_get_messages(self, client: httpx.Client) -> None:
        """Getting messages returns the chat history."""
        chat_resp = client.post("/api/v1/chats", json={"title": "GetMsg Test Chat"})
        chat_id = chat_resp.json()["id"]

        client.post(
            f"/api/v1/chats/{chat_id}/messages",
            json={"role": "user", "content": "E2E test message"},
        )

        resp = client.get(f"/api/v1/chats/{chat_id}/messages")
        assert resp.status_code == 200
        assert len(resp.json()) >= 1

    def test_delete_chat(self, client: httpx.Client) -> None:
        """Deleting a chat removes it."""
        chat_resp = client.post("/api/v1/chats", json={"title": "Delete Me Chat"})
        chat_id = chat_resp.json()["id"]

        del_resp = client.delete(f"/api/v1/chats/{chat_id}")
        assert del_resp.status_code == 204
