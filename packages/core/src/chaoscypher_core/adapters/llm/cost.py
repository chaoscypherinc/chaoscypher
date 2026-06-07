# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Cost Tracker for LLM Token Usage.

Tracks and calculates costs for different LLM providers based on token usage.
Pricing is based on the official pricing as of January 2025.
"""

import structlog


logger = structlog.get_logger(__name__)

# Pricing tables (USD per 1M tokens)
# Updated as of January 2025

PRICING_TABLES = {
    "openai": {
        # GPT-4.1 Series (2025)
        "gpt-4.1": {"input": 2.00, "output": 8.00},
        "gpt-4.1-mini": {"input": 0.40, "output": 1.60},
        "gpt-4.1-nano": {"input": 0.10, "output": 0.40},
        # GPT-4o (Omni)
        "gpt-4o": {"input": 2.50, "output": 10.00},
        "gpt-4o-2024-08-06": {"input": 2.50, "output": 10.00},
        "gpt-4o-mini": {"input": 0.15, "output": 0.60},
        "gpt-4o-mini-2024-07-18": {"input": 0.15, "output": 0.60},
        # O3 Models (Reasoning - 2025)
        "o3": {"input": 2.00, "output": 8.00},
        "o3-mini": {"input": 1.10, "output": 4.40},
        # O1 Models (Reasoning)
        "o1": {"input": 15.00, "output": 60.00},
        "o1-preview": {"input": 15.00, "output": 60.00},
        "o1-mini": {"input": 3.00, "output": 12.00},
        # Legacy models
        "gpt-4-turbo": {"input": 10.00, "output": 30.00},
        "gpt-4": {"input": 30.00, "output": 60.00},
        "gpt-3.5-turbo": {"input": 0.50, "output": 1.50},
        # Embeddings
        "text-embedding-3-small": {"input": 0.02, "output": 0.00},
        "text-embedding-3-large": {"input": 0.13, "output": 0.00},
        "text-embedding-ada-002": {"input": 0.10, "output": 0.00},
    },
    "anthropic": {
        # Claude 4.5 Series (2025)
        "claude-sonnet-4-5": {"input": 3.00, "output": 15.00},
        "claude-haiku-4-5": {"input": 1.00, "output": 5.00},
        "claude-opus-4-5": {"input": 5.00, "output": 25.00},
        # Claude 4 Series
        "claude-sonnet-4": {"input": 3.00, "output": 15.00},
        "claude-opus-4": {"input": 15.00, "output": 75.00},
        # Claude 3.5/3.7 Series (legacy)
        "claude-3-7-sonnet": {"input": 3.00, "output": 15.00},
        "claude-3-5-sonnet-20241022": {"input": 3.00, "output": 15.00},
        "claude-3-5-haiku-20241022": {"input": 1.00, "output": 5.00},
        "claude-3-opus-20240229": {"input": 15.00, "output": 75.00},
        "claude-3-haiku-20240307": {"input": 0.25, "output": 1.25},
    },
    "gemini": {
        # Gemini 3 Series (2025)
        "gemini-3-flash-preview": {"input": 0.50, "output": 3.00},
        "gemini-3-pro-preview": {"input": 2.00, "output": 12.00},
        # Gemini 2.5 Series
        "gemini-2.5-pro": {"input": 1.25, "output": 10.00},
        "gemini-2.5-flash": {"input": 0.15, "output": 0.60},
        "gemini-2.5-flash-lite": {"input": 0.10, "output": 0.40},
        # Gemini 2.0 Series
        "gemini-2.0-flash": {"input": 0.10, "output": 0.40},
        "gemini-2.0-flash-exp": {"input": 0.10, "output": 0.40},
        # Gemini 1.5 Series (legacy)
        "gemini-1.5-pro": {"input": 1.25, "output": 5.00},
        "gemini-1.5-flash": {"input": 0.075, "output": 0.30},
        # Embeddings
        "text-embedding-004": {"input": 0.000025, "output": 0.00},
    },
    "ollama": {
        # Ollama is free (local execution)
        # Default entry for any Ollama model
        "*": {"input": 0.00, "output": 0.00}
    },
}


class CostTracker:
    """Tracks and calculates LLM costs based on token usage.

    Maintains pricing tables for major LLM providers (OpenAI, Anthropic,
    Gemini) and calculates costs based on input/output token usage.
    Supports custom pricing for self-hosted or unlisted models. Ollama
    (local execution) is tracked as $0.00 cost.

    Pricing tables are updated as of January 2025 and include all major
    model families. Uses per-million-token pricing for accurate cost
    calculation down to fractions of a cent.

    Attributes:
        custom_input_cost: Custom cost per million input tokens (overrides provider pricing)
        custom_output_cost: Custom cost per million output tokens (overrides provider pricing)

    Example:
        >>> from chaoscypher_core.adapters.llm.cost import get_cost_tracker
        >>>
        >>> # Standard pricing lookup
        >>> tracker = get_cost_tracker()
        >>> cost = tracker.calculate_cost(
        ...     provider="openai",
        ...     model="gpt-4o-mini",
        ...     input_tokens=1000,
        ...     output_tokens=500
        ... )
        >>> print(f"Cost: ${cost:.4f}")
        Cost: $0.0005

    Note:
        Ollama is always $0.00 unless custom costs are provided.
        Pricing tables should be updated periodically as providers
        adjust their rates.

    """

    def __init__(self, custom_input_cost: float = 0.0, custom_output_cost: float = 0.0):
        """Initialize cost tracker.

        Args:
            custom_input_cost: Custom cost per million input tokens (overrides provider pricing)
            custom_output_cost: Custom cost per million output tokens (overrides provider pricing)

        """
        self.custom_input_cost = custom_input_cost
        self.custom_output_cost = custom_output_cost

    def calculate_cost(
        self, provider: str, model: str, input_tokens: int, output_tokens: int
    ) -> float:
        """Calculate cost for a single LLM operation.

        Args:
            provider: LLM provider name (openai, anthropic, gemini, ollama)
            model: Model name
            input_tokens: Number of input tokens
            output_tokens: Number of output tokens

        Returns:
            Total cost in USD

        """
        # If custom costs are set (non-zero), use them regardless of provider
        if self.custom_input_cost > 0 or self.custom_output_cost > 0:
            input_cost = (input_tokens / 1_000_000) * self.custom_input_cost
            output_cost = (output_tokens / 1_000_000) * self.custom_output_cost
            total_cost = input_cost + output_cost

            logger.debug(
                "cost_calculated_custom",
                provider=provider,
                model=model,
                input_tokens=input_tokens,
                input_cost_per_million=self.custom_input_cost,
                output_tokens=output_tokens,
                output_cost_per_million=self.custom_output_cost,
                total_cost_usd=round(total_cost, 6),
            )

            return total_cost

        provider = provider.lower()

        # Ollama is always free (unless custom cost is set)
        if provider == "ollama":
            return 0.0

        # Get pricing table for provider
        pricing_table = PRICING_TABLES.get(provider)
        if not pricing_table:
            logger.warning("no_pricing_table_for_provider", provider=provider)
            return 0.0

        # Get pricing for specific model
        model_pricing = pricing_table.get(model)
        if not model_pricing:
            # Try to find a matching model by prefix (for versioned models)
            for model_key, pricing in pricing_table.items():
                if model.startswith(model_key):
                    model_pricing = pricing
                    break

        if not model_pricing:
            logger.warning("no_pricing_for_model", model=model, provider=provider)
            return 0.0

        # Calculate cost (pricing is per 1M tokens)
        input_cost = (input_tokens / 1_000_000) * model_pricing["input"]
        output_cost = (output_tokens / 1_000_000) * model_pricing["output"]
        total_cost = input_cost + output_cost

        logger.debug(
            "cost_calculated",
            provider=provider,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_cost_usd=round(total_cost, 6),
        )

        return total_cost


# Global cost tracker instance
_cost_tracker = None


def get_cost_tracker(
    custom_input_cost: float = 0.0, custom_output_cost: float = 0.0
) -> CostTracker:
    """Get cost tracker instance with optional custom costs.

    Args:
        custom_input_cost: Custom cost per million input tokens (overrides provider pricing)
        custom_output_cost: Custom cost per million output tokens (overrides provider pricing)

    Returns:
        CostTracker instance configured with custom costs if provided

    """
    # If custom costs are provided, always create a new instance
    # This allows different callers to use different custom costs
    if custom_input_cost > 0 or custom_output_cost > 0:
        return CostTracker(custom_input_cost, custom_output_cost)

    # Otherwise use the global singleton for standard pricing
    global _cost_tracker
    if _cost_tracker is None:
        _cost_tracker = CostTracker()
    return _cost_tracker
