# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Authentication module — nginx auth_request model.

Exports the dependency aliases used by feature `api.py` files. Auth itself
is handled at the nginx edge; these aliases read `X-Auth-User` from the
request and return the authenticated username string.
"""

from chaoscypher_cortex.shared.auth.dependencies import (
    CurrentUsername,
    get_current_username,
    has_valid_edge_token,
    read_auth_header_optional,
)


__all__ = [
    "CurrentUsername",
    "get_current_username",
    "has_valid_edge_token",
    "read_auth_header_optional",
]
