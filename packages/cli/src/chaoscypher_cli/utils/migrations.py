# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Ask before applying migrations that need the owner's confirmation.

The one place the CLI decides what to show and ask before an explicit apply.
``chaoscypher upgrade`` and ``chaoscypher db migrate apply`` both call it, so a
migration that holds (its preview lists changes to the data) is never applied
unseen from either.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import click
from rich.markup import escape
from rich.text import Text


if TYPE_CHECKING:
    from rich.console import Console

    from chaoscypher_core.database.migrations.upgrade import (
        PendingMigration,
        PendingMigrationsResponse,
    )


def needing_confirmation(pending: PendingMigrationsResponse) -> list[PendingMigration]:
    """The pending migrations to confirm: any above ``safe_auto``, and any that holds.

    None on a first build: a database with no schema yet is built, not upgraded.
    """
    if pending.first_build:
        return []
    return [m for m in pending.blocked_on if str(m.tier) != "safe_auto" or m.hold]


def confirm_pending_migrations(pending: PendingMigrationsResponse, *, console: Console) -> None:
    """List what needs confirmation, with every change each one lists, and ask.

    Returns at once when nothing needs confirmation. Declining raises ``click.Abort``
    (exit code 1), so nothing is applied.
    """
    rows = needing_confirmation(pending)
    if not rows:
        return
    console.print(f"[yellow]{len(rows)} migration(s) need confirmation:[/yellow]")
    for m in rows:
        console.print(f"  - {m.revision} ({m.tier}): {escape(m.description)}")
        if m.hold:
            console.print(f"    It would make {len(m.hold)} change(s) to your data:")
        for line in m.hold:
            # Plain Text: a line quotes the user's data (a tag name), never Rich markup.
            console.print(Text(f"      {line}"))
    if pending.last_backup:
        console.print(f"\nPre-upgrade backup: {pending.last_backup}")
    click.confirm("Apply all pending migrations?", abort=True)


__all__ = ["confirm_pending_migrations", "needing_confirmation"]
