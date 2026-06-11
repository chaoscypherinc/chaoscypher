# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Utils - Chaos Cypher CLI Utilities.

Shared utility modules for the CLI package. Import from the submodules
directly — this package intentionally re-exports nothing:

- console: Rich console utilities and formatting
- paths: Cross-platform path management with platformdirs
- display: Status/quality color helpers
- llm_check: LLM configuration verification utilities

Archive helpers live in ``chaoscypher_core.services.package``; Lexicon API
access goes through ``chaoscypher_core.services.lexicon.LexiconClient``.

Example:
    from chaoscypher_cli.utils.console import get_console, print_error
    from chaoscypher_cli.utils.paths import get_config_dir, get_packages_dir
    from chaoscypher_cli.utils.llm_check import check_llm_or_skip
"""

__all__: list[str] = []
