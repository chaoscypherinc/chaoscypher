# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The one policy for a failed extraction group (chunk task).

The queue's chunk handler and the CLI's in-process loop both decide, for an
exception raised while extracting one group, whether to try again; and both
treat a model that returned nothing for a non-trivial group as a transient
failure rather than "nothing to extract". Retries are bounded by
``settings.retries.extraction_chunk_max``; a group that still fails counts
``QualityCounter.LLM_CHUNKS_FAILED_PERMANENT`` on its source.

A retry is only a retry if something changes: under pinned decoding
(temperature 0, seed 42) the same request gives the same output, so a
deterministic empty output repeated itself on every retry (audit wave 5).
Both paths take each retry's decoding from :func:`decode_fallback`, the
extraction ladder of the pipeline recommendations (§ 4b): the first rung asks
at the same temperature with the next seed, the second and later at
:data:`FALLBACK_TEMPERATURE` with a further seed and :data:`REPEAT_PENALTY`.
Every fallback is recorded (``decode_fallback: {seed, temperature, step}``,
plus ``repeat_penalty`` on the rungs that send one) on the call's metrics rows,
on the stored chunk result and on the attempt history a rerun keeps, so the
completion cache, the commit and the benchmark can tell a rescued chunk from a
pinned one; a group kept from a rung counts
``QualityCounter.LLM_CHUNKS_RESCUED_BY_FALLBACK`` on its source
(:func:`completion_counters`).

The ladder climbs only on DEGENERATE output (:class:`DegenerateOutputError`:
a loop abort, an empty output, an output nothing parses from:
:func:`degenerate_kind`; the one check both paths run is
:func:`degenerate_output_check`), never on a
transport error: a 503 or a dropped connection says nothing about the
decoding, so its retry asks at the same rung (until audit wave 6 the rung was
the retry count, so a host suspend moved a chunk off the pinned decoding, and
a loop abort, which completed the chunk with what it had, never retried at
all). A chunk interrupted by a worker restart is requeued at the rung it was
on (the neuron recovery records ``error_type="interrupted"``). The rung a
group is on is :func:`ladder_step`; the next one after a failure,
:func:`next_ladder_step`.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from chaoscypher_core.exceptions import LLMServiceError


if TYPE_CHECKING:
    from chaoscypher_core.services.quality.counters import QualityCounter


def is_retryable_chunk_failure(exc: BaseException) -> bool:
    """Whether a group that raised ``exc`` should be extracted again.

    Fatal model errors (the model cannot do this, or does not exist) never
    retry; anything else retries when it says it is retryable (rate limits,
    transient service errors, the empty-output guard below).
    """
    from chaoscypher_core.exceptions import LLMModelError, ModelCapabilityError

    if isinstance(exc, (ModelCapabilityError, LLMModelError)):
        return False
    return bool(getattr(exc, "is_retryable", False))


DEGENERATE_KINDS = ("loop_abort", "empty_output", "unparseable")
"""The outputs a new decoding may rescue: a loop the stream guard aborted, nothing, nothing parsed."""


class DegenerateOutputError(LLMServiceError):
    """The model answered, but with output a new decoding may rescue: it climbs the ladder.

    Retryable like any service error; ``kind`` is one of :data:`DEGENERATE_KINDS`.
    """

    def __init__(self, kind: str, *, provider: str, model: str | None, reason: str) -> None:
        """Record what was degenerate about the output."""
        super().__init__(provider=provider, model=model, reason=reason)
        self.kind = kind


def degenerate_output_error(
    kind: str, *, provider: str, model: str | None, reason: str
) -> DegenerateOutputError:
    """A retryable :class:`DegenerateOutputError` of ``kind`` (one of :data:`DEGENERATE_KINDS`)."""
    return DegenerateOutputError(kind, provider=provider, model=model, reason=reason)


def is_degenerate_failure(exc: BaseException) -> bool:
    """Whether ``exc`` says the output was degenerate (the ladder climbs), not the transport."""
    return isinstance(exc, DegenerateOutputError) and exc.kind in DEGENERATE_KINDS


def nothing_parsed(
    *, entities: list[Any], relationships: list[Any], chunk_metrics: dict[str, Any] | None
) -> bool:
    """No entity and no relationship came out, and the parser read no line of the answer.

    ``chunk_metrics["parser_lines_parsed"]`` when the extractor reports it
    (the line parser's ``parsed_lines``); without it, the empty result alone.
    """
    if entities or relationships:
        return False
    parsed = (chunk_metrics if isinstance(chunk_metrics, dict) else {}).get("parser_lines_parsed")
    return not (isinstance(parsed, int) and not isinstance(parsed, bool) and parsed > 0)


