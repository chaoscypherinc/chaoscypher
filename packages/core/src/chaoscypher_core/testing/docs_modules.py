# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Run the docs site's own ``src/lib`` modules under node on boards a test builds.

Test infrastructure only. The leaderboard page, the homepage hero and the
teaser read the exported board through small TypeScript modules in
``packages/docs/src/lib``; a test judges them on the committed board and on
altered copies of it by running the modules themselves (node strips the
types), never a Python re-implementation. Until 2026-10-22 three tests each
copied the modules and wrote their own runner.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from typing import TYPE_CHECKING, Any

import pytest

from chaoscypher_core.testing.repo_files import repo_file_or_skip


if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

DOCS_LIB = "packages/docs/src/lib"
"""Where the page's modules live, relative to the repo root."""

_SIBLING_IMPORT = re.compile(r'from "\./([A-Za-z0-9_]+)"')


def judge_boards(
    tmp_path: Path,
    imports: Mapping[str, Sequence[str]],
    expression: str,
    boards: Mapping[str, Any],
) -> dict[str, Any]:
    """``expression`` evaluated by node on each of ``boards``, with the page's modules imported.

    Args:
        tmp_path: A scratch directory; the modules are copied there as ``.mts``
            with their sibling imports rewritten to match.
        imports: Module name (``"heroStats"``) to the names the expression uses.
        expression: A JavaScript expression over ``board``, JSON-serialisable.
        boards: Name to board; the result maps each name to its value.

    Returns:
        ``{name: value}`` as node printed it.
    """
    if shutil.which("node") is None:
        pytest.skip("needs node")
    lib = repo_file_or_skip(DOCS_LIB)
    for module in lib.glob("*.ts"):
        source = _SIBLING_IMPORT.sub(r'from "./\1.mts"', module.read_text(encoding="utf-8"))
        (tmp_path / f"{module.stem}.mts").write_text(source, encoding="utf-8")
    (tmp_path / "boards.json").write_text(json.dumps(boards), encoding="utf-8")
    lines = [
        'import { readFileSync } from "node:fs";',
        *(
            f'import {{ {", ".join(names)} }} from "./{module}.mts";'
            for module, names in imports.items()
        ),
        'const boards = JSON.parse(readFileSync(process.argv[2], "utf-8"));',
        "const out = {};",
        "for (const [name, board] of Object.entries(boards)) {",
        f"  out[name] = {expression};",
        "}",
        "console.log(JSON.stringify(out));",
    ]
    (tmp_path / "judge.mts").write_text("\n".join(lines) + "\n", encoding="utf-8")
    done = subprocess.run(  # noqa: S603 - node on the page's own modules
        ["node", str(tmp_path / "judge.mts"), str(tmp_path / "boards.json")],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode != 0:
        msg = f"node failed on the page's modules:\n{done.stderr}"
        raise AssertionError(msg)
    result: dict[str, Any] = json.loads(done.stdout)
    return result
