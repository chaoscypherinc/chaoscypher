# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Shared CLI domain choices.

The extraction-domain list was previously duplicated verbatim in
``commands/source/add.py`` and ``commands/source/extract.py``. It now lives
here so ``add``, ``extract``, and ``confirm`` stay in lockstep.
"""

from __future__ import annotations


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

# ``add`` lists "auto" first because its --domain default is "auto".
ADD_DOMAIN_CHOICES: tuple[str, ...] = ("auto", *DOMAIN_NAMES)

# ``extract`` historically listed "auto" first too (extract.py:41).
EXTRACT_DOMAIN_CHOICES: tuple[str, ...] = ("auto", *DOMAIN_NAMES)


__all__ = ["ADD_DOMAIN_CHOICES", "DOMAIN_NAMES", "EXTRACT_DOMAIN_CHOICES"]
