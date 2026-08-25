# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Database Repository.

Handles data access for multi-database operations (file/directory management).
"""

import os
import shutil
from pathlib import Path

import structlog

from chaoscypher_core.exceptions import ValidationError
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

    def _is_strict_child(self, db_path: str) -> bool:
        """Return True only if ``db_path`` resolves to a direct child of ``databases_dir``.

        ``Path.is_relative_to`` is reflexive (a path is relative to itself),
        so a name of ``"."`` resolves to ``databases_dir`` itself and would
        pass a plain containment check while pointing at the databases
        directory instead of a database inside it (entry 827). Comparing
        ``resolved.parent`` to ``databases_dir`` rejects that reflexive case
        along with any traversal (``".."``, nested ``"a/b"`` segments)
        without weakening the boundary check for ordinary names.
        """
        resolved = Path(db_path).resolve()
        return resolved.parent == Path(self.databases_dir).resolve()

    def get_database(self, name: str) -> DatabaseInfo | None:
        """Get information about a specific database."""
        if name.lower() in _RESERVED_DB_NAMES:
            return None
        db_path = os.path.join(self.databases_dir, name)
        if not self._is_strict_child(db_path):
            return None
        if not os.path.exists(db_path):
            return None
        return DatabaseInfo.from_path(name, db_path, self.path_settings.app_db_filename)

    def create_database(self, name: str) -> DatabaseInfo:
        """Create a new database with empty structure.

        Raises:
            ValidationError: If the name is invalid, reserved, too long,
                or the database already exists (mapped to HTTP 400 at the
                Cortex error boundary).
        """
        # Validate name
        if not name or not name.replace("_", "").replace("-", "").isalnum():
            msg = "Database name must be alphanumeric (underscores and hyphens allowed)"
            raise ValidationError(msg, field="name")
        if len(name) > _MAX_DB_NAME_LENGTH:
            msg = f"Database name must be {_MAX_DB_NAME_LENGTH} characters or fewer"
            raise ValidationError(msg, field="name")
        if name.lower() in _RESERVED_DB_NAMES:
            msg = f"Database name '{name}' is reserved"
            raise ValidationError(msg, field="name")

        db_path = os.path.join(self.databases_dir, name)

        # Check if already exists
        if os.path.exists(db_path):
            msg = f"Database '{name}' already exists"
            raise ValidationError(msg, field="name")

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
        """Delete a database (with safety checks).

        Raises:
            ValidationError: If deleting the default database without
                ``allow_default``, the name escapes the databases dir, or
                the database does not exist (mapped to HTTP 400 at the
                Cortex error boundary).
        """
        if name == "default" and not allow_default:
            msg = "Cannot delete default database"
            raise ValidationError(msg, field="name")

        if name.lower() in _RESERVED_DB_NAMES:
            msg = "Invalid database name"
            raise ValidationError(msg, field="name")

        db_path = os.path.join(self.databases_dir, name)

        # Path traversal protection (strict-child, not a reflexive
        # containment check — see ``_is_strict_child``).
        if not self._is_strict_child(db_path):
            msg = "Invalid database name"
            raise ValidationError(msg, field="name")

        if not os.path.exists(db_path):
            msg = f"Database '{name}' does not exist"
            raise ValidationError(msg, field="name")

        # Delete the entire directory
        shutil.rmtree(db_path)

        logger.info("database_deleted", database_name=name)
        return True

    def get_database_path(self, name: str) -> str | None:
        """Get the full path to a database's app.db file.

        Uses centralized PathSettings for filename.
        """
        if name.lower() in _RESERVED_DB_NAMES:
            return None
        db_dir = os.path.join(self.databases_dir, name)
        if not self._is_strict_child(db_dir):
            return None
        return os.path.join(db_dir, self.path_settings.app_db_filename)
