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

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

import structlog


if TYPE_CHECKING:
    from chaoscypher_core.ports.llm import LLMProviderPort
    from chaoscypher_core.services.sources.engine.extraction.domains.config_schema import (
        ExclusionRule,
    )
    from chaoscypher_core.settings import EngineSettings

from chaoscypher_core.services.quality.counters import (
    QualityCounter,
    increment_quality_counter,
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
from chaoscypher_core.services.sources.engine.extraction.utils.prompts import (
    ENTITY_HARVEST_TEMPLATE,
    EXTRACTION_RULES_TEMPLATE,
    RELATIONSHIP_HARVEST_TEMPLATE,
    SYSTEM_PROMPT,
)
from chaoscypher_core.services.sources.engine.extraction.utils.quality_analyzer import (
    calculate_density_stats,
)
from chaoscypher_core.services.sources.engine.extraction.utils.template_formatter import (
    format_domain_edge_templates,
    format_domain_node_templates,
    format_entity_examples,
    format_relationship_examples,
)
from chaoscypher_core.services.sources.engine.extraction.utils.type_inferencer import (
    detect_domain,
)
from chaoscypher_core.services.sources.engine.extraction.utils.type_rescue import (
    rescue_invalid_entity_types,
)
from chaoscypher_core.utils.tokens import estimate_message_tokens, estimate_tokens


logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class CallLLMResult:
    """Result of one streaming LLM extraction call.

    Combines the original ``(content, input_tokens, output_tokens)``
    triple with two observability signals introduced by Workstream 8:

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
    """

    def __init__(
        self,
        extraction_cfg: Any,
        max_entity_count_override: int | None = None,
    ) -> None:
        """Initialise detector state and bind loop-detection thresholds."""
        self.entity_count = 0
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
    on_chunk: Callable[[], None] | None = None,
) -> tuple[str, int, int, str, bool]:
    """Consume an LLM streaming response with loop detection.

    Iterates over stream chunks, accumulates content, and checks each
    completed line against the ``detector`` for degenerate patterns.
    Aborts early if a loop is detected. Raises on stream-level errors.

    Workstream 8 (2026-05-07) extends the return shape with two
    observability signals: the normalized provider ``finish_reason`` and
    a ``aborted`` flag derived from ``detector.aborted``. The trailing
    partial line (no terminating newline) is flushed through the
    detector so the last entity / relationship line is no longer
    silently dropped when the model tops out mid-token.

    Args:
        response: Async-iterable stream of chunk dicts from the LLM provider.
        detector: Loop detector that inspects completed output lines.
        on_chunk: Optional best-effort callback fired on every received
            content chunk so callers can use stream activity (not
            wall-clock time) as the liveness signal for SourceRecovery.
            The 'done' chunk is metadata — the callback is NOT invoked
            for it. Errors raised by the callback are logged at WARNING
            and suppressed so a flaky heartbeat never aborts extraction.

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
                    on_chunk()
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
            error_msg = chunk.get("error", "Unknown LLM error")
            logger.error(
                "llm_extraction_stream_error",
                error=error_msg,
            )
            if "not found" in error_msg.lower():
                from chaoscypher_core.exceptions import LLMModelError

                raise LLMModelError(
                    provider="unknown",
                    model="unknown",
                    reason=error_msg,
                )
            from chaoscypher_core.exceptions import LLMError

            raise LLMError(error_msg)

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
        extraction_cfg, "extraction_filtering_mode", "balanced"
    )
    config = resolve_filtering_config(
        mode=str(_filtering_mode),
        domain_overrides=dict(domain_limits) if domain_limits else None,
    )
    if evidence_validation_mode:
        config.evidence_validation_mode = evidence_validation_mode
    return config


def _resolve_loop_max_entity_count(
    *,
    filtering_config: Any,
    domain_limits: dict[str, Any],
    extraction_cfg: Any,
) -> int:
    """Resolve the per-chunk entity-count cap for the streaming loop detector.

    Precedence (highest first):
    1. Domain extraction limit ``loop_max_entity_count`` — already shipped
       in some domain configs and kept as the highest-priority override.
    2. ``filtering_config.loop_max_entity_count`` — slider-driven cap; the
       canonical knob users adjust via filtering mode.
    3. ``extraction_cfg.loop_max_entity_count`` — final fallback for tests
       and call sites that have no FilteringConfig in scope.

    Args:
        filtering_config: Resolved FilteringConfig (may be None at deep
            internal call sites).
        domain_limits: Domain extraction limit overrides.
        extraction_cfg: Extraction settings instance.

    Returns:
        Integer max entity count for the per-chunk loop detector.
    """
    if "loop_max_entity_count" in domain_limits:
        domain_value = int(domain_limits["loop_max_entity_count"])
        slider_value = (
            getattr(filtering_config, "loop_max_entity_count", None)
            if filtering_config is not None
            else None
        )
        if slider_value is not None and int(slider_value) != domain_value:
            logger.debug(
                "filter_field_overridden_by_domain",
                field="loop_max_entity_count",
                slider_value=int(slider_value),
                domain_value=domain_value,
            )
        return domain_value
    if filtering_config is not None:
        cap = getattr(filtering_config, "loop_max_entity_count", None)
        if cap is not None:
            return int(cap)
    return int(extraction_cfg.loop_max_entity_count)


def _resolve_minimum_alias_length(
    *,
    filtering_config: Any,
    domain_limits: dict[str, Any],
    extraction_cfg: Any,
) -> int:
    """Resolve the minimum alias length for the line parser.

    Precedence mirrors :func:`_resolve_loop_max_entity_count`: domain
    limits beat FilteringConfig beats extraction settings.

    Args:
        filtering_config: Resolved FilteringConfig (may be None).
        domain_limits: Domain extraction limit overrides.
        extraction_cfg: Extraction settings instance.

    Returns:
        Integer minimum alias length.
    """
    if "minimum_alias_length" in domain_limits:
        domain_value = int(domain_limits["minimum_alias_length"])
        slider_value = (
            getattr(filtering_config, "minimum_alias_length", None)
            if filtering_config is not None
            else None
        )
        if slider_value is not None and int(slider_value) != domain_value:
            logger.debug(
                "filter_field_overridden_by_domain",
                field="minimum_alias_length",
                slider_value=int(slider_value),
                domain_value=domain_value,
            )
        return domain_value
    if filtering_config is not None:
        value = getattr(filtering_config, "minimum_alias_length", None)
        if value is not None:
            return int(value)
    return int(extraction_cfg.minimum_alias_length)


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


def _build_entity_prompt(
    *,
    template: str,
    numbered_text: str,
    node_templates_formatted: str,
    entity_exclusions: list[ExclusionRule] | None,
    strict_entity_types: bool,
    entity_guidance: str | None,
    entity_examples: str | None,
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

    Returns:
        Formatted entity extraction prompt.
    """
    exclusion_lines = ""
    if entity_exclusions:
        exclusion_lines = "- SKIP these — they are NOT entities:\n" + "\n".join(
            f"  * {rule.as_prompt_text()}" for rule in entity_exclusions
        )

    strict_instruction = ""
    if strict_entity_types:
        strict_instruction = (
            "\nIMPORTANT: ONLY use the entity types listed above. "
            "Do NOT invent new types. If an entity does not fit any "
            "listed type, skip it or use the closest match."
        )

    prompt = template.format(
        numbered_sentences=numbered_text,
        node_templates=node_templates_formatted,
        entity_exclusions=exclusion_lines,
        strict_type_instruction=strict_instruction,
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
    edge_templates: str,
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
        edge_templates: Formatted edge template descriptions.
        relationship_guidance: Additional relationship guidance text.
        relationship_examples: Relationship extraction examples.

    Returns:
        Formatted relationship extraction prompt.
    """
    prompt = template.format(
        numbered_sentences=numbered_sentences,
        entity_list=entity_list,
        max_entity_index=max_entity_index,
        edge_templates=edge_templates,
    )
    if relationship_guidance:
        prompt = f"{prompt}\n\nAdditional guidance:\n{relationship_guidance}"
    if relationship_examples:
        prompt = f"{prompt}\n\n{relationship_examples}"
    return prompt


async def _filter_entities(
    *,
    entities: list[dict[str, Any]],
    sentences: list[str],
    chunk_content: str,
    filtering_config: Any,
    entity_exclusions: list[ExclusionRule] | None,
    strict_entity_types: bool,
    valid_entity_type_names: set[str] | None,
    named_referent_types: set[str] | None,
    normalization_rules: dict[str, list[str]] | None,
    property_type_mapping: dict[str, dict[str, str]] | None,
    filtering_log: Any,
    adapter: Any | None = None,
    source_id: str | None = None,
    database_name: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Apply all entity filters between pass 1 and pass 2.

    Runs evidence validation, exclusion filtering, type rescue, and
    plausibility filtering in sequence. Each filter narrows the entity
    list so pass 2 only sees clean, validated entities.

    Args:
        entities: Raw entities from pass 1.
        sentences: Numbered sentences from chunk text.
        chunk_content: Original chunk text for visual content detection.
        filtering_config: Resolved filtering configuration.
        entity_exclusions: Domain-specific exclusion rules.
        strict_entity_types: Whether strict type enforcement is enabled.
        valid_entity_type_names: Allowed entity type names.
        named_referent_types: Entity types requiring proper names.
        normalization_rules: Domain normalization rules for type rescue.
        property_type_mapping: Domain property-type mapping for type rescue.
        filtering_log: FilteringLog instance for tracking removals.

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
        )
        evidence_stats.update(entity_stats)

    # Code-level exclusion filter
    if filtering_config.enable_entity_exclusions and entity_exclusions:
        entities, _excl_mapping = filter_excluded_entities(
            entities, entity_exclusions, filtering_log=filtering_log
        )

    # Type rescue (Phase 6: gated on enable_type_rescue; disabled for unfiltered/minimal)
    _type_rescue_enabled = getattr(filtering_config, "enable_type_rescue", True)
    if _type_rescue_enabled and strict_entity_types and valid_entity_type_names:
        entities, _pass1_rels_unused, _type_mapping, rescue_stats = rescue_invalid_entity_types(
            entities,
            [],
            valid_types=valid_entity_type_names,
            normalization_rules=normalization_rules or {},
            property_type_mapping=property_type_mapping or {},
            filtering_log=filtering_log,
        )
        evidence_stats["type_rescue"] = rescue_stats

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
        )

    return entities, evidence_stats


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
        "raw_llm_response": (
            f"=== PASS 1 (Entities) ===\n{pass1_content}\n\n"
            f"=== PASS 2 (Relationships) ===\n{pass2_content}"
        ),
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


def _safe_type_aliases(domain: Any) -> dict[str, str]:
    """Return the domain's type_aliases mapping, defensively.

    Skips when the domain doesn't implement the accessor, when the
    accessor raises, or when the return value isn't a ``dict[str, str]``.
    Defensive against MagicMock test fixtures that auto-respond to
    ``hasattr`` with a child MagicMock rather than a real dict.
    """
    accessor = getattr(domain, "get_type_aliases", None)
    if not callable(accessor):
        return {}
    try:
        result = accessor()
    except Exception:
        return {}
    if not isinstance(result, dict):
        return {}
    return {
        str(k): str(v)
        for k, v in result.items()
        if isinstance(k, str) and isinstance(v, str) and k and v
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
        on_stream_progress: Callable[[], None] | None = None,
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
        )

        # Stream and monitor for degenerate loops
        detector = _StreamLoopDetector(
            extraction_cfg=self.settings.extraction,
            max_entity_count_override=max_entity_count_override,
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
        filtering_config: Any | None = None,
        on_stream_progress: Callable[[], None] | None = None,
        temperature_override: float | None = None,
        max_tokens_override: int | None = None,
        adapter: Any | None = None,
        source_id: str | None = None,
        database_name: str | None = None,
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
            strict_entity_types: When True, adds prompt instruction to only
                use listed entity types and filters non-matching entities.
            valid_entity_type_names: Set of allowed entity type names for
                strict filtering. Required when strict_entity_types is True.
            evidence_validation_mode: Per-domain override for evidence validation
                strictness. If None, falls back to ``ExtractionSettings``.
            named_referent_types: Entity types that require proper names.
                Used by the plausibility filter to apply stricter thresholds.
            normalization_rules: Domain normalization rules for type rescue.
                Maps target_type → list of trigger keywords.
            property_type_mapping: Domain property-type mapping for type rescue.
                Maps invalid_type → {"target_type": ..., "property": ...}.
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

        Returns:
            Tuple of (entities, relationships, input_tokens, output_tokens, extraction_metrics)

        """
        from chaoscypher_core.services.sources.engine.extraction.utils.sentence_splitter import (
            format_numbered_sentences,
            split_into_sentences,
        )

        logger.info("harvest_extraction_started", chunk_length=len(chunk_content))

        sentences = split_into_sentences(chunk_content)
        numbered_text = format_numbered_sentences(sentences)

        # Resolve filtering and domain limit overrides
        extraction_cfg = self.settings.extraction
        _domain_limits = domain_extraction_limits or {}
        filtering_config = _resolve_chunk_filtering_config(
            filtering_config=filtering_config,
            filtering_mode=filtering_mode,
            domain_limits=_domain_limits,
            extraction_cfg=extraction_cfg,
            evidence_validation_mode=evidence_validation_mode,
        )

        # Resolve the per-chunk entity-count cap and alias length from the
        # FilteringConfig (slider-driven) with domain limits as the highest
        # override and extraction-settings defaults as the final fallback.
        _max_entity_count_override = _resolve_loop_max_entity_count(
            filtering_config=filtering_config,
            domain_limits=_domain_limits,
            extraction_cfg=extraction_cfg,
        )
        _min_alias_len = _resolve_minimum_alias_length(
            filtering_config=filtering_config,
            domain_limits=_domain_limits,
            extraction_cfg=extraction_cfg,
        )

        # Pass 1: Extract entities + properties
        entity_prompt = _build_entity_prompt(
            template=self.ENTITY_HARVEST_TEMPLATE,
            numbered_text=numbered_text,
            node_templates_formatted=node_templates_formatted,
            entity_exclusions=entity_exclusions,
            strict_entity_types=strict_entity_types,
            entity_guidance=entity_guidance,
            entity_examples=entity_examples,
        )

        pass1_result = await self.call_llm(
            entity_prompt,
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

        entities, _pass1_relationships, properties = parse_extraction_output(
            pass1_content,
            max_out_of_bounds=extraction_cfg.loop_max_out_of_bounds,
            max_source_type_repeat=extraction_cfg.loop_max_source_type_repeat,
            skip_loop_detection=True,
            minimum_alias_length=_min_alias_len,
            stats=parser_stats,
        )

        if properties:
            apply_properties_to_entities(entities, properties)

        logger.info(
            "harvest_entities_complete",
            entity_count=len(entities),
            property_count=len(properties),
            pass1_input_tokens=pass1_input_tokens,
            pass1_output_tokens=pass1_output_tokens,
        )

        # Filter entities between passes
        from chaoscypher_core.services.sources.engine.extraction.utils.filtering_log import (
            FilteringLog,
        )

        filtering_log = FilteringLog()
        entities, evidence_stats = await _filter_entities(
            entities=entities,
            sentences=sentences,
            chunk_content=chunk_content,
            filtering_config=filtering_config,
            entity_exclusions=entity_exclusions,
            strict_entity_types=strict_entity_types,
            valid_entity_type_names=valid_entity_type_names,
            named_referent_types=named_referent_types,
            normalization_rules=normalization_rules,
            property_type_mapping=property_type_mapping,
            filtering_log=filtering_log,
            adapter=adapter,
            source_id=source_id,
            database_name=database_name,
        )

        # Pass 2: Extract relationships using filtered entity list.
        # Type-constraint validation and relationship-limit enforcement
        # used to run here per-chunk; they now run cross-chunk after dedup
        # in ``apply_cross_chunk_relationship_filters`` (extractor.py).
        pass2_result = await self._extract_relationships(
            entities=entities,
            numbered_text=numbered_text,
            sentences=sentences,
            edge_templates_formatted=edge_templates_formatted,
            relationship_guidance=relationship_guidance,
            relationship_examples=relationship_examples,
            extraction_cfg=extraction_cfg,
            filtering_config=filtering_config,
            evidence_stats=evidence_stats,
            filtering_log=filtering_log,
            chunk_content=chunk_content,
            _max_entity_count_override=_max_entity_count_override,
            _min_alias_len=_min_alias_len,
            parser_stats=parser_stats,
            on_stream_progress=on_stream_progress,
            temperature_override=temperature_override,
            max_tokens_override=max_tokens_override,
            adapter=adapter,
            source_id=source_id,
            database_name=database_name,
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
        combined_finish_reason = (
            "length"
            if "length" in (pass1_finish_reason, pass2_finish_reason)
            else (pass1_finish_reason if pass1_finish_reason != "stop" else pass2_finish_reason)
        )
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
        entity_prompt_template = _build_entity_prompt(
            template=self.ENTITY_HARVEST_TEMPLATE,
            numbered_text=PROMPT_CHUNK_TEXT_PLACEHOLDER,
            node_templates_formatted=node_templates_formatted,
            entity_exclusions=entity_exclusions,
            strict_entity_types=strict_entity_types,
            entity_guidance=entity_guidance,
            entity_examples=entity_examples,
        )
        relationship_prompt_template = _build_relationship_prompt(
            template=self.RELATIONSHIP_HARVEST_TEMPLATE,
            numbered_sentences=PROMPT_CHUNK_TEXT_PLACEHOLDER,
            entity_list=PROMPT_PASS1_ENTITIES_PLACEHOLDER,
            max_entity_index="N",
            edge_templates=edge_templates_formatted,
            relationship_guidance=relationship_guidance,
            relationship_examples=relationship_examples,
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
            system_prompt=getattr(self, "_system_prompt", self.SYSTEM_PROMPT),
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
        )

        return entities, relationships, input_tokens, output_tokens, extraction_metrics

    async def _extract_relationships(
        self,
        *,
        entities: list[dict[str, Any]],
        numbered_text: str,
        sentences: list[str],
        edge_templates_formatted: str,
        relationship_guidance: str | None,
        relationship_examples: str | None,
        extraction_cfg: Any,
        filtering_config: Any,
        evidence_stats: dict[str, Any],
        filtering_log: Any,
        chunk_content: str,
        _max_entity_count_override: int | None,
        _min_alias_len: int,
        parser_stats: dict[str, int] | None = None,
        on_stream_progress: Callable[[], None] | None = None,
        temperature_override: float | None = None,
        max_tokens_override: int | None = None,
        adapter: Any | None = None,
        source_id: str | None = None,
        database_name: str | None = None,
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
            edge_templates_formatted: Formatted edge template descriptions.
            relationship_guidance: Relationship-specific guidance.
            relationship_examples: Relationship-specific examples.
            extraction_cfg: Extraction settings.
            filtering_config: Resolved filtering configuration.
            evidence_stats: Evidence statistics dict (mutated in place).
            filtering_log: FilteringLog instance for tracking removals.
            chunk_content: Original chunk text for logging.
            _max_entity_count_override: Domain override for max entity count.
            _min_alias_len: Minimum alias length.
            on_stream_progress: Optional best-effort callback fired on every
                pass-2 LLM stream chunk. Forwarded to ``call_llm`` so
                ``last_activity_at`` keeps moving while a long-streaming
                relationship pass is still emitting tokens.

        Returns:
            Dict with relationships, content, input_tokens, output_tokens,
            and invalid_count.
        """
        from chaoscypher_core.services.sources.engine.extraction.utils.evidence_validator import (
            filter_relationships_by_evidence,
        )

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

        entity_list_str = self._serialize_entities_for_prompt(entities)

        relationship_prompt = _build_relationship_prompt(
            template=self.RELATIONSHIP_HARVEST_TEMPLATE,
            numbered_sentences=numbered_text,
            entity_list=entity_list_str,
            max_entity_index=len(entities) - 1,
            edge_templates=edge_templates_formatted,
            relationship_guidance=relationship_guidance,
            relationship_examples=relationship_examples,
        )

        pass2_result = await self.call_llm(
            relationship_prompt,
            temperature=temperature_override,
            max_tokens=max_tokens_override,
            max_entity_count_override=_max_entity_count_override,
            on_stream_progress=on_stream_progress,
        )
        pass2_content = pass2_result.content
        pass2_input_tokens = pass2_result.input_tokens
        pass2_output_tokens = pass2_result.output_tokens
        pass2_finish_reason = pass2_result.finish_reason
        pass2_aborted = pass2_result.aborted_by_loop

        _pass2_entities, raw_relationships, _pass2_properties = parse_extraction_output(
            pass2_content,
            max_out_of_bounds=extraction_cfg.loop_max_out_of_bounds,
            max_source_type_repeat=extraction_cfg.loop_max_source_type_repeat,
            skip_loop_detection=True,
            minimum_alias_length=_min_alias_len,
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
            )
            evidence_stats.update(rel_stats)

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

        Returns:
            Dictionary with entities, relationships, domain info

        """
        if file_info is None:
            file_info = {}

        # Detect domain
        domain, domain_confidence = detect_domain(chunks, file_info, self.settings)

        logger.info(
            "domain_detected",
            domain=domain.name,
            confidence=domain_confidence,
            filename=file_info.get("filename", "unknown"),
        )

        # Get domain guidance
        entity_guidance = domain.get_entity_guidance()
        relationship_guidance = domain.get_relationship_guidance()

        # Get templates
        domain_templates = domain.get_templates()
        node_templates_formatted = format_domain_node_templates(domain_templates)
        edge_templates_formatted = format_domain_edge_templates(domain_templates)

        # Get examples
        entity_examples_formatted = ""
        relationship_examples_formatted = ""
        if self.settings.llm.extraction_examples_enabled:
            domain_examples = domain.get_examples()
            if domain_examples:
                entity_examples_formatted = format_entity_examples(
                    domain_examples,
                    max_chars=self.settings.llm.extraction_examples_max_chars,
                )
                relationship_examples_formatted = format_relationship_examples(
                    domain_examples,
                    max_chars=self.settings.llm.extraction_examples_max_chars,
                )

        # Get domain-specific extraction limits (override global settings)
        domain_extraction_limits = domain.get_extraction_limits()
        # Preset selector — sibling to the limits dict, never merged into it.
        domain_filtering_mode: str | None = (
            domain.get_filtering_mode() if hasattr(domain, "get_filtering_mode") else None
        )

        # Get domain-specific entity exclusion rules
        domain_entity_exclusions = domain.get_entity_exclusions()

        # Get per-domain evidence validation mode (if configured)
        domain_evidence_mode = (
            domain.get_evidence_validation_mode()
            if hasattr(domain, "get_evidence_validation_mode")
            else None
        )

        # Get domain property_type_mapping and normalization_rules for type rescue
        domain_normalization_rules = domain.get_normalization_rules()
        domain_property_type_mapping: dict[str, dict[str, str]] = (
            domain.get_property_type_mapping()
            if hasattr(domain, "get_property_type_mapping")
            else {}
        )

        # Get domain edge type constraints for semantic validation
        domain_edge_type_constraints: dict[str, dict[str, list[str]]] = (
            domain.get_edge_type_constraints()
            if hasattr(domain, "get_edge_type_constraints")
            else {}
        )

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
                domain=domain.name,
                system_prompt_length=len(domain_system_prompt_override),
            )

        # Get strict entity type enforcement setting
        domain_strict_types = (
            domain.get_strict_entity_types()
            if hasattr(domain, "get_strict_entity_types")
            else False
        )
        domain_valid_type_names: set[str] | None = None
        if domain_strict_types:
            domain_valid_type_names = {
                t["name"] for t in domain_templates.get("node_templates", []) if t.get("name")
            }

        # Build set of entity types requiring proper names for plausibility filter
        domain_named_referent_types: set[str] | None = None
        node_tmpls = domain_templates.get("node_templates", [])
        named_types = {t["name"] for t in node_tmpls if t.get("requires_named_referent")}
        if named_types:
            domain_named_referent_types = named_types

        all_entities: list[dict[str, Any]] = []
        all_relationships: list[dict[str, Any]] = []

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
                    node_templates_formatted=node_templates_formatted,
                    edge_templates_formatted=edge_templates_formatted,
                    entity_guidance=entity_guidance,
                    relationship_guidance=relationship_guidance,
                    entity_examples=entity_examples_formatted or None,
                    relationship_examples=relationship_examples_formatted or None,
                    domain_extraction_limits=domain_extraction_limits or None,
                    filtering_mode=domain_filtering_mode,
                    entity_exclusions=domain_entity_exclusions or None,
                    strict_entity_types=domain_strict_types,
                    valid_entity_type_names=domain_valid_type_names,
                    evidence_validation_mode=domain_evidence_mode,
                    named_referent_types=domain_named_referent_types,
                    normalization_rules=domain_normalization_rules or None,
                    property_type_mapping=domain_property_type_mapping or None,
                )

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
                logger.exception(
                    "chunk_extraction_failed",
                    chunk_number=chunk_idx + 1,
                    error_type=type(e).__name__,
                    error_message=str(e),
                )
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

        result: dict[str, Any] = {
            "entities": all_entities,
            "relationships": all_relationships,
            "domain": domain.name,
            "domain_confidence": domain_confidence,
            "normalization_rules": domain.get_normalization_rules(),
            "extraction_limits": domain.get_extraction_limits(),
            # Sibling to extraction_limits; the cross-chunk caller passes
            # this directly as the preset selector instead of fishing it
            # out of the overrides dict.
            "filtering_mode": domain_filtering_mode,
            "density_stats": density_stats,
            # Bubbled up so the cross-chunk filter pass in
            # ``extract_entities_from_groups`` can validate edge types
            # against canonical entities post-dedup.
            "edge_type_constraints": domain_edge_type_constraints or {},
            # Phase 5 (2026-05-18): domain-level entity-type aliasing.
            # Collapsed near-duplicate node templates (e.g. literary's
            # ``Historical Figure`` → ``Character``) before dedup so name
            # variants merge correctly and edge-type validation sees the
            # canonical type. Empty {} when the domain declares no aliases
            # or when the accessor returns a non-dict (defensive against
            # mocked test fixtures that haven't stubbed it).
            "type_aliases": _safe_type_aliases(domain),
        }

        return result


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

        merged.append(base)

    return merged
