# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""A scripted extraction model: pass-1 and pass-2 lines keyed by the text they answer.

``FakeLLMProvider`` serves one canned pair for every chunk; the memory tests need the pair
to depend on the memory's text ("Pierre moved to Lyon." names Pierre and Lyon, "The weather
was grey." names nothing). ``ScriptedExtractionLLM`` reads the prompt it is given, finds
the script key the prompt contains, and serves that key's pass-1 lines on the first call
for the key and its pass-2 lines on the second (then alternates, one pair per group). A
prompt that contains no key is answered with nothing on both passes.

Two hooks make a model that is busy or failing: ``hold_on`` maps a key to an
``asyncio.Event`` the call awaits before it answers (a model that does not answer until
the test says so), ``raise_on`` maps a key to the exception the call raises (a model that
fails). Both record the prompt first.

The lines are the V2 pipe-delimited format ``fakes/llm.py`` documents:
``E|name|type|aliases|confidence|sent_ref|description`` and
``R|src_idx|dst_idx|type|confidence|sent_ref|justification``.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Callable, Mapping
from typing import Any

from chaoscypher_core.models import LLMChatResponse, TokenUsage
from tests.fakes.llm import _StreamingResponse


__all__ = ["Script", "ScriptedExtractionLLM"]

Script = tuple[str, str] | Callable[[str], tuple[str, str]]
"""A key's pass-1 and pass-2 lines, or a function of the prompt that returns them (a model
that reads the KNOWN ENTITIES block writes the name the block gives)."""


class ScriptedExtractionLLM:
    """``LLMProviderPort``-shaped: ``chat`` answers from ``script``, and records every prompt."""

    def __init__(
        self,
        script: dict[str, Script],
        *,
        provider_name: str = "scripted",
        hold_on: Mapping[str, asyncio.Event] | None = None,
        raise_on: Mapping[str, BaseException] | None = None,
    ) -> None:
        self.script = dict(script)
        self.hold_on: dict[str, asyncio.Event] = dict(hold_on or {})
        self.raise_on: dict[str, BaseException] = dict(raise_on or {})
        self.provider_name = provider_name
        self.prompts: list[str] = []
        self.last_prompt: str = ""
        self.call_count = 0
        self._calls_by_key: Counter[str | None] = Counter()

    def _key_for(self, prompt: str) -> str | None:
        """The longest script key the prompt contains, or None."""
        matches = [key for key in self.script if key in prompt]
        return max(matches, key=len) if matches else None

    async def chat(
        self,
        messages: str | list[Any],
        tools: list[Any] | None = None,
        stream: bool = False,
        **kwargs: Any,
    ) -> LLMChatResponse | _StreamingResponse:
        prompt = messages if isinstance(messages, str) else str(messages[-1].get("content", ""))
        self.prompts.append(prompt)
        self.last_prompt = prompt
        self.call_count += 1
        for held, event in self.hold_on.items():
            if held in prompt:
                await event.wait()
        for failing, error in self.raise_on.items():
            if failing in prompt:
                raise error
        key = self._key_for(prompt)
        self._calls_by_key[key] += 1
        entry: Script = self.script.get(key, ("", "")) if key is not None else ("", "")
        pass1, pass2 = entry(prompt) if callable(entry) else entry
        text = pass1 if self._calls_by_key[key] % 2 == 1 else pass2
        if stream:
            return _StreamingResponse(
                text=text, finish_reason="stop", input_tokens=100, output_tokens=len(text)
            )
        return LLMChatResponse(
            content=text,
            tool_calls=None,
            thinking=None,
            usage=TokenUsage(
                input_tokens=100, output_tokens=len(text), total_tokens=100 + len(text)
            ),
            provider=self.provider_name,
            is_stream=False,
        )