def degenerate_kind(
    *,
    output_tokens: int | None,
    entities: list[Any],
    relationships: list[Any],
    chunk_metrics: dict[str, Any] | None,
    chunk_content: str,
    engine_settings: Any,
) -> str | None:
    """Which of :data:`DEGENERATE_KINDS` a try's output is, or None for a usable one.

    The one classification of an output: :func:`degenerate_output_check`
    raises on it, :func:`completion_counters` counts a rescue only for a kept
    output that is none of them. On a group of at least
    ``extraction.empty_output_retry_min_chars`` characters:

    * ``empty_output``: zero output tokens (a provider hiccup: a
      reasoning-mode timeout, a soft stop, a stream reset; not a group with
      nothing in it);
    * ``unparseable``: output tokens, and nothing parsed from them
      (:func:`nothing_parsed`): an answer in JSON, a markdown table or
      another line prefix as much as one whose every line was malformed.
      Until audit wave 8 only malformed prefixed lines counted
      (``parser_lines_dropped``), and the parser counts no line of a format
      it does not know, so such an answer completed with nothing.

    And on any group, ``loop_abort``: the stream's loop guard aborted the
    output (``chunk_metrics["aborted_by_loop"]``). A shorter group with no
    output, or none parsed, is no failure (a heading holds nothing).
    """
    metrics = chunk_metrics if isinstance(chunk_metrics, dict) else {}

    def sized() -> bool:
        """The group is long enough for an empty or unparsed answer to be a failure."""
        return bool(len(chunk_content) >= engine_settings.extraction.empty_output_retry_min_chars)

    if output_tokens == 0 and sized():
        return "empty_output"
    if metrics.get("aborted_by_loop"):
        return "loop_abort"
    if (
        output_tokens
        and nothing_parsed(
            entities=entities, relationships=relationships, chunk_metrics=chunk_metrics
        )
        and sized()
    ):
        return "unparseable"
    return None


def degenerate_output_check(
    *,
    output_tokens: int | None,
    entities: list[Any],
    relationships: list[Any],
    chunk_metrics: dict[str, Any] | None,
    chunk_content: str,
    engine_settings: Any,
    provider: str,
    model: str | None,
    last_attempt: bool,
    duration_ms: int | None = None,
) -> DegenerateOutputError | None:
    """The degenerate-output error a group's try raises, or None to keep its output.

    The one check both extraction paths run on every try's output (the
    queue's chunk handler and the CLI's group loop), so a kind the ladder
    climbs on is never checked at one call site and missed at the other
    (until audit wave 7 the unparseable check had no caller: a group whose
    every line failed to parse completed with nothing on its first try). The
    kind is :func:`degenerate_kind`'s. An empty output is raised on every
    try, the last included: a group that never answers fails and surfaces as
    failed. A loop abort and an unparseable output are the model's answer:
    raised only while a retry is left; the last try keeps what it has (a
    loop abort's partial output; an unparseable group completes empty and
    counts as zero yield, never as rescued: :func:`completion_counters`).

    Args:
        output_tokens: The try's output tokens.
        entities: The entities parsed from it.
        relationships: The relationships parsed from it.
        chunk_metrics: The try's extraction metrics (``aborted_by_loop``,
            ``parser_lines_parsed``).
        chunk_content: The group text the model was sent.
        engine_settings: Engine settings (``extraction.empty_output_retry_min_chars``).
        provider: The provider, for the error.
        model: The model, for the error.
        last_attempt: No retry is left after this try.
        duration_ms: How long the call took, for the empty-output message.

    Returns:
        The error to raise (it climbs the ladder), or None.
    """
    kind = degenerate_kind(
        output_tokens=output_tokens,
        entities=entities,
        relationships=relationships,
        chunk_metrics=chunk_metrics,
        chunk_content=chunk_content,
        engine_settings=engine_settings,
    )
    if kind == "empty_output":
        took = f" after {duration_ms}ms" if duration_ms is not None else ""
        return degenerate_output_error(
            "empty_output",
            provider=provider,
            model=model,
            reason=f"LLM produced empty extraction output for {len(chunk_content)}-char chunk{took}",
        )
    if kind is None or last_attempt:
        return None
    if kind == "loop_abort":
        return degenerate_output_error(
            "loop_abort", provider=provider, model=model, reason="the loop guard aborted the output"
        )
    dropped = (chunk_metrics if isinstance(chunk_metrics, dict) else {}).get(
        "parser_lines_dropped", 0
    )
    return degenerate_output_error(
        "unparseable",
        provider=provider,
        model=model,
        reason=f"nothing of the extraction output parsed ({dropped} lines dropped)",
    )


FALLBACK_TEMPERATURE = 0.3
"""The temperature the second and later retries of a group ask at (recommendations § 4b)."""

