# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Alembic migration environment for chaoscypher-core.

The live ``alembic.ini`` points script_location at this package so the
migrations directory travels with the core package wheel. Callers go
through :func:`chaoscypher_core.database.migrations.runner.upgrade_to_head`
rather than invoking Alembic's CLI directly — the runner adds tier
classification, auto-backup, and startup-gate behaviour on top of
Alembic's mechanical layer.
"""
