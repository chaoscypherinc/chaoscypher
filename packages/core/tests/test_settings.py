# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for orchestration-driving fields on ``Settings``.

These guard the Pydantic fields the PR1 Phase C orchestration renderer
will read to render nginx, valkey, supervisord, and compose configs.
The fields are pure-additive — no call-sites have been updated yet —
so the tests only assert default values and the new validator.
"""

from __future__ import annotations


def test_timeout_settings_nginx_proxy_defaults() -> None:
    from chaoscypher_core.app_config import Settings

    s = Settings()
    assert s.timeouts.nginx_proxy_connect_timeout == 60
    assert s.timeouts.nginx_proxy_read_timeout == 300
    assert s.timeouts.nginx_proxy_send_timeout == 300


def test_queue_settings_valkey_defaults() -> None:
    from chaoscypher_core.app_config import Settings

    s = Settings()
    assert s.queue.max_memory == "2gb"
    assert s.queue.tcp_keepalive_seconds == 60
    # Default is ``noeviction``: under ``volatile-lru`` only TTL'd keys
    # (heartbeats, results) are eviction candidates — exactly the wrong
    # set when the 2GB ceiling hits, since evicted heartbeats look like
    # dead workers and trigger spurious recovery storms while the actual
    # memory pressure (``queue:task:*`` hashes) survives. ``noeviction``
    # makes memory exhaustion surface as loud write failures instead.
    assert s.queue.maxmemory_policy == "noeviction"


def test_rate_limit_settings_orchestration_defaults() -> None:
    from chaoscypher_core.app_config import Settings

    s = Settings()
    assert s.rate_limit.enabled is True
    assert s.rate_limit.api_general_max_requests == 100
    assert s.rate_limit.api_general_window_seconds == 1
    assert s.rate_limit.uploads_max_requests == 10
    assert s.rate_limit.uploads_window_seconds == 1


def test_shutdown_settings_orchestration_defaults() -> None:
    from chaoscypher_core.app_config import Settings

    s = Settings()
    assert s.shutdown.docker_compose_grace_seconds == 60
    assert s.shutdown.supervisor_startsecs_cortex == 10
    assert s.shutdown.supervisor_startsecs_neuron == 15


def test_shutdown_settings_compose_grace_validator_accepts_when_gte() -> None:
    from chaoscypher_core.app_config import ShutdownSettings

    # Defaults satisfy the invariant (60 >= max(30, 30))
    s = ShutdownSettings()
    assert s.docker_compose_grace_seconds >= max(
        s.cortex_shutdown_grace_seconds, s.worker_shutdown_grace_seconds
    )


def test_shutdown_settings_compose_grace_validator_rejects_when_lt() -> None:
    import pytest
    from pydantic import ValidationError

    from chaoscypher_core.app_config import ShutdownSettings

    with pytest.raises(ValidationError) as exc:
        ShutdownSettings(
            cortex_shutdown_grace_seconds=120,
            worker_shutdown_grace_seconds=120,
            docker_compose_grace_seconds=60,
        )
    assert "must be >=" in str(exc.value)
    assert "SIGKILL" in str(exc.value)


def test_shutdown_settings_compose_grace_validator_accepts_equality() -> None:
    """Equal grace values satisfy the >= invariant (boundary case).

    An ops team setting all three graces to the same value (aggressive
    shutdown) should not be rejected.
    """
    from chaoscypher_core.app_config import ShutdownSettings

    s = ShutdownSettings(
        cortex_shutdown_grace_seconds=45,
        worker_shutdown_grace_seconds=45,
        docker_compose_grace_seconds=45,
    )
    assert s.docker_compose_grace_seconds == 45


def test_orchestration_int_fields_reject_zero() -> None:
    """Representative ge=1 enforcement on the new orchestration fields."""
    import pytest
    from pydantic import ValidationError

    from chaoscypher_core.app_config import (
        QueueSettings,
        RateLimitSettings,
        ShutdownSettings,
        TimeoutSettings,
    )

    with pytest.raises(ValidationError):
        TimeoutSettings(nginx_proxy_connect_timeout=0)
    with pytest.raises(ValidationError):
        QueueSettings(tcp_keepalive_seconds=0)
    with pytest.raises(ValidationError):
        RateLimitSettings(api_general_max_requests=0)
    with pytest.raises(ValidationError):
        ShutdownSettings(docker_compose_grace_seconds=0)


def test_web_settings_defaults() -> None:
    from chaoscypher_core.app_config import Settings, WebSettings

    s = Settings()
    assert s.web.fetch_timeout_seconds == 30.0
    assert s.web.workflow_http_default_timeout_seconds == 60.0
    assert s.web.max_redirects == 10
    assert s.web.content_type_detection_window_bytes == 2048
    # constraints
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        WebSettings(max_redirects=-1)
    with pytest.raises(ValidationError):
        WebSettings(fetch_timeout_seconds=0)


def test_extraction_settings_defaults() -> None:
    from chaoscypher_core.app_config import Settings

    s = Settings()
    assert s.extraction.domain_detection_sample_count == 5
    assert s.extraction.domain_detection_sample_chars == 2000
    assert s.extraction.entity_desc_min_length == 10
    assert s.extraction.entity_desc_incomplete_threshold == 20
    assert s.extraction.research_context_window_chars == 2000


def test_graph_snapshot_settings_defaults() -> None:
    from chaoscypher_core.app_config import Settings

    s = Settings()
    assert s.graph_snapshot.staleness_threshold_seconds == 3600
    assert s.graph_snapshot.count_drift_threshold == 0.10


def test_pause_settings_defaults() -> None:
    from chaoscypher_core.app_config import Settings

    s = Settings()
    assert s.pause.reason_max_chars == 500
    assert s.pause.sources_per_request_max == 500


def test_quality_settings_defaults() -> None:
    from chaoscypher_core.app_config import Settings

    s = Settings()
    assert s.quality.top_sources_count == 5
    assert s.quality.top_cited_entities_limit == 10
    assert s.quality.llm_outlier_std_dev_threshold == 2.0


def test_pr2b_extended_settings_defaults() -> None:
    """Sanity check on every field added in PR2 Phase B."""
    from chaoscypher_core.app_config import Settings

    s = Settings()

    # Timeouts
    assert s.timeouts.tls_validation_seconds == 10

    # Backoff
    assert s.backoff.exponential_multiplier == 2.0

    # Retries
    assert s.retries.embedding_max_retries == 3
    assert s.retries.embedding_initial_backoff_seconds == 0.5
    assert s.retries.workflow_rename_max_attempts == 10
    assert s.retries.ollama_drain_max_iterations == 10

    # Batching
    assert s.batching.search_index_pending_batch_size == 500
    assert s.batching.graphrag_edge_query_limit == 50
    assert s.batching.quality_score_max_tracked_errors == 100
    assert s.batching.quality_score_max_returned_errors == 10

    # Pagination
    assert s.pagination.workflow_executions_fetch_limit == 10_000

    # ChatContext
    assert s.chat_context.title_generation_max_chars == 500
    assert s.chat_context.auto_generated_title_max_words == 10
    assert s.chat_context.chat_title_max_length == 500
    assert s.chat_context.chat_message_max_length == 500_000

    # Logs
    assert s.logs.error_message_preview_chars == 200

    # Intervals
    assert s.intervals.upgrade_poll_seconds == 5

    # Chat
    assert s.chat.log_message_preview_chars == 200
    assert s.chat.citation_search_key_chars == 60
    assert s.chat.citation_min_quote_chars == 40
    assert s.chat.citation_min_match_chars == 12

    # Search
    assert s.search.result_preview_chars == 100
    assert s.search.min_similarity_threshold == 0.55

    # Embedding
    assert s.embedding.default_ollama_model == "qwen3-embedding:0.6b"

    # Paths
    assert s.paths.max_filename_length == 80

    # CORS
    assert s.cors.dev_fallback_origins == [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]


def test_pr3a_frontend_default_settings_exist() -> None:
    """Sanity check that all the frontend-default fields are present."""
    from chaoscypher_core.app_config import Settings

    s = Settings()

    # BatchSettings additions
    assert s.batching.bulk_operation_size == 50
    assert s.batching.polling_max_attempts == 60
    assert s.batching.polling_wait_ms == 1_000
    assert s.batching.export_max_attempts == 120
    assert s.batching.import_max_attempts == 180
    assert s.batching.graph_source_page_size == 200
    assert s.batching.frontend_upload_timeout_ms == 120_000
    assert s.batching.frontend_batch_upload_timeout_ms == 300_000

    # SearchSettings additions
    assert s.search.default_result_limit == 10
    assert s.search.omnibar_entity_limit == 10
    assert s.search.omnibar_source_limit == 5
    assert s.search.debounce_ms == 300

    # IntervalsSettings additions
    assert s.intervals.frontend_log_poll_ms == 3_000
    assert s.intervals.frontend_status_poll_ms == 10_000
    assert s.intervals.frontend_log_initial_lines == 2_000
    assert s.intervals.frontend_log_poll_lines == 200
    assert s.intervals.frontend_chat_poll_ms == 5_000
    assert s.intervals.frontend_sse_recent_event_window_ms == 10_000
    assert s.intervals.frontend_mcp_stale_threshold_ms == 600_000
    assert s.intervals.frontend_spotlight_hover_debounce_ms == 150
    assert s.intervals.frontend_cache_default_stale_time_ms == 30_000
    assert s.intervals.frontend_cache_default_gc_time_ms == 300_000
    assert s.intervals.frontend_cache_graph_snapshot_stale_time_ms == 60_000
    assert s.intervals.frontend_cache_graph_snapshot_null_refetch_ms == 3_000
    assert s.intervals.frontend_cache_graph_snapshot_data_refetch_ms == 120_000

    # TimeoutSettings addition
    assert s.timeouts.frontend_http_default_timeout_ms == 30_000


def test_cli_settings_defaults() -> None:
    from chaoscypher_core.app_config import Settings

    s = Settings()
    assert s.cli.ollama_connect_timeout_seconds == 3
    assert s.cli.setup_ollama_test_timeout_seconds == 5
    assert s.cli.api_test_timeout_seconds == 15
    assert s.cli.health_check_workers == 2
    assert s.cli.list_page_size == 50
    assert s.cli.search_default_limit == 20
    assert s.cli.edge_batch_size == 100
    assert s.cli.download_chunk_size_bytes == 8_192
    assert s.cli.serve_default_page_size == 50


def test_benchmark_settings_defaults() -> None:
    from chaoscypher_core.app_config import Settings

    s = Settings()
    assert s.benchmark.reindex_node_batch_limit == 100_000
    assert "low_8gb" in s.benchmark.vram_presets
    assert s.benchmark.vram_presets["mid_24gb"]["chat"] == "qwen3:30b"
    assert s.benchmark.vram_presets["high_80gb"]["embedding"] == "qwen3-embedding:0.6b"


def test_batching_bulk_request_max_operations_default() -> None:
    from chaoscypher_core.app_config import Settings

    assert Settings().batching.bulk_request_max_operations == 500