REPEAT_PENALTY = 1.15
"""The repeat penalty the second and later rungs send (``options.repeat_penalty`` on Ollama).

At :data:`FALLBACK_TEMPERATURE` a loop can repeat without one (audit wave 5);
the first rung sends none, so it stays as deterministic as the pinned try.
"""

DECODE_FALLBACK_KEY = "decode_fallback"
"""Key of the decoding a retried group asked with, on its metrics rows and stored result."""


@dataclass(frozen=True)
class DecodeFallback:
    """The decoding one retry of a group asks with instead of the pinned one.

    Attributes:
        seed: The seed sent (None when the configured seed is None: the
            provider already picks one per request).
        temperature: The temperature sent.
        attempt: Which rung of the ladder (1 for the first): the degenerate
            outputs the group produced before, never its retry count.
        repeat_penalty: The repeat penalty sent (:data:`REPEAT_PENALTY` from
            rung 2 on), or None for none (the model's default applies).
        seed_honoured: The provider sends the seed
            (``utils.decoding.seed_honoured``). When it does not, rung 1
            would repeat the pinned request, so every rung asks as rung 2
            does (:func:`decode_fallback`).
    """

    seed: int | None
    temperature: float
    attempt: int
    repeat_penalty: float | None = None
    seed_honoured: bool = True

    def record(self) -> dict[str, Any]:
        """What the metrics rows and the stored chunk result record.

        ``{seed, temperature, step}``, plus ``repeat_penalty`` when this rung
        sends one and ``seed_honoured: False`` when the provider sends no
        seed (the seed rung was skipped).
        """
        record: dict[str, Any] = {
            "seed": self.seed,
            "temperature": self.temperature,
            "step": self.attempt,
        }
        if self.repeat_penalty is not None:
            record["repeat_penalty"] = self.repeat_penalty
        if not self.seed_honoured:
            record["seed_honoured"] = False
        return record

    @contextmanager
    def applied(self) -> Iterator[None]:
        """Send this fallback's seed and repeat penalty on every provider call inside the block.

        The temperature goes as the extractor's ``temperature_override`` (it
        names one on every call), so it is not overridden here.
        """
        from chaoscypher_core.utils.decoding import decoding_override

        with decoding_override(seed=self.seed, repeat_penalty=self.repeat_penalty):
            yield


def decode_fallback(
    attempt: int,
    *,
    seed: int | None,
    temperature: float | None,
    seed_honoured: bool = True,
) -> DecodeFallback | None:
    """The decoding rung ``attempt`` of the ladder asks with, or None for the pinned decoding.

    ``seed`` and ``temperature`` are the pinned ones the first try used. Rung
    1: the same temperature, the next seed (deterministic, cacheable, and
    off the degenerate path). Rung 2 and later: :data:`FALLBACK_TEMPERATURE`
    (or the pinned temperature when it is higher) with a further seed and
    :data:`REPEAT_PENALTY`. A provider that sends no seed
    (``seed_honoured`` False: :func:`provider_seed_honoured`) would ask rung
    1 exactly as the pinned try asked, so its ladder starts at the
    temperature rung, recorded on the fallback (until audit wave 8 rung 1
    repeated the same request on every hosted provider).

    Args:
        attempt: The rung (:func:`ladder_step`): 0 until the group has
            produced a degenerate output, ``n`` after ``n`` of them. Never
            the retry count (a transport error retries at the same rung).
        seed: The configured seed.
        temperature: The configured extraction temperature.
        seed_honoured: Whether the extraction provider sends the seed.

    Returns:
        The fallback, or None for rung 0.
    """
    from chaoscypher_core.utils.decoding import resample_decoding

    if attempt <= 0:
        return None
    base = float(temperature or 0.0)
    if attempt == 1 and seed_honoured:
        return DecodeFallback(seed=resample_decoding(seed, attempt), temperature=base, attempt=1)
    return DecodeFallback(
        seed=resample_decoding(seed, attempt),
        temperature=max(base, FALLBACK_TEMPERATURE),
        attempt=attempt,
        repeat_penalty=REPEAT_PENALTY,
        seed_honoured=seed_honoured,
    )


def provider_seed_honoured(llm_settings: Any) -> bool:
    """Whether the extraction's chat provider sends the configured seed (``utils.decoding.seed_honoured``).

    The provider both extraction paths call (``llm.chat_provider``); what
    :func:`decode_fallback` reads to skip the seed rung.
    """
    from chaoscypher_core.utils.decoding import seed_honoured

    return seed_honoured(str(getattr(llm_settings, "chat_provider", "") or "") or None)


