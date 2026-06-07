# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Coverage for handlers.validate_database_name."""

from __future__ import annotations

from chaoscypher_neuron.handlers import validate_database_name


class TestValidateDatabaseName:
    """Tests for validate_database_name."""

    def test_valid_name_passes_through(self) -> None:
        """A safe database name is returned unchanged."""
        assert validate_database_name("good_db", "fb") == "good_db"

    def test_invalid_name_falls_back(self) -> None:
        """A name with illegal characters returns the fallback."""
        assert validate_database_name("bad name!", "fallback") == "fallback"

    def test_none_falls_back(self) -> None:
        """A None name returns the fallback."""
        assert validate_database_name(None, "fallback") == "fallback"

    def test_empty_string_falls_back(self) -> None:
        """An empty name returns the fallback."""
        assert validate_database_name("", "fallback") == "fallback"

    def test_hyphen_and_digits_allowed(self) -> None:
        """Hyphens and digits are within the safe charset."""
        assert validate_database_name("db-01", "fb") == "db-01"
