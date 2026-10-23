# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""A scripted LLM for tests that drive the two-pass extraction at ``AIEntityExtractor.call_llm``.

Test infrastructure only (see this package's docstring). Assign it as an
extractor's ``call_llm`` (or patch the class attribute: it is no method, so it
receives no ``self``). It tells pass 1 from pass 2 by the first line of each
harvest template, answers each pass from its own script in turn, and records
every prompt it is sent, in order and per pass.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from chaoscypher_core.services.sources.engine.extraction.utils.ai_entities import CallLLMResult
from chaoscypher_core.services.sources.engine.extraction.utils.prompts import (
    ENTITY_HARVEST_TEMPLATE,
    RELATIONSHIP_HARVEST_TEMPLATE,
)


PASS1_OPENING = ENTITY_HARVEST_TEMPLATE.split("\n", 1)[0]
"""The first line of every pass-1 (entity) prompt."""
PASS2_OPENING = RELATIONSHIP_HARVEST_TEMPLATE.split("\n", 1)[0]
"""The first line of every pass-2 (relationship) prompt."""


class ScriptedLLMError(RuntimeError):
    """A failure the script asked for (``pass2_failing``): transient, so a retry policy retries it."""

    is_retryable = True


@dataclass
class ScriptedExtractionLLM:
    """Answers pass 1 and pass 2 from their scripts and records the prompts.

    ``pass1_answers`` are taken in turn, one per pass-1 call; running out is an
    ``AssertionError`` (the test sent more groups than it scripted).
    ``pass2_answers`` are taken in turn and "" (no edges) once they run out.
    A pass-1 call whose 0-based turn is in ``pass1_aborted`` answers as a stream
    the loop guard aborted (``aborted_by_loop``: degenerate output a retry
    policy retries). A pass-2 call whose 0-based turn is in ``pass2_failing`` raises
    :class:`ScriptedLLMError` instead of answering (it still counts as a turn).
    """

    pass1_answers: Sequence[str] = ()
    pass2_answers: Sequence[str] = ()
    pass1_aborted: frozenset[int] = frozenset()
    pass2_failing: frozenset[int] = frozenset()
    input_tokens: int = 1
    output_tokens: int = 1
    prompts: list[str] = field(default_factory=list)
    """Every prompt, in the order sent."""
    pass1_prompts: list[str] = field(default_factory=list)
    pass2_prompts: list[str] = field(default_factory=list)

    async def __call__(self, prompt: str, **_: Any) -> CallLLMResult:
        """Record ``prompt`` and answer it from its pass's script."""
        self.prompts.append(prompt)
        aborted = False
        if prompt.startswith(PASS1_OPENING):
            turn = len(self.pass1_prompts)
            self.pass1_prompts.append(prompt)
            if turn >= len(self.pass1_answers):
                msg = f"pass-1 call {turn + 1} has no scripted answer"
                raise AssertionError(msg)
            content = self.pass1_answers[turn]
            aborted = turn in self.pass1_aborted
        elif prompt.startswith(PASS2_OPENING):
            turn = len(self.pass2_prompts)
            self.pass2_prompts.append(prompt)
            if turn in self.pass2_failing:
                msg = f"scripted pass-2 failure on call {turn + 1}"
                raise ScriptedLLMError(msg)
            content = self.pass2_answers[turn] if turn < len(self.pass2_answers) else ""
        else:
            msg = f"neither a pass-1 nor a pass-2 prompt: {prompt[:80]!r}"
            raise AssertionError(msg)
        return CallLLMResult(
            content=content,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            finish_reason="stop",
            aborted_by_loop=aborted,
        )


__all__ = ["PASS1_OPENING", "PASS2_OPENING", "ScriptedExtractionLLM", "ScriptedLLMError"]
