# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Pure LLM metrics aggregation helpers and collector.

Relocated from `chaoscypher_core.adapters.llm.metrics` as part of the
architecture remediation.  Lives here so both the LLM adapter and
the SQLite adapter can import from a shared, neutral location without creating
a cross-adapter dependency.

Note: `compute_metrics_summary` calls `get_cost_tracker` from
`chaoscypher_core.adapters.llm.cost`.  That dependency is intentional and
pre-existing; it is NOT introduced by this relocation.  A future task can
extract CostTracker into this package if further decoupling is desired.
"""

import statistics
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from chaoscypher_core.adapters.llm.cost import get_cost_tracker
from chaoscypher_core.utils.id import generate_id


__all__ = ["LLMMetricsCollector", "compute_metrics_summary"]


def compute_metrics_summary(
    attempts: list[dict[str, Any]],
    provider: str,
    model: str,
    custom_input_cost: float = 0.0,
    custom_output_cost: float = 0.0,
    outlier_std_dev_threshold: float | None = None,
) -> dict[str, Any]:
    """Compute summary statistics from LLM call attempts.

    Shared logic used by both LLMMetricsCollector.get_summary() and
    SqliteAdapter.compute_llm_summary() to ensure consistent calculations.

    Args:
        attempts: List of attempt dictionaries with keys: success, was_retry,
            input_tokens, output_tokens, duration_ms, error_type
        provider: LLM provider name (openai, anthropic, gemini, ollama)
        model: Model name
        custom_input_cost: Custom cost per million input tokens (for Ollama/self-hosted)
        custom_output_cost: Custom cost per million output tokens (for Ollama/self-hosted)
        outlier_std_dev_threshold: Std-dev multiple above the mean call duration
            beyond which a call counts as a latency outlier. ``None`` (default)
            resolves to ``QualitySettings().llm_outlier_std_dev_threshold`` (the
            class default); app-layer callers holding engine settings inject the
            configured value.

    Returns:
        Summary dictionary with aggregated metrics including:
        - total_calls, successful_calls, failed_calls, retry_calls
        - first_try_successes, retry_successes, permanent_failures
        - total_input_tokens, total_output_tokens, wasted_tokens
        - avg_call_duration_ms, total_duration_ms
        - error_breakdown (dict of error_type -> count)
        - success_rate, retry_rate, waste_percentage
        - outlier_count (calls > 2 std dev from mean duration)
        - estimated_cost_usd (calculated via CostTracker)
        - model
        - total_items_recovered_from_truncation
        - total_items_skipped_due_to_error

    """
    if not attempts:
        return {
            "total_calls": 0,
            "successful_calls": 0,
            "failed_calls": 0,
            "retry_calls": 0,
            "first_try_successes": 0,
            "retry_successes": 0,
            "permanent_failures": 0,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "wasted_tokens": 0,
            "avg_call_duration_ms": None,
            "total_duration_ms": 0,
            "error_breakdown": {},
            "success_rate": 0.0,
            "retry_rate": 0.0,
            "waste_percentage": 0.0,
            "outlier_count": 0,
            "estimated_cost_usd": 0.0,
            "model": model,
            "total_items_recovered_from_truncation": 0,
            "total_items_skipped_due_to_error": 0,
        }

    total_calls = len(attempts)
    successful_calls = sum(1 for a in attempts if a.get("success"))
    failed_calls = total_calls - successful_calls
    retry_calls = sum(1 for a in attempts if a.get("was_retry"))

    # Derived outcome metrics for clearer user understanding
    # First-try successes: calls that succeeded on the first attempt
    first_try_successes = sum(1 for a in attempts if a.get("success") and not a.get("was_retry"))
    # Retry successes: calls that succeeded after retry
    retry_successes = sum(1 for a in attempts if a.get("success") and a.get("was_retry"))
    # Permanent failures: failed calls that were not later recovered by retry
    permanent_failures = max(0, failed_calls - retry_successes)

    total_input_tokens = sum(a.get("input_tokens", 0) or 0 for a in attempts)
    total_output_tokens = sum(a.get("output_tokens", 0) or 0 for a in attempts)
    wasted_tokens = sum(
        (a.get("input_tokens", 0) or 0) + (a.get("output_tokens", 0) or 0)
        for a in attempts
        if not a.get("success")
    )
    total_tokens = total_input_tokens + total_output_tokens

    durations = [
        a.get("duration_ms", 0) or 0 for a in attempts if (a.get("duration_ms", 0) or 0) > 0
    ]
    total_duration_ms = sum(durations)
    avg_call_duration_ms = total_duration_ms // len(durations) if durations else None

    # Error breakdown
    error_breakdown: dict[str, int] = {}
    for a in attempts:
        error_type = a.get("error_type")
        if error_type:
            error_breakdown[error_type] = error_breakdown.get(error_type, 0) + 1

    # Outlier detection (configurable std dev threshold)
    outlier_count = 0
    if len(durations) >= 3:
        if outlier_std_dev_threshold is None:
            from chaoscypher_core.settings import QualitySettings

            outlier_std_dev_threshold = QualitySettings().llm_outlier_std_dev_threshold
        mean_duration = statistics.mean(durations)
        std_duration = statistics.stdev(durations)
        threshold = mean_duration + (outlier_std_dev_threshold * std_duration)
        outlier_count = sum(1 for d in durations if d > threshold)

    # Cost estimation using CostTracker
    # Pass custom costs from settings (for Ollama/self-hosted cost tracking)
    cost_tracker = get_cost_tracker(
        custom_input_cost=custom_input_cost,
        custom_output_cost=custom_output_cost,
    )
    estimated_cost_usd = cost_tracker.calculate_cost(
        provider=provider,
        model=model,
        input_tokens=total_input_tokens,
        output_tokens=total_output_tokens,
    )

    # Truncation and error resilience metrics
    total_items_recovered = sum(a.get("items_recovered_from_truncation", 0) or 0 for a in attempts)
    total_items_skipped = sum(a.get("items_skipped_due_to_error", 0) or 0 for a in attempts)

    return {
        "total_calls": total_calls,
        "successful_calls": successful_calls,
        "failed_calls": failed_calls,
        "retry_calls": retry_calls,
        "first_try_successes": first_try_successes,
        "retry_successes": retry_successes,
        "permanent_failures": permanent_failures,
        "total_input_tokens": total_input_tokens,
        "total_output_tokens": total_output_tokens,
        "wasted_tokens": wasted_tokens,
        "avg_call_duration_ms": avg_call_duration_ms,
        "total_duration_ms": total_duration_ms,
        "error_breakdown": error_breakdown,
        "success_rate": successful_calls / total_calls if total_calls > 0 else 0.0,
        "retry_rate": retry_calls / total_calls if total_calls > 0 else 0.0,
        "waste_percentage": wasted_tokens / total_tokens if total_tokens > 0 else 0.0,
        "outlier_count": outlier_count,
        "estimated_cost_usd": estimated_cost_usd,
        "model": model,
        "total_items_recovered_from_truncation": total_items_recovered,
        "total_items_skipped_due_to_error": total_items_skipped,
    }


@dataclass
class LLMMetricsCollector:
    """Collects LLM call metrics during extraction operations.

    Thread-safe collector that accumulates per-attempt metrics for later
    persistence and aggregation. Designed to be passed to StructuredExtractor
    and other LLM-calling code.

    Example:
        collector = LLMMetricsCollector(
            source_id="src_123",
            operation_type="entity_extraction",
            provider="ollama",
            model="qwen3:30b-instruct",
        )

        # In extraction loop
        collector.record_attempt(
            success=True,
            input_tokens=1500,
            output_tokens=500,
            duration_ms=2500,
        )

        # After extraction
        for attempt in collector.get_all_attempts():
            storage.create_llm_call_metric(attempt)

        summary = collector.get_summary()
    """

    # Context (set at initialization)
    source_id: str | None = None
    chunk_task_id: str | None = None
    database_name: str = ""
    operation_type: str = "entity_extraction"
    provider: str = ""
    model: str = ""

    # Custom cost settings (from user settings, for Ollama/self-hosted cost tracking)
    custom_input_cost: float = 0.0  # Cost per million input tokens
    custom_output_cost: float = 0.0  # Cost per million output tokens

    # Accumulated attempts
    attempts: list[dict[str, Any]] = field(default_factory=list)

    def record_attempt(
        self,
        success: bool,
        input_tokens: int = 0,
        output_tokens: int = 0,
        duration_ms: int = 0,
        was_retry: bool = False,
        retry_reason: str | None = None,
        error_type: str | None = None,
        error_message: str | None = None,
        chunk_index: int | None = None,
        chunk_size_chars: int | None = None,
        entities_extracted: int | None = None,
        relationships_extracted: int | None = None,
        items_recovered_from_truncation: int | None = None,
        items_skipped_due_to_error: int | None = None,
    ) -> None:
        """Record a single LLM call attempt.

        Args:
            success: Whether the call succeeded
            input_tokens: Number of input tokens used
            output_tokens: Number of output tokens generated
            duration_ms: Call duration in milliseconds
            was_retry: Whether this was a retry attempt (not first try)
            retry_reason: Why retry was needed (schema_validation, quality_issues, exception)
            error_type: Type of error if failed (validation_error, timeout, rate_limit, etc.)
            error_message: Error message if failed
            chunk_index: Index of chunk being processed (for extraction)
            chunk_size_chars: Size of input text in characters
            entities_extracted: Number of entities extracted (if successful)
            relationships_extracted: Number of relationships extracted (if successful)
            items_recovered_from_truncation: Number of items recovered from truncated response
            items_skipped_due_to_error: Number of items skipped due to parse errors

        """
        now = datetime.now(tz=UTC)
        self.attempts.append(
            {
                "id": generate_id("llm"),
                "database_name": self.database_name,
                "source_id": self.source_id,
                "chunk_task_id": self.chunk_task_id,
                "operation_type": self.operation_type,
                "call_sequence": len(self.attempts) + 1,
                "provider": self.provider,
                "model": self.model,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "duration_ms": duration_ms,
                "started_at": now,
                "completed_at": now,
                "success": success,
                "error_type": error_type,
                "error_message": error_message,
                "was_retry": was_retry,
                "retry_reason": retry_reason,
                "chunk_index": chunk_index,
                "chunk_size_chars": chunk_size_chars,
                "entities_extracted": entities_extracted,
                "relationships_extracted": relationships_extracted,
                "items_recovered_from_truncation": items_recovered_from_truncation,
                "items_skipped_due_to_error": items_skipped_due_to_error,
                "created_at": now,
            }
        )

    def get_all_attempts(self) -> list[dict[str, Any]]:
        """Get all recorded attempts for persistence.

        Returns:
            List of attempt dictionaries ready for storage

        """
        return self.attempts.copy()

    def get_summary(self, outlier_std_dev_threshold: float | None = None) -> dict[str, Any]:
        """Compute summary statistics from recorded attempts.

        Args:
            outlier_std_dev_threshold: Std-dev multiple for latency-outlier
                detection. ``None`` (default) resolves to
                ``QualitySettings().llm_outlier_std_dev_threshold``; callers
                holding engine settings inject the configured value.

        Returns:
            Summary dictionary with aggregated metrics including:
            - total_calls, successful_calls, failed_calls, retry_calls
            - first_try_successes, retry_successes, permanent_failures
            - total_input_tokens, total_output_tokens, wasted_tokens
            - avg_call_duration_ms, total_duration_ms
            - error_breakdown (dict of error_type -> count)
            - success_rate, retry_rate, waste_percentage
            - outlier_count (calls > 2 std dev from mean duration)
            - estimated_cost_usd (calculated via CostTracker)

        """
        return compute_metrics_summary(
            attempts=self.attempts,
            provider=self.provider,
            model=self.model,
            custom_input_cost=self.custom_input_cost,
            custom_output_cost=self.custom_output_cost,
            outlier_std_dev_threshold=outlier_std_dev_threshold,
        )

    def __len__(self) -> int:
        """Return number of recorded attempts."""
        return len(self.attempts)

    def __bool__(self) -> bool:
        """Always return True so collector is truthy even when empty."""
        return True
