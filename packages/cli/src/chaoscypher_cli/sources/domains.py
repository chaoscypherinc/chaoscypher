# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Shared CLI domain choices.

The extraction-domain list was previously duplicated verbatim in
``commands/source/add.py`` and ``commands/source/extract.py``. It now lives
here so ``add``, ``extract``, and ``confirm`` stay in lockstep.
"""

from __future__ import annotations


# The 14 registry domains in display order. ``generic`` is the fallback domain.
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
)

# ``add`` lists "auto" first because its --domain default is "auto".
ADD_DOMAIN_CHOICES: tuple[str, ...] = ("auto", *DOMAIN_NAMES)

# ``extract`` historically listed "auto" first too (extract.py:41).
EXTRACT_DOMAIN_CHOICES: tuple[str, ...] = ("auto", *DOMAIN_NAMES)


__all__ = ["ADD_DOMAIN_CHOICES", "DOMAIN_NAMES", "EXTRACT_DOMAIN_CHOICES"]
