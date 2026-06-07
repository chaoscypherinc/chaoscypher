# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Database protocol interface for chaoscypher-engine.

Defines Protocol for database metadata operations.
Main app implements this via an adapter that wraps its database repository.
"""

from typing import TYPE_CHECKING, Protocol


if TYPE_CHECKING:
    from chaoscypher_core.models import DatabaseInfo


class DatabaseProtocol(Protocol):
    """Interface for database metadata operations.

    Provides access to database configuration and paths.
    Used by services that need database directory paths
    (e.g., for partitioning graph data).
    """

    def get_database(self, database_name: str) -> DatabaseInfo:
        """Get database metadata.

        Args:
            database_name: Name of the database

        Returns:
            DatabaseInfo object with name, path, etc.

        Raises:
            ValueError: If database not found

        Example:
            db_info = database_repo.get_database("my_database")
            print(f"Database path: {db_info.path}")
            # path might be: /data/databases/my_database

        """
        ...
