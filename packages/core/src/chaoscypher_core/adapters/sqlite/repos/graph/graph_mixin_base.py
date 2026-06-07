# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Base class for GraphRepository mixins.

Declares the shared interface (attributes and methods) that all graph
repository mixins access at runtime through the composed GraphRepository class.
These are provided by GraphRepository.__init__() and its own methods.
"""

from __future__ import annotations

from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite.safe_session import SafeSession


class GraphMixinBase:
    """Base class for GraphRepository mixins.

    Provides type declarations for attributes and methods shared across
    all mixins. These are implemented by GraphRepository at composition time.

    This class exists solely for mypy type-checking support.
    It is never instantiated directly.
    """

    session: SafeSession
    database_name: str

    def _generate_id(self, prefix: str = "node") -> str:
        """Generate a unique ID with prefix (implemented by GraphRepository)."""
        raise NotImplementedError

    def _get_graph_name_for_template(self, template_id: str) -> str:
        """Determine which graph to use based on template ID (implemented by GraphRepository)."""
        raise NotImplementedError
