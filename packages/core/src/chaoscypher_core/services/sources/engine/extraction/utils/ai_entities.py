# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""AI Entity Extraction.

Extracts entities and relationships from text using AI with line-based output.
Uses domain analyzers to provide domain-specific extraction guidance.

Pipeline architecture (2-pass per chunk):
- Pass 1: Extract entities (E|) and properties (P|) from text
- Pass 2: Extract relationships (R|) using filtered entity list from pass 1

No tool calling - just direct LLM output parsed with line_parser.

Helper logic is split into focused modules:
- prompts: All LLM prompt templates
- entity_cleaner: Cleanup, validation, and rename functions
- quality_analyzer: Density statistics and formatting helpers
- type_inferencer: Domain detection and inference
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any, ClassVar

import structlog

from chaoscypher_core.constants import SOURCE_ROW_FILTERING_MODE
from chaoscypher_core.exceptions import OperationError


if TYPE_CHECKING:
    from chaoscypher_core.ports.llm import LLMProviderPort
    from chaoscypher_core.services.sources.engine.extraction.domains.config_schema import (
        ExclusionRule,
    )
    from chaoscypher_core.services.sources.engine.extraction.utils.known_entities import (
        KnownEntities,
    )
    from chaoscypher_core.settings import EngineSettings

from chaoscypher_core.services.quality.counters import (
    QualityCounter,
    increment_quality_counter,
)
from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
    DEFAULT_NAME_CONVENTIONS,
    EXCLUDED_NAME_KEY,
    LOW_PLAUSIBILITY_KEY,
    UNPLACED_TYPE_KEY,
    NameConventions,
    pipeline_flag,
)
from chaoscypher_core.services.sources.engine.extraction.domains.packs import packs_label
from chaoscypher_core.services.sources.engine.extraction.extraction_plan import (
    PROMPT_KWARG_NAMES,
)
from chaoscypher_core.services.sources.engine.extraction.utils.entity_cleaner import (
    apply_properties_to_entities,
    filter_excluded_entities,
    filter_implausible_entities,
    validate_relationships,
)
from chaoscypher_core.services.sources.engine.extraction.utils.line_parser import (
    parse_extraction_output,
)
from chaoscypher_core.services.sources.engine.extraction.utils.offered_types import (
    OfferedType,
    offered_in_prompt,
    render_offered_types,
    stamp_offered_types,
)
from chaoscypher_core.services.sources.engine.extraction.utils.prompts import (
    ENTITY_HARVEST_TEMPLATE,
    EXCLUSIONS_HEADER,
    EXTRACTION_RULES_TEMPLATE,
    RELATIONSHIP_HARVEST_TEMPLATE,
    SYSTEM_PROMPT,
    declared_type_names,
    render_entity_format_example,
    render_relationship_format_example,
    strict_type_instruction,
)
from chaoscypher_core.services.sources.engine.extraction.utils.quality_analyzer import (
    calculate_density_stats,
)
from chaoscypher_core.services.sources.engine.extraction.utils.relation_vocabulary import (
    EMPTY_RELATION_VOCABULARY,
    RelationVocabulary,
)
from chaoscypher_core.services.sources.engine.extraction.utils.sentence_splitter import (
    merge_evidence,
)
from chaoscypher_core.services.sources.engine.extraction.utils.type_inferencer import (
    detect_domain,
)
from chaoscypher_core.services.sources.engine.extraction.utils.type_rescue import (
    FLOOR_TYPE_VOCABULARY,
    TypeVocabulary,
    rescue_invalid_entity_types,
)
from chaoscypher_core.utils.logging.privacy import log_failure
from chaoscypher_core.utils.tokens import estimate_message_tokens, estimate_tokens


logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class CallLLMResult:
    """Result of one streaming LLM extraction call.

    Combines the original ``(content, input_tokens, output_tokens)``
    triple with two observability signals:

    - ``finish_reason``: stable token from the provider's done chunk
      (``stop`` / ``length`` / ``content_filter`` / ``tool_calls`` /
      ``error`` / ``unknown``). Surfaces token-budget truncation that
      used to be invisible.
    - ``aborted_by_loop``: True when ``_StreamLoopDetector`` cut the
      stream short on a degenerate pattern (out-of-bounds indices,
      repeating types, entity-count cap exceeded). Lets the chunk
      handler bump per-source counters even though the extraction
      "succeeded" structurally.

    Attributes:
        content: Concatenated text content from the stream.
        input_tokens: Prompt tokens reported by the provider (or
            estimated when the provider didn't report).
        output_tokens: Completion tokens reported by the provider (or
            estimated).
        finish_reason: Normalized provider finish reason.
        aborted_by_loop: True when the loop detector aborted the stream.
    """

    content: str
    input_tokens: int
    output_tokens: int
    finish_reason: str
    aborted_by_loop: bool


class _StreamLoopDetector:
    """Detects degenerate patterns in streamed extraction output.

    Tracks entity/relationship/property line counts and streaks,
    aborting when thresholds are exceeded to save GPU time.

    Args:
        extraction_cfg: Extraction settings with loop detection thresholds.
        max_entity_count_override: Domain-specific entity count override.
        known_entity_count: Entities the stream can reference that it does
            not write itself: pass 2 streams ``R|`` lines over pass 1's
            entities, and every relationship check (count cap, bounds, same
            pair, repeating source type, invalid rate) is gated on
            ``entity_count > 0`` — a fresh detector with 0 skipped all of
            them for the whole relationship pass (section-audit 2026-10-12).
    """

    def __init__(
        self,
        extraction_cfg: Any,
        max_entity_count_override: int | None = None,
        known_entity_count: int = 0,
    ) -> None:
        """Initialise detector state and bind loop-detection thresholds."""
        self.entity_count = known_entity_count
        self.relationship_count = 0
        self.aborted = False

        self._oob_streak = 0
        self._oob_total = 0
        self._last_source_type: tuple[str, str] | None = None
        self._source_type_streak = 0
        self._last_prop_key: tuple[str, str] | None = None
        self._prop_key_streak = 0
        self._source_target_pair_counts: dict[tuple[int, int], int] = {}

        self._max_oob = extraction_cfg.loop_max_out_of_bounds
        self._max_source_type_repeat = extraction_cfg.loop_max_source_type_repeat
        self._max_prop_repeat = extraction_cfg.loop_max_property_repeat
        self._max_entity_count = max_entity_count_override or extraction_cfg.loop_max_entity_count
        self._max_relationship_count = int(
            self._max_entity_count * extraction_cfg.loop_max_relationship_multiplier
        )
        self._max_same_pair = extraction_cfg.loop_max_same_pair
        # Invalid-rate detector — catches the failure mode where the
        # model emits a high fraction of out-of-bounds relationship
        # lines but interleaves them with valid ones, so the
        # consecutive-streak detector never trips. Real-world example:
        # 336 invalid out of 352 total at extraction time, no streak
        # ever reached 3 in a row.
        self._invalid_rate_warmup = extraction_cfg.loop_invalid_relationship_rate_warmup
        self._invalid_rate_threshold = extraction_cfg.loop_invalid_relationship_rate_threshold

    def check_line(self, line: str, content_length: int) -> bool:
        """Check a completed output line for loop patterns.

        Updates internal counters and sets ``self.aborted`` if a
        degenerate pattern is detected.

        Args:
            line: A stripped, non-empty output line (e.g. ``"E|0|Person|..."``).
            content_length: Current total content length for logging context.

        Returns:
            True if the stream should be aborted, False otherwise.

        """
        if line.startswith("E|"):
            return self._check_entity_line(content_length)
        if line.startswith("P|"):
            return self._check_property_line(line, content_length)
        if line.startswith("R|") and self.entity_count > 0:
            return self._check_relationship_line(line, content_length)
        return False

    def _check_entity_line(self, content_length: int) -> bool:
        """Check entity line for count cap violation.

        Args:
            content_length: Current total content length for logging.

        Returns:
            True if the entity count cap is exceeded.

        """
        self.entity_count += 1
        self._oob_streak = 0
        self._last_source_type = None
        self._source_type_streak = 0
        self._last_prop_key = None
        self._prop_key_streak = 0

        if self.entity_count >= self._max_entity_count:
            logger.warning(
                "stream_loop_detected_aborting",
                reason="entity_count_exceeded",
                entity_count=self.entity_count,
                max_entity_count=self._max_entity_count,
                content_length=content_length,
            )
            self.aborted = True
            return True
        return False

    def _check_property_line(self, line: str, content_length: int) -> bool:
        """Check property line for repeating key pattern.

        Args:
            line: The property output line.
            content_length: Current total content length for logging.

        Returns:
            True if repeating property key threshold is exceeded.

        """
        parts = line.split("|", 3)
        if len(parts) >= 3:
            pk = (parts[1], parts[2])
            if pk == self._last_prop_key:
                self._prop_key_streak += 1
                if self._prop_key_streak >= self._max_prop_repeat:
                    logger.warning(
                        "stream_loop_detected_aborting",
                        reason="repeating_property_key",
                        pattern=f"P|{pk[0]}|{pk[1]}",
                        streak=self._prop_key_streak,
                        content_length=content_length,
                    )
                    self.aborted = True
                    return True
            else:
                self._last_prop_key = pk
                self._prop_key_streak = 1
        return False

    def _check_relationship_line(self, line: str, content_length: int) -> bool:  # noqa: PLR0911
        """Check relationship line for count caps, OOB indices, and repeats.

        Args:
            line: The relationship output line.
            content_length: Current total content length for logging.

        Returns:
            True if any relationship loop pattern is detected.

        """
        self.relationship_count += 1

        if self.relationship_count >= self._max_relationship_count:
            logger.warning(
                "stream_loop_detected_aborting",
                reason="relationship_count_exceeded",
                relationship_count=self.relationship_count,
                max_relationship_count=self._max_relationship_count,
                content_length=content_length,
            )
            self.aborted = True
            return True

        parts = line.split("|")
        if len(parts) < 4:
            return False

        try:
            src = int(parts[1])
            tgt = int(parts[2])
            rel_type = parts[3]
        except (ValueError, IndexError):  # fmt: skip
            return False

        # Same source-target pair cap (catches alternating types)
        pair = (src, tgt)
        self._source_target_pair_counts[pair] = self._source_target_pair_counts.get(pair, 0) + 1
        if self._source_target_pair_counts[pair] >= self._max_same_pair:
            logger.warning(
                "stream_loop_detected_aborting",
                reason="same_pair_exceeded",
                src=src,
                tgt=tgt,
                pair_count=self._source_target_pair_counts[pair],
                content_length=content_length,
            )
            self.aborted = True
            return True

        # Out-of-bounds index detection — both consecutive-streak and
        # overall-rate checks. The streak catches a runaway block of
        # invalid lines; the rate catches the case where invalid
        # lines are interleaved with valid ones (the model "knows
        # the format" but is hallucinating indices) so the streak
        # never reaches the threshold.
        if src >= self.entity_count or tgt >= self.entity_count:
            self._oob_streak += 1
            self._oob_total += 1
            if self._oob_streak >= self._max_oob:
                logger.warning(
                    "stream_loop_detected_aborting",
                    reason="out_of_bounds_indices",
                    src=src,
                    tgt=tgt,
                    entity_count=self.entity_count,
                    content_length=content_length,
                )
                self.aborted = True
                return True
            if (
                self.relationship_count >= self._invalid_rate_warmup
                and (self._oob_total / self.relationship_count) >= self._invalid_rate_threshold
            ):
                logger.warning(
                    "stream_loop_detected_aborting",
                    reason="high_invalid_index_rate",
                    invalid_count=self._oob_total,
                    total_relationships_seen=self.relationship_count,
                    invalid_rate=round(self._oob_total / self.relationship_count, 3),
                    threshold=self._invalid_rate_threshold,
                    entity_count=self.entity_count,
                    content_length=content_length,
                )
                self.aborted = True
                return True
        else:
            self._oob_streak = 0

        # Same (source, type) repeating
        st = (str(src), rel_type)
        if st == self._last_source_type:
            self._source_type_streak += 1
            if self._source_type_streak >= self._max_source_type_repeat:
                logger.warning(
                    "stream_loop_detected_aborting",
                    reason="repeating_source_type",
                    pattern=f"{st[0]}|*|{st[1]}",
                    content_length=content_length,
                )
                self.aborted = True
                return True
        else:
            self._last_source_type = st
            self._source_type_streak = 1

        return False


