# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Admin plugin management feature (Cortex).

Exposes a single endpoint (``POST /api/v1/admin/plugins/reload``) that
invalidates the Core plugin registry caches so the next request
re-discovers plugins from disk.
"""

from chaoscypher_cortex.features.admin_plugins.api import router


__all__ = ["router"]
