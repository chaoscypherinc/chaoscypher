# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Backup and Restore feature.

Provides API endpoints for creating, listing, restoring, downloading,
and deleting database backups.

Note: This feature has no repository.py because backups operate directly
on the filesystem and raw SQLite connections rather than through SQLModel
entities or the ORM session.
"""

from chaoscypher_cortex.features.backup.api import router
from chaoscypher_cortex.features.backup.service import BackupFeatureService


__all__ = ["BackupFeatureService", "router"]