async def _consume_extraction_stream(
    response: Any,
    detector: _StreamLoopDetector,
    *,
    on_chunk: Callable[[], Awaitable[None] | None] | None = None,
) -> tuple[str, int, int, str, bool]:
    """Consume an LLM streaming response with loop detection.

    Iterates over stream chunks, accumulates content, and checks each
    completed line against the ``detector`` for degenerate patterns.
    Aborts early if a loop is detected. Raises on stream-level errors.

    The return shape carries two observability signals: the normalized
    provider ``finish_reason`` and an ``aborted`` flag derived from
    ``detector.aborted``. The trailing partial line (no terminating
    newline) is flushed through the detector so the last entity /
    relationship line is not silently dropped when the model tops out
    mid-token.

    Args:
        response: Async-iterable stream of chunk dicts from the LLM provider.
        detector: Loop detector that inspects completed output lines.
        on_chunk: Optional best-effort callback fired on every received
            content chunk so callers can use stream activity (not
            wall-clock time) as the liveness signal for SourceRecovery.
            The 'done' chunk is metadata — the callback is NOT invoked
            for it. Errors raised by the callback are logged at WARNING
            and suppressed so a flaky heartbeat never aborts extraction.
            The callback may be ``async`` (return an awaitable), in which
            case it is awaited — callers that write to SQLite use this to
            offload the write with ``asyncio.to_thread`` instead of
            blocking the event loop.

    Returns:
        Tuple of ``(content, input_tokens, output_tokens, finish_reason,
        aborted)``. ``finish_reason`` is one of the stable tokens from
        :func:`normalize_finish_reason` and defaults to ``"unknown"``
        when the stream ended without a done chunk. ``aborted`` mirrors
        ``detector.aborted``.

    Raises:
        LLMModelError: If the stream reports a model-not-found error.
        LLMError: If the stream reports any other error.

    """
    content = ""
    input_tokens = 0
    output_tokens = 0
    line_buffer = ""
    finish_reason = "unknown"

    stream: Any = response
    async for chunk in stream:
        chunk_type = chunk.get("type", "")

        if chunk_type == "content":
            delta = chunk.get("delta", "")
            content += delta

            if on_chunk is not None:
                try:
                    beat = on_chunk()
                    if beat is not None:
                        await beat
                except Exception as exc:
                    logger.warning(
                        "extraction_stream_on_chunk_failed",
                        error_type=type(exc).__name__,
                        error_message=str(exc),
                    )

            line_buffer += delta
            while "\n" in line_buffer:
                line, line_buffer = line_buffer.split("\n", 1)
                line = line.strip()
                if not line:
                    continue

                if detector.check_line(line, len(content)):
                    break

            if detector.aborted:
                break

        elif chunk_type == "error":
            # The one stream classifier (``utils.stream_errors``): a 404 or a
            # missing model is fatal, a 429/503/timeout/dropped connection retryable,
            # the same way in the chat loop and here (until audit wave 5 this read
            # "not found" in the text, which "LLM streaming failed" never contains).
            from chaoscypher_core.utils.stream_errors import error_from_stream_chunk

            error = error_from_stream_chunk(chunk)
            logger.error(
                "llm_extraction_stream_error",
                error=error.message,
                error_code=error.code,
                retryable=error.is_retryable,
            )
            raise error

        elif chunk_type == "done":
            usage = chunk.get("usage", {})
            input_tokens = usage.get("prompt_tokens", 0)
            output_tokens = usage.get("completion_tokens", 0)
            content = chunk.get("content", content)
            finish_reason = chunk.get("finish_reason", "unknown") or "unknown"
            break

    # Flush trailing partial line. Models that hit ``finish_reason='length'``
    # mid-token leave the last entity / relationship without a newline; if we
    # don't run it through the detector here it never reaches the parser
    # downstream. Skip when the detector already aborted (the partial line
    # belongs to the runaway pattern that triggered the abort).
    if not detector.aborted:
        trailing = line_buffer.strip()
        if trailing:
            detector.check_line(trailing, len(content))

    return content, input_tokens, output_tokens, finish_reason, detector.aborted


async def replay_harvest_output(
    text: str,
    extraction_cfg: Any,
    max_entity_count_override: int | None = None,
    known_entity_count: int = 0,
) -> tuple[str, str, bool]:
    """Run a complete harvest answer through the stream loop detector.

    For output that did not arrive over a provider stream (an MCP client's
    answer), this replays ``text`` one line per chunk through
    :func:`_consume_extraction_stream` with the same detector
    :meth:`AIEntityExtractor.call_llm` uses, so a runaway answer is cut where
    the streaming path would have cut it (at the end of the offending line)
    and reported the same way.

    ``known_entity_count`` is pass 1's entity count when ``text`` is a pass-2
    (relationship) answer, so its ``R|`` lines are checked.

    Returns:
        ``(content, finish_reason, aborted_by_loop)``: the content the
        streaming path would have kept, ``"stop"`` for a complete answer or
        ``"unknown"`` for an aborted one (an aborted stream never sees the
        provider's done chunk), and the abort flag.
    """

    async def _chunks() -> Any:
        """Yield each line as a content delta, then a done chunk."""
        for line in text.splitlines(keepends=True):
            yield {"type": "content", "delta": line}
        yield {"type": "done", "usage": {}, "finish_reason": "stop"}

    detector = _StreamLoopDetector(
        extraction_cfg=extraction_cfg,
        max_entity_count_override=max_entity_count_override,
        known_entity_count=known_entity_count,
    )
    content, _in, _out, finish_reason, aborted = await _consume_extraction_stream(
        _chunks(), detector
    )
    return content, finish_reason, aborted


def combine_finish_reasons(pass1: str, pass2: str) -> str:
    """Combine the two passes' finish reasons into the chunk's.

    ``"length"`` in either pass wins (any truncation truncated the chunk);
    otherwise pass 1's reason unless it is ``"stop"``, then pass 2's.
    """
    if "length" in (pass1, pass2):
        return "length"
    return pass1 if pass1 != "stop" else pass2


def format_raw_harvest_response(pass1_content: str, pass2_content: str) -> str:
    """Join both passes' raw answers the way extraction metrics record them."""
    return (
        f"=== PASS 1 (Entities) ===\n{pass1_content}\n\n"
        f"=== PASS 2 (Relationships) ===\n{pass2_content}"
    )


@dataclass(frozen=True)
class HarvestPrompts:
    """The pass-1 prompts for one chunk and the sentences they number.

    Attributes:
        system_prompt: System prompt both passes are sent under.
        entity_prompt: Pass-1 (entity harvest) user prompt.
        sentences: The chunk split into sentences; ``S<n>`` refers to
            ``sentences[n - 1]``.
        numbered_text: ``sentences`` formatted as the prompts show them.
    """

    system_prompt: str
    entity_prompt: str
    sentences: list[str]
    numbered_text: str


@dataclass(frozen=True)
class PlanPrompts:
    """Both prompts of one chunk, rendered from a plan (:meth:`AIEntityExtractor.render_chunk_prompts`).

    Attributes:
        system_prompt: System prompt both passes are sent under.
        numbered_text: The chunk's sentences as the prompts number them.
        entity_prompt: Pass-1 (entity harvest) user prompt.
        relationship_prompt: Pass-2 (relationship harvest) user prompt.
    """

    system_prompt: str
    numbered_text: str
    entity_prompt: str
    relationship_prompt: str


@dataclass(frozen=True)
class HarvestLimits:
    """Per-chunk filtering configuration resolved before pass 1.

    Attributes:
        filtering_config: Resolved ``FilteringConfig``.
        max_entity_count: Entity-count cap for the stream loop detector.
        min_alias_length: Minimum alias length for the line parser.
        relation_vocabulary: The domain's relation vocabulary the pass-2
            evidence filter reads (the empty one for a domain without any).
        name_conventions: The domain's naming conventions both evidence
            filters name endpoints by (titles, surname forms; the universal
            ones for no domain).
        type_vocabulary: The plan's packs, subtypes and aliases the type
            rescue reads (the core types and the mapping table alone for no
            plan).
    """

    filtering_config: Any
    max_entity_count: int
    min_alias_length: int
    relation_vocabulary: RelationVocabulary = EMPTY_RELATION_VOCABULARY
    name_conventions: NameConventions = DEFAULT_NAME_CONVENTIONS
    type_vocabulary: TypeVocabulary = FLOOR_TYPE_VOCABULARY


@dataclass
class EntityHarvest:
    """Pass-1 output after parsing and the between-pass entity filters.

    Attributes:
        entities: Entities that survived filtering (what pass 2 sees).
        property_count: ``P|`` lines parsed and applied.
        evidence_stats: Evidence/type-rescue statistics; pass 2 adds to it.
        filtering_log: ``FilteringLog`` recording every removal.
        parser_stats: Parser counters (``dropped_lines``) shared by both passes.
    """

    entities: list[dict[str, Any]]
    property_count: int
    evidence_stats: dict[str, Any]
    filtering_log: Any
    parser_stats: dict[str, int]


@dataclass
class HarvestParse:
    """Both passes' raw output turned into what ``extract_single_chunk`` returns.

    Attributes:
        entities: Final entities.
        relationships: Final relationships (empty when pass 2 did not run).
        invalid_relationship_count: Relationships dropped as structurally invalid.
        parser_lines_dropped: Output lines the parser could not read, both passes.
        parser_lines_parsed: Output lines the parser read, both passes.
        evidence_stats: Evidence statistics across both passes.
        filtering_log: ``FilteringLog`` recording every removal.
        relationship_prompt: The pass-2 prompt built from ``entities``, or
            None when there were no entities and pass 2 was skipped.
    """

    entities: list[dict[str, Any]]
    relationships: list[dict[str, Any]]
    invalid_relationship_count: int
    parser_lines_dropped: int
    evidence_stats: dict[str, Any]
    filtering_log: Any
    relationship_prompt: str | None
    parser_lines_parsed: int = 0


def _resolve_chunk_filtering_config(
    *,
    filtering_config: Any,
    filtering_mode: str | None,
    domain_limits: dict[str, Any],
    extraction_cfg: Any,
    evidence_validation_mode: str | None,
) -> Any:
    """Resolve the filtering configuration from presets and overrides.

    If a pre-resolved FilteringConfig is provided, returns it as-is.
    Otherwise resolves ``filtering_mode`` as the preset selector and
    ``domain_limits`` as field overrides on top of that preset.

    Args:
        filtering_config: Pre-resolved config, or None to resolve.
        filtering_mode: Preset selector. When None, falls back to
            ``extraction_cfg.extraction_filtering_mode``. The selector is
            distinct from ``domain_limits`` — it must never be passed as
            an override or ``resolve_filtering_config`` will reject it.
        domain_limits: Domain ``FilteringConfig`` field overrides.
        extraction_cfg: Extraction settings from engine config.
        evidence_validation_mode: Per-domain evidence validation override.

    Returns:
        Resolved FilteringConfig instance.
    """
    if filtering_config is not None:
        return filtering_config

    from chaoscypher_core.services.sources.engine.extraction.utils.filtering_config import (
        resolve_filtering_config,
    )

    _filtering_mode = filtering_mode or getattr(
        extraction_cfg, "extraction_filtering_mode", SOURCE_ROW_FILTERING_MODE
    )
    config = resolve_filtering_config(
        mode=str(_filtering_mode),
        domain_overrides=dict(domain_limits) if domain_limits else None,
    )
    if evidence_validation_mode:
        config.evidence_validation_mode = evidence_validation_mode
    return config


def _resolve_slider_field(
    field: str,
    *,
    filtering_config: Any,
    domain_limits: dict[str, Any],
    extraction_cfg: Any,
) -> int:
    """Resolve an integer slider field a domain also tunes (the loop cap, the alias length).

    Precedence (highest first): the domain ``extraction_limits`` value, then
    ``filtering_config.<field>`` (the slider-driven value), then
    ``extraction_cfg.<field>`` (call sites with no FilteringConfig in
    scope). The plan's limits already hold the effective value: a domain's
    tuning moved with a slider the user moved (``follow_slider_limits``,
    applied once in ``build_extraction_plan``), so the resolved
    FilteringConfig and this agree for every reader.
    """
    if field in domain_limits:
        return int(domain_limits[field])
    if filtering_config is not None:
        value = getattr(filtering_config, field, None)
        if value is not None:
            return int(value)
    return int(getattr(extraction_cfg, field))


def _resolve_loop_max_entity_count(
    *,
    filtering_config: Any,
    domain_limits: dict[str, Any],
    extraction_cfg: Any,
) -> int:
    """Resolve the per-chunk entity-count cap for the streaming loop detector.

    :func:`_resolve_slider_field` for ``loop_max_entity_count``. The plan
    has already moved a domain's cap with the slider the user chose: as
    tuned at the default mode, raised to at least a looser mode's cap,
    lowered to at most a stricter one's (the technical pack's 30: maximum
    25, strict 30, balanced 30, lenient 75, minimal 100, unfiltered 200).

    Args:
        filtering_config: Resolved FilteringConfig (may be None at deep
            internal call sites).
        domain_limits: Domain extraction limit overrides.
        extraction_cfg: Extraction settings instance.

    Returns:
        Integer max entity count for the per-chunk loop detector.
    """
    return _resolve_slider_field(
        "loop_max_entity_count",
        filtering_config=filtering_config,
        domain_limits=domain_limits,
        extraction_cfg=extraction_cfg,
    )


