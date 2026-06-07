# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Stalled-threshold default sized for local-LLM workloads.

The reconciler's stall debounce (``_is_recently_active``) classifies a
source as stalled when ``last_activity_at`` is older than this many
seconds. The default must comfortably exceed realistic per-chunk LLM
latencies for the canonical self-hosted deployment (Ollama on
consumer hardware) — otherwise healthy long-running extractions look
stalled to the reconciler and accumulate spurious recoveries toward
the 10-attempt exhaustion cap.

Cloud LLMs (Claude / GPT-4) typically emit chunks in <30s; the
historical 120s default fit them. Local Ollama on a long literary
chunk routinely runs 60-300s, so 120s consistently mis-fires. 600s
(10 min) leaves headroom for the slowest realistic configurations
while still surfacing genuine stalls within the same reconcile pass.
"""

from __future__ import annotations

from chaoscypher_core.app_config import SourceRecoverySettings
from chaoscypher_core.services.sources.recovery import SourceRecovery


def test_class_default_threshold_fits_local_llm_chunk_latency() -> None:
    """``DEFAULT_STALLED_THRESHOLD_SECONDS`` is at least 10 minutes."""
    assert SourceRecovery.DEFAULT_STALLED_THRESHOLD_SECONDS >= 600, (
        "Default stall threshold too tight for local-LLM workloads — long "
        "chunks on Ollama routinely run 60-300s and would be flagged as "
        f"stalled. Current default: "
        f"{SourceRecovery.DEFAULT_STALLED_THRESHOLD_SECONDS}s."
    )


def test_settings_field_default_matches_class_default() -> None:
    """``SourceRecoverySettings.stalled_threshold_seconds`` matches the class.

    Otherwise operators reading the docs would see one value while the
    code uses another, leading to ops-time confusion.
    """
    settings_default = SourceRecoverySettings().stalled_threshold_seconds
    assert settings_default == SourceRecovery.DEFAULT_STALLED_THRESHOLD_SECONDS, (
        f"settings_default={settings_default} but class default is "
        f"{SourceRecovery.DEFAULT_STALLED_THRESHOLD_SECONDS}; the two must "
        "stay in sync so settings.yaml documentation matches runtime behavior."
    )


def test_settings_override_propagates() -> None:
    """An explicit settings override produces the configured value."""
    overridden = SourceRecoverySettings(stalled_threshold_seconds=180)
    assert overridden.stalled_threshold_seconds == 180
