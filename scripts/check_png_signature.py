# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Validate that every staged ``*.png`` file starts with the PNG magic bytes.

Guards against the class of corruption seen in commit ``c9e47c918`` where a
text-mode pipe stripped the leading ``0x89`` sentinel and every ``\\r`` byte
from binary assets, leaving files that 200-OK over HTTP but fail to decode.

Usage:
    python scripts/check_png_signature.py path/to/a.png path/to/b.png
"""

from __future__ import annotations

import sys
from pathlib import Path


PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def main(argv: list[str]) -> int:
    bad: list[tuple[str, bytes]] = []
    for arg in argv:
        path = Path(arg)
        try:
            head = path.read_bytes()[:8]
        except OSError as exc:
            print(f"check_png_signature: cannot read {arg}: {exc}", file=sys.stderr)
            return 2
        if head != PNG_MAGIC:
            bad.append((arg, head))

    if not bad:
        return 0

    print("check_png_signature: invalid PNG signature in:", file=sys.stderr)
    for arg, head in bad:
        print(f"  {arg}  (got {head.hex()}, expected {PNG_MAGIC.hex()})", file=sys.stderr)
    print(
        "\nLikely cause: file was passed through a text-mode tool that stripped "
        "non-ASCII bytes and/or normalized CRLF. Re-export the asset as binary.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