def _resolve_minimum_alias_length(
    *,
    filtering_config: Any,
    domain_limits: dict[str, Any],
    extraction_cfg: Any,
) -> int:
    """Resolve the minimum alias length for the line parser.

    :func:`_resolve_slider_field` for ``minimum_alias_length``, as
    :func:`_resolve_loop_max_entity_count`.

    Args:
        filtering_config: Resolved FilteringConfig (may be None).
        domain_limits: Domain extraction limit overrides.
        extraction_cfg: Extraction settings instance.

    Returns:
        Integer minimum alias length.
    """
    return _resolve_slider_field(
        "minimum_alias_length",
        filtering_config=filtering_config,
        domain_limits=domain_limits,
        extraction_cfg=extraction_cfg,
    )


# Sentinel placeholders used when capturing the entity/relationship prompt
# *templates* for UI display (Processing tab -> AI prompts). The real
# extraction substitutes the chunk's numbered sentences / pass-1 entity list
# at runtime; for display we keep these markers so operators see the reusable
# template instead of one chunk's baked-in text. Kept ASCII + distinctive
# (``[[ ... ]]``) so the frontend can highlight them.
PROMPT_CHUNK_TEXT_PLACEHOLDER = (
    "[[ CHUNK TEXT - the chunk's sentences are inserted here at runtime ]]"
)
PROMPT_PASS1_ENTITIES_PLACEHOLDER = (
    "[[ PASS-1 ENTITIES - the entities found in pass 1 are inserted here ]]"
)
PROMPT_KNOWN_ENTITIES_PLACEHOLDER = (
    "[[ KNOWN ENTITIES - the names earlier groups of the source gave are inserted here ]]"
)
"""The pass-1 template's mark for the group's KNOWN ENTITIES block: per group (group 1 and
``known_entities_limit`` 0 get none), so the template shows its slot, as it renders, rather
than one group's names. Never the block's header (``KNOWN_ENTITIES_HEADER``)."""


def _build_entity_prompt(
    *,
    template: str,
    numbered_text: str,
    node_templates_formatted: str,
    entity_exclusions: list[ExclusionRule] | None,
    strict_entity_types: bool,
    entity_guidance: str | None,
    entity_examples: str | None,
    known_entities: str = "",
) -> str:
    """Build the pass 1 entity extraction prompt.

    Formats the harvest template with numbered sentences and appends
    optional exclusion rules, strict type instructions, domain guidance,
    and examples.

    Args:
        template: Entity harvest prompt template.
        numbered_text: Numbered sentences text.
        node_templates_formatted: Formatted node template descriptions.
        entity_exclusions: Domain-specific exclusion rules. Each rule is
            rendered to its LLM-facing form via ``ExclusionRule.as_prompt_text()``.
        strict_entity_types: Whether to enforce strict type matching.
        entity_guidance: Additional entity guidance text.
        entity_examples: Entity extraction examples.
        known_entities: The group's KNOWN ENTITIES block
            (:meth:`KnownEntities.block`), rendered between the sentences and
            the output format; "" renders nothing, leaving no blank slot.

    Returns:
        Formatted entity extraction prompt.
    """
    exclusion_lines = ""
    if entity_exclusions:
        exclusion_lines = (
            EXCLUSIONS_HEADER
            + "\n"
            + "\n".join(f"  * {rule.as_prompt_text()}" for rule in entity_exclusions)
        )

    strict_instruction = ""
    if strict_entity_types:
        strict_instruction = strict_type_instruction(node_templates_formatted)

    prompt = template.format(
        numbered_sentences=numbered_text,
        node_templates=node_templates_formatted,
        entity_exclusions=exclusion_lines,
        strict_type_instruction=strict_instruction,
        entity_format_example=render_entity_format_example(node_templates_formatted),
        known_entities=f"{known_entities}\n\n" if known_entities else "",
    )

    if entity_guidance:
        prompt = f"{prompt}\n\nAdditional guidance:\n{entity_guidance}"
    if entity_examples:
        prompt = f"{prompt}\n\n{entity_examples}"
    return prompt


def _build_relationship_prompt(
    *,
    template: str,
    numbered_sentences: str,
    entity_list: str,
    max_entity_index: int | str,
    edge_templates_formatted: str,
    relationship_guidance: str | None,
    relationship_examples: str | None,
) -> str:
    """Build the pass 2 relationship extraction prompt.

    Mirrors the inline formatting previously done in
    ``_extract_relationships`` so the same code path produces both the real
    per-chunk prompt and the placeholder *template* captured for UI display
    (where ``numbered_sentences``/``entity_list`` are sentinel markers rather
    than a specific chunk's text).

    Args:
        template: Relationship harvest prompt template.
        numbered_sentences: Numbered sentences text (or a placeholder marker).
        entity_list: Serialized pass-1 entity list (or a placeholder marker).
        max_entity_index: Highest valid entity index (or ``"N"`` for templates).
        edge_templates_formatted: Formatted edge template descriptions.
        relationship_guidance: Additional relationship guidance text.
        relationship_examples: Relationship extraction examples.

    Returns:
        Formatted relationship extraction prompt.
    """
    prompt = template.format(
        numbered_sentences=numbered_sentences,
        entity_list=entity_list,
        max_entity_index=max_entity_index,
        edge_templates=edge_templates_formatted,
        relationship_format_example=render_relationship_format_example(edge_templates_formatted),
    )
    if relationship_guidance:
        prompt = f"{prompt}\n\nAdditional guidance:\n{relationship_guidance}"
    if relationship_examples:
        prompt = f"{prompt}\n\n{relationship_examples}"
    return prompt


_ENTITY_PASS_INPUTS: tuple[str, ...] = (
    "node_templates_formatted", "entity_exclusions", "strict_entity_types", "entity_guidance",
    "entity_examples",
)  # fmt: skip
"""The prompt inputs pass 1 renders (:func:`_build_entity_prompt` takes them by these names)."""

_RELATIONSHIP_PASS_INPUTS: tuple[str, ...] = (
    "edge_templates_formatted", "relationship_guidance", "relationship_examples",
)  # fmt: skip
"""The prompt inputs pass 2 renders (:func:`_build_relationship_prompt` takes them by these names)."""


def named_values(values: Mapping[str, Any], names: Iterable[str]) -> dict[str, Any]:
    """``{name: values[name]}`` for ``names``: a method's prompt-input parameters, from ``locals()``.

    The parameters carry the plan's key names, so a caller passes them on by
    name rather than mapping each one by hand; a name that is not a
    parameter raises ``KeyError``.
    """
    return {name: values[name] for name in names}


def _pass_inputs(chunk_kwargs: Mapping[str, Any], names: tuple[str, ...]) -> dict[str, Any]:
    """The prompt inputs one pass renders, checked against :data:`PROMPT_KWARG_NAMES`.

    Raises:
        OperationError: A prompt input the plan supplies is rendered by neither
            pass, or one of this pass's inputs is missing from ``chunk_kwargs``.
    """
    unrendered = PROMPT_KWARG_NAMES - {*_ENTITY_PASS_INPUTS, *_RELATIONSHIP_PASS_INPUTS}
    if unrendered:
        msg = f"prompt inputs the plan supplies but the prompts do not render: {sorted(unrendered)}"
        raise OperationError(msg, operation="render_extraction_pass")
    missing = [name for name in names if name not in chunk_kwargs]
    if missing:
        msg = f"chunk kwargs lack the prompt inputs {missing}"
        raise OperationError(msg, operation="render_extraction_pass")
    return {name: chunk_kwargs[name] for name in names}


def render_entity_pass(
    chunk_kwargs: Mapping[str, Any],
    *,
    numbered_text: str,
    template: str = ENTITY_HARVEST_TEMPLATE,
    known_entities: str = "",
) -> str:
    """The pass-1 prompt a plan's prompt inputs render to over ``numbered_text``.

    ``known_entities`` is the group's KNOWN ENTITIES block: per group, not a
    plan prompt input, so it arrives beside ``numbered_text`` ("" for none).
    """
    return _build_entity_prompt(
        template=template,
        numbered_text=numbered_text,
        known_entities=known_entities,
        **_pass_inputs(chunk_kwargs, _ENTITY_PASS_INPUTS),
    )


def render_relationship_pass(
    chunk_kwargs: Mapping[str, Any],
    *,
    numbered_text: str,
    entity_list: str,
    max_entity_index: int | str,
    template: str = RELATIONSHIP_HARVEST_TEMPLATE,
) -> str:
    """The pass-2 prompt a plan's prompt inputs render to over ``numbered_text`` and ``entity_list``."""
    return _build_relationship_prompt(
        template=template,
        numbered_sentences=numbered_text,
        entity_list=entity_list,
        max_entity_index=max_entity_index,
        **_pass_inputs(chunk_kwargs, _RELATIONSHIP_PASS_INPUTS),
    )


def render_plan_prompts(
    chunk_kwargs: Mapping[str, Any],
    *,
    numbered_text: str,
    entity_list: str,
    max_entity_index: int | str,
    entity_template: str = ENTITY_HARVEST_TEMPLATE,
    relationship_template: str = RELATIONSHIP_HARVEST_TEMPLATE,
    known_entities: str = "",
) -> tuple[str, str]:
    """The pass-1 and pass-2 prompts a plan's chunk kwargs render to.

    :func:`render_entity_pass` and :func:`render_relationship_pass` are the one
    mapping of the plan's prompt inputs onto :func:`_build_entity_prompt` and
    :func:`_build_relationship_prompt`; extraction itself
    (:meth:`AIEntityExtractor.extract_single_chunk`) renders through them. Every
    name in :data:`PROMPT_KWARG_NAMES` is rendered by one of the two passes; a
    prompt input the plan gains that neither renders raises instead of silently
    missing from every prompt (the ones sent, the MCP instructions, the recipe
    digest, the prompt budget).

    Args:
        chunk_kwargs: An :class:`ExtractionPlan`'s chunk kwargs (or
            ``prompt_inputs``' subset of them).
        numbered_text: The chunk's numbered sentences (or a placeholder).
        entity_list: The serialized pass-1 entity list (or a placeholder).
        max_entity_index: Highest valid entity index (``"N"`` for templates).
        entity_template: Pass-1 harvest template.
        relationship_template: Pass-2 harvest template.
        known_entities: Pass 1's KNOWN ENTITIES block ("" for none, or a
            placeholder for templates); pass 2 gets no block.

    Returns:
        ``(entity_prompt, relationship_prompt)``.

    Raises:
        KeyError: A prompt input is missing from ``chunk_kwargs``.
        RuntimeError: A prompt input is rendered by neither pass.
    """
    return (
        render_entity_pass(
            chunk_kwargs,
            numbered_text=numbered_text,
            template=entity_template,
            known_entities=known_entities,
        ),
        render_relationship_pass(
            chunk_kwargs,
            numbered_text=numbered_text,
            entity_list=entity_list,
            max_entity_index=max_entity_index,
            template=relationship_template,
        ),
    )


def render_plan_templates(
    chunk_kwargs: Mapping[str, Any],
    *,
    entity_template: str = ENTITY_HARVEST_TEMPLATE,
    relationship_template: str = RELATIONSHIP_HARVEST_TEMPLATE,
    known_entities: str = PROMPT_KNOWN_ENTITIES_PLACEHOLDER,
) -> tuple[str, str]:
    """Both prompts as reusable templates: the chunk text and the pass-1 entities as placeholders.

    What extraction metrics record and what MCP serves as instructions
    (:data:`PROMPT_CHUNK_TEXT_PLACEHOLDER`, :data:`PROMPT_PASS1_ENTITIES_PLACEHOLDER`,
    ``max_entity_index`` ``"N"``). Pass 1's KNOWN ENTITIES slot holds
    ``known_entities``: :data:`PROMPT_KNOWN_ENTITIES_PLACEHOLDER` for the
    recorded template, "" where no block is served (MCP).
    """
    return render_plan_prompts(
        chunk_kwargs,
        numbered_text=PROMPT_CHUNK_TEXT_PLACEHOLDER,
        entity_list=PROMPT_PASS1_ENTITIES_PLACEHOLDER,
        max_entity_index="N",
        entity_template=entity_template,
        relationship_template=relationship_template,
        known_entities=known_entities,
    )


