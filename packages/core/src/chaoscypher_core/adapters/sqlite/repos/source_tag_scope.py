# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The one SQL form of "the sources carrying these tags".

Every tag filter (the document listing, the source list, the tag-scoped
searches and the tagged-source lookup) embeds this subquery, so the filter
runs inside SQL, before any ``LIMIT`` or ``k``. A tag set that matches no
document selects no source; it never widens to an unscoped query.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from chaoscypher_core.exceptions import ValidationError
from chaoscypher_core.models import TagMatch


if TYPE_CHECKING:
    from collections.abc import Sequence


def tagged_sources_sql(
    tags: Sequence[str],
    match: TagMatch,
    *,
    param_prefix: str = "scope_tag",
    database_name: str | None = None,
) -> tuple[str, dict[str, str]]:
    """Return ``(sql, params)`` selecting the ``source_id`` of every source carrying ``tags``.

    Args:
        tags: Tag names, already normalised by the tag rule. Duplicates count once.
        match: ``ANY``: a source carrying at least one of ``tags``; ``ALL``: every one.
        param_prefix: Prefix of the bind parameter names, so two filters can
            share one statement (``<prefix>_0``, ``<prefix>_1``, ``<prefix>_db``).
        database_name: When given, only tags of this database count.

    Returns:
        The SQL text (one ``source_id`` column) and its bind parameters. An
        empty ``tags`` gives SQL that selects nothing.

    Raises:
        ValidationError: ``param_prefix`` is not a Python identifier.

    """
    # The prefix is spliced into the SQL as parameter names: never free text.
    if not param_prefix.isidentifier():
        msg = f"param_prefix must be an identifier: {param_prefix!r}"
        raise ValidationError(msg, field="param_prefix")
    distinct = list(dict.fromkeys(tags))
    params = {f"{param_prefix}_{i}": tag for i, tag in enumerate(distinct)}
    placeholders = ", ".join(f":{name}" for name in params)
    lines = [
        "SELECT a.source_id FROM source_tag_assignments a",
        "JOIN source_tags t ON t.id = a.tag_id",
        f"WHERE t.name IN ({placeholders})",
    ]
    if database_name is not None:
        params[f"{param_prefix}_db"] = database_name
        lines.append(f"AND t.database_name = :{param_prefix}_db")
    lines.append("GROUP BY a.source_id")
    if match == TagMatch.ALL:
        lines.append(f"HAVING count(DISTINCT t.name) = {len(distinct)}")
    return "\n".join(lines), params
