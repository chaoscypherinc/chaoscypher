# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Databases Feature.

Multi-database management with creation, switching, and export.

This feature provides multi-tenancy via isolated database directories. Each
database has its own app.db (SQLModel) and search indices.
Supports database creation, switching, export/import, and deletion. Enables
users to maintain separate knowledge bases for different projects or domains
without cross-contamination.

Components:
- DatabasesService: Database lifecycle management and operations
- DatabaseRepository: File system operations for database directories
- DatabaseInfo: Database metadata DTO (name, size, created_at, etc.)

Architecture:
VSA pattern with repository handling file system operations and service
orchestrating database lifecycle. Factory function provides dependency injection.
Integrates with settings for current database tracking and initialization logic
for schema setup.

Example:
    from chaoscypher_cortex.features.databases import DatabasesService

    # Manage multiple databases
    service = DatabasesService(repository, settings)
    db_info = service.create_database("project_alpha")
    service.switch_database("project_alpha")
    databases = service.list_databases()

"""

from chaoscypher_core.models import DatabaseInfo
from chaoscypher_cortex.features.databases.service import DatabasesService


__all__ = [
    "DatabaseInfo",
    "DatabasesService",
]