def ladder_step(record: dict[str, Any] | None) -> int:
    """The rung a group's next try asks at, from the fallback its last try recorded (0 for none).

    ``record`` is a task row's ``decode_fallback`` (the ``{seed, temperature,
    step, ...}`` of :meth:`DecodeFallback.record`); a row written before the step
    was recorded reads as rung 1 when it carries a fallback at all.
    """
    if not isinstance(record, dict) or not record:
        return 0
    step = record.get("step")
    return step if isinstance(step, int) and not isinstance(step, bool) and step >= 0 else 1


def next_ladder_step(step: int, failure: BaseException | None) -> int:
    """The rung after a try at ``step`` that failed with ``failure``: up only on degenerate output."""
    return step + 1 if failure is not None and is_degenerate_failure(failure) else step


@contextmanager
def decoding_applied(fallback: DecodeFallback | None) -> Iterator[None]:
    """Send ``fallback``'s seed and repeat penalty on every provider call inside the block.

    Nothing for a first try (rung 0): its calls keep the pinned decoding.
    """
    if fallback is None:
        yield
        return
    with fallback.applied():
        yield


def record_decode_fallback(
    fallback: DecodeFallback | None,
    metrics_collector: Any,
    chunk_metrics: dict[str, Any] | None,
    *,
    first_attempt: int = 0,
) -> None:
    """Record ``fallback`` on the call's metrics rows and the chunk's result, in place.

    The one recording both extraction paths make: the attempt rows the call
    added to the collector, from position ``first_attempt`` on
    (``llm_call_metrics.decode_fallback``; a collector shared by several
    groups keeps the others' rows as they are), and the chunk metrics the
    stored result is built from (:data:`DECODE_FALLBACK_KEY`). A first try
    records nothing.
    """
    if fallback is None:
        return
    record = fallback.record()
    for attempt in (getattr(metrics_collector, "attempts", None) or [])[first_attempt:]:
        attempt[DECODE_FALLBACK_KEY] = dict(record)
    if chunk_metrics is not None:
        chunk_metrics[DECODE_FALLBACK_KEY] = dict(record)


def zero_yield(*, output_tokens: int | None, entities: list[Any], relationships: list[Any]) -> bool:
    """A group whose model produced tokens but kept no entity and no relationship.

    Counted on the source (``QualityCounter.LLM_CHUNKS_ZERO_YIELD``): unlike
    an empty output it is no failure (a passage may hold nothing), but a run
    of them says the output is being thrown away downstream.
    """
    return bool(output_tokens) and not entities and not relationships


def completion_counters(
    *,
    output_tokens: int | None,
    entities: list[Any],
    relationships: list[Any],
    chunk_metrics: dict[str, Any] | None,
    degenerate: str | None,
) -> list[QualityCounter]:
    """The quality counters a group that completed bumps on its source, one each.

    The one rule both extraction paths apply to a kept output:
    ``LLM_CHUNKS_ZERO_YIELD`` for :func:`zero_yield`, and
    ``LLM_CHUNKS_RESCUED_BY_FALLBACK`` when the try that produced it ran on a
    rung of the ladder (its chunk metrics carry :data:`DECODE_FALLBACK_KEY`,
    which :func:`record_decode_fallback` sets for rung 1 and up only) and its
    output is no longer degenerate (``degenerate``: the kept try's
    :func:`degenerate_kind`, read before the caller consumes its metrics).
    The last try keeps a loop abort or an unparseable output; until audit
    wave 8 a group unparseable on every rung counted as rescued.
    """
    from chaoscypher_core.services.quality.counters import QualityCounter

    counters: list[QualityCounter] = []
    if zero_yield(output_tokens=output_tokens, entities=entities, relationships=relationships):
        counters.append(QualityCounter.LLM_CHUNKS_ZERO_YIELD)
    if degenerate is None and (chunk_metrics or {}).get(DECODE_FALLBACK_KEY):
        counters.append(QualityCounter.LLM_CHUNKS_RESCUED_BY_FALLBACK)
    return counters


def max_chunk_retries(engine_settings: Any) -> int:
    """How many times a failed group is extracted again (``retries.extraction_chunk_max``)."""
    return int(engine_settings.retries.extraction_chunk_max)


__all__ = [
    "DECODE_FALLBACK_KEY",
    "DEGENERATE_KINDS",
    "FALLBACK_TEMPERATURE",
    "REPEAT_PENALTY",
    "DecodeFallback",
    "DegenerateOutputError",
    "completion_counters",
    "decode_fallback",
    "decoding_applied",
    "degenerate_kind",
    "degenerate_output_check",
    "degenerate_output_error",
    "is_degenerate_failure",
    "is_retryable_chunk_failure",
    "ladder_step",
    "max_chunk_retries",
    "next_ladder_step",
    "nothing_parsed",
    "provider_seed_honoured",
    "record_decode_fallback",
    "zero_yield",
]
