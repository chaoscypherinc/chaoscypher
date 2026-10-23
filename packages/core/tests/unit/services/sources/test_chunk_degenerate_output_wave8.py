# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The degenerate-output ladder after audit wave 8, on the queue path (the CLI's are in its tests).

* **Unparseable** was only malformed prefixed lines: the parser counts no line
  of a format it does not know, so an answer in JSON, a markdown table or an
  ``E:`` style gave 0 dropped lines, 0 entities and no climb. Now an answer
  is unparseable when it has output tokens, nothing parsed from it
  (``parser_lines_parsed``) and the group is at least
  ``empty_output_retry_min_chars`` long; a short group (a heading) whose
  answer holds nothing stays untouched.
* **Rung 1 on a hosted provider** repeated the pinned request: the OpenAI,
  Anthropic and Gemini adapters send no seed, so "the next seed" changed
  nothing. Such a provider's ladder starts at the temperature rung, recorded
  on the fallback (``seed_honoured: False``).
* **The rescued counter** counted any kept try on a rung, so a group
  unparseable on every rung (its last try kept) counted as rescued. Only a
  kept try that is no longer degenerate counts.

These drive the queue's real chunk handler and the real extractor against a
fake provider (the helpers of ``test_chunk_extraction_decode_fallback``).
"""

from __future__ import annotations

from typing import Any

import pytest

from chaoscypher_core.services.quality.counters import QualityCounter
from chaoscypher_core.services.sources.engine.extraction.chunk_failures import (
    FALLBACK_TEMPERATURE,
    REPEAT_PENALTY,
    completion_counters,
    decode_fallback,
    degenerate_kind,
    nothing_parsed,
    provider_seed_honoured,
)
from chaoscypher_core.settings import EngineSettings
from tests.unit.services.sources.test_chunk_extraction_decode_fallback import (
    _CHUNK,
    _counted,
    _FakeProvider,
    _kept_output,
    _requeued,
    _run,
)


def _answer(text: str, tokens: int = 40) -> list[dict[str, Any]]:
    """A stream that answers ``text``."""
    return [
        {"type": "content", "delta": text},
        {
            "type": "done",
            "content": text,
            "usage": {"prompt_tokens": 900, "completion_tokens": tokens},
            "finish_reason": "stop",
        },
    ]


_OTHER_FORMATS = {
    "json": '{"entities": [{"name": "Pierre", "type": "Character"}]}',
    "markdown table": "| name | type |\n|---|---|\n| Pierre | Character |\n",
    "E: style": "E: Pierre (Character): a guest at the soirée\n",
}
"""Answers the line parser reads nothing from and drops nothing of."""


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", sorted(_OTHER_FORMATS))
async def test_an_answer_in_another_format_climbs_the_ladder(answer: str) -> None:
    """Nothing parsed and nothing dropped: requeued one rung up, its output not stored."""
    adapter = await _run(_FakeProvider(_answer(_OTHER_FORMATS[answer])), retry_count=0)
    assert _requeued(adapter)
    assert adapter.update_chunk_task.call_args.args[1]["decode_fallback"] == {"step": 1}
    adapter.complete_chunk_task_with_output.assert_not_called()


@pytest.mark.asyncio
async def test_a_short_group_whose_answer_holds_nothing_completes() -> None:
    """A heading below ``empty_output_retry_min_chars``: an answer with nothing in it is kept."""
    heading = "CHAPTER I"
    assert len(heading) < EngineSettings().extraction.empty_output_retry_min_chars
    adapter = await _run(_FakeProvider(_answer("There are no entities here.")), 0, chunk=heading)
    assert not _requeued(adapter)
    stored = adapter.complete_chunk_task_with_output.call_args.kwargs
    assert stored["raw_entities"] == []
    assert "llm_chunks_rescued_by_fallback" not in _counted(adapter)


@pytest.mark.asyncio
async def test_a_group_unparseable_on_its_last_rung_is_no_rescue() -> None:
    """The last try keeps the unparseable output: zero yield, never rescued by the fallback."""
    adapter = await _run(_FakeProvider(_answer(_OTHER_FORMATS["json"])), retry_count=3, rung=3)
    assert not _requeued(adapter)
    stored = adapter.complete_chunk_task_with_output.call_args.kwargs
    assert stored["raw_entities"] == [] and stored["decode_fallback"]["step"] == 3
    counted = _counted(adapter)
    assert "llm_chunks_zero_yield" in counted
    assert "llm_chunks_rescued_by_fallback" not in counted
    # a good output on the same rung is a rescue
    rescued = await _run(_FakeProvider(_kept_output()), retry_count=3, rung=3)
    assert _counted(rescued).count("llm_chunks_rescued_by_fallback") == 1


@pytest.mark.asyncio
async def test_a_hosted_provider_starts_the_ladder_at_the_temperature_rung() -> None:
    """No seed reaches an OpenAI model: rung 1 asks at the fallback temperature, recorded so."""
    provider = _FakeProvider(_kept_output())
    adapter = await _run(provider, retry_count=1, rung=1, chat_provider="openai")
    assert provider.calls and {t for _, t in provider.calls} == {FALLBACK_TEMPERATURE}
    assert set(provider.penalties) == {REPEAT_PENALTY}
    stored = adapter.complete_chunk_task_with_output.call_args.kwargs["decode_fallback"]
    assert stored["step"] == 1 and stored["seed_honoured"] is False
    # Ollama sends the seed: its rung 1 is the next seed at the pinned temperature
    seeded = _FakeProvider(_kept_output())
    adapter = await _run(seeded, retry_count=1, rung=1)
    assert set(seeded.calls) == {(43, 0.0)}
    assert (
        "seed_honoured"
        not in adapter.complete_chunk_task_with_output.call_args.kwargs["decode_fallback"]
    )


def test_the_ladder_of_a_provider_that_sends_no_seed() -> None:
    """Every rung of a seedless provider asks as rung 2 does; the record says the seed was not sent."""
    first = decode_fallback(1, seed=42, temperature=0.0, seed_honoured=False)
    assert first is not None
    assert first.record() == {
        "seed": 43,
        "temperature": FALLBACK_TEMPERATURE,
        "step": 1,
        "repeat_penalty": REPEAT_PENALTY,
        "seed_honoured": False,
    }
    assert decode_fallback(0, seed=42, temperature=0.0, seed_honoured=False) is None
    assert decode_fallback(1, seed=42, temperature=0.0) == decode_fallback(
        1, seed=42, temperature=0.0, seed_honoured=True
    )

    class _Llm:
        chat_provider = "ollama"

    assert provider_seed_honoured(_Llm())
    _Llm.chat_provider = "anthropic"
    assert not provider_seed_honoured(_Llm())


def _kind(tokens: int, metrics: dict[str, Any], *, chunk: str = _CHUNK, entities: Any = ()) -> Any:
    """:func:`degenerate_kind` of an output under the default settings."""
    return degenerate_kind(
        output_tokens=tokens,
        entities=list(entities),
        relationships=[],
        chunk_metrics=metrics,
        chunk_content=chunk,
        engine_settings=EngineSettings(),
    )


def test_unparseable_is_nothing_parsed_on_a_group_of_size() -> None:
    """Output tokens, nothing parsed, a group at least ``empty_output_retry_min_chars`` long."""
    assert _kind(40, {"parser_lines_dropped": 0, "parser_lines_parsed": 0}) == "unparseable"
    assert _kind(40, {}) == "unparseable"  # no parser count: the empty result alone
    assert _kind(40, {"parser_lines_dropped": 6}) == "unparseable"
    # the parser read lines (their entities were filtered out): the model's answer
    assert _kind(40, {"parser_lines_parsed": 2}) is None
    assert _kind(40, {}, entities=[{"name": "Pierre"}]) is None
    # a heading: nothing in it, nothing to retry
    assert _kind(40, {}, chunk="CHAPTER I") is None
    assert _kind(0, {}, chunk="CHAPTER I") is None
    assert _kind(0, {}) == "empty_output"
    assert _kind(40, {"aborted_by_loop": True}, chunk="CHAPTER I") == "loop_abort"
    assert nothing_parsed(entities=[], relationships=[], chunk_metrics={"parser_lines_parsed": 0})
    assert not nothing_parsed(entities=[], relationships=[{"x": 1}], chunk_metrics=None)


def test_a_kept_degenerate_output_is_no_rescue() -> None:
    """``completion_counters`` counts a rescue only for a rung's kept output that is not degenerate."""
    on_a_rung = {"decode_fallback": {"seed": 45, "temperature": 0.3, "step": 3}}
    kept_unparsed = completion_counters(
        output_tokens=40,
        entities=[],
        relationships=[],
        chunk_metrics=on_a_rung,
        degenerate="unparseable",
    )
    assert kept_unparsed == [QualityCounter.LLM_CHUNKS_ZERO_YIELD]
    kept_loop = completion_counters(
        output_tokens=40,
        entities=[{"name": "Partial"}],
        relationships=[],
        chunk_metrics=on_a_rung,
        degenerate="loop_abort",
    )
    assert kept_loop == []
    rescued = completion_counters(
        output_tokens=40,
        entities=[{"name": "Pierre"}],
        relationships=[],
        chunk_metrics=on_a_rung,
        degenerate=None,
    )
    assert rescued == [QualityCounter.LLM_CHUNKS_RESCUED_BY_FALLBACK]
