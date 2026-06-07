# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Utils - Chaos Cypher CLI Utilities.

Shared utility modules for the CLI package:

- console: Rich console utilities and formatting
- paths: Cross-platform path management with platformdirs
- files: File operations for package management (archive, download)
- llm_check: LLM configuration verification utilities

Lexicon API access goes through ``chaoscypher_core.services.lexicon.LexiconClient``
directly — the CLI no longer maintains a parallel wrapper.

Example:
    from chaoscypher_cli.utils.console import get_console, print_error
    from chaoscypher_cli.utils.paths import get_config_dir, get_cache_dir
    from chaoscypher_cli.utils.files import create_archive, extract_archive
    from chaoscypher_cli.utils.llm_check import require_llm_configured

    # Console output
    console = get_console()
    console.print("[green]Success![/green]")

    # Path management
    config_dir = get_config_dir()

    # Archive operations (from core)
    archive = create_archive(Path("./pkg"), Path("./pkg.ccx"))

    # LLM configuration check
    if not require_llm_configured("entity extraction"):
        return  # User declined setup
"""

from chaoscypher_cli.utils.console import (
    get_console,
    print_error,
    print_success,
    print_table,
    print_warning,
)
from chaoscypher_cli.utils.files import (
    CCX_EXTENSION,
    ArchiveInfo,
    ArchiveOptions,
    create_archive,
    download_file,
    extract_archive,
    format_size,
    get_archive_info,
)
from chaoscypher_cli.utils.llm_check import (
    check_llm_or_skip,
    is_llm_configured,
    require_llm_configured,
)
from chaoscypher_cli.utils.paths import (
    get_cache_dir,
    get_config_dir,
    get_data_dir,
    get_databases_dir,
    get_package_cache_dir,
    get_packages_dir,
)


__all__ = [
    "CCX_EXTENSION",
    "ArchiveInfo",
    "ArchiveOptions",
    "check_llm_or_skip",
    "create_archive",
    "download_file",
    "extract_archive",
    "format_size",
    "get_archive_info",
    "get_cache_dir",
    "get_config_dir",
    "get_console",
    "get_data_dir",
    "get_databases_dir",
    "get_package_cache_dir",
    "get_packages_dir",
    "is_llm_configured",
    "print_error",
    "print_success",
    "print_table",
    "print_warning",
    "require_llm_configured",
]
