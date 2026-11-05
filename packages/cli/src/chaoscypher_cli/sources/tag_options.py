# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The ``--tag`` and ``--match`` options of ``source add``, ``source list`` and ``source search``.

One definition, so the commands spell, repeat and explain them the same way.
The values are checked by Core's tag rule (``validate_tags`` or
``filter_tag_scope``) where the command uses them, never here.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import click

from chaoscypher_core.models import TagMatch


def tag_option(help_text: str) -> Callable[[Any], Any]:
    """``--tag T``, repeatable, into the ``tags`` parameter (a tuple of names as typed)."""
    return click.option("--tag", "tags", multiple=True, metavar="TAG", help=help_text)


def match_option() -> Callable[[Any], Any]:
    """``--match any|all``: how the ``--tag`` filter matches (default any)."""
    return click.option(
        "--match",
        "match",
        type=click.Choice([m.value for m in TagMatch]),
        default=TagMatch.ANY.value,
        show_default=True,
        help="With several --tag: documents carrying any of them, or all of them.",
    )


def tag_list(tags: list[str]) -> str:
    """A document's tags as one line: ``alpha, beta``, or ``none``."""
    return ", ".join(tags) if tags else "none"


__all__ = ["match_option", "tag_list", "tag_option"]
