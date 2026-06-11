# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Configuration Management for Chaos Cypher VSA.

Uses dynaconf for loading settings with automatic environment variable support.
Pydantic models provide type safety and validation.

Security: No hardcoded secrets, environment variable support
Correctness: Type-safe with Pydantic validation
Maintainability: Uses battle-tested dynaconf instead of custom parsing

**Intentional Duplication:** Some settings groups (LLM, batching, chunking, etc.)
appear in both this file and ``chaoscypher_core.settings``. This is by design:
- Cortex (this file): Dynaconf-backed settings loaded from settings.yaml at runtime.
- Core (settings.py): Pure Pydantic models, no dynaconf. Framework-agnostic.
- Bridge: ``chaoscypher_core.app_config.engine_factory`` maps Cortex → Core.
"""

import difflib
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import structlog
import yaml
from dynaconf import Dynaconf
from pydantic import BaseModel, Field, SecretStr, field_serializer, model_validator
from pydantic_settings import BaseSettings

from chaoscypher_core.exceptions import ConfigError as ConfigError  # re-export


logger = structlog.get_logger(__name__)


# ============================================================================
# Pydantic Settings Models (Type Safety + Validation)
# ============================================================================


class LocalAuthSettings(BaseModel):
    """Single-user local auth configuration (nginx auth_request migration).

    Credentials live in a JSON file at ``credentials_path``; the HMAC
    session secret at ``session_secret_path``. Nginx handles auth at the
    edge via ``auth_request /api/v1/auth/verify`` — the app just reads
    ``X-Auth-User`` downstream.
    """

    credentials_path: Path = Field(
        default=Path("/data/credentials.json"),
        description="Path to the credentials JSON file (bcrypt password + API keys).",
    )
    session_secret_path: Path = Field(
        default=Path("/data/secrets/session_secret"),
        description="Path to the 32-byte session HMAC secret file.",
    )
    cookie_name: str = Field(
        default="cc_session",
        description="Session cookie name.",
    )
    cookie_ttl_seconds: int = Field(
        default=60 * 60 * 24 * 30,  # 30 days
        description="Session cookie lifetime in seconds (sliding).",
    )
    cookie_secure: bool = Field(
        default=False,
        description=(
            "Set Secure flag on session cookie. "
            "When not explicitly set, the flag is auto-resolved at boot by "
            "``Settings._resolve_cookie_secure``: True if TLS cert files are present in "
            "the configured tls.cert_dir, False otherwise (plain-HTTP deployment). "
            "Set explicitly to True or False in settings.yaml / env to override "
            "auto-detection — useful when running behind a TLS-terminating reverse proxy "
            "that doesn't expose certs to the app container."
        ),
    )
    edge_auth_header: str = Field(
        default="X-Auth-Edge-Token",
        description="Header nginx sets when forwarding a verified X-Auth-User identity.",
    )
    edge_auth_token: SecretStr | None = Field(
        default=None,
        description="Shared nginx-to-Cortex token for trusted identity headers.",
    )
    edge_auth_token_path: Path = Field(
        default=Path("/data/secrets/edge_auth_token"),
        description="Path to the shared nginx-to-Cortex token when not set by env.",
    )

    @field_serializer("edge_auth_token", when_used="always")
    def _serialize_edge_auth_token(self, v: SecretStr | None) -> str | None:
        """Unwrap SecretStr for persistence (see LLMSettings._serialize_api_keys)."""
        return v.get_secret_value() if v is not None else None


class QueueSettings(BaseModel):
    """Valkey queue backend configuration."""

    queue_host: str = "valkey"
    queue_port: int = 6379
    queue_database: int = 0
    queue_password: SecretStr | None = None
    queue_ssl: bool = False
    connection_max_retries: int = Field(
        default=10,
        ge=1,
        description="Max attempts when connecting to Valkey on startup before failing",
    )
    connection_retry_delay: float = Field(
        default=1.0,
        gt=0,
        description="Initial backoff delay (seconds) between Valkey connection retries",
    )
    stats_retention_hours: int = Field(
        default=24,
        ge=1,
        description="Hours to retain completed LLM task statistics before cleanup",
    )
    max_pending_queue_depth: int = Field(
        default=10000,
        ge=1,
        description=(
            "Maximum number of pending tasks allowed per queue (llm, operations). "
            "Enqueue attempts are rejected with QueueFullError when this limit is "
            "reached, providing backpressure to prevent unbounded Valkey memory growth."
        ),
    )
    max_memory: str = Field(
        default="2gb",
        description=(
            "Valkey ``--maxmemory`` value (e.g. '256mb', '2gb'). Drives the "
            "valkey-startup.sh CLI flag in the all-in-one container and the "
            "QUEUE_MAX_MEMORY env var in the multi-container compose."
        ),
    )
    tcp_keepalive_seconds: int = Field(
        default=60,
        ge=1,
        description="Valkey ``--tcp-keepalive`` value (seconds).",
    )
    maxmemory_policy: str = Field(
        default="noeviction",
        description=(
            "Valkey ``--maxmemory-policy`` value. Default is ``noeviction`` so "
            "memory pressure surfaces as loud write failures the operator can "
            "act on. Under ``volatile-lru`` only TTL'd keys are eviction "
            "candidates — which here is mostly the wrong set: heartbeats "
            "(``queue:task:*:heartbeat``), result records (``queue:result:*``), "
            "and terminal-failed task hashes (``queue:task:*`` with the "
            "``failed_result_ttl`` dead-letter retention applied) carry TTLs "
            "while live pending-task hashes and pending zsets "
            "(``queue:{queue}:pending``) do not. With ``volatile-lru`` the "
            "eviction order at the 2GB ceiling is heartbeats, results, and "
            "dead-letter records first while the actual memory pressure "
            "(pending tasks) survives untouched. Evicted heartbeats then look "
            "like dead workers and trigger spurious recovery storms; evicted "
            "dead-letter records erase post-mortem data the operator needs. "
            "``noeviction`` fails writes loudly instead — depth backpressure "
            "(``max_pending_queue_depth``) plus AOF persistence cover the "
            "durability story; this setting governs how memory exhaustion "
            "presents to the operator."
        ),
    )

    @field_serializer("queue_password", when_used="always")
    def _serialize_queue_password(self, v: SecretStr | None) -> str | None:
        """Unwrap SecretStr for persistence (see LLMSettings._serialize_api_keys)."""
        return v.get_secret_value() if v is not None else None


# These settings classes are imported from core to avoid duplicate definitions.
# Any new fields should be added to chaoscypher_core.settings.
#
# Tier 2 schema unification (2026-06-03): the six dual-schema pairs collapsed to
# a single core class each (extraction/database/analysis/export/cli/batching),
# and the four app-only groups core reads (web/backoff/retries/quality) moved
# into core. ``CliSettings`` and ``BatchSettings`` keep their pre-Tier-2 names
# as back-compat aliases (assigned below) so settings.yaml keys and existing
# imports are unchanged.
from chaoscypher_core.settings import (
    AnalysisSettings,
    BackoffSettings,
    BatchingSettings,
    ChatSettings,
    ChunkingSettings,
    CLISettings,
    DatabaseSettings,
    EmbeddingSettings,
    ExportSettings,
    ExtractionSettings,
    LexiconSettings,
    LLMSettings,
    MCPSettings,
    OllamaInstance,
    PaginationSettings,
    PathSettings,
    QualitySettings,
    RetrySettings,
    SearchSettings,
    SourceProcessingSettings,
    WebSettings,
)


# Back-compat aliases for the pre-Tier-2 app-only class names. The Settings
# model still declares its fields with these names where the app naming
# differed from core's; new code should prefer the core names.
CliSettings = CLISettings  # back-compat alias (pre-Tier-2 name)
BatchSettings = BatchingSettings  # back-compat alias (pre-Tier-2 name)


class PrioritySettings(BaseModel):
    """Queue priority configuration.

    Tasks are dequeued via ZPOPMAX on the Valkey sorted set, so HIGHER
    numeric values pop first. Canonical ordering:

        interactive (100) > background (50) > default (1)

    Within a priority tier, earlier-enqueued tasks pop first (FIFO) via a
    small time-based score offset applied at enqueue time.

    Values are bounded to [0, 100] inclusive to keep the scheduler in a
    well-defined regime.
    """

    interactive: int = Field(
        default=100,
        ge=0,
        le=100,
        description="Highest priority — pops first (chat, UI actions)",
    )
    background: int = Field(
        default=50,
        ge=0,
        le=100,
        description="Medium priority (imports, discovery, workflows)",
    )
    default: int = Field(
        default=1,
        ge=0,
        le=100,
        description="Lowest priority — pops last (scheduled background tasks)",
    )


class TimeoutSettings(BaseModel):
    """Timeout configuration (all values in seconds unless noted)."""

    # API timeouts
    llm_chat_wait: int = Field(default=120, description="Wait timeout for chat operations (2 min)")
    llm_embedding_wait: int = Field(
        default=60, description="Wait timeout for embedding operations (1 min)"
    )
    llm_operation_max: int = Field(
        default=300, description="Max timeout for LLM operations (5 min)"
    )
    http_request: int = Field(default=30, description="HTTP request timeout")

    # Worker timeouts
    llm_worker_default: int = Field(default=3600, description="Default LLM worker timeout (1 hour)")
    operations_worker_default: int = Field(
        default=3600, description="Default operations worker timeout (1 hour)"
    )
    operations_result_ttl: int = Field(
        default=7200, description="Operations result TTL in seconds (2 hours)"
    )
    llm_result_ttl: int = Field(default=3600, description="LLM result TTL in seconds (1 hour)")
    # Dead-letter retention: terminal-failed task hashes get this TTL applied
    # so operators have a window to investigate post-mortems without leaving
    # months of debris in Valkey. 14 days matches the backup-retention default
    # documented in packages/docs/docs/getting-started/backup-restore.md and
    # was chosen per the 2026-05-18 production-launch audit (pass 2).
    failed_result_ttl: int = Field(
        default=14 * 86_400,
        ge=3600,
        description=(
            "Terminal-failed task retention in seconds (default 14 days). "
            "Applied as an EXPIRE on the ``queue:task:{id}`` hash whenever a "
            "task reaches a terminal ``status=failed`` (permanent handler error, "
            "no-handler, timeout exhaustion, or reconciler abandonment) so the "
            "post-mortem record outlives the success ``result_ttl`` without "
            "accumulating forever."
        ),
    )

    # Health & startup
    health_check: float = Field(default=2.0, description="Health check timeout")
    hot_reload_delay: int = Field(default=10, description="Hot reload delay for file watching")
    trigger_event_queue: float = Field(default=1.0, description="Trigger event queue timeout")

    # Queue polling
    queue_poll_interval: float = Field(default=0.5, description="Queue polling interval in seconds")
    queue_semaphore_acquire: float = Field(
        default=1.0,
        description="Timeout for acquiring a worker concurrency slot before re-checking shutdown",
    )

    # Worker shutdown
    settings_listener_shutdown: float = Field(
        default=5.0,
        description="Max time to await the settings listener task during worker shutdown",
    )

    # SQLite timeouts
    sqlite_connection: int = Field(default=60, description="SQLite connection timeout")
    sqlite_busy_timeout_ms: int = Field(
        default=60000, description="SQLite busy timeout in milliseconds"
    )

    # Subprocess timeouts (prevent a hung external tool from wedging a worker)
    ffmpeg_subprocess: int = Field(
        default=600,
        description=(
            "Max wall-time (seconds) for ffmpeg audio/video conversion. A "
            "crafted media file can make ffmpeg spin forever without this."
        ),
    )
    nginx_reload_subprocess: int = Field(
        default=10,
        description="Max wall-time (seconds) for 'nginx -s reload' during TLS toggle.",
    )

    # nginx proxy timeouts (used by orchestration renderer)
    # These are SEPARATE from `http_request` (which is the app's outbound HTTP
    # timeout). proxy_read_timeout governs how long nginx waits for upstream
    # cortex/neuron responses — must be high enough for streaming LLM responses.
    nginx_proxy_connect_timeout: int = Field(
        default=60,
        ge=1,
        description="nginx proxy_connect_timeout (seconds) — nginx-to-cortex TCP connect.",
    )
    nginx_proxy_read_timeout: int = Field(
        default=300,
        ge=1,
        description=(
            "nginx proxy_read_timeout (seconds). High default because LLM "
            "streaming responses can take minutes; tune down for short-RPC-only "
            "deployments."
        ),
    )
    nginx_proxy_send_timeout: int = Field(
        default=300,
        ge=1,
        description="nginx proxy_send_timeout (seconds) — outbound to upstream.",
    )

    # LLM infrastructure
    llm_health_pause: float = Field(
        default=2.0, description="Pause before retrying healthy provider"
    )
    instance_drain_check_interval: float = Field(
        default=1.0, description="Interval for checking instance drain status"
    )
    instance_drain_max_wait: int = Field(
        default=30, description="Maximum wait time for instance drain"
    )
    ollama_health_check: float = Field(default=5.0, description="Ollama health check timeout")
    ollama_verify_timeout: int = Field(
        default=5, description="Timeout for Ollama URL verification from settings UI"
    )
    llm_stream_chunk_timeout: float = Field(
        default=120.0,
        description="Max seconds to wait for next streaming chunk before aborting (dead connection detection)",
    )

    # Ollama
    ollama_http_request: int = Field(
        default=30, description="HTTP request timeout for Ollama model listing API calls"
    )

    # Process management
    process_wait: int = Field(default=5, description="Process wait timeout")
    job_abort: int = Field(default=2, description="Job abort timeout")

    tls_validation_seconds: int = Field(
        default=10,
        ge=1,
        description="Timeout for TLS cert validation requests (cortex tls_service.py).",
    )

    # Frontend-facing timeout defaults (PR3-A) — exposed via /api/v1/settings/public
    frontend_http_default_timeout_ms: int = Field(
        default=30_000,
        ge=100,
        description="SPA HTTP client default timeout.",
    )


class PortSettings(BaseModel):
    """Port configuration."""

    web_ui_api: int = Field(default=8080, description="Web UI API server port")
    valkey: int = Field(default=6379, description="Valkey server port")


class WorkerSettings(BaseModel):
    """Worker concurrency configuration."""

    operations_max_concurrent: int = Field(
        default=8, description="Maximum concurrent operations worker tasks"
    )
    health_report_interval: int = Field(
        default=2, description="Health reporter poll interval in seconds"
    )


class RateLimitSettings(BaseModel):
    """Rate limiting configuration for authentication endpoints."""

    login_max_requests: int = Field(
        default=5, ge=1, description="Max login attempts per IP per window"
    )
    login_window_seconds: int = Field(
        default=60, ge=10, description="Sliding window for login rate limit (seconds)"
    )
    setup_max_requests: int = Field(
        default=3, ge=1, description="Max setup attempts per IP per window"
    )
    setup_window_seconds: int = Field(
        default=60, ge=10, description="Sliding window for setup rate limit (seconds)"
    )
    api_key_max_requests: int = Field(
        default=10, ge=1, description="Max API key creation attempts per IP per window"
    )
    api_key_window_seconds: int = Field(
        default=60, ge=10, description="Sliding window for API key rate limit (seconds)"
    )
    refresh_max_requests: int = Field(
        default=10, ge=1, description="Max token refresh attempts per IP per window"
    )
    refresh_window_seconds: int = Field(
        default=60, ge=10, description="Sliding window for token refresh rate limit (seconds)"
    )
    register_max_requests: int = Field(
        default=3, ge=1, description="Max registration attempts per IP per window"
    )
    register_window_seconds: int = Field(
        default=60, ge=10, description="Sliding window for registration rate limit (seconds)"
    )
    enabled: bool = Field(
        default=True,
        description=(
            "Master toggle for nginx rate limiting. When False, the orchestration "
            "renderer omits all `limit_req_zone` and `limit_req zone=...` directives "
            "from the rendered nginx configs."
        ),
    )
    api_general_max_requests: int = Field(
        default=100,
        ge=1,
        description="nginx general API zone — max requests per second per IP.",
    )
    api_general_window_seconds: int = Field(
        default=1,
        ge=1,
        description="nginx general API zone — sliding window seconds.",
    )
    uploads_max_requests: int = Field(
        default=10,
        ge=1,
        description="nginx uploads zone — max requests per second per IP.",
    )
    uploads_window_seconds: int = Field(
        default=1,
        ge=1,
        description="nginx uploads zone — sliding window seconds.",
    )
    mutations_max_requests: int = Field(
        default=10,
        ge=1,
        description=(
            "nginx mutations zone — max mutating requests (POST/PUT/PATCH/DELETE) "
            "per second per IP on /api/* routes. Closes the network-retry "
            "double-submit risk on upload, chat send, queue POST, etc. without "
            "requiring an Idempotency-Key API contract. Default is a generous "
            "single-operator self-hosted shape; raise for multi-user deployments."
        ),
    )
    mutations_window_seconds: int = Field(
        default=1,
        ge=1,
        description="nginx mutations zone — sliding window seconds.",
    )
    mutations_burst: int = Field(
        default=20,
        ge=1,
        description=(
            "nginx mutations zone — burst size paired with `nodelay`. Sized so "
            "legitimate UI click bursts (form save + immediate edit) clear, but "
            "an accidental retry-storm gets throttled within ~2 seconds."
        ),
    )


class CorsSettings(BaseModel):
    """CORS (Cross-Origin Resource Sharing) configuration."""

    allowed_origins: list[str] = Field(
        default=[],
        description="Allowed CORS origins. Empty = auto-allow localhost origins (any port) for zero-config development.",
    )
    allow_credentials: bool = Field(default=False, description="Allow credentials in CORS requests")
    allow_methods: list[str] = Field(default=["*"], description="Allowed HTTP methods for CORS")
    allow_headers: list[str] = Field(
        default=["Content-Type", "Authorization"],
        description="Allowed HTTP headers for CORS",
    )
    dev_fallback_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:3000", "http://127.0.0.1:3000"],
        description=(
            "Origins auto-allowed for dev when `allowed_origins` is empty "
            "(zero-config local development). Override to support non-3000 dev ports."
        ),
    )


class TLSSettings(BaseModel):
    """TLS certificate and Nginx configuration paths (all-in-one container)."""

    cert_dir: str = Field(
        default="/data/secrets/tls",
        description="Directory for TLS certificate and key files",
    )
    cert_filename: str = Field(
        default="server.crt",
        description="TLS certificate filename within cert_dir",
    )
    key_filename: str = Field(
        default="server.key",
        description="TLS private key filename within cert_dir",
    )
    nginx_active_conf: str = Field(
        default="/run/chaoscypher/nginx-active.conf",
        description="Path to the active Nginx configuration symlink",
    )
    nginx_http_conf: str = Field(
        default="/etc/nginx/nginx-http.conf",
        description="Path to the HTTP-only Nginx configuration template",
    )
    nginx_https_conf: str = Field(
        default="/etc/nginx/nginx-https.conf",
        description="Path to the HTTPS Nginx configuration template",
    )


class SecuritySettings(BaseModel):
    """HTTP boundary security settings."""

    allowed_hosts: list[str] = Field(
        default_factory=lambda: ["localhost", "127.0.0.1", "::1"],
        description=(
            "Allowed Host header values. Always honoured. When "
            "allow_external_access is True, this list is effectively bypassed."
        ),
    )
    allow_external_access: bool = Field(
        default=False,
        description=(
            "When True, the Host-header check is bypassed (any host accepted). "
            "Equivalent to adding '*' to allowed_hosts. Disables DNS-rebinding "
            "protection — only enable for trusted LAN deployments."
        ),
    )


class LogsSettings(BaseModel):
    """Logging service configuration."""

    known_services: list[str] = Field(
        default=["cortex", "neuron", "nginx", "valkey"],
        description="List of service names whose logs can be viewed",
    )
    max_log_lines: int = Field(
        default=10000,
        ge=100,
        description="Maximum number of lines to read from a single log file",
    )
    supervisor_password: SecretStr | None = Field(
        default=None,
        description=(
            "Password for supervisord HTTP Basic Auth (used by LogService to "
            "fetch service status). Sourced from the SUPERVISOR_PASSWORD env var."
        ),
    )
    error_message_preview_chars: int = Field(
        default=200,
        ge=10,
        description="Max chars of error messages stored in source.last_error / log responses.",
    )

    @field_serializer("supervisor_password", when_used="always")
    def _serialize_supervisor_password(self, v: SecretStr | None) -> str | None:
        """Unwrap SecretStr for persistence (see LLMSettings._serialize_api_keys)."""
        return v.get_secret_value() if v is not None else None


class ChatContextSettings(BaseModel):
    """Chat context window and preview configuration."""

    default_context_window: int = Field(
        default=32768, description="Fallback context window when provider info unavailable"
    )
    tools_token_estimate: int = Field(
        default=2000, description="Approximate tokens reserved for tool schemas"
    )
    history_allocation_percent: float = Field(
        default=0.50,
        ge=0.1,
        le=0.9,
        description="Fraction of context window allocated to chat history",
    )
    min_history_budget_tokens: int = Field(
        default=1000, ge=100, description="Minimum tokens for history even in small contexts"
    )
    content_preview_length: int = Field(
        default=200, ge=10, description="Max characters for content previews in logs"
    )
    json_result_preview_length: int = Field(
        default=500, ge=50, description="Max characters for JSON result previews in logs"
    )
    enable_response_validation: bool = Field(
        default=True,
        description="Run a post-response LLM call to verify answer relevance (adds ~1-3s latency after streaming)",
    )
    tool_call_token_overhead: int = Field(
        default=10,
        ge=0,
        description="Approximate token overhead added per tool call when estimating message size",
    )
    message_structure_token_overhead: int = Field(
        default=4,
        ge=0,
        description="Approximate token overhead added per message for role/structure metadata",
    )
    title_generation_max_chars: int = Field(
        default=500,
        ge=50,
        description="Max chars from the first user message used for auto-title generation.",
    )
    auto_generated_title_max_words: int = Field(
        default=10,
        ge=1,
        description=(
            "Auto-generated titles longer than this are treated as model "
            "thinking-leak and discarded."
        ),
    )
    chat_title_max_length: int = Field(
        default=500,
        ge=10,
        description="Operator-tunable max length for chat titles (settings-driven validator).",
    )
    chat_message_max_length: int = Field(
        default=500_000,
        ge=1_000,
        description="Operator-tunable max length for chat message content (settings-driven validator).",
    )
    response_token_reserve: int = Field(
        default=4096,
        ge=256,
        description=(
            "Tokens reserved for the model's reply (thinking + answer) when "
            "budgeting the tool-calling loop prompt against the context window."
        ),
    )
    compacted_tool_result_max_chars: int = Field(
        default=2000,
        ge=200,
        description=(
            "Characters of an older tool result kept when compacting the "
            "tool-loop prompt to fit the model context window."
        ),
    )
    context_overflow_warning_margin: int = Field(
        default=256,
        ge=0,
        description=(
            "Warn the user when reported prompt tokens come within this margin "
            "of the model context window (the provider silently truncated input)."
        ),
    )


class SourceRecoverySettings(BaseModel):
    """Source-processing resumability configuration.

    See source processing resumability tests for design context.
    """

    worker_scan_interval_seconds: int = Field(
        default=60,
        description="How often workers scan for non-terminal sources.",
        ge=10,
    )
    cortex_scan_interval_seconds: int = Field(
        default=300,
        description="How often Cortex runs the safety-net source reconciler.",
        ge=30,
    )
    stalled_threshold_seconds: int = Field(
        default=600,
        description=(
            "Age of last_activity_at (seconds) above which a source is "
            "considered stalled and eligible for reconciler re-dispatch. "
            "Sized for local-LLM workloads: a long literary chunk on Ollama "
            "routinely runs 60-300s in pass 1 + pass 2 of extraction, so "
            "anything under ~10 minutes risks false-positive recoveries on "
            "healthy long-running work. Lower this for cloud LLMs (Claude / "
            "GPT-4) where chunk latency rarely exceeds 30s."
        ),
        ge=30,
    )
    stream_heartbeat_min_interval_seconds: float = Field(
        default=5.0,
        ge=0.5,
        description=(
            "Minimum seconds between stream-activity heartbeats during chunk "
            "extraction. The chunk handler bumps last_activity_at on each "
            "received content chunk from the LLM stream, but caps writes at "
            "this interval to avoid hammering the DB on every token. The floor "
            "must stay comfortably under stalled_threshold_seconds so a stream "
            "emitting tokens at any rate stays live."
        ),
    )
    max_recovery_attempts: int = Field(
        default=10,
        description=(
            "Maximum reconciler-driven recovery attempts before a source "
            "is transitioned to status='error' with "
            "error_stage='recovery_exhausted'. Manual un-error (Cluster D "
            "API) resets the counter."
        ),
        ge=1,
    )
    recovery_warn_threshold: int = Field(
        default=5,
        description=(
            "Log a WARNING when a source's recovery_attempts first "
            "reaches this value, giving operators early signal before "
            "max_recovery_attempts is hit."
        ),
        ge=1,
    )
    orphan_task_retention_days: int = Field(
        default=7,
        ge=1,
        description=(
            "Age in days that chunk tasks in status='orphaned' must reach "
            "before the periodic cleanup job deletes them. Orphaned tasks "
            "are created when an ExtractionJob fails (BE-7) and their "
            "non-terminal tasks get cascade-updated to 'orphaned'. A short "
            "retention preserves debugging visibility; a long retention "
            "hoards rows indefinitely. The cutoff uses "
            "ChunkExtractionTask.created_at (since the table has no "
            "updated_at column); a task 7+ days old in 'orphaned' state is "
            "definitively stale regardless of when BE-7 cascade-updated it."
        ),
    )
    orphan_task_cleanup_interval_seconds: int = Field(
        default=86400,  # 24 hours
        ge=3600,
        description=(
            "How often the neuron worker runs the orphan task cleanup job. "
            "Default 24 hours. Applies to chunk tasks in 'orphaned' state "
            "older than orphan_task_retention_days."
        ),
    )
    orphan_files_retention_days: int = Field(
        default=1,
        ge=1,
        description=(
            "Age in days that staged source files (staging_dir/<source_id>/...) "
            "must reach before the periodic cleanup job removes them when "
            "no matching SourceRow.id exists. Orphan files arise when a hard "
            "kill (SIGKILL/OOM/container crash) lands between the file write "
            "and the row commit in upload_source. The retention window covers "
            "in-flight uploads so files that are mid-commit aren't reaped "
            "prematurely. Default 1 day — orphan files are unrecoverable "
            "(no row exists for the user to retry from), so a short window "
            "is appropriate."
        ),
    )
    orphan_files_cleanup_interval_seconds: int = Field(
        default=86400,  # 24 hours
        ge=3600,
        description=(
            "How often the neuron worker runs the orphan source-file cleanup "
            "job. Default 24 hours. Applies to staging_dir entries with no "
            "matching SourceRow older than orphan_files_retention_days."
        ),
    )
    orphan_files_cleanup_timeout_seconds: int = Field(
        default=300,  # 5 minutes
        ge=10,
        le=3600,
        description=(
            "Per-pass timeout for the orphan source-file cleanup job. A "
            "wedged filesystem (NFS hang, IO error storm) otherwise stalls "
            "the periodic loop forever. A pass that exceeds this is "
            "cancelled and the next interval's pass proceeds."
        ),
    )
    reconcile_timeout_seconds: int = Field(
        default=30,
        description=(
            "Per-call timeout for reconcile_database(...). Caps how long a "
            "single reconciliation pass can run; a slower-than-threshold "
            "pass is cancelled and the next interval's pass proceeds."
        ),
        ge=5,
        le=600,
    )

    @model_validator(mode="after")
    def _validate_heartbeat_lt_half_stall(self) -> SourceRecoverySettings:
        """Heartbeat interval must fire at least twice per stall window.

        ``stream_heartbeat_min_interval_seconds`` is the minimum gap between
        ``last_activity_at`` writes during a streaming chunk extraction. The
        reconciler classifies a source as stalled once
        ``now - last_activity_at`` exceeds ``stalled_threshold_seconds``.

        If a single heartbeat interval is more than half the stall threshold,
        a healthy stream that bumps activity once per interval can still cross
        the stall threshold between writes — yielding a false-positive recovery
        on work that is in fact making progress. Requiring at least two
        heartbeats to fit per stall window guarantees the reconciler sees a
        recent write before the threshold elapses.

        Raises:
            ValueError: if the configured interval/threshold pair would let a
                live stream get reclassified as stalled.
        """
        if self.stream_heartbeat_min_interval_seconds * 2 > self.stalled_threshold_seconds:
            msg = (
                "Heartbeat interval must be at most half the stall threshold "
                "to avoid false-positive stall detection. Got "
                f"stream_heartbeat_min_interval_seconds="
                f"{self.stream_heartbeat_min_interval_seconds} and "
                f"stalled_threshold_seconds={self.stalled_threshold_seconds}; "
                "either lower the heartbeat interval or raise the stall "
                "threshold so heartbeat * 2 <= stall_threshold."
            )
            raise ValueError(msg)
        return self


class QueueRecoverySettings(BaseModel):
    """Self-healing configuration for the queue layer.

    See queue reconciliation tests for design context.
    """

    heartbeat_ttl_seconds: int = Field(
        default=30,
        description="TTL on queue:task:{id}:heartbeat keys.",
        ge=2,
    )
    heartbeat_refresh_interval_seconds: int = Field(
        default=10,
        description="How often workers refresh the heartbeat key during execution.",
        ge=1,
    )
    worker_reconcile_interval_seconds: int = Field(
        default=30,
        description="How often workers run a reconciliation pass.",
        ge=5,
    )
    cortex_reconcile_interval_seconds: int = Field(
        default=150,
        description="How often Cortex runs a safety-net reconciliation pass.",
        ge=10,
    )

    @model_validator(mode="after")
    def _validate_refresh_lt_half_ttl(self) -> QueueRecoverySettings:
        """Refresh interval must fire at least twice per TTL.

        Otherwise a single delayed heartbeat refresh could let the key
        expire, causing the reconciler to classify a healthy task as
        abandoned.
        """
        if self.heartbeat_refresh_interval_seconds * 2 >= self.heartbeat_ttl_seconds:
            msg = (
                "heartbeat_refresh_interval_seconds * 2 must be less than "
                "heartbeat_ttl_seconds so at least two refreshes fit per TTL"
            )
            raise ValueError(msg)
        return self


class ShutdownSettings(BaseModel):
    """Graceful shutdown drain configuration.

    See source pause and shutdown tests for design context.
    """

    worker_shutdown_grace_seconds: int = Field(
        default=30,
        description="How long a worker waits for in-flight tasks after SIGTERM.",
        ge=1,
    )
    cortex_shutdown_grace_seconds: int = Field(
        default=30,
        description="How long Cortex waits for in-flight HTTP requests after SIGTERM.",
        ge=1,
    )
    docker_compose_grace_seconds: int = Field(
        default=60,
        ge=1,
        description=(
            "Docker compose ``stop_grace_period`` — must be >= max(cortex_grace, "
            "worker_grace) so the orchestrator never SIGKILLs a process that's "
            "still in its grace window."
        ),
    )
    supervisor_startsecs_cortex: int = Field(
        default=10,
        ge=1,
        description="supervisor ``startsecs`` for the cortex program.",
    )
    supervisor_startsecs_neuron: int = Field(
        default=15,
        ge=1,
        description="supervisor ``startsecs`` for the neuron program.",
    )

    @model_validator(mode="after")
    def _validate_compose_grace(self) -> ShutdownSettings:
        """Compose grace period must dominate per-process graces.

        Docker compose ``stop_grace_period`` is the wall-clock window
        between SIGTERM and SIGKILL. If it is shorter than the longest
        per-process grace (cortex or worker), the orchestrator can
        SIGKILL a process that is still inside its own graceful-shutdown
        window, defeating the purpose of
        ``cortex_shutdown_grace_seconds`` and
        ``worker_shutdown_grace_seconds``.

        Raises:
            ValueError: if ``docker_compose_grace_seconds`` is below
                ``max(cortex_shutdown_grace_seconds, worker_shutdown_grace_seconds)``.
        """
        max_app_grace = max(
            self.cortex_shutdown_grace_seconds,
            self.worker_shutdown_grace_seconds,
        )
        if self.docker_compose_grace_seconds < max_app_grace:
            msg = (
                f"docker_compose_grace_seconds ({self.docker_compose_grace_seconds}) "
                f"must be >= max(cortex_shutdown_grace_seconds, "
                f"worker_shutdown_grace_seconds) = {max_app_grace}, otherwise "
                "the orchestrator will SIGKILL processes mid-graceful-shutdown."
            )
            raise ValueError(msg)
        return self


class HealthMonitorSettings(BaseModel):
    """Health monitor and conditional auto-pause settings."""

    enabled: bool = Field(default=True, description="Enable health-based auto-pause.")
    check_interval_seconds: float = Field(
        default=30.0, description="Seconds between health evaluation ticks.", ge=5.0
    )
    trip_threshold: int = Field(
        default=3, description="Consecutive probe failures before auto-pause.", ge=1
    )
    clear_threshold: int = Field(
        default=3, description="Consecutive probe passes before auto-resume.", ge=1
    )
    disk_warn_bytes: int = Field(
        default=2_147_483_648, description="Disk free space warning threshold (bytes)."
    )
    disk_error_bytes: int = Field(
        default=1_073_741_824, description="Disk free space error threshold (bytes)."
    )
    error_rate_window: int = Field(
        default=20, description="Number of recent tasks to evaluate for error rate.", ge=5
    )
    error_rate_warn: float = Field(
        default=0.5, description="Failure ratio triggering warning.", ge=0.0, le=1.0
    )
    error_rate_error: float = Field(
        default=0.8, description="Failure ratio triggering error.", ge=0.0, le=1.0
    )
    max_audit_events: int = Field(
        default=100, description="Maximum pause events to keep in audit trail.", ge=10
    )


class BackupSettings(BaseModel):
    """Database backup configuration."""

    enabled: bool = Field(default=True, description="Enable scheduled backups")
    interval: str = Field(
        default="daily",
        description="Backup interval: hourly, daily, or weekly",
    )
    retention_count: int = Field(
        default=7,
        description="Number of backups to retain per database",
        ge=1,
        le=100,
    )
    backup_dir: str = Field(
        default="backups",
        description="Backup directory name under data_dir",
    )


class IntervalsSettings(BaseModel):
    """Periodic background-task interval configuration.

    Groups timer intervals for worker maintenance loops so they can be
    tuned via settings.yaml without touching code.
    """

    search_sweep_seconds: int = Field(
        default=300,
        description=(
            "How often the neuron worker runs the search-index orphan-sweep + "
            "pending_search_index drain (Task 6.2). Default 5 minutes."
        ),
        ge=30,
    )
    search_sweep_max_attempts: int = Field(
        default=5,
        description=(
            "Maximum retries the search-sweep worker performs against a single "
            "pending_search_index entry before flipping the source's "
            "vector_indexing_status to 'failed' and removing the entry "
            "(Workstream 10). Default 5 attempts."
        ),
        ge=1,
    )
    upgrade_poll_seconds: int = Field(
        default=5,
        ge=1,
        description="Poll interval for the neuron worker's upgrade-gate loop.",
    )

    # Frontend-facing interval defaults (PR3-A) — exposed via /api/v1/settings/public
    frontend_log_poll_ms: int = Field(default=3_000, ge=100)
    frontend_status_poll_ms: int = Field(default=10_000, ge=100)
    frontend_log_initial_lines: int = Field(default=2_000, ge=10)
    frontend_log_poll_lines: int = Field(default=200, ge=10)
    frontend_chat_poll_ms: int = Field(default=5_000, ge=100)
    chat_approval_poll_ms: int = Field(
        default=500,
        ge=50,
        description=(
            "How often the chat tool loop polls Valkey for a pending "
            "tool-approval decision while a tool call waits for the user."
        ),
    )
    frontend_sse_recent_event_window_ms: int = Field(default=10_000, ge=100)
    frontend_mcp_stale_threshold_ms: int = Field(default=600_000, ge=1_000)
    frontend_spotlight_hover_debounce_ms: int = Field(default=150, ge=10)
    frontend_cache_default_stale_time_ms: int = Field(default=30_000, ge=0)
    frontend_cache_default_gc_time_ms: int = Field(default=300_000, ge=0)
    frontend_cache_graph_snapshot_stale_time_ms: int = Field(default=60_000, ge=0)
    frontend_cache_graph_snapshot_null_refetch_ms: int = Field(default=3_000, ge=100)
    frontend_cache_graph_snapshot_data_refetch_ms: int = Field(default=120_000, ge=100)


class ServicesSettings(BaseModel):
    """Internal service URLs for container-to-container communication.

    These URLs are used by workers and other services to communicate with
    each other within the Docker network. Defaults are set for Docker Compose
    service names, but can be overridden for different deployment scenarios.
    """

    cortex_internal_url: str = Field(
        default="http://cortex:8080",
        description="Internal URL for Cortex API (used by workers to trigger reloads)",
    )
    valkey_internal_url: str = Field(
        default="valkey://valkey:6379",
        description="Internal URL for Valkey (queue backend)",
    )
    uvicorn_workers: int = Field(
        default=1,
        description="Number of Uvicorn worker processes",
        ge=1,
        le=8,
    )


class WorkflowsSettings(BaseModel):
    """Workflow engine configuration."""

    max_recursion_depth: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Max depth for nested workflow calls before rejecting.",
    )
    step_retry_base_delay_seconds: float = Field(
        default=1.0,
        gt=0.0,
        le=60.0,
        description=(
            "Base delay (seconds) before the first workflow step retry; grows by "
            "backoff.exponential_multiplier each subsequent retry, capped at "
            "step_retry_max_delay_seconds."
        ),
    )
    step_retry_max_delay_seconds: float = Field(
        default=30.0,
        gt=0.0,
        le=600.0,
        description="Upper cap (seconds) on workflow step retry backoff delay.",
    )


class GraphSnapshotSettings(BaseModel):
    """Graph snapshot staleness detection (used by cortex graph_snapshot feature)."""

    staleness_threshold_seconds: int = Field(
        default=3600,
        ge=60,
        description="Snapshot age (seconds) above which it's considered stale.",
    )
    count_drift_threshold: float = Field(
        default=0.10,
        ge=0.0,
        le=1.0,
        description=(
            "Fractional drift in node/edge counts above which the snapshot is "
            "considered stale (e.g. 0.10 = 10%)."
        ),
    )


class PauseSettings(BaseModel):
    """Pause-operation tuning (used by cortex pause feature)."""

    reason_max_chars: int = Field(
        default=500,
        ge=10,
        description="Max chars for an operator-supplied pause reason.",
    )
    sources_per_request_max: int = Field(
        default=500,
        ge=1,
        description="Max source IDs accepted in one pause request.",
    )


class BenchmarkSettings(BaseModel):
    """Benchmark orchestration tunables."""

    reindex_node_batch_limit: int = Field(
        default=100_000,
        ge=1,
        description="Max nodes loaded per batch when reindexing embeddings during benchmark runs.",
    )

    vram_presets: dict[str, dict[str, str]] = Field(
        default_factory=lambda: {
            "low_8gb": {
                "chat": "phi4:14b",
                "extraction": "phi4:14b",
                "embedding": "qwen3-embedding:0.6b",
            },
            "mid_24gb": {
                "chat": "qwen3:30b",
                "extraction": "qwen3:30b-instruct",
                "embedding": "qwen3-embedding:0.6b",
            },
            "high_80gb": {
                "chat": "gpt-oss:120b",
                "extraction": "gpt-oss:120b",
                "embedding": "qwen3-embedding:0.6b",
            },
        },
        description=(
            "VRAM-tier presets used by the setup wizard. Each tier maps to a "
            "dict of model role → model name. Operators can override or add tiers "
            "via settings.yaml."
        ),
    )


# Settings keys removed from the schema; old settings.yaml files may still
# carry them. Scrubbed (with a warning) instead of failing the sections'
# extra="forbid" validation, so an upgrade never bricks startup.
_RETIRED_KEYS: dict[str, frozenset[str]] = {
    "llm": frozenset({"thinking_auto_detect", "chat_interactive_streaming"}),
    "chat": frozenset({"enable_response_validation"}),
}


def _scrub_retired_keys(section: str, section_data: Any) -> Any:
    """Drop schema-retired keys from a loaded settings section.

    Args:
        section: Section name (e.g. ``"llm"``).
        section_data: Raw section dict from the YAML/dynaconf load.

    Returns:
        The section data without retired keys (dicts are mutated in place).

    """
    retired = _RETIRED_KEYS.get(section)
    if not retired or not isinstance(section_data, dict):
        return section_data
    for key in list(section_data.keys()):
        if str(key).lower() in retired:
            section_data.pop(key)
            logger.warning(
                "settings_retired_key_ignored",
                section=section,
                key=str(key).lower(),
            )
    return section_data


# ============================================================================
# Main Settings Class (Single Responsibility: Application Configuration)
# ============================================================================


class Settings(BaseSettings):
    """Application settings with dynaconf-powered loading.

    Provides type-safe access to configuration with automatic:
    - Environment variable substitution (${VAR_NAME})
    - Multi-environment support (dev, prod, test)
    - YAML/TOML/JSON file loading
    - Secrets management
    """

    # Core settings
    app_name: str = "Chaos Cypher"
    current_database: str = "default"
    data_dir: Path = Field(default=Path("/data"))

    # Feature toggles
    dark_mode: bool = True
    auto_enable: bool = True  # Automatically enable sources (visible in graph/search) after commit

    # First-run setup wizard completion flag. Stays False on a fresh install
    # until the user completes every step of the wizard (LLM, embeddings,
    # tool approval). Used by AuthGuard on the frontend to keep an
    # authenticated user inside the wizard if they bailed partway through.
    setup_completed: bool = False

    # Dev flag — when true, bypass X-Auth-User check (uvicorn-direct dev only).
    # Production deployments always terminate at nginx, which sets the header.
    dev_mode: bool = Field(
        default=False,
        description="When true, bypass X-Auth-User check (uvicorn-direct dev only).",
    )

    # Nested settings
    local_auth: LocalAuthSettings = Field(default_factory=LocalAuthSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    queue: QueueSettings = Field(default_factory=QueueSettings)
    chunking: ChunkingSettings = Field(default_factory=ChunkingSettings)
    embedding: EmbeddingSettings = Field(default_factory=EmbeddingSettings)
    search: SearchSettings = Field(default_factory=SearchSettings)
    source_processing: SourceProcessingSettings = Field(default_factory=SourceProcessingSettings)
    export: ExportSettings = Field(default_factory=ExportSettings)
    lexicon: LexiconSettings = Field(default_factory=LexiconSettings)

    # New centralized configuration settings
    paths: PathSettings = Field(default_factory=PathSettings)
    priorities: PrioritySettings = Field(default_factory=PrioritySettings)
    timeouts: TimeoutSettings = Field(default_factory=TimeoutSettings)
    ports: PortSettings = Field(default_factory=PortSettings)
    batching: BatchSettings = Field(default_factory=BatchSettings)
    pagination: PaginationSettings = Field(default_factory=PaginationSettings)
    retries: RetrySettings = Field(default_factory=RetrySettings)
    queue_recovery: QueueRecoverySettings = Field(default_factory=QueueRecoverySettings)
    source_recovery: SourceRecoverySettings = Field(default_factory=SourceRecoverySettings)
    services: ServicesSettings = Field(default_factory=ServicesSettings)
    backoff: BackoffSettings = Field(default_factory=BackoffSettings)
    analysis: AnalysisSettings = Field(default_factory=AnalysisSettings)
    chat_context: ChatContextSettings = Field(default_factory=ChatContextSettings)
    chat: ChatSettings = Field(default_factory=ChatSettings)
    workers: WorkerSettings = Field(default_factory=WorkerSettings)
    cors: CorsSettings = Field(default_factory=CorsSettings)
    mcp: MCPSettings = Field(default_factory=MCPSettings)
    backup: BackupSettings = Field(default_factory=BackupSettings)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    shutdown: ShutdownSettings = Field(default_factory=ShutdownSettings)
    health_monitor: HealthMonitorSettings = Field(default_factory=HealthMonitorSettings)
    rate_limit: RateLimitSettings = Field(default_factory=RateLimitSettings)
    tls: TLSSettings = Field(default_factory=TLSSettings)
    security: SecuritySettings = Field(default_factory=SecuritySettings)
    logs: LogsSettings = Field(default_factory=LogsSettings)
    intervals: IntervalsSettings = Field(default_factory=IntervalsSettings)
    workflows: WorkflowsSettings = Field(default_factory=WorkflowsSettings)
    web: WebSettings = Field(default_factory=WebSettings)
    extraction: ExtractionSettings = Field(default_factory=ExtractionSettings)
    graph_snapshot: GraphSnapshotSettings = Field(default_factory=GraphSnapshotSettings)
    pause: PauseSettings = Field(default_factory=PauseSettings)
    quality: QualitySettings = Field(default_factory=QualitySettings)
    cli: CliSettings = Field(default_factory=CliSettings)
    benchmark: BenchmarkSettings = Field(default_factory=BenchmarkSettings)

    # Custom settings (extensible)
    custom_settings: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _derive_local_auth_paths(self) -> Settings:
        """Rewrite `LocalAuthSettings` default paths under ``paths.data_dir``.

        `LocalAuthSettings` ships with `/data/*` defaults because that's
        the canonical Docker mount. When a caller sets `CHAOSCYPHER_DATA_DIR`
        (dev runs, the Docker types-builder stage, tests) those overrides
        already flow into `paths.data_dir` but historically did not reach
        the auth file paths. This derives them so overrides propagate
        transparently; explicit `local_auth.*_path` values in settings.yaml
        still win because they no longer match the hardcoded default.
        """
        data_root = Path(self.paths.data_dir)
        defaults = LocalAuthSettings()
        if self.local_auth.session_secret_path == defaults.session_secret_path:
            self.local_auth.session_secret_path = data_root / "secrets" / "session_secret"
        if self.local_auth.credentials_path == defaults.credentials_path:
            self.local_auth.credentials_path = data_root / "credentials.json"
        if self.local_auth.edge_auth_token_path == defaults.edge_auth_token_path:
            self.local_auth.edge_auth_token_path = data_root / "secrets" / "edge_auth_token"
        return self

    @model_validator(mode="after")
    def _resolve_cookie_secure(self) -> Settings:
        """Auto-toggle ``local_auth.cookie_secure`` based on TLS cert file presence.

        When ``cookie_secure`` was *not* explicitly set by the caller (i.e. it
        is absent from ``local_auth.model_fields_set``), the flag is computed
        at boot time by probing the filesystem:

        - Both ``tls.cert_dir/tls.cert_filename`` and
          ``tls.cert_dir/tls.key_filename`` exist on disk → ``True`` (HTTPS is
          configured; the Secure cookie flag is safe and required).
        - Either cert file is absent → ``False`` (plain-HTTP deployment;
          browsers silently drop Secure cookies, causing login-loops on LAN
          installs at e.g. ``http://192.168.1.100``).

        An explicit True or False in settings.yaml / env always wins: if
        ``cookie_secure`` appears in ``local_auth.model_fields_set`` the auto-
        detection is skipped entirely.
        """
        if "cookie_secure" not in self.local_auth.model_fields_set:
            cert_dir = Path(self.tls.cert_dir)
            tls_ready = (cert_dir / self.tls.cert_filename).exists() and (
                cert_dir / self.tls.key_filename
            ).exists()
            self.local_auth.cookie_secure = tls_ready
        return self

    @classmethod
    def load_from_yaml(cls, yaml_path: str | Path) -> Settings:  # noqa: PLR0915
        """Load settings from YAML using dynaconf (replaces 150+ lines of custom parsing).

        Dynaconf automatically handles:
        - Environment variable substitution: ${VAR_NAME} or @format {env[VAR_NAME]}
        - Multiple file formats (YAML, TOML, JSON, .env)
        - Nested settings
        - Type coercion

        Args:
            yaml_path: Path to settings.yaml file

        Returns:
            Validated Settings instance

        """
        yaml_path = Path(yaml_path)

        if not yaml_path.exists():
            # Debug, not warning: defaults + env overrides is a deliberately-valid
            # config path (first-run users, schema-only Docker build, tests with
            # tmp_path). The actual problems — typo'd path, defaults masking real
            # config — surface as unexpected behavior elsewhere, not via this log.
            logger.debug(
                "settings_file_not_found",
                settings_path=str(yaml_path),
                action="using_defaults_with_env_overrides",
            )
            data: dict[str, Any] = {}
        else:
            # Strict-mode: reject unknown top-level keys before dynaconf silences them.
            # Read raw YAML so we can compare against the canonical field names on the
            # Settings model.  dynaconf uppercases everything, so it cannot surface
            # typos like 'embedding_settings' → 'embedding' to the caller.
            raw_yaml: object = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
            if not isinstance(raw_yaml, dict):
                msg = (
                    f"Settings YAML at {yaml_path} must be a mapping, got"
                    f" {type(raw_yaml).__name__}."
                )
                raise ConfigError(msg)
            known: set[str] = set(cls.model_fields.keys())
            # Normalise to lowercase for case-insensitive comparison (users may write
            # top-level keys in UPPER_CASE as dynaconf conventionally accepts).
            unknown: list[str] = sorted(key for key in raw_yaml if key.lower() not in known)
            if unknown:
                msg_parts: list[str] = [f"Unrecognized top-level setting(s) in {yaml_path}:"]
                for key in unknown:
                    close = difflib.get_close_matches(key.lower(), list(known), n=1, cutoff=0.6)
                    hint = f" (did you mean '{close[0]}'?)" if close else ""
                    msg_parts.append(f"  - {key}{hint}")
                raise ConfigError("\n".join(msg_parts))

            # Use dynaconf to load settings with env var support
            # This replaces 150+ lines of custom regex parsing and manual restructuring
            dynaconf_settings = Dynaconf(
                settings_files=[str(yaml_path)],
                environments=False,  # Single environment mode
                load_dotenv=True,  # Load .env files if present
                envvar_prefix="CHAOSCYPHER",  # Env vars: CHAOSCYPHER_OLLAMA_BASE_URL
                merge_enabled=True,  # Merge multiple sources
            )

            # Extract data from dynaconf and build nested structure
            # Dynaconf provides clean dict access, we just need to structure it
            data = dynaconf_settings.as_dict()

        # Helper to get values with case-insensitive keys
        def get_ci(key: str, default: Any = None) -> Any:
            return data.get(key.upper(), data.get(key.lower(), default))

        # Extract nested settings (dynaconf converts keys to UPPERCASE)
        llm_data = _scrub_retired_keys("llm", data.get("LLM", {}))
        local_auth_data = data.get("LOCAL_AUTH", {})
        queue_data = data.get("QUEUE", {})

        # Override queue settings from environment variables.
        # Docker compose sets QUEUE_HOST/PORT/DB/PASSWORD which take
        # precedence over whatever is in settings.yaml (the Docker
        # named volume may have stale values).
        if os.environ.get("QUEUE_HOST"):
            queue_data["queue_host"] = os.environ["QUEUE_HOST"]
        if os.environ.get("QUEUE_PORT"):
            queue_data["queue_port"] = int(os.environ["QUEUE_PORT"])
        if os.environ.get("QUEUE_DB"):
            queue_data["queue_database"] = int(os.environ["QUEUE_DB"])
        if os.environ.get("QUEUE_PASSWORD"):
            queue_data["queue_password"] = os.environ["QUEUE_PASSWORD"]
        if os.environ.get("CHAOSCYPHER_EDGE_AUTH_TOKEN"):
            local_auth_data["edge_auth_token"] = os.environ["CHAOSCYPHER_EDGE_AUTH_TOKEN"]
        if os.environ.get("CHAOSCYPHER_EDGE_AUTH_TOKEN_FILE"):
            local_auth_data["edge_auth_token_path"] = os.environ["CHAOSCYPHER_EDGE_AUTH_TOKEN_FILE"]
        if os.environ.get("CHAOSCYPHER_EDGE_AUTH_TOKEN_PATH"):
            local_auth_data["edge_auth_token_path"] = os.environ["CHAOSCYPHER_EDGE_AUTH_TOKEN_PATH"]
        chunking_data = data.get("CHUNKING", {})
        embedding_data = data.get("EMBEDDING", data.get("embedding", {}))
        search_data = data.get("SEARCH", {})
        source_processing_data = data.get("SOURCE_PROCESSING", {})
        export_data = data.get("EXPORT", {})
        lexicon_data = data.get("LEXICON", {})
        paths_data = data.get("PATHS", {})
        priorities_data = data.get("PRIORITIES", {})
        timeouts_data = data.get("TIMEOUTS", {})
        ports_data = data.get("PORTS", {})
        batching_data = data.get("BATCHING", {})
        pagination_data = data.get("PAGINATION", {})
        retries_data = data.get("RETRIES", {})
        queue_recovery_data = data.get("QUEUE_RECOVERY", {})
        source_recovery_data = data.get("SOURCE_RECOVERY", {})
        services_data = data.get("SERVICES", {})
        backoff_data = data.get("BACKOFF", {})
        analysis_data = data.get("ANALYSIS", {})
        chat_context_data = data.get("CHAT_CONTEXT", {})
        chat_data = _scrub_retired_keys("chat", data.get("CHAT", {}))
        workers_data = data.get("WORKERS", {})
        cors_data = data.get("CORS", {})
        mcp_data = data.get("MCP", {})
        backup_data = data.get("BACKUP", {})
        database_data = data.get("DATABASE", {})
        shutdown_data = data.get("SHUTDOWN", {})
        health_monitor_data = data.get("HEALTH_MONITOR", {})
        rate_limit_data = data.get("RATE_LIMIT", {})
        tls_data = data.get("TLS", {})
        security_data = data.get("SECURITY", {})
        if os.environ.get("CHAOSCYPHER_ALLOWED_HOSTS"):
            security_data["allowed_hosts"] = [
                host.strip()
                for host in os.environ["CHAOSCYPHER_ALLOWED_HOSTS"].split(",")
                if host.strip()
            ]
        logs_data = data.get("LOGS", {})
        if os.environ.get("SUPERVISOR_PASSWORD"):
            logs_data["supervisor_password"] = os.environ["SUPERVISOR_PASSWORD"]
        intervals_data = data.get("INTERVALS", {})
        workflows_data = data.get("WORKFLOWS", {})
        web_data = data.get("WEB", {})
        extraction_data = data.get("EXTRACTION", {})
        graph_snapshot_data = data.get("GRAPH_SNAPSHOT", {})
        pause_data = data.get("PAUSE", {})
        quality_data = data.get("QUALITY", {})
        cli_data = data.get("CLI", {})
        benchmark_data = data.get("BENCHMARK", {})

        # Create validated Settings instance
        return cls(
            app_name=get_ci("app_name", "Chaos Cypher"),
            current_database=get_ci("current_database", "default"),
            dark_mode=get_ci("dark_mode", True),
            auto_enable=get_ci("auto_enable", True),
            setup_completed=get_ci("setup_completed", False),
            local_auth=(
                LocalAuthSettings(**local_auth_data) if local_auth_data else LocalAuthSettings()
            ),
            llm=LLMSettings(**llm_data) if llm_data else LLMSettings(),
            queue=QueueSettings(**queue_data) if queue_data else QueueSettings(),
            chunking=ChunkingSettings(**chunking_data) if chunking_data else ChunkingSettings(),
            embedding=(
                EmbeddingSettings(**embedding_data) if embedding_data else EmbeddingSettings()
            ),
            search=SearchSettings(**search_data) if search_data else SearchSettings(),
            source_processing=(
                SourceProcessingSettings(**source_processing_data)
                if source_processing_data
                else SourceProcessingSettings()
            ),
            export=ExportSettings(**export_data) if export_data else ExportSettings(),
            lexicon=LexiconSettings(**lexicon_data) if lexicon_data else LexiconSettings(),
            paths=PathSettings(**paths_data) if paths_data else PathSettings(),
            priorities=(
                PrioritySettings(**priorities_data) if priorities_data else PrioritySettings()
            ),
            timeouts=(TimeoutSettings(**timeouts_data) if timeouts_data else TimeoutSettings()),
            ports=PortSettings(**ports_data) if ports_data else PortSettings(),
            batching=(BatchSettings(**batching_data) if batching_data else BatchSettings()),
            pagination=(
                PaginationSettings(**pagination_data) if pagination_data else PaginationSettings()
            ),
            retries=RetrySettings(**retries_data) if retries_data else RetrySettings(),
            queue_recovery=(
                QueueRecoverySettings(**queue_recovery_data)
                if queue_recovery_data
                else QueueRecoverySettings()
            ),
            source_recovery=(
                SourceRecoverySettings(**source_recovery_data)
                if source_recovery_data
                else SourceRecoverySettings()
            ),
            services=ServicesSettings(**services_data) if services_data else ServicesSettings(),
            backoff=BackoffSettings(**backoff_data) if backoff_data else BackoffSettings(),
            analysis=AnalysisSettings(**analysis_data) if analysis_data else AnalysisSettings(),
            chat_context=(
                ChatContextSettings(**chat_context_data)
                if chat_context_data
                else ChatContextSettings()
            ),
            chat=ChatSettings(**chat_data) if chat_data else ChatSettings(),
            workers=WorkerSettings(**workers_data) if workers_data else WorkerSettings(),
            cors=CorsSettings(**cors_data) if cors_data else CorsSettings(),
            mcp=MCPSettings(**mcp_data) if mcp_data else MCPSettings(),
            backup=BackupSettings(**backup_data) if backup_data else BackupSettings(),
            database=(DatabaseSettings(**database_data) if database_data else DatabaseSettings()),
            shutdown=ShutdownSettings(**shutdown_data) if shutdown_data else ShutdownSettings(),
            health_monitor=(
                HealthMonitorSettings(**health_monitor_data)
                if health_monitor_data
                else HealthMonitorSettings()
            ),
            rate_limit=(
                RateLimitSettings(**rate_limit_data) if rate_limit_data else RateLimitSettings()
            ),
            tls=TLSSettings(**tls_data) if tls_data else TLSSettings(),
            security=(SecuritySettings(**security_data) if security_data else SecuritySettings()),
            logs=LogsSettings(**logs_data) if logs_data else LogsSettings(),
            intervals=IntervalsSettings(**intervals_data)
            if intervals_data
            else IntervalsSettings(),
            workflows=(
                WorkflowsSettings(**workflows_data) if workflows_data else WorkflowsSettings()
            ),
            web=WebSettings(**web_data) if web_data else WebSettings(),
            extraction=(
                ExtractionSettings(**extraction_data) if extraction_data else ExtractionSettings()
            ),
            graph_snapshot=(
                GraphSnapshotSettings(**graph_snapshot_data)
                if graph_snapshot_data
                else GraphSnapshotSettings()
            ),
            pause=PauseSettings(**pause_data) if pause_data else PauseSettings(),
            quality=QualitySettings(**quality_data) if quality_data else QualitySettings(),
            cli=CliSettings(**cli_data) if cli_data else CliSettings(),
            benchmark=(
                BenchmarkSettings(**benchmark_data) if benchmark_data else BenchmarkSettings()
            ),
            custom_settings=get_ci("custom_settings", {}),
        )

    # ========================================================================
    # Path Properties (Computed from settings)
    # ========================================================================

    @property
    def database_dir(self) -> Path:
        """Get current database directory."""
        return Path(self.paths.data_dir) / self.paths.databases_subdir / self.current_database

    @property
    def app_db_path(self) -> Path:
        """Get path to app.db SQLite file."""
        return self.database_dir / self.paths.app_db_filename

    @property
    def graphs_dir(self) -> Path:
        """Get path to graphs directory (RDF .ttl files)."""
        return self.database_dir / self.paths.graphs_subdir

    @property
    def settings_path(self) -> Path:
        """Get path to settings.yaml file."""
        return Path(self.paths.data_dir) / self.paths.settings_filename


# ============================================================================
# Global Settings Instance (Singleton Pattern)
# ============================================================================

_settings: Settings | None = None


@lru_cache
def get_settings() -> Settings:
    """Get global settings instance (singleton pattern).

    Use this as FastAPI dependency: settings = Depends(get_settings).

    Returns:
        Validated Settings instance loaded from settings file (path from PathSettings)

    """
    global _settings

    if _settings is None:
        # Use centralized PathSettings for the settings file location
        path_defaults = PathSettings()
        settings_path = Path(path_defaults.data_dir) / path_defaults.settings_filename
        _settings = Settings.load_from_yaml(settings_path)
        logger.info(
            "settings_loaded",
            settings_path=str(settings_path),
            database=_settings.current_database,
            llm_provider=_settings.llm.chat_provider,
        )

    return _settings


def set_settings(settings: Settings) -> None:
    """Set the global settings instance directly (e.g., after external reload)."""
    global _settings
    _settings = settings
    get_settings.cache_clear()


def reload_settings() -> Settings:
    """Reload settings from disk (e.g., after user changes config)."""
    global _settings
    get_settings.cache_clear()
    _settings = None
    return get_settings()


# ============================================================================
# Secret Masking (for safe API responses)
# ============================================================================

_SECRET_FIELD_PATHS: set[str] = {
    "llm.openai_api_key",
    "llm.anthropic_api_key",
    "llm.gemini_api_key",
    "embedding.api_key",
    "queue.queue_password",
    "lexicon.token",
    "lexicon.refresh_token",
    "local_auth.edge_auth_token",
}

_MASKED_PLACEHOLDER = "configured"

_DEFAULT_SECRET_KEYS: tuple[str, ...] = (
    "api_key",
    "apikey",
    "secret",
    "password",
    "token",
    "private_key",
    "access_key",
)


def mask_secret_value(value: str | SecretStr | None) -> str | None:
    """Return ``"configured"`` when set, ``None`` when unset.

    Replaces the previous "first 4 + last 4" partial reveal which leaked
    secret prefixes/suffixes (e.g. ``sk-a...uvwx``) through diagnostic
    exports and settings APIs. A boolean-style indicator is sufficient for
    the "is this configured?" UX question; the rest of the value never needs
    to leave the process.

    Accepts either a plain ``str`` or a ``SecretStr`` — ``settings.model_dump()``
    in Pydantic v2 returns ``SecretStr`` instances for fields typed that way
    (rather than unwrapping), so callers do not have to pre-unwrap.
    """
    if isinstance(value, SecretStr):
        value = value.get_secret_value()
    if not value:
        return None
    return _MASKED_PLACEHOLDER


def is_masked_value(value: str | None) -> bool:
    """Check if a value is a masked placeholder (should be ignored on update)."""
    if not value:
        return False
    return value == _MASKED_PLACEHOLDER


def mask_settings_dict(
    settings_dict: dict[str, Any],
    *,
    secret_keys: tuple[str, ...] = _DEFAULT_SECRET_KEYS,
) -> dict[str, Any]:
    """Return the settings dict with secret-bearing fields masked in-place.

    Two complementary masking strategies are applied:

    1. **Path-based** — explicit dot-separated paths in ``_SECRET_FIELD_PATHS``
       are always masked regardless of the ``secret_keys`` parameter. This is
       the primary mechanism for production settings fields.

    2. **Keyword-based** — any key whose name *contains* a fragment from
       ``secret_keys`` (case-insensitive) is masked when the dict is walked
       recursively. Callers can pass an explicit ``secret_keys`` tuple to
       enrol future credential keys that are not yet in
       ``_SECRET_FIELD_PATHS``.

    Args:
        settings_dict: Settings dict to mask (modified in-place).
        secret_keys: Tuple of key-name fragments to treat as secrets during
            the recursive keyword walk.  Defaults to ``_DEFAULT_SECRET_KEYS``.

    Returns:
        The same ``settings_dict`` object, modified in-place, for
        convenience.
    """
    # --- Pass 1: explicit path-based masking ---
    for path in _SECRET_FIELD_PATHS:
        parts = path.split(".")
        obj = settings_dict
        found = True
        for part in parts[:-1]:
            if isinstance(obj, dict) and part in obj:
                obj = obj[part]
            else:
                found = False
                break
        if found:
            key = parts[-1]
            if isinstance(obj, dict) and key in obj:
                obj[key] = mask_secret_value(obj[key])

    # --- Pass 2: recursive keyword-based masking for custom secret_keys ---
    # Only string-like values are masked here. Integer/float/bool fields whose
    # names happen to contain a secret keyword (e.g. ``max_tokens``,
    # ``api_key_max_requests``, ``enable_token_cost_tracking``) are NOT
    # secrets — masking them with ``"configured"`` would corrupt the value
    # and break PATCH /settings round-trips with pydantic validation errors.
    def _walk(node: dict[str, Any]) -> None:
        for k, v in node.items():
            if isinstance(v, dict):
                _walk(v)
            elif isinstance(v, list):
                for item in v:
                    if isinstance(item, dict):
                        _walk(item)
            elif isinstance(v, (str, SecretStr)) and any(kw in k.lower() for kw in secret_keys):
                node[k] = mask_secret_value(v)

    if secret_keys:
        _walk(settings_dict)

    return settings_dict


def strip_masked_values(updates: dict[str, Any]) -> dict[str, Any]:
    """Remove masked placeholder values from updates so they dont overwrite real secrets."""
    for path in _SECRET_FIELD_PATHS:
        parts = path.split(".")
        obj = updates
        found = True
        for part in parts[:-1]:
            if isinstance(obj, dict) and part in obj:
                obj = obj[part]
            else:
                found = False
                break
        if found:
            key = parts[-1]
            if isinstance(obj, dict) and key in obj and is_masked_value(obj[key]):
                del obj[key]
    return updates


# ============================================================================
# ConfigManager Import
# ============================================================================

# Re-export at end of file after Settings definition
from chaoscypher_core.app_config.manager import ConfigManager


@lru_cache
def get_config_manager() -> ConfigManager:
    """Get singleton ConfigManager instance.

    Avoids re-reading settings.yaml from disk on every request.
    ConfigManager is needed for write operations (update settings).
    For read-only access, use get_settings() instead.
    """
    return ConfigManager()


def get_current_database_name() -> str:
    """Get current database name from settings (FastAPI dependency)."""
    return get_settings().current_database


__all__ = [
    "AnalysisSettings",
    "BackoffSettings",
    "BackupSettings",
    "BatchSettings",
    "BatchingSettings",
    "BenchmarkSettings",
    "CLISettings",
    "ChatContextSettings",
    "ChunkingSettings",
    "CliSettings",
    "ConfigError",
    "ConfigManager",
    "DatabaseSettings",
    "EmbeddingSettings",
    "ExportSettings",
    "ExtractionSettings",
    "GraphSnapshotSettings",
    "HealthMonitorSettings",
    "IntervalsSettings",
    "LLMSettings",
    "LexiconSettings",
    "MCPSettings",
    "OllamaInstance",
    "PaginationSettings",
    "PathSettings",
    "PauseSettings",
    "QualitySettings",
    "RetrySettings",
    "SearchSettings",
    "Settings",
    "ShutdownSettings",
    "SourceProcessingSettings",
    "TimeoutSettings",
    "WebSettings",
    "WorkerSettings",
    "get_config_manager",
    "get_current_database_name",
    "get_settings",
    "is_masked_value",
    "mask_secret_value",
    "mask_settings_dict",
    "reload_settings",
    "set_settings",
    "strip_masked_values",
]
