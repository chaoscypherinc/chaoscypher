# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Shared CLI domain choices and the ``--pack`` option.

The extraction-domain list was previously duplicated verbatim in
``commands/source/add.py`` and ``commands/source/extract.py``. It now lives
here so ``add``, ``extract``, and ``confirm`` stay in lockstep.

Every source extracts with the floor (``generic``) plus the packs it chooses
(spec 2026-10-07 § 5). ``--pack NAME`` is repeatable; ``--domain`` is kept as
its alias (one name, as before). ``generic`` alone is the floor alone and
``auto`` detects. :func:`pack_option` declares the option once for every
command and :func:`chosen_packs` reads it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import click

from chaoscypher_core.services.sources.engine.extraction.domains.packs import (
    packs_choice,
    requested_packs,
)


if TYPE_CHECKING:
    from collections.abc import Callable, Sequence


# The builtin registry domains in display order. ``generic`` is the fallback
# domain. Must stay in lockstep with the core registry plugins
# (``chaoscypher_core/.../extraction/domains/plugins/*.jsonld``) — enforced by
# ``tests/test_source_domains_constant.py::test_domain_names_match_builtin_registry``.
DOMAIN_NAMES: tuple[str, ...] = (
    "generic",
    "technical",
    "scientific",
    "medical",
    "legal",
    "financial",
    "news",
    "educational",
    "biographical",
    "historical",
    "literary",
    "philosophical",
    "political",
    "theological",
    "cybersecurity",
    "design",
    "intelligence",
    "investigation",
    "reference",
)

# The packs: every builtin plugin but the floor (``generic``), which every
# source has. Pinned to the registry's ``role == "pack"`` plugins by
# ``tests/test_source_domains_constant.py``. ``DOMAIN_NAMES`` keeps the floor
# because ``--pack generic`` / the confirm prompt's ``generic`` choose it alone.
PACK_NAMES: tuple[str, ...] = tuple(name for name in DOMAIN_NAMES if name != "generic")

# ``add`` lists "auto" first because its --domain default is "auto".
ADD_DOMAIN_CHOICES: tuple[str, ...] = ("auto", *DOMAIN_NAMES)

# ``extract`` historically listed "auto" first too (extract.py:41).
EXTRACT_DOMAIN_CHOICES: tuple[str, ...] = ("auto", *DOMAIN_NAMES)


def pack_option(choices: Sequence[str], help_text: str) -> Callable[[Any], Any]:
    """The repeatable ``--pack`` option (``--domain`` its alias), stored in the ``packs`` parameter."""
    return click.option(
        "--pack",
        "--domain",
        "packs",
        multiple=True,
        type=click.Choice(list(choices)),
        help=help_text,
    )


def chosen_packs(values: Sequence[str]) -> str | None:
    """The one-name form of ``--pack`` values: ``auto``, ``generic``, a pack, or the packs' label.

    None when the option was not given. The name is what the CLI's one-name
    paths store (``forced_domain`` split by ``pack_columns``): one pack's
    label is its name, several packs' is ``historical+literary``.

    Raises:
        click.BadParameter: When ``auto`` is mixed with packs.
    """
    if not values:
        return None
    try:
        packs = requested_packs(values, where="--pack")
    except ValueError as exc:
        raise click.BadParameter(str(exc), param_hint="--pack") from exc
    return packs_choice(packs) or "auto"


__all__ = [
    "ADD_DOMAIN_CHOICES",
    "DOMAIN_NAMES",
    "EXTRACT_DOMAIN_CHOICES",
    "PACK_NAMES",
    "chosen_packs",
    "pack_option",
]
