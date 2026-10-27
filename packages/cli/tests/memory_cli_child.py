# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Run the real ``chaoscypher`` CLI in this process with a fake extraction model.

``python memory_cli_child.py MODEL ARG...`` runs ``chaoscypher ARG...`` the way the console
script does (its logging set up by the CLI itself, from its own argv), with the extraction
model replaced: ``raise`` raises an error that quotes the whole prompt it was given (and so
the memory), ``hold`` is called and never answers, ``malformed`` answers with lines that
quote the memory but do not parse, and entities with no relationship. No connection to a model server is
allowed. The memory CLI tests run it with ``subprocess`` and read what it printed.
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any
from unittest.mock import patch


MALFORMED_PASS1 = (
    "E|Pierre Bezukhov|Person|Pierre|0.9|S9|A count who moved to Lyon\n"
    "E||Person||0.9|S1|Pierre Bezukhov moved to Lyon\n"
    "Pierre Bezukhov moved to Lyon, this is no line at all\n"
    "E|Lyon|Location||0.9|S1|A city\n"
    "E|Pierre Bezukhov|Person|Pierre|0.9|S1|A count\n"
)
"""Entity lines that quote the memory and do not parse, then two that do."""
MALFORMED_PASS2 = (
    "R|0|7|located_in|0.9|S1|Pierre Bezukhov moved to Lyon\nR|Pierre Bezukhov|Lyon|located_in\n"
)
"""Relationship lines that quote the memory and do not parse: no relationship survives."""


def main() -> None:
    """Run the CLI with the fake model named by the first argument."""
    model, args = sys.argv[1], sys.argv[2:]
    sys.argv = ["chaoscypher", *args]
    # imported first: the CLI sets its logging up at import, from the argv above
    import chaoscypher_cli.__main__ as cli
    from chaoscypher_core.exceptions import LLMError
    from chaoscypher_core.services.sources.engine.extraction.utils.ai_entities import (
        AIEntityExtractor,
        CallLLMResult,
    )
    from chaoscypher_core.testing.extraction_llm import PASS1_OPENING
    from chaoscypher_core.testing.network_fixtures import install_guard

    install_guard()  # a connection to a model server raises

    async def call_llm(_self: Any, prompt: str, **_kw: Any) -> Any:
        if model == "hold":
            await asyncio.Event().wait()
        if model == "malformed":
            content = MALFORMED_PASS1 if prompt.startswith(PASS1_OPENING) else MALFORMED_PASS2
            return CallLLMResult(
                content=content,
                input_tokens=1,
                output_tokens=1,
                finish_reason="stop",
                aborted_by_loop=False,
            )
        msg = f"cannot read the prompt: {prompt}"
        raise LLMError(msg)

    with patch.object(AIEntityExtractor, "call_llm", call_llm):
        cli.main()


if __name__ == "__main__":
    main()
