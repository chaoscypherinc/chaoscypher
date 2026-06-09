# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Generate the public model-cards docs page from the benchmark registry.

Run: ``uv run python scripts/generate_model_cards.py``
Writes: ``packages/docs/docs/reference/model-cards.md``
"""

from __future__ import annotations

from pathlib import Path

from chaoscypher_cli.benchmark.cards import render_cards


_OUT = Path("packages/docs/docs/reference/model-cards.md")


def main() -> None:
    _OUT.write_text(render_cards(), encoding="utf-8")
    print(f"wrote {_OUT}")


if __name__ == "__main__":
    main()
