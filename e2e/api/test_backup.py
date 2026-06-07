# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""E2E tests for backup and restore endpoints."""

import time

import httpx


def _create_backup_with_retry(client: httpx.Client, max_attempts: int = 3) -> dict:
    """Create a backup, retrying on filename collision (same-second collisions).

    Backup filenames use second-level precision (app_YYYYMMDD_HHMMSS.db),
    so rapid successive calls can collide.
    """
    for attempt in range(max_attempts):
        resp = client.post("/api/v1/backup")
        if resp.status_code == 200:
            return resp.json()
        if attempt < max_attempts - 1:
            time.sleep(1.1)
    resp.raise_for_status()
    return resp.json()


class TestBackup:
    """Test backup creation, listing, and download."""

    def test_list_backups(self, client: httpx.Client) -> None:
        """Listing backups returns a list."""
        resp = client.get("/api/v1/backup")
        assert resp.status_code == 200
        data = resp.json()
        assert "backups" in data
        assert isinstance(data["backups"], list)

    def test_create_backup(self, client: httpx.Client) -> None:
        """Creating a backup returns filename and metadata."""
        data = _create_backup_with_retry(client)
        assert "filename" in data
        assert "size" in data
        assert "created_at" in data

    def test_create_and_list_flow(self, client: httpx.Client) -> None:
        """Creating a backup adds it to the list."""
        # Ensure previous test backup has distinct timestamp
        time.sleep(1.1)

        # Create with retry to handle same-second filename collisions
        data = _create_backup_with_retry(client)
        filename = data["filename"]

        # List should include it
        list_resp = client.get("/api/v1/backup")
        filenames = [b["filename"] for b in list_resp.json()["backups"]]
        assert filename in filenames

    def test_delete_nonexistent_backup(self, client: httpx.Client) -> None:
        """Deleting a non-existent backup returns 422 or 404."""
        resp = client.delete("/api/v1/backup/app_99999999_999999.db")
        # 404 if validation passes but file doesn't exist,
        # 422 if pattern validation fails
        assert resp.status_code in (404, 422)
