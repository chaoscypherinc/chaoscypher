# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""``python -m chaoscypher_core.services.orchestration`` — render configs.

Direct module entry point so the all-in-one container's entrypoint can
materialise nginx-http.conf / supervisord.conf / valkey-args.txt without
depending on the ``chaoscypher`` CLI script (which lives in the user-
facing ``chaoscypher-cli`` package, not the runtime substrate).

The ``chaoscypher render-orchestration`` Click wrapper in
``packages/cli/`` remains for terminal use; both invocations call into
the same ``render_all`` function below.

Usage:
    python -m chaoscypher_core.services.orchestration --output-dir <DIR>
    python -m chaoscypher_core.services.orchestration --list
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from chaoscypher_core.app_config import get_settings
from chaoscypher_core.services.orchestration.renderer import (
    list_templates,
    render_all,
)


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns process exit code."""
    parser = argparse.ArgumentParser(
        prog="python -m chaoscypher_core.services.orchestration",
        description=(
            "Render orchestration templates (nginx, supervisord, valkey) "
            "from current Pydantic settings."
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory to write rendered configs into. Required unless --list is passed.",
    )
    parser.add_argument(
        "--list",
        dest="do_list",
        action="store_true",
        default=False,
        help="List known templates and exit.",
    )
    args = parser.parse_args(argv)

    if args.do_list:
        for name in list_templates():
            print(name)
        return 0

    if args.output_dir is None:
        parser.error("--output-dir is required unless --list is passed")

    settings = get_settings()
    written = render_all(settings, args.output_dir)
    for path in written:
        print(f"  rendered: {path}")
    print(f"  total: {len(written)} templates")
    return 0


if __name__ == "__main__":
    sys.exit(main())