async def _filter_entities(
    *,
    entities: list[dict[str, Any]],
    sentences: list[str],
    chunk_content: str,
    filtering_config: Any,
    entity_exclusions: list[ExclusionRule] | None,
    valid_entity_type_names: set[str] | None,
    named_referent_types: set[str] | None,
    normalization_rules: dict[str, list[str]] | None,
    property_type_mapping: dict[str, dict[str, str]] | None,
    filtering_log: Any,
    adapter: Any | None = None,
    source_id: str | None = None,
    database_name: str | None = None,
    name_conventions: NameConventions = DEFAULT_NAME_CONVENTIONS,
    type_vocabulary: TypeVocabulary = FLOOR_TYPE_VOCABULARY,
    known: KnownEntities | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Apply all entity filters between pass 1 and pass 2.

    Runs evidence validation, the known-entities guards (``known``), exclusion
    filtering, type rescue, and plausibility filtering in sequence. Each filter narrows the entity
    list so pass 2 only sees clean, validated entities. The type rescue
    always runs: every entity leaves typed as one of
    ``valid_entity_type_names`` (the core types; a probe's own list); one no
    core type answers to is kept as a ``Concept`` flagged ``unplaced_type``
    and counted under ``unknown_type`` (``type_rescue``). Keep, flag, never
    delete (2026-10-07): an entity the plausibility score puts below the bar
    is kept flagged ``low_plausibility`` when it carries evidence; since
    2026-10-08 so is an entity its cited sentence does not name
    (``unevidenced``) and one whose name is an exclusion example
    (``excluded_name``). Every flag is counted on the source row
    (``unplaced_type_entities_flagged``, ``plausibility_entities_flagged``,
    ``entities_unevidenced_kept`` by the evidence filter itself,
    ``excluded_entities_kept``). The guards (:meth:`KnownEntities.guard`)
    count what they did (``aliases_crossed_known_dropped``, and
    ``referents_unevidenced_demoted``: the known names demoted or kept
    flagged) and record it under ``evidence_stats["known_guard"]``.

    Args:
        entities: Raw entities from pass 1.
        sentences: Numbered sentences from chunk text.
        chunk_content: Original chunk text for visual content detection.
        filtering_config: Resolved filtering configuration.
        entity_exclusions: Domain-specific exclusion rules.
        valid_entity_type_names: The types entities leave with (None: the
            core types).
        named_referent_types: Entity types requiring proper names.
        normalization_rules: The floor's normalization keywords (the
            rescue's keyword tiers).
        property_type_mapping: Domain property-type mapping for type rescue.
        filtering_log: FilteringLog instance for tracking removals.
        adapter: Storage adapter for the evidence filter's quality counters.
        source_id: Source row identifier for those counters.
        database_name: Database name for those counters.
        name_conventions: The domain's naming conventions the evidence
            filter names entities by.
        type_vocabulary: The plan's packs, subtypes and aliases the type
            rescue reads (``HarvestLimits.type_vocabulary``).
        known: The source's known entities so far, whose guards run right
            after the evidence filter; None: no guards.

    Returns:
        Tuple of (filtered_entities, evidence_stats_dict).
    """
    from chaoscypher_core.services.sources.engine.extraction.utils.evidence_validator import (
        filter_entities_by_evidence,
    )

    _is_visual_content = "[Visual Content]" in chunk_content or "[/Visual Content]" in chunk_content

    evidence_stats: dict[str, Any] = {}
    evidence_mode = filtering_config.evidence_validation_mode

    if evidence_mode == "off":
        evidence_stats = {"entities_checked": len(entities), "entities_dropped": 0}
    else:
        if _is_visual_content and evidence_mode == "strict":
            evidence_mode = "standard"
        entities, _entity_index_mapping, entity_stats = await filter_entities_by_evidence(
            entities,
            sentences,
            mode=evidence_mode,
            filtering_log=filtering_log,
            min_significant_word_length=filtering_config.min_significant_word_length,
            adapter=adapter,
            source_id=source_id,
            database_name=database_name,
            conventions=name_conventions,
        )
        evidence_stats.update(entity_stats)

    # The known-entities guards (spec 2026-10-11 D3): before pass 2 sees a crossed alias.
    if known is not None:
        entities, guard_stats = known.guard(
            entities,
            sentences,
            min_word_length=filtering_config.min_significant_word_length,
        )
        evidence_stats["known_guard"] = asdict(guard_stats)
        await _count_flagged(
            adapter, source_id, database_name,
            QualityCounter.ALIASES_CROSSED_KNOWN_DROPPED, guard_stats.aliases_crossed,
        )  # fmt: skip
        await _count_flagged(
            adapter, source_id, database_name,
            QualityCounter.REFERENTS_UNEVIDENCED_DEMOTED,
            guard_stats.names_demoted + guard_stats.names_flagged,
        )  # fmt: skip

    # Code-level exclusion filter
    if filtering_config.enable_entity_exclusions and entity_exclusions:
        entities, _excl_mapping = filter_excluded_entities(
            entities, entity_exclusions, filtering_log=filtering_log, conventions=name_conventions
        )
        await _count_flagged(
            adapter, source_id, database_name,
            QualityCounter.EXCLUDED_ENTITIES_KEPT,
            sum(pipeline_flag(e.get("properties"), EXCLUDED_NAME_KEY) for e in entities),
        )  # fmt: skip

    # Type rescue: always (every entity leaves core-typed or is dropped);
    # ``enable_type_rescue`` (off for unfiltered/minimal) gates only its junk
    # and absorption tiers.
    entities, _pass1_rels_unused, _type_mapping, rescue_stats = rescue_invalid_entity_types(
        entities,
        [],
        TypeVocabulary.from_config(
            type_vocabulary, types=valid_entity_type_names, keywords=normalization_rules
        ),
        property_type_mapping=property_type_mapping or {},
        filtering_log=filtering_log,
        conventions=name_conventions,
        salvage=bool(getattr(filtering_config, "enable_type_rescue", True)),
    )
    evidence_stats["type_rescue"] = rescue_stats
    # The flagged entities themselves: no core type (``unknown_type``) or an
    # unoffered ``Core/subtype`` (``unplaced_subtype``), as the neighbours count.
    await _count_flagged(
        adapter, source_id, database_name,
        QualityCounter.UNPLACED_TYPE_ENTITIES_FLAGGED,
        sum(pipeline_flag(e.get("properties"), UNPLACED_TYPE_KEY) for e in entities),
    )  # fmt: skip

    # Plausibility filter
    if filtering_config.enable_plausibility_filter:
        _plaus_threshold = filtering_config.plausibility_threshold
        _plaus_threshold_non_named = filtering_config.plausibility_threshold_non_named
        _visual_factor = filtering_config.visual_content_plausibility_factor
        if _is_visual_content and _visual_factor < 1.0:
            _plaus_threshold *= _visual_factor
            _plaus_threshold_non_named *= _visual_factor
            logger.info(
                "visual_content_plausibility_adjusted",
                factor=_visual_factor,
                threshold=_plaus_threshold,
                threshold_non_named=_plaus_threshold_non_named,
            )
        entities, _plausibility_mapping = filter_implausible_entities(
            entities,
            sentences,
            named_referent_types=named_referent_types,
            threshold=float(_plaus_threshold),
            threshold_non_named=float(_plaus_threshold_non_named),
            filtering_log=filtering_log,
            conventions=name_conventions,
        )
        await _count_flagged(
            adapter, source_id, database_name,
            QualityCounter.PLAUSIBILITY_ENTITIES_FLAGGED,
            sum(pipeline_flag(e.get("properties"), LOW_PLAUSIBILITY_KEY) for e in entities),
        )  # fmt: skip

    return entities, evidence_stats


async def _count_flagged(
    adapter: Any | None,
    source_id: str | None,
    database_name: str | None,
    counter: QualityCounter,
    n: int,
) -> None:
    """Add ``n`` flagged entities to the source row's ``counter`` (best effort; no row, no count)."""
    if n and adapter is not None and source_id is not None:
        await increment_quality_counter(
            adapter=adapter,
            source_id=source_id,
            database_name=database_name or "default",
            counter=counter,
            n=n,
        )


def _record_extraction_metrics(
    *,
    metrics_collector: Any | None,
    chunk_content: str,
    entities: list[dict[str, Any]],
    relationships: list[dict[str, Any]],
    pass1_input_tokens: int,
    pass1_output_tokens: int,
    pass2_input_tokens: int,
    pass2_output_tokens: int,
) -> None:
    """Record per-pass extraction metrics on the collector.

    Records pass 1 (entities) and pass 2 (relationships) as separate
    metric entries if a collector is provided.

    Args:
        metrics_collector: Optional LLMMetricsCollector.
        chunk_content: Original chunk text for size tracking.
        entities: Extracted entities.
        relationships: Extracted relationships.
        pass1_input_tokens: Input tokens from pass 1.
        pass1_output_tokens: Output tokens from pass 1.
        pass2_input_tokens: Input tokens from pass 2.
        pass2_output_tokens: Output tokens from pass 2.
    """
    if metrics_collector is None:
        return

    metrics_collector.record_attempt(
        success=True,
        input_tokens=pass1_input_tokens,
        output_tokens=pass1_output_tokens,
        duration_ms=0,
        was_retry=False,
        chunk_size_chars=len(chunk_content),
        entities_extracted=len(entities),
        relationships_extracted=0,
    )
    if entities:
        metrics_collector.record_attempt(
            success=True,
            input_tokens=pass2_input_tokens,
            output_tokens=pass2_output_tokens,
            duration_ms=0,
            was_retry=False,
            chunk_size_chars=len(chunk_content),
            entities_extracted=0,
            relationships_extracted=len(relationships),
        )


def _build_extraction_metrics(
    *,
    pass1_content: str,
    pass2_content: str,
    input_tokens: int,
    output_tokens: int,
    entities: list[dict[str, Any]],
    relationships: list[dict[str, Any]],
    invalid_count: int,
    evidence_stats: dict[str, Any],
    sentences: list[str],
    filtering_log: Any,
    entity_prompt: str,
    relationship_prompt: str,
    system_prompt: str,
    extraction_rules_template: str,
    node_templates_formatted: str,
    edge_templates_formatted: str,
    entity_guidance: str | None,
    relationship_guidance: str | None,
    entity_examples: str | None,
    relationship_examples: str | None,
    finish_reason: str = "unknown",
    aborted_by_loop: bool = False,
    parser_lines_dropped: int = 0,
    parser_lines_parsed: int = 0,
) -> dict[str, Any]:
    """Build the extraction metrics dict returned to callers.

    Combines token counts, entity/relationship counts, evidence stats,
    filtering log, and prompt data into a single dict. The _prompt_data
    sub-dict is consumed once by the chunk extraction service for
    job-level prompt storage, then discarded.

    Args:
        pass1_content: Raw LLM response from pass 1.
        pass2_content: Raw LLM response from pass 2.
        input_tokens: Total input tokens.
        output_tokens: Total output tokens.
        entities: Final filtered entities.
        relationships: Final filtered relationships.
        invalid_count: Count of invalid relationships.
        evidence_stats: Evidence statistics dict.
        sentences: Sentence list from text splitting.
        filtering_log: FilteringLog instance.
        entity_prompt: Entity (pass 1) prompt template captured for UI display
            (chunk text replaced by a placeholder marker).
        relationship_prompt: Relationship (pass 2) prompt template captured for
            UI display (chunk text + pass-1 entity list replaced by markers).
        system_prompt: System prompt text.
        extraction_rules_template: Extraction rules template.
        node_templates_formatted: Formatted node templates.
        edge_templates_formatted: Formatted edge templates.
        entity_guidance: Entity guidance text.
        relationship_guidance: Relationship guidance text.
        entity_examples: Entity examples text.
        relationship_examples: Relationship examples text.

    Returns:
        Extraction metrics dictionary.
    """
    if entities and not relationships:
        logger.warning(
            "chunk_has_entities_but_no_relationships",
            entity_count=len(entities),
            entity_names=[e.get("name") for e in entities[:5]],
        )

    logger.info(
        "harvest_extraction_complete",
        entity_count=len(entities),
        relationship_count=len(relationships),
        invalid_relationship_count=invalid_count,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        **evidence_stats,
    )

    return {
        "raw_llm_response": format_raw_harvest_response(pass1_content, pass2_content),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "entity_count": len(entities),
        "relationship_count": len(relationships),
        "invalid_relationship_count": invalid_count,
        "evidence_stats": evidence_stats,
        "sentences": sentences,
        "filtering_log": filtering_log.to_dict() if filtering_log.has_removals else None,
        # Workstream 8 observability — propagated from per-pass call_llm
        # results so the chunk handler can bump source counters and
        # persist per-chunk-task fields without re-deriving them.
        "finish_reason": finish_reason,
        "aborted_by_loop": aborted_by_loop,
        # Workstream 2 (2026-05-08) observability — count of LLM-output
        # lines that the line-parser couldn't deserialize across both
        # passes. Surfaces as the ``parser_lines_dropped`` quality counter
        # on the source row once the chunk handler aggregates it.
        "parser_lines_dropped": parser_lines_dropped,
        # The lines the parser read, both passes: an answer in another format
        # (JSON, a table) parses none and drops none
        # (``chunk_failures.degenerate_output_check``, audit wave 8).
        "parser_lines_parsed": parser_lines_parsed,
        "_prompt_data": {
            "system_prompt": system_prompt,
            "extraction_rules_template": extraction_rules_template,
            "user_instructions": entity_prompt,
            "relationship_instructions": relationship_prompt,
            "entity_templates": node_templates_formatted,
            "relationship_templates": edge_templates_formatted,
            "domain_guidance": f"{entity_guidance}\n\n{relationship_guidance}".strip()
            if entity_guidance or relationship_guidance
            else None,
            "domain_examples": f"{entity_examples}\n\n{relationship_examples}".strip()
            if entity_examples or relationship_examples
            else None,
        },
    }


class AIEntityExtractor:
    """Extracts entities and relationships from text chunks using AI.

    Uses evidence-gated pipe-delimited format with sentence references:
    - E|name|type|aliases|confidence|sent_ref|description
    - R|source_index|target_index|type|confidence|sent_ref|justification
    - P|entity_index|key|value

    Extraction is 2-pass per chunk:
    - Pass 1: Entities + Properties (E|, P| lines)
    - Pass 2: Relationships (R| lines) using filtered entity list from pass 1

    Benefits:
    - No tool calling failures (works with any LLM)
    - Simple format is easy for LLMs to output
    - Greedy parsing handles unescaped pipes in descriptions
    - Each line is independent (partial output still usable)
    - Sentence references enable evidence validation
    - 2-pass eliminates token competition between entities and relationships
    """

    # Prompt templates — re-exported as ClassVars for access as
    # ``extractor.SYSTEM_PROMPT`` etc.
    # Phase 6 (2026-05-08): SYSTEM_PROMPT ClassVar is the hardcoded default;
    # the resolved runtime value lives in self._system_prompt (set in __init__)
    # which honours settings.extraction.system_prompt and domain overrides.
    SYSTEM_PROMPT: ClassVar[str] = SYSTEM_PROMPT
    ENTITY_HARVEST_TEMPLATE: ClassVar[str] = ENTITY_HARVEST_TEMPLATE
    RELATIONSHIP_HARVEST_TEMPLATE: ClassVar[str] = RELATIONSHIP_HARVEST_TEMPLATE
    EXTRACTION_RULES_TEMPLATE: ClassVar[str] = EXTRACTION_RULES_TEMPLATE

    def __init__(
        self,
        settings: EngineSettings,
        llm_provider: LLMProviderPort | None = None,
    ):
        """Initialize AI entity extractor.

        Args:
            settings: Settings instance
            llm_provider: Extraction-configured LLM provider. When omitted,
                the extractor lazily builds one via ``ProviderFactory`` on
                the first :meth:`call_llm` call. Tests and Engine wiring
                should inject an explicit port.

        """
        self.settings = settings
        self._llm_provider: LLMProviderPort | None = llm_provider
        # Phase 6 (2026-05-08): resolve system prompt from settings.
        # Domain overrides are applied per-chunk in extract_from_chunks.
        self._system_prompt: str = getattr(settings.extraction, "system_prompt", self.SYSTEM_PROMPT)

    def _get_llm_provider(self) -> LLMProviderPort:
        """Return the injected LLM provider, building one on first use.

        Lazily constructs the extraction-configured provider via
        ``ProviderFactory`` when the constructor did not receive an
        explicit port. Caches the result so subsequent extraction calls
        reuse the same instance within this extractor.
        """
        if self._llm_provider is None:
            from typing import cast

            from chaoscypher_core.adapters.llm import ProviderFactory

            # ``BaseLLMProvider`` satisfies ``LLMProviderPort`` structurally but
            # mypy can't unify the ABC with the Protocol when assigning to
            # ``LLMProviderPort | None`` here.
            self._llm_provider = cast(
                "LLMProviderPort",
                ProviderFactory(self.settings).get_extraction_provider(),
            )
        return self._llm_provider

    async def call_llm(
        self,
        prompt: str,
        temperature: float | None = None,
        max_tokens: int | None = None,
        max_entity_count_override: int | None = None,
        on_stream_progress: Callable[[], Awaitable[None] | None] | None = None,
        known_entity_count: int = 0,
    ) -> CallLLMResult:
        """Call LLM with streaming and real-time loop detection.

        Streams the response token-by-token. As complete lines arrive, checks
        for degenerate patterns (out-of-bounds indices, repeating relationship
        types). Aborts the stream early if a loop is detected, saving GPU time.

        Args:
            prompt: The user prompt
            temperature: LLM temperature (uses settings.llm.extraction_temperature if None)
            max_tokens: Max tokens (uses settings default if None)
            max_entity_count_override: Domain-specific override for max entities
                per chunk. If None, falls back to ExtractionSettings default.
            on_stream_progress: Optional best-effort callback fired on every
                received content chunk. Used by the chunk extraction handler
                to bump ``last_activity_at`` based on stream activity so a
                long-running but healthy LLM call does not look stalled to
                ``SourceRecovery``. Errors raised by the callback are logged
                and suppressed.
            known_entity_count: For the relationship pass, pass 1's entity
                count: the stream writes only ``R|`` lines, and the detector
                checks them only against entities it knows of.

        Returns:
            :class:`CallLLMResult` with the streamed content, input/output
            token counts, normalized provider ``finish_reason``, and an
            ``aborted_by_loop`` flag the caller can use to bump
            observability counters.

        """
        provider = self._get_llm_provider()

        _active_system_prompt = getattr(self, "_system_prompt", self.SYSTEM_PROMPT)
        messages = [
            {"role": "system", "content": _active_system_prompt},
            {"role": "user", "content": prompt},
        ]

        # Use settings values if not specified
        if temperature is None:
            temperature = self.settings.llm.extraction_temperature
        if max_tokens is None:
            max_tokens = self.settings.llm.extraction_max_tokens

        logger.debug(
            "llm_extraction_request",
            system_prompt_length=len(_active_system_prompt),
            user_prompt_length=len(prompt),
            prompt=prompt,
        )

        response = await provider.chat(
            messages=messages,
            tools=None,
            stream=True,
            temperature=temperature,
            max_tokens=max_tokens,
            enable_thinking=self.settings.llm.thinking_for_extraction,
        )

        # Stream and monitor for degenerate loops
        detector = _StreamLoopDetector(
            extraction_cfg=self.settings.extraction,
            max_entity_count_override=max_entity_count_override,
            known_entity_count=known_entity_count,
        )
        (
            content,
            input_tokens,
            output_tokens,
            finish_reason,
            aborted,
        ) = await _consume_extraction_stream(response, detector, on_chunk=on_stream_progress)

        # Fallback: estimate tokens when streaming didn't provide them
        # (aborted streams miss the "done" chunk; some providers don't report
        # streaming usage)
        if input_tokens == 0:
            input_tokens = estimate_message_tokens(messages)
        if output_tokens == 0:
            output_tokens = estimate_tokens(content)

        logger.info(
            "llm_extraction_call_complete",
            content_length=len(content),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            stream_aborted=aborted,
            finish_reason=finish_reason,
        )

        logger.debug("llm_extraction_response", response=content)

        return CallLLMResult(
            content=content,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            finish_reason=finish_reason,
            aborted_by_loop=aborted,
        )

    def _serialize_entities_for_prompt(self, entities: list[dict[str, Any]]) -> str:
        """Format filtered entities as numbered list for pass 2 prompt.

        Args:
            entities: List of entity dicts with name, type, and optional aliases.

        Returns:
            Formatted string with one entity per line.

        """
        lines = []
        for idx, entity in enumerate(entities):
            name = entity.get("name", "Unknown")
            etype = entity.get("type", "Unknown")
            aliases = entity.get("aliases", [])
            if aliases:
                alias_str = "; ".join(aliases) if isinstance(aliases, list) else str(aliases)
                lines.append(f"{idx}: {name} ({etype}) [aliases: {alias_str}]")
            else:
                lines.append(f"{idx}: {name} ({etype})")
        return "\n".join(lines)

    def render_harvest_prompts(
        self,
        chunk_content: str,
        node_templates_formatted: str,
        *,
        entity_guidance: str | None = None,
        entity_examples: str | None = None,
        entity_exclusions: list[ExclusionRule] | None = None,
        strict_entity_types: bool = False,
        known_entities: str = "",
    ) -> HarvestPrompts:
        """Render the system prompt and pass-1 prompt exactly as extraction sends them.

        Splits ``chunk_content`` into numbered sentences and renders pass 1
        through :func:`render_entity_pass` (the parameters are the plan's
        prompt inputs by name). :meth:`extract_single_chunk` sends these
        prompts; callers that obtain the model's answer some other way (the
        MCP benchmark) render the same bytes with this method. ``known_entities``
        is the group's KNOWN ENTITIES block ("" for none).
        """
        return self._render_entity_harvest(
            named_values(locals(), _ENTITY_PASS_INPUTS), chunk_content, known_entities
        )

    def _render_entity_harvest(
        self, prompt_inputs: Mapping[str, Any], chunk_content: str, known_entities: str = ""
    ) -> HarvestPrompts:
        """The system prompt, the chunk's sentences and the pass-1 prompt for ``prompt_inputs``.

        ``known_entities`` is the group's KNOWN ENTITIES block ("" for none).
        """
        from chaoscypher_core.services.sources.engine.extraction.utils.sentence_splitter import (
            format_numbered_sentences,
            split_into_sentences,
        )

        sentences = split_into_sentences(chunk_content)
        numbered_text = format_numbered_sentences(sentences)
        return HarvestPrompts(
            system_prompt=getattr(self, "_system_prompt", self.SYSTEM_PROMPT),
            entity_prompt=render_entity_pass(
                prompt_inputs,
                numbered_text=numbered_text,
                template=self.ENTITY_HARVEST_TEMPLATE,
                known_entities=known_entities,
            ),
            sentences=sentences,
            numbered_text=numbered_text,
        )

    def _render_relationship(
        self,
        prompt_inputs: Mapping[str, Any],
        entities: list[dict[str, Any]],
        numbered_text: str,
    ) -> str:
        """The pass-2 prompt for ``prompt_inputs`` over the filtered pass-1 ``entities``."""
        return render_relationship_pass(
            prompt_inputs,
            numbered_text=numbered_text,
            entity_list=self._serialize_entities_for_prompt(entities),
            max_entity_index=len(entities) - 1,
            template=self.RELATIONSHIP_HARVEST_TEMPLATE,
        )

    def render_plan_templates(
        self,
        chunk_kwargs: Mapping[str, Any],
        *,
        known_entities: str = PROMPT_KNOWN_ENTITIES_PLACEHOLDER,
    ) -> tuple[str, str]:
        """Both prompts of a plan as placeholder templates (:func:`render_plan_templates`).

        ``known_entities`` fills pass 1's KNOWN ENTITIES slot: the placeholder
        by default, "" where no block is served.
        """
        return render_plan_templates(
            chunk_kwargs,
            entity_template=self.ENTITY_HARVEST_TEMPLATE,
            relationship_template=self.RELATIONSHIP_HARVEST_TEMPLATE,
            known_entities=known_entities,
        )

    def render_chunk_prompts(
        self,
        chunk_kwargs: Mapping[str, Any],
        chunk_content: str,
        entities: list[dict[str, Any]],
        *,
        known_entities: str = "",
    ) -> PlanPrompts:
        """Both prompts a plan sends for ``chunk_content``, pass 2 over ``entities``.

        The chunk is numbered and the entity list serialized as extraction
        does, through the same pass renderers :meth:`extract_single_chunk` uses.
        ``known_entities`` is the group's KNOWN ENTITIES block ("" for none,
        as group 1 is sent; :meth:`KnownEntities.block` for a later group).
        """
        harvest = self._render_entity_harvest(chunk_kwargs, chunk_content, known_entities)
        return PlanPrompts(
            system_prompt=harvest.system_prompt,
            numbered_text=harvest.numbered_text,
            entity_prompt=harvest.entity_prompt,
            relationship_prompt=self._render_relationship(
                chunk_kwargs, entities, harvest.numbered_text
            ),
        )

    def resolve_harvest_limits(
        self,
        *,
        domain_extraction_limits: dict[str, float | int] | None = None,
        filtering_mode: str | None = None,
        evidence_validation_mode: str | None = None,
        filtering_config: Any | None = None,
        relation_vocabulary: RelationVocabulary | dict[str, Any] | None = None,
        name_conventions: NameConventions | dict[str, Any] | None = None,
        type_vocabulary: TypeVocabulary | dict[str, Any] | None = None,
    ) -> HarvestLimits:
        """Resolve the filtering config, loop-detector cap and alias length for a chunk.

        Same precedence as :meth:`extract_single_chunk`: a pre-resolved
        ``filtering_config`` wins, otherwise the preset from
        ``filtering_mode`` (or settings) with ``domain_extraction_limits`` as
        overrides (the plan's, a domain's cap and alias length already moved
        with the slider: ``follow_slider_limits``).
        ``relation_vocabulary`` (the plan's, a domain's JSON shape) is carried
        to the pass-2 evidence filter, ``name_conventions`` (the plan's JSON,
        ``NameConventions.as_dict``) to both evidence filters,
        ``type_vocabulary`` (the plan's JSON, ``TypeVocabulary.as_config``) to
        the type rescue.
        """
        from chaoscypher_core.services.sources.engine.extraction.utils.evidence_validator import (
            as_name_conventions,
        )

        extraction_cfg = self.settings.extraction
        domain_limits = domain_extraction_limits or {}
        resolved = _resolve_chunk_filtering_config(
            filtering_config=filtering_config,
            filtering_mode=filtering_mode,
            domain_limits=domain_limits,
            extraction_cfg=extraction_cfg,
            evidence_validation_mode=evidence_validation_mode,
        )
        return HarvestLimits(
            filtering_config=resolved,
            max_entity_count=_resolve_loop_max_entity_count(
                filtering_config=resolved,
                domain_limits=domain_limits,
                extraction_cfg=extraction_cfg,
            ),
            min_alias_length=_resolve_minimum_alias_length(
                filtering_config=resolved,
                domain_limits=domain_limits,
                extraction_cfg=extraction_cfg,
            ),
            relation_vocabulary=RelationVocabulary.from_config(relation_vocabulary),
            name_conventions=as_name_conventions(name_conventions),
            type_vocabulary=TypeVocabulary.from_config(type_vocabulary),
        )

    async def parse_entity_harvest(
        self,
        pass1_content: str,
        *,
        sentences: list[str],
        chunk_content: str,
        limits: HarvestLimits,
        entity_exclusions: list[ExclusionRule] | None = None,
        valid_entity_type_names: set[str] | None = None,
        named_referent_types: set[str] | None = None,
        normalization_rules: dict[str, list[str]] | None = None,
        property_type_mapping: dict[str, dict[str, str]] | None = None,
        parser_stats: dict[str, int] | None = None,
        filtering_log: Any | None = None,
        adapter: Any | None = None,
        source_id: str | None = None,
        database_name: str | None = None,
        known: KnownEntities | None = None,
    ) -> EntityHarvest:
        """Parse pass-1 output and apply the between-pass entity filters.

        Parses ``E|``/``P|`` lines, applies properties to their entities, then
        runs evidence validation, the known-entities guards (``known``; None:
        none), exclusion filtering, type rescue and the plausibility filter.
        The surviving entities are what pass 2 lists.
        """
        from chaoscypher_core.services.sources.engine.extraction.utils.filtering_log import (
            FilteringLog,
        )

        extraction_cfg = self.settings.extraction
        stats = parser_stats if parser_stats is not None else {"dropped_lines": 0}
        entities, _pass1_relationships, properties = parse_extraction_output(
            pass1_content,
            max_out_of_bounds=extraction_cfg.loop_max_out_of_bounds,
            max_source_type_repeat=extraction_cfg.loop_max_source_type_repeat,
            skip_loop_detection=True,
            minimum_alias_length=limits.min_alias_length,
            stats=stats,
        )
        if properties:
            apply_properties_to_entities(entities, properties)

        log = filtering_log if filtering_log is not None else FilteringLog()
        entities, evidence_stats = await _filter_entities(
            entities=entities,
            sentences=sentences,
            chunk_content=chunk_content,
            filtering_config=limits.filtering_config,
            entity_exclusions=entity_exclusions,
            valid_entity_type_names=valid_entity_type_names,
            named_referent_types=named_referent_types,
            normalization_rules=normalization_rules,
            property_type_mapping=property_type_mapping,
            filtering_log=log,
            adapter=adapter,
            source_id=source_id,
            database_name=database_name,
            name_conventions=limits.name_conventions,
            type_vocabulary=limits.type_vocabulary,
            known=known,
        )
        return EntityHarvest(
            entities=entities,
            property_count=len(properties),
            evidence_stats=evidence_stats,
            filtering_log=log,
            parser_stats=stats,
        )

    def render_relationship_prompt(
        self,
        entities: list[dict[str, Any]],
        numbered_text: str,
        edge_templates_formatted: str,
        *,
        relationship_guidance: str | None = None,
        relationship_examples: str | None = None,
    ) -> str:
        """Render the pass-2 prompt from the filtered pass-1 entity list."""
        return self._render_relationship(
            named_values(locals(), _RELATIONSHIP_PASS_INPUTS), entities, numbered_text
        )

    def render_relationship_prompt_template(
        self,
        edge_templates_formatted: str,
        *,
        relationship_guidance: str | None = None,
        relationship_examples: str | None = None,
    ) -> str:
        """Render the reusable pass-2 prompt with placeholder markers.

        The chunk text and the pass-1 entity list are replaced by
        :data:`PROMPT_CHUNK_TEXT_PLACEHOLDER` and
        :data:`PROMPT_PASS1_ENTITIES_PLACEHOLDER`; this is what extraction
        metrics record as ``relationship_instructions``.
        """
        return render_relationship_pass(
            named_values(locals(), _RELATIONSHIP_PASS_INPUTS),
            numbered_text=PROMPT_CHUNK_TEXT_PLACEHOLDER,
            entity_list=PROMPT_PASS1_ENTITIES_PLACEHOLDER,
            max_entity_index="N",
            template=self.RELATIONSHIP_HARVEST_TEMPLATE,
        )

    async def parse_relationship_harvest(
        self,
        pass2_content: str,
        *,
        entities: list[dict[str, Any]],
        sentences: list[str],
        limits: HarvestLimits,
        filtering_log: Any,
        evidence_stats: dict[str, Any],
        parser_stats: dict[str, int] | None = None,
        extraction_cfg: Any | None = None,
        adapter: Any | None = None,
        source_id: str | None = None,
        database_name: str | None = None,
        known: KnownEntities | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """Parse pass-2 output, validate structure and apply the evidence filter.

        Returns the relationships that survive bounds/self-loop validation and
        the per-chunk evidence filter, and the count of structurally invalid
        ones. ``evidence_stats`` is updated in place. ``extraction_cfg``
        defaults to ``settings.extraction``. ``known`` is the list pass 1's
        name guard read (None: none): an endpoint whose record name is a known
        entity is named only by the known-entities name rule (spec D8). The
        guard ran on ``entities`` before pass 2, so the edge rule reads the
        record names as the guard left them (a demoted record is a reference
        and reads today's rungs).
        """
        from chaoscypher_core.services.sources.engine.extraction.utils.evidence_validator import (
            filter_relationships_by_evidence,
        )

        if extraction_cfg is None:
            extraction_cfg = self.settings.extraction
        filtering_config = limits.filtering_config
        _pass2_entities, raw_relationships, _pass2_properties = parse_extraction_output(
            pass2_content,
            max_out_of_bounds=extraction_cfg.loop_max_out_of_bounds,
            max_source_type_repeat=extraction_cfg.loop_max_source_type_repeat,
            skip_loop_detection=True,
            minimum_alias_length=limits.min_alias_length,
            stats=parser_stats,
        )

        relationships, invalid_count = validate_relationships(
            raw_relationships,
            entities,
            filtering_log=filtering_log,
            allow_self_loops=getattr(filtering_config, "allow_self_loops", False),
        )

        # Per-chunk evidence filtering — depends on chunk-local sentences
        # and so must run here. Type-constraint validation and relationship-
        # limit enforcement are now applied CROSS-chunk after dedup; see
        # ``apply_cross_chunk_relationship_filters`` in extractor.py and the
        # pipeline-order banner above its definition for the rationale.
        evidence_mode = filtering_config.evidence_validation_mode
        if evidence_mode != "off":
            relationships, rel_stats = await filter_relationships_by_evidence(
                relationships,
                entities,
                sentences,
                mode=evidence_mode,
                filtering_log=filtering_log,
                min_significant_word_length=filtering_config.min_significant_word_length,
                adapter=adapter,
                source_id=source_id,
                database_name=database_name,
                vocabulary=limits.relation_vocabulary,
                conventions=limits.name_conventions,
                known=known,
            )
            evidence_stats.update(rel_stats)
        return relationships, invalid_count

    async def parse_harvest_outputs(
        self,
        entities_text: str,
        relationships_text: str,
        *,
        sentences: list[str],
        chunk_content: str,
        numbered_text: str,
        edge_templates_formatted: str,
        limits: HarvestLimits | None = None,
        entity_exclusions: list[ExclusionRule] | None = None,
        valid_entity_type_names: set[str] | None = None,
        named_referent_types: set[str] | None = None,
        normalization_rules: dict[str, list[str]] | None = None,
        property_type_mapping: dict[str, dict[str, str]] | None = None,
        relationship_guidance: str | None = None,
        relationship_examples: str | None = None,
    ) -> HarvestParse:
        """Turn both passes' raw answers into the entities and relationships extraction keeps.

        The post-LLM half of :meth:`extract_single_chunk` without the LLM:
        pass-1 parse and entity filters, then (only when entities survive,
        as in the pipeline) the pass-2 parse, validation and evidence
        filter. ``relationships_text`` is ignored when no entity survives,
        because the pipeline would not have run pass 2. The LLM path adds
        only the streaming loop detector (see :func:`replay_harvest_output`
        for answers that did not stream), token accounting and metrics.
        """
        resolved = limits or self.resolve_harvest_limits()
        parser_stats: dict[str, int] = {"dropped_lines": 0}
        harvest = await self.parse_entity_harvest(
            entities_text,
            sentences=sentences,
            chunk_content=chunk_content,
            limits=resolved,
            entity_exclusions=entity_exclusions,
            valid_entity_type_names=valid_entity_type_names,
            named_referent_types=named_referent_types,
            normalization_rules=normalization_rules,
            property_type_mapping=property_type_mapping,
            parser_stats=parser_stats,
        )
        relationships: list[dict[str, Any]] = []
        invalid_count = 0
        relationship_prompt: str | None = None
        if harvest.entities:
            relationship_prompt = self.render_relationship_prompt(
                harvest.entities,
                numbered_text,
                edge_templates_formatted,
                relationship_guidance=relationship_guidance,
                relationship_examples=relationship_examples,
            )
            relationships, invalid_count = await self.parse_relationship_harvest(
                relationships_text,
                entities=harvest.entities,
                sentences=sentences,
                limits=resolved,
                filtering_log=harvest.filtering_log,
                evidence_stats=harvest.evidence_stats,
                parser_stats=parser_stats,
            )
        return HarvestParse(
            entities=harvest.entities,
            relationships=relationships,
            invalid_relationship_count=invalid_count,
            parser_lines_dropped=parser_stats["dropped_lines"],
            parser_lines_parsed=parser_stats.get("parsed_lines", 0),
            evidence_stats=harvest.evidence_stats,
            filtering_log=harvest.filtering_log,
            relationship_prompt=relationship_prompt,
        )

    async def extract_single_chunk(
        self,
        chunk_content: str,
        node_templates_formatted: str,
        edge_templates_formatted: str,
        entity_guidance: str | None = None,
        relationship_guidance: str | None = None,
        entity_examples: str | None = None,
        relationship_examples: str | None = None,
        metrics_collector: Any | None = None,
        domain_extraction_limits: dict[str, float | int] | None = None,
        filtering_mode: str | None = None,
        entity_exclusions: list[ExclusionRule] | None = None,
        strict_entity_types: bool = False,
        valid_entity_type_names: set[str] | None = None,
        evidence_validation_mode: str | None = None,
        named_referent_types: set[str] | None = None,
        normalization_rules: dict[str, list[str]] | None = None,
        property_type_mapping: dict[str, dict[str, str]] | None = None,
        type_vocabulary: dict[str, Any] | None = None,
        relation_vocabulary: dict[str, Any] | None = None,
        name_conventions: dict[str, Any] | None = None,
        filtering_config: Any | None = None,
        on_stream_progress: Callable[[], Awaitable[None] | None] | None = None,
        temperature_override: float | None = None,
        max_tokens_override: int | None = None,
        adapter: Any | None = None,
        source_id: str | None = None,
        database_name: str | None = None,
        *,
        known: KnownEntities | None = None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int, int, dict[str, Any]]:
        """Extract entities and relationships using 2-pass approach.

        Pass 1: Extract entities (E|) and properties (P|) with full context.
        Pass 2: Extract relationships (R|) using filtered entity list.

        This eliminates token competition — entities get full token budget
        in pass 1, relationships get dedicated attention in pass 2.

        Args:
            chunk_content: Text content to extract from
            node_templates_formatted: Formatted node template descriptions
            edge_templates_formatted: Formatted edge template descriptions
            entity_guidance: Entity-specific extraction guidance
            relationship_guidance: Relationship-specific extraction guidance
            entity_examples: Entity-specific examples
            relationship_examples: Relationship-specific examples
            metrics_collector: Optional metrics collector
            domain_extraction_limits: Domain-specific ``FilteringConfig``
                field overrides. Keys override the resolved preset; missing
                keys fall back to ``ExtractionSettings`` defaults. The
                preset selector (``extraction_filtering_mode``) is passed
                via the ``filtering_mode`` argument — never inlined here.
            filtering_mode: Preset selector (``"strict"``, ``"balanced"``,
                etc.). When None, falls back to
                ``settings.extraction.extraction_filtering_mode``.
            entity_exclusions: Domain-specific exclusion rules injected into
                the prompt. Each string describes a category to skip.
            strict_entity_types: When True, adds the prompt instruction to
                use only the listed entity types.
            valid_entity_type_names: The types every entity leaves the
                per-chunk filters with (the plan's core types; None: the core
                types). The type rescue always runs.
            evidence_validation_mode: Per-domain override for evidence validation
                strictness. If None, falls back to ``ExtractionSettings``.
            named_referent_types: Entity types that require proper names.
                Used by the plausibility filter to apply stricter thresholds.
            normalization_rules: Domain normalization rules for type rescue.
                Maps target_type → list of trigger keywords.
            property_type_mapping: Domain property-type mapping for type rescue.
                Maps invalid_type → {"target_type": ..., "property": ...}.
            type_vocabulary: The plan's packs, subtypes and aliases
                (``TypeVocabulary.as_config``) the type rescue reads. None:
                the core types and the mapping table alone.
            relation_vocabulary: The domain's ``relation_vocabulary`` (relation
                -> labels and words) the pass-2 evidence filter's
                one-endpoint rule reads. None: the labels' own words only.
            name_conventions: The domain's naming conventions
                (``NameConventions.as_dict``, the plan's) both evidence
                filters name entities by. None: the universal ones.
            filtering_config: Pre-resolved FilteringConfig. When None, resolved
                from ``extraction_filtering_mode`` setting + domain overrides.
            on_stream_progress: Optional best-effort callback fired on every
                LLM stream chunk. Forwarded to both pass-1 and pass-2 ``call_llm``
                invocations so ``last_activity_at`` keeps moving while a
                long-streaming model is still emitting tokens.
            adapter: Storage adapter forwarded to the evidence validator for
                quality counter increments. When None, evidence drops are
                still logged but no counter is bumped.
            source_id: Source row identifier forwarded to the evidence
                validator for quality counter increments.
            database_name: Database name forwarded to the evidence validator
                for quality counter increments.
            known: The source's known entities so far, the one input of
                all three: its block (:meth:`KnownEntities.block`) is rendered
                into the pass-1 prompt between the sentences and the output
                format, its guards run on the pass-1 records
                (:meth:`KnownEntities.guard`), and the pass-2 evidence filter
                names an endpoint whose (guarded) record name is a known
                entity only by the name guard's rules 1 and 2
                (:meth:`KnownEntities.identified`, spec D8), and one the
                guard kept but flagged by none. None: none of them.

        Returns:
            Tuple of (entities, relationships, input_tokens, output_tokens, extraction_metrics)

        """
        # The plan's prompt inputs, by the parameter names they arrive under: both
        # passes and the recorded templates render from this one mapping
        # (render_entity_pass / render_relationship_pass), never input by input.
        prompt_inputs = named_values(locals(), PROMPT_KWARG_NAMES)
        logger.info("harvest_extraction_started", chunk_length=len(chunk_content))

        known_entities = known.block() if known is not None else ""
        prompts = self._render_entity_harvest(prompt_inputs, chunk_content, known_entities)
        sentences = prompts.sentences
        numbered_text = prompts.numbered_text

        # Resolve filtering and domain limit overrides: the per-chunk
        # entity-count cap and alias length come from the FilteringConfig
        # (slider-driven) with the plan's domain limits (already moved with
        # the slider) on top, extraction-settings defaults last.
        extraction_cfg = self.settings.extraction
        limits = self.resolve_harvest_limits(
            domain_extraction_limits=domain_extraction_limits,
            filtering_mode=filtering_mode,
            evidence_validation_mode=evidence_validation_mode,
            filtering_config=filtering_config,
            relation_vocabulary=relation_vocabulary,
            name_conventions=name_conventions,
            type_vocabulary=type_vocabulary,
        )
        filtering_config = limits.filtering_config
        _max_entity_count_override = limits.max_entity_count
        _min_alias_len = limits.min_alias_length

        # Pass 1: Extract entities + properties
        pass1_result = await self.call_llm(
            prompts.entity_prompt,
            temperature=temperature_override,
            max_tokens=max_tokens_override,
            max_entity_count_override=_max_entity_count_override,
            on_stream_progress=on_stream_progress,
        )
        pass1_content = pass1_result.content
        pass1_input_tokens = pass1_result.input_tokens
        pass1_output_tokens = pass1_result.output_tokens
        pass1_finish_reason = pass1_result.finish_reason
        pass1_aborted = pass1_result.aborted_by_loop

        # Workstream 2 (2026-05-08): aggregate parser drops across both
        # passes so the chunk-extraction handler can surface them as a
        # row-level quality counter.
        parser_stats: dict[str, int] = {"dropped_lines": 0}

        # Parse pass 1 and filter entities between passes.
        harvest = await self.parse_entity_harvest(
            pass1_content,
            sentences=sentences,
            chunk_content=chunk_content,
            limits=limits,
            entity_exclusions=entity_exclusions,
            valid_entity_type_names=valid_entity_type_names,
            named_referent_types=named_referent_types,
            normalization_rules=normalization_rules,
            property_type_mapping=property_type_mapping,
            parser_stats=parser_stats,
            adapter=adapter,
            source_id=source_id,
            database_name=database_name,
            known=known,
        )
        entities = harvest.entities
        evidence_stats = harvest.evidence_stats
        filtering_log = harvest.filtering_log

        logger.info(
            "harvest_entities_complete",
            entity_count=len(entities),
            property_count=harvest.property_count,
            pass1_input_tokens=pass1_input_tokens,
            pass1_output_tokens=pass1_output_tokens,
        )

        # Pass 2: Extract relationships using filtered entity list.
        # Type-constraint validation and relationship-limit enforcement
        # used to run here per-chunk; they now run cross-chunk after dedup
        # in ``apply_cross_chunk_relationship_filters`` (extractor.py).
        pass2_result = await self._extract_relationships(
            entities=entities,
            numbered_text=numbered_text,
            sentences=sentences,
            prompt_inputs=prompt_inputs,
            extraction_cfg=extraction_cfg,
            filtering_config=filtering_config,
            evidence_stats=evidence_stats,
            filtering_log=filtering_log,
            chunk_content=chunk_content,
            _max_entity_count_override=_max_entity_count_override,
            _min_alias_len=_min_alias_len,
            relation_vocabulary=limits.relation_vocabulary,
            name_conventions=limits.name_conventions,
            parser_stats=parser_stats,
            on_stream_progress=on_stream_progress,
            temperature_override=temperature_override,
            max_tokens_override=max_tokens_override,
            adapter=adapter,
            source_id=source_id,
            database_name=database_name,
            known=known,
        )
        relationships = pass2_result["relationships"]
        pass2_content = pass2_result["content"]
        pass2_input_tokens = pass2_result["input_tokens"]
        pass2_output_tokens = pass2_result["output_tokens"]
        invalid_count = pass2_result["invalid_count"]
        pass2_finish_reason = pass2_result.get("finish_reason", "stop")
        pass2_aborted = bool(pass2_result.get("aborted_by_loop", False))

        # Build combined metrics
        input_tokens = pass1_input_tokens + pass2_input_tokens
        output_tokens = pass1_output_tokens + pass2_output_tokens

        # Combine pass-1 and pass-2 observability so the chunk handler
        # learns about truncation / loop-abort that happened in either
        # pass without having to peek at intermediate state. "length"
        # wins over "stop" (any truncation truncated the chunk);
        # "aborted_by_loop" is a logical OR.
        combined_finish_reason = combine_finish_reasons(pass1_finish_reason, pass2_finish_reason)
        combined_aborted_by_loop = pass1_aborted or pass2_aborted

        _record_extraction_metrics(
            metrics_collector=metrics_collector,
            chunk_content=chunk_content,
            entities=entities,
            relationships=relationships,
            pass1_input_tokens=pass1_input_tokens,
            pass1_output_tokens=pass1_output_tokens,
            pass2_input_tokens=pass2_input_tokens,
            pass2_output_tokens=pass2_output_tokens,
        )

        # Capture the reusable prompt *templates* (placeholders where the
        # per-chunk text / pass-1 entities get injected) for UI display,
        # rather than this chunk's filled-in prompts.
        entity_prompt_template, relationship_prompt_template = self.render_plan_templates(
            prompt_inputs
        )

        extraction_metrics = _build_extraction_metrics(
            pass1_content=pass1_content,
            pass2_content=pass2_content,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            entities=entities,
            relationships=relationships,
            invalid_count=invalid_count,
            evidence_stats=evidence_stats,
            sentences=sentences,
            filtering_log=filtering_log,
            entity_prompt=entity_prompt_template,
            relationship_prompt=relationship_prompt_template,
            system_prompt=prompts.system_prompt,
            extraction_rules_template=self.EXTRACTION_RULES_TEMPLATE,
            node_templates_formatted=node_templates_formatted,
            edge_templates_formatted=edge_templates_formatted,
            entity_guidance=entity_guidance,
            relationship_guidance=relationship_guidance,
            entity_examples=entity_examples,
            relationship_examples=relationship_examples,
            finish_reason=combined_finish_reason,
            aborted_by_loop=combined_aborted_by_loop,
            parser_lines_dropped=parser_stats["dropped_lines"],
            parser_lines_parsed=parser_stats.get("parsed_lines", 0),
        )

        return entities, relationships, input_tokens, output_tokens, extraction_metrics

    async def _extract_relationships(
        self,
        *,
        entities: list[dict[str, Any]],
        numbered_text: str,
        sentences: list[str],
        prompt_inputs: Mapping[str, Any],
        extraction_cfg: Any,
        filtering_config: Any,
        evidence_stats: dict[str, Any],
        filtering_log: Any,
        chunk_content: str,
        _max_entity_count_override: int | None,
        _min_alias_len: int,
        relation_vocabulary: RelationVocabulary = EMPTY_RELATION_VOCABULARY,
        name_conventions: NameConventions = DEFAULT_NAME_CONVENTIONS,
        parser_stats: dict[str, int] | None = None,
        on_stream_progress: Callable[[], Awaitable[None] | None] | None = None,
        temperature_override: float | None = None,
        max_tokens_override: int | None = None,
        adapter: Any | None = None,
        source_id: str | None = None,
        database_name: str | None = None,
        known: KnownEntities | None = None,
    ) -> dict[str, Any]:
        """Run pass 2: extract relationships using filtered entity list.

        Calls the LLM with the relationship prompt, parses the output, then
        validates structural integrity (bounds, self-loops) and runs the
        per-chunk evidence filter (which depends on chunk-local sentences).
        Type-constraint validation and relationship-limit enforcement run
        cross-chunk after dedup -- see
        ``apply_cross_chunk_relationship_filters`` in extractor.py.

        Args:
            entities: Filtered entity list from pass 1.
            numbered_text: Numbered sentences text.
            sentences: Original sentences list for evidence validation.
            prompt_inputs: The plan's prompt inputs (pass 2 renders its own).
            extraction_cfg: Extraction settings.
            filtering_config: Resolved filtering configuration.
            evidence_stats: Evidence statistics dict (mutated in place).
            filtering_log: FilteringLog instance for tracking removals.
            chunk_content: Original chunk text for logging.
            _max_entity_count_override: Domain override for max entity count.
            _min_alias_len: Minimum alias length.
            relation_vocabulary: The domain's relation vocabulary for the
                evidence filter.
            name_conventions: The domain's naming conventions for the
                evidence filter.
            on_stream_progress: Optional best-effort callback fired on every
                pass-2 LLM stream chunk. Forwarded to ``call_llm`` so
                ``last_activity_at`` keeps moving while a long-streaming
                relationship pass is still emitting tokens.
            known: The source's known entities so far, the list pass 1's
                name guard read, for the evidence filter's known endpoints.

        Returns:
            Dict with relationships, content, input_tokens, output_tokens,
            and invalid_count.
        """
        relationships: list[dict[str, Any]] = []
        pass2_input_tokens = 0
        pass2_output_tokens = 0
        pass2_content = ""
        invalid_count = 0
        pass2_finish_reason = "stop"
        pass2_aborted = False

        if not entities:
            logger.warning(
                "harvest_relationships_skipped_no_entities",
                chunk_length=len(chunk_content),
            )
            return {
                "relationships": relationships,
                "content": pass2_content,
                "input_tokens": pass2_input_tokens,
                "output_tokens": pass2_output_tokens,
                "invalid_count": invalid_count,
                "finish_reason": pass2_finish_reason,
                "aborted_by_loop": pass2_aborted,
            }

        relationship_prompt = self._render_relationship(prompt_inputs, entities, numbered_text)

        pass2_result = await self.call_llm(
            relationship_prompt,
            temperature=temperature_override,
            max_tokens=max_tokens_override,
            max_entity_count_override=_max_entity_count_override,
            on_stream_progress=on_stream_progress,
            known_entity_count=len(entities),
        )
        pass2_content = pass2_result.content
        pass2_input_tokens = pass2_result.input_tokens
        pass2_output_tokens = pass2_result.output_tokens
        pass2_finish_reason = pass2_result.finish_reason
        pass2_aborted = pass2_result.aborted_by_loop

        relationships, invalid_count = await self.parse_relationship_harvest(
            pass2_content,
            entities=entities,
            sentences=sentences,
            limits=HarvestLimits(
                filtering_config=filtering_config,
                max_entity_count=_max_entity_count_override or 0,
                min_alias_length=_min_alias_len,
                relation_vocabulary=relation_vocabulary,
                name_conventions=name_conventions,
            ),
            filtering_log=filtering_log,
            evidence_stats=evidence_stats,
            parser_stats=parser_stats,
            extraction_cfg=extraction_cfg,
            adapter=adapter,
            source_id=source_id,
            database_name=database_name,
            known=known,
        )

        logger.info(
            "harvest_relationships_complete",
            relationship_count=len(relationships),
            invalid_relationship_count=invalid_count,
            pass2_input_tokens=pass2_input_tokens,
            pass2_output_tokens=pass2_output_tokens,
        )

        return {
            "relationships": relationships,
            "content": pass2_content,
            "input_tokens": pass2_input_tokens,
            "output_tokens": pass2_output_tokens,
            "invalid_count": invalid_count,
            "finish_reason": pass2_finish_reason,
            "aborted_by_loop": pass2_aborted,
        }

    async def extract_from_chunks(
        self,
        chunks: list[str],
        file_info: dict[str, Any] | None = None,
        adapter: Any | None = None,
        source_id: str | None = None,
        database_name: str | None = None,
        seed: Sequence[Mapping[str, Any]] = (),
        offered_types: Sequence[OfferedType] = (),
    ) -> dict[str, Any]:
        """Extract entities and relationships from text chunks.

        Uses domain analyzers to detect content type and provide
        domain-specific extraction guidance to the LLM.

        Args:
            chunks: List of text chunks
            file_info: Optional file metadata
            adapter: Optional storage adapter for quality counter increments.
                Pass ``None`` (default) when called without a source row
                (e.g. pure CLI / notebook use) — the increment becomes a no-op.
            source_id: Source row ID paired with *adapter*.  No-op when ``None``.
            database_name: Database name for the quality counter update.
                Defaults to ``"default"`` when *adapter* and *source_id* are
                provided but this is omitted.
            seed: Records the KNOWN ENTITIES list starts with (the memory
                path's graph nodes and speaker); empty for the document path.
            offered_types: Templates offered as known types (a scoped memory's,
                D26): listed under the type list of this call's prompts, and an
                entity written with one is stamped for the commit to bind. Empty
                (every document) leaves the prompts as they are.

        Returns:
            Dictionary with entities, relationships, domain info

        """
        if file_info is None:
            file_info = {}

        # Detect the packs (or take the forced ones); ``domain`` is the first's object
        domain, domain_confidence, packs = detect_domain(chunks, file_info, self.settings)

        logger.info(
            "domain_detected",
            domain=getattr(domain, "name", "generic"),
            packs=list(packs),
            confidence=domain_confidence,
            filename=file_info.get("filename", "unknown"),
        )

        # The one extraction plan (shared with the queue planner and the CLI),
        # from the floor plus the detected packs: templates, guidance, examples,
        # limits, preset selector and the type-rescue / plausibility inputs. No
        # source row here, so the preset cascade is the stack's mode, then settings.
        from chaoscypher_core.services.sources.engine.extraction.domains import (
            get_domain_registry,
        )
        from chaoscypher_core.services.sources.engine.extraction.domains.stack import DomainStack
        from chaoscypher_core.services.sources.engine.extraction.extraction_plan import (
            build_extraction_plan,
        )

        registry = get_domain_registry(
            self.settings, database_name=file_info.get("database_name", "default")
        )
        stack = DomainStack.from_stored(registry, packs)
        plan = build_extraction_plan(stack, self.settings)
        chunk_kwargs, shown_types = _with_offered_types(plan.chunk_kwargs, offered_types)

        # Phase 6 (2026-05-08): get per-domain system-prompt override.
        # When set, temporarily replaces self._system_prompt for all chunk calls
        # in this extraction session. Restored to the settings default after the
        # loop (no thread-safety concern — extractor instances are not shared).
        domain_system_prompt_override: str | None = (
            domain.get_system_prompt_override()
            if hasattr(domain, "get_system_prompt_override")
            else None
        )
        original_system_prompt = getattr(self, "_system_prompt", self.SYSTEM_PROMPT)
        if domain_system_prompt_override is not None:
            self._system_prompt = domain_system_prompt_override
            logger.info(
                "domain_system_prompt_override_applied",
                domain=getattr(domain, "name", "generic"),
                system_prompt_length=len(domain_system_prompt_override),
            )

        all_entities: list[dict[str, Any]] = []
        all_relationships: list[dict[str, Any]] = []

        # The running list (spec 2026-10-11): each group is shown what the
        # earlier ones named, and guarded against crossing those names.
        from chaoscypher_core.services.sources.engine.extraction.utils.known_entities import (
            KnownEntities,
        )

        known = KnownEntities.for_plan(chunk_kwargs, self.settings, seed=seed)
        # The groups whose extraction raised (each yields nothing): a strict caller
        # (a memory's link) reads a model that failed as a failure, not as no facts.
        chunk_failures = 0

        for chunk_idx, chunk in enumerate(chunks):
            try:
                logger.info(
                    "extracting_from_chunk",
                    chunk_number=chunk_idx + 1,
                    total_chunks=len(chunks),
                    chunk_length=len(chunk),
                )

                (
                    chunk_entities,
                    chunk_relationships,
                    _,
                    _,
                    _chunk_metrics,
                ) = await self.extract_single_chunk(
                    chunk_content=chunk,
                    **chunk_kwargs,
                    adapter=adapter,
                    source_id=source_id,
                    database_name=database_name,
                    known=known,
                )
                # The written type is still on each entity here (the stamp reads it).
                stamp_offered_types(chunk_entities, shown_types)
                # Folded in here, with the chunk-local edge indices: the offset loop
                # below rewrites them in place. That loop cannot raise on indices
                # ``validate_relationships`` checked, so a group added here is one
                # whose records also reach ``all_entities``.
                known.add_group(chunk_entities, chunk_relationships)

                chunk_entity_start_idx = len(all_entities)

                # Add chunk metadata to entities
                for entity in chunk_entities:
                    entity["chunk_index"] = chunk_idx
                    all_entities.append(entity)

                # Offset chunk-local indices to global indices
                # validate_relationships already ensured source/target are valid ints
                for rel in chunk_relationships:
                    rel["source"] = rel["source"] + chunk_entity_start_idx
                    rel["target"] = rel["target"] + chunk_entity_start_idx
                    rel["chunk_index"] = chunk_idx
                    all_relationships.append(rel)

                logger.info(
                    "chunk_extraction_complete",
                    chunk_number=chunk_idx + 1,
                    entity_count=len(chunk_entities),
                    relationship_count=len(chunk_relationships),
                )

            except Exception as e:
                chunk_failures += 1
                # the error may quote the chunk: inside private_text() (a memory) only its type
                log_failure(logger, "chunk_extraction_failed", e, chunk_number=chunk_idx + 1)
                if adapter is not None and source_id is not None:
                    await increment_quality_counter(
                        adapter=adapter,
                        source_id=source_id,
                        database_name=database_name or "default",
                        counter=QualityCounter.STANDALONE_CHUNK_FAILURES,
                    )

        # Phase 6: restore original system prompt after domain-overridden extraction.
        self._system_prompt = original_system_prompt

        # Merge within-task duplicate relationships (same chunk = same context)
        before_merge = len(all_relationships)
        all_relationships = _merge_within_task_relationships(all_relationships)
        merged_count = before_merge - len(all_relationships)
        if merged_count > 0:
            logger.info(
                "within_task_relationships_merged",
                before=before_merge,
                after=len(all_relationships),
                merged=merged_count,
            )

        density_stats = calculate_density_stats(chunks, all_entities)
        logger.info("known_entities_list", **known.stats())

        result: dict[str, Any] = {
            "entities": all_entities,
            "relationships": all_relationships,
            # The packs' label: what a finalize threads as its one-name string
            # (one pack's label is its name, the floor alone's ``generic``).
            "domain": packs_label(packs),
            "domain_confidence": domain_confidence,
            "normalization_rules": chunk_kwargs["normalization_rules"] or {},
            "extraction_limits": chunk_kwargs["domain_extraction_limits"],
            # Sibling to extraction_limits; the cross-chunk caller passes
            # this directly as the preset selector instead of fishing it
            # out of the overrides dict.
            "filtering_mode": plan.filtering_mode,
            # The plan's FilteringConfig: the cross-chunk stages and the
            # dedup threshold read this one, as on the queue and CLI paths.
            "filtering_config": plan.filtering_config,
            "density_stats": density_stats,
            # Bubbled up so the cross-chunk filter pass in
            # ``extract_entities_from_groups`` can validate edge types
            # against canonical entities post-dedup.
            "edge_type_constraints": plan.edge_type_constraints or {},
            # The plan's type vocabulary (packs, subtypes, aliases): the
            # standalone caller rewrites old type names with the same one the
            # per-chunk rescue read (``type_normalizer.canonicalize_entity_types``).
            "type_vocabulary": chunk_kwargs["type_vocabulary"],
            # The running list's size: the groups folded in and the entities named.
            "known_entities": known.stats(),
            "chunk_failures": chunk_failures,
        }

        return result


def _with_offered_types(
    chunk_kwargs: Mapping[str, Any], offered_types: Sequence[OfferedType]
) -> tuple[Mapping[str, Any], list[OfferedType]]:
    """``chunk_kwargs`` with the offered types' lines under its type list, and those types.

    A copy for one call; with no offered types, or none the prompt shows
    (``offered_types.offered_in_prompt``), ``chunk_kwargs`` itself and no types. Only the
    types returned are stamped: a type the prompt hid binds nothing.
    """
    if not offered_types:
        return chunk_kwargs, []
    listed = str(chunk_kwargs["node_templates_formatted"] or "").rstrip("\n")
    shown = offered_in_prompt(offered_types, declared_type_names(listed))
    if not shown:
        return chunk_kwargs, []
    block = render_offered_types(shown, ())
    typed = f"{listed}\n{block}" if listed else block
    return {**chunk_kwargs, "node_templates_formatted": typed}, shown


def _merge_within_task_relationships(
    relationships: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge duplicate relationships extracted from the same chunk context.

    When the same (source, target, type) triple appears multiple times within
    the standalone extraction path, these are true duplicates from the same
    context. Merges them by combining justifications and keeping the highest
    confidence.

    Args:
        relationships: List of relationship dicts with source, target, type keys.

    Returns:
        Merged relationship list.

    """
    if not relationships:
        return relationships

    groups: dict[tuple[Any, Any, str], list[dict[str, Any]]] = {}
    for rel in relationships:
        src = rel.get("source", rel.get("source_index"))
        tgt = rel.get("target", rel.get("target_index"))
        rtype = (rel.get("type") or "").strip().lower()
        key = (src, tgt, rtype)
        groups.setdefault(key, []).append(rel)

    merged: list[dict[str, Any]] = []
    for group in groups.values():
        if len(group) == 1:
            merged.append(group[0])
            continue

        base = dict(group[0])
        base["confidence"] = max(r.get("confidence", 0.0) for r in group)

        # Combine justifications (deduplicated)
        seen_justifications: list[str] = []
        for r in group:
            j = (r.get("justification") or "").strip()
            if j and j not in seen_justifications:
                seen_justifications.append(j)
        if seen_justifications:
            base["justification"] = "; ".join(seen_justifications)

        # Combine sent_refs (deduplicated)
        seen_refs: list[str] = []
        for r in group:
            ref = (r.get("sent_ref") or "").strip()
            for raw_part in ref.split(","):
                cleaned = raw_part.strip()
                if cleaned and cleaned not in seen_refs:
                    seen_refs.append(cleaned)
        if seen_refs:
            base["sent_ref"] = ", ".join(seen_refs)

        # Keep every cited sentence the merged duplicates carried.
        if any(r.get("evidence") for r in group):
            evidence: list[dict[str, Any]] = []
            for r in group:
                evidence = merge_evidence(evidence, r.get("evidence"))
            base["evidence"] = evidence

        merged.append(base)

    return merged
