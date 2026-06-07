# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Database Repository.

Handles data access for multi-database operations (file/directory management).
"""

import os
import shutil
from pathlib import Path

import structlog

from chaoscypher_core.models import DatabaseInfo


logger = structlog.get_logger(__name__)

_RESERVED_DB_NAMES = frozenset({".", "..", "con", "prn", "aux", "nul"})
_MAX_DB_NAME_LENGTH = 64


class DatabaseRepository:
    """Repository for database file/directory operations."""

    def __init__(self, data_root: str):
        """Initialize database repository."""
        from chaoscypher_core.app_config import PathSettings

        self.data_root = data_root
        self.path_settings = PathSettings()

        # Use centralized path settings for subdirectory name
        self.databases_dir = os.path.join(data_root, self.path_settings.databases_subdir)

        # Ensure databases directory exists
        os.makedirs(self.databases_dir, exist_ok=True)

        logger.debug("database_repository_initialized", data_root=data_root)

    def list_databases(self) -> list[DatabaseInfo]:
        """List all available databases."""
        databases: list[DatabaseInfo] = []

        if not os.path.exists(self.databases_dir):
            return databases

        for name in os.listdir(self.databases_dir):
            db_path = os.path.join(self.databases_dir, name)
            if os.path.isdir(db_path):
                db_info = DatabaseInfo.from_path(name, db_path, self.path_settings.app_db_filename)
                # Only include directories that have app.db (actual databases)
                if db_info.exists:
                    databases.append(db_info)

        # Sort by name
        databases.sort(key=lambda x: x.name)
        return databases

    def get_database(self, name: str) -> DatabaseInfo | None:
        """Get information about a specific database."""
        db_path = os.path.join(self.databases_dir, name)
        # Path-boundary containment (not a string prefix, which would let a
        # sibling like ``databases_evil`` pass the ``databases`` prefix test).
        if not Path(db_path).resolve().is_relative_to(Path(self.databases_dir).resolve()):
            return None
        if not os.path.exists(db_path):
            return None
        return DatabaseInfo.from_path(name, db_path, self.path_settings.app_db_filename)

    def create_database(self, name: str) -> DatabaseInfo:
        """Create a new database with empty structure."""
        # Validate name
        if not name or not name.replace("_", "").replace("-", "").isalnum():
            msg = "Database name must be alphanumeric (underscores and hyphens allowed)"
            raise ValueError(msg)
        if len(name) > _MAX_DB_NAME_LENGTH:
            msg = f"Database name must be {_MAX_DB_NAME_LENGTH} characters or fewer"
            raise ValueError(msg)
        if name.lower() in _RESERVED_DB_NAMES:
            msg = f"Database name '{name}' is reserved"
            raise ValueError(msg)

        db_path = os.path.join(self.databases_dir, name)

        # Check if already exists
        if os.path.exists(db_path):
            msg = f"Database '{name}' already exists"
            raise ValueError(msg)

        # Create directory structure using centralized path settings
        os.makedirs(db_path, exist_ok=True)

        # Initialize app.db (SQLModel database with graph tables)
        from chaoscypher_core.database.engine import init_database
        from chaoscypher_core.database.seed import seed_default_templates

        init_database(name)

        # Seed default templates (node/edge types) for the new database
        seed_default_templates(name)

        logger.info("database_created", database_name=name)

        return DatabaseInfo.from_path(name, db_path, self.path_settings.app_db_filename)

    def delete_database(self, name: str, allow_default: bool = False) -> bool:
        """Delete a database (with safety checks)."""
        if name == "default" and not allow_default:
            msg = "Cannot delete default database"
            raise ValueError(msg)

        db_path = os.path.join(self.databases_dir, name)

        # Path traversal protection
        resolved = Path(db_path).resolve()
        if not resolved.is_relative_to(Path(self.databases_dir).resolve()):
            msg = "Invalid database name"
            raise ValueError(msg)

        if not os.path.exists(db_path):
            msg = f"Database '{name}' does not exist"
            raise ValueError(msg)

        # Delete the entire directory
        shutil.rmtree(db_path)

        logger.info("database_deleted", database_name=name)
        return True

    def get_database_path(self, name: str) -> str | None:
        """Get the full path to a database's app.db file.

        Uses centralized PathSettings for filename.
        """
        db_path = os.path.join(self.databases_dir, name, self.path_settings.app_db_filename)
        # Path-boundary containment (see get_database) — not a string prefix.
        if not Path(db_path).resolve().is_relative_to(Path(self.databases_dir).resolve()):
            return None
        return db_path
