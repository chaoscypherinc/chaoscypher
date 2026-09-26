# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Framework-agnostic benchmark pieces: probe fixtures, scorers and query models.

The benchmark runner, dataset wiring and leaderboard live in the CLI package
and build on these. Keeping probe loading and scoring here lets any Core
consumer (such as the MCP server) serve probes and score submissions without
depending on the CLI.
"""
