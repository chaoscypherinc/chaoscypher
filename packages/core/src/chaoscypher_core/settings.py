# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Settings for chaoscypher-engine.

Pure Pydantic settings (no dynaconf) - main app will construct these
from its own configuration and pass to engine services.

Engine needs only a subset of main app settings:
- Current database context
- LLM configuration (all providers)
- Batching settings
- Import/extraction settings
- Chunking settings
- Path settings (XDG-compliant)

**Intentional Duplication:** Some settings groups (LLM, batching, chunking, etc.)
appear in both this file and ``chaoscypher_core.app_config``. This is by design:
- This module: Pure Pydantic models, no dynaconf. Framework-agnostic.
- ``app_config``: Dynaconf-backed settings loaded from settings.yaml.
- Bridge: ``chaoscypher_core.app_config.engine_factory`` maps app_config → EngineSettings.
"""

import os
from pathlib import Path
from typing import Any, Literal

import platformdirs
import structlog
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_serializer,
    field_validator,
    model_validator,
)


# LexiconSettings.url default — env-driven so operators can override
# without editing settings.yaml. Mirrors what models.py used to import
# from a top-level DEFAULT_LEXICON_URL constant; lifted here so the
# constant stays a single-purpose internal default.
def _default_lexicon_url() -> str:
    """Return the Lexicon URL from the LEXICON_URL env var (with a public hub default)."""
    return os.environ.get("LEXICON_URL", "https://lexicon.chaoscypher.com")


# LexiconSettings.timeout default — env-driven so operators can override the
# request timeout without editing settings.yaml. This is where the CLI's
# former CLIConfig._apply_env_overrides hook for CHAOSCYPHER_LEXICON_TIMEOUT
# now lives, making the engine config the single source of truth. A garbage
# value falls back to the package default rather than crashing at import.
_DEFAULT_LEXICON_TIMEOUT = 30


def _default_lexicon_timeout() -> int:
    """Return the Lexicon request timeout from CHAOSCYPHER_LEXICON_TIMEOUT (default 30)."""
    raw = os.environ.get("CHAOSCYPHER_LEXICON_TIMEOUT")
    if raw is None:
        return _DEFAULT_LEXICON_TIMEOUT
    try:
        return int(raw)
    except ValueError:
        return _DEFAULT_LEXICON_TIMEOUT


# ============================================================================
# Path Settings (XDG Base Directory Specification)
# ============================================================================


# System paths that are almost certainly a mistake as CHAOSCYPHER_DATA_DIR.
# Advisory only -- the validator warns but does not raise.
_SUSPICIOUS_DATA_DIR_ROOTS: tuple[str, ...] = (
    "/etc",
    "/root",
    "/boot",
    "/sys",
    "/proc",
    "/usr",
    "/bin",
    "/sbin",
    "/lib",
    "/lib64",
    "/dev",
    "C:\\Windows",
    "C:\\Program Files",
    "C:\\Program Files (x86)",
)


class PathSettings(BaseModel):
    r"""Cross-platform path configuration following XDG Base Directory specification.

    Provides sensible defaults for data, config, and cache directories that work
    across Linux, macOS, and Windows:

    - Linux/Mac: ~/.local/share/chaoscypher/, ~/.config/chaoscypher/, ~/.cache/chaoscypher/
    - Windows: %LOCALAPPDATA%\\chaoscypher\\, %APPDATA%\\chaoscypher\\, %LOCALAPPDATA%\\chaoscypher\\cache\\

    Environment variables can override defaults:
    - CHAOSCYPHER_DATA_DIR: Override data directory
    - CHAOSCYPHER_CONFIG_DIR: Override config directory
    - CHAOSCYPHER_CACHE_DIR: Override cache directory

    Example:
        # Use XDG defaults
        paths = PathSettings()
        print(paths.data_dir)  # ~/.local/share/chaoscypher on Linux

        # Override with environment variable
        os.environ["CHAOSCYPHER_DATA_DIR"] = "/custom/path"
        paths = PathSettings()
        print(paths.data_dir)  # /custom/path

    """

    data_dir: str = Field(
        default_factory=lambda: os.getenv(
            "CHAOSCYPHER_DATA_DIR", platformdirs.user_data_dir("chaoscypher", appauthor=False)
        ),
        description="Data directory (databases, graphs, search indices)",
    )

    config_dir: str = Field(
        default_factory=lambda: os.getenv(
            "CHAOSCYPHER_CONFIG_DIR", platformdirs.user_config_dir("chaoscypher", appauthor=False)
        ),
        description="Configuration directory (settings, user config)",
    )

    cache_dir: str = Field(
        default_factory=lambda: os.getenv(
            "CHAOSCYPHER_CACHE_DIR",
            platformdirs.user_cache_dir("chaoscypher", appauthor=False),
        ),
        description="Cache directory (downloaded packages, temp files)",
    )

    # Subdirectory names (relative to data_dir)
    databases_subdir: str = Field(default="databases", description="Databases subdirectory name")
    graphs_subdir: str = Field(default="graphs", description="Graph data subdirectory")
    imports_subdir: str = Field(default="imports", description="Subdirectory for imported files")
    logs_subdir: str = Field(default="logs", description="Service logs subdirectory")

    # File names
    app_db_filename: str = Field(default="app.db", description="SQLite database filename")
    settings_filename: str = Field(default="settings.yaml", description="User settings filename")
    workers_config_filename: str = Field(
        default="workers.yaml", description="Worker config filename"
    )

    # Deployment-specific paths (overridden by env vars in Docker)
    default_settings_path: str = Field(
        default_factory=lambda: os.getenv(
            "CHAOSCYPHER_DEFAULT_SETTINGS_PATH", "/app/backend/default_settings.yaml"
        ),
        description="Default settings template path",
    )
    static_dir: str = Field(
        default_factory=lambda: os.getenv("CHAOSCYPHER_STATIC_DIR", "/app/backend/static"),
        description="Static files directory for SPA serving",
    )

    max_filename_length: int = Field(
        default=80,
        ge=8,
        le=255,
        description="Max chars for sanitized filenames (used by mcp/server.py).",
    )

    @field_validator("data_dir", "config_dir", "cache_dir")
    @classmethod
    def ensure_absolute_path(cls, v: str) -> str:
        """Ensure paths are absolute."""
        path = Path(v).expanduser().resolve()
        return str(path)

    @model_validator(mode="after")
    def _warn_suspicious_data_dir(self) -> PathSettings:
        """Warn if data_dir resolves under a system-sensitive root.

        This is advisory: running Chaos Cypher with
        ``CHAOSCYPHER_DATA_DIR=/etc`` is almost always a misconfiguration,
        but we don't raise because there exist unusual container layouts
        where unusual roots are intentional.
        """
        resolved = Path(self.data_dir).resolve()
        resolved_str = str(resolved)
        for root in _SUSPICIOUS_DATA_DIR_ROOTS:
            try:
                norm_root = str(Path(root).resolve())
            except OSError:
                continue
            try:
                resolved.relative_to(norm_root)
            except ValueError:
                continue
            structlog.get_logger(__name__).warning(
                "path_settings_suspicious_data_dir",
                data_dir=resolved_str,
                matched_root=norm_root,
                hint="CHAOSCYPHER_DATA_DIR resolves under a system path; "
                "user plugin files there would execute with the service's "
                "privileges. See plugins/TRUST_BOUNDARY.md.",
            )
            break
        return self

    @property
    def mcp_dir(self) -> Path:
        """Sandboxed root for files ingested via MCP add_document.

        Lives inside data_dir but is explicitly separated from operator
        data and deployment secrets — a prompt-injected LLM call cannot
        escape this directory to read anything under /data/secrets/ or
        /data/credentials.json.
        """
        return Path(self.data_dir) / "mcp"

    @property
    def packages_dir(self) -> Path:
        """Directory for cached package downloads."""
        return Path(self.cache_dir) / "packages"

    @property
    def auth_file(self) -> Path:
        """Path to authentication token file."""
        return Path(self.config_dir) / "auth.json"

    model_config = ConfigDict(extra="forbid")


# ============================================================================
# Lexicon Settings (Package Registry)
# ============================================================================


class LexiconSettings(BaseModel):
    """Lexicon package registry connection settings.

    Configures the connection to the Chaos Cypher Lexicon for
    downloading, uploading, and searching packages. Used by both
    Cortex (server-side auth) and CLI (client-side requests).

    Environment variables:
        LEXICON_URL: Override Lexicon URL
        LEXICON_API_PATH: Override API path suffix
        CHAOSCYPHER_LEXICON_TIMEOUT: Override request timeout
    """

    url: str = Field(
        default_factory=_default_lexicon_url,
        description="Lexicon server base URL (without API path)",
    )

    @field_validator("url")
    @classmethod
    def _validate_url(cls, v: str) -> str:
        """Reject Lexicon URLs that target cloud-metadata endpoints."""
        from chaoscypher_core.utils.url_safety import validate_url_safety

        if not validate_url_safety(v):
            msg = f"Lexicon url rejected by safety policy: {v!r}"
            raise ValueError(msg)
        return v

    api_path: str = Field(
        default_factory=lambda: os.environ.get("LEXICON_API_PATH", "/api/v1"),
        description="Lexicon API path suffix",
    )
    timeout: int = Field(
        default_factory=_default_lexicon_timeout,
        ge=5,
        le=300,
        description="HTTP request timeout in seconds (env: CHAOSCYPHER_LEXICON_TIMEOUT)",
    )
    upload_timeout: int = Field(
        default=300,
        ge=30,
        le=600,
        description="HTTP timeout for package uploads in seconds",
    )
    max_retries: int = Field(
        default=4,
        ge=0,
        le=10,
        description="Maximum retry attempts for failed requests",
    )
    retry_backoff: list[float] = Field(
        default=[2.0, 4.0, 8.0, 16.0],
        description="Exponential backoff delays in seconds for retries",
    )
    # token / refresh_token use SecretStr so they redact in repr() and
    # logs. A field_serializer round-trips the plaintext to disk so the
    # settings.yaml + auth.json read-write cycle continues to work.
    token: SecretStr | None = Field(
        default=None,
        description="JWT access token (populated after auth)",
    )
    refresh_token: SecretStr | None = Field(
        default=None,
        description="JWT refresh token (populated after auth)",
    )
    username: str | None = Field(
        default=None,
        description="Authenticated username (populated after auth)",
    )

    model_config = ConfigDict(extra="forbid")

    @field_serializer("token", "refresh_token", when_used="always")
    def _serialize_secret(self, v: SecretStr | None) -> str | None:
        """Unwrap SecretStr to its raw value (or None) for persistence."""
        return v.get_secret_value() if v is not None else None

    @property
    def api_url(self) -> str:
        """Get the full Lexicon API URL (base URL + API path)."""
        base = self.url.rstrip("/")
        path = self.api_path if self.api_path.startswith("/") else f"/{self.api_path}"
        return f"{base}{path}"


# ============================================================================
# LLM Settings (Extracted from main app)
# ============================================================================


class OllamaInstance(BaseModel):
    """Configuration for a single Ollama instance.

    Allows distributing LLM workload across multiple Ollama servers.
    All instances share the same model configuration from global settings.
    """

    id: str = Field(description="Unique identifier for this instance")
    name: str = Field(description="Display name (e.g., 'GPU Server 1')")
    base_url: str = Field(description="Ollama server URL (e.g., 'http://192.168.1.10:11434')")
    enabled: bool = Field(default=True, description="Whether this instance is active")
    healthy: bool = Field(default=True, description="Health status (auto-updated)")
    last_health_check: str | None = Field(
        default=None, description="ISO timestamp of last health check"
    )
    last_error: str | None = Field(default=None, description="Last error message if unhealthy")

    @field_validator("base_url")
    @classmethod
    def _validate_base_url(cls, v: str) -> str:
        """Reject base URLs that target cloud-metadata endpoints.

        Permissive policy: loopback/private IPs stay allowed so local
        Ollama on host.docker.internal / 127.0.0.1 / LAN continues to
        work. Cloud-metadata IPs and non-http schemes are rejected.
        """
        from chaoscypher_core.utils.url_safety import validate_url_safety

        if not validate_url_safety(v):
            msg = f"Ollama base_url rejected by safety policy: {v!r}"
            raise ValueError(msg)
        return v


class LLMSettings(BaseModel):
    """LLM provider configuration.

    Engine needs all LLM settings to support multiple providers.
    """

    # Provider selection
    chat_provider: str = Field(
        default_factory=lambda: os.environ.get("CHAOSCYPHER_LLM_PROVIDER", "ollama"),
        description="Chat provider: ollama | openai | anthropic | gemini",
    )

    # Ollama instances. There is always at least one — the default factory
    # seeds a single "default" instance pointed at the Docker host. Multi-GPU
    # users can add more in the settings UI to enable load balancing.
    ollama_instances: list[OllamaInstance] = Field(
        default_factory=lambda: [
            OllamaInstance(
                id="default",
                name="Default",
                base_url="http://host.docker.internal:11434",
            )
        ],
        description=(
            "Ollama instances available for chat/embedding/extraction. The list "
            "must contain at least one entry; the default seeds one pointed at "
            "the Docker host."
        ),
    )
    ollama_load_balancing: str = Field(
        default="round_robin",
        description="Load balancing strategy: round_robin, least_loaded, or random",
    )

    ollama_chat_model: str = Field(default="qwen3:30b-instruct", description="Ollama chat model")
    ollama_extraction_model: str | None = Field(
        default=None, description="Ollama extraction model (falls back to chat model if not set)"
    )
    ollama_vision_model: str | None = Field(
        default=None, description="Ollama vision model (None = vision disabled)"
    )
    ollama_num_batch: int | None = Field(default=None, description="Ollama batch size")
    ollama_num_ctx: int | None = Field(default=32768, description="Ollama context size")
    ollama_num_parallel: int | None = Field(default=None, description="Ollama parallel requests")
    ollama_num_thread: int | None = Field(default=None, description="Ollama thread count")

    # Ollama configuration mode
    ollama_config_mode: str = Field(
        default="quick",
        description="Ollama configuration mode: quick (preset-based) | advanced (full control)",
    )
    ollama_quick_preset: str | None = Field(
        default=None, description="Selected VRAM preset ID for Quick mode"
    )

    # OpenAI configuration
    openai_api_key: SecretStr | None = Field(
        default_factory=lambda: SecretStr(v) if (v := os.environ.get("OPENAI_API_KEY")) else None,
        description="OpenAI API key",
    )
    openai_base_url: str = Field(
        default="https://api.openai.com/v1", description="OpenAI API base URL"
    )
    openai_chat_model: str = Field(default="gpt-4.1", description="OpenAI chat model")
    openai_extraction_model: str | None = Field(
        default=None, description="OpenAI extraction model (falls back to chat model if not set)"
    )
    openai_vision_model: str | None = Field(
        default=None, description="OpenAI vision model (None = vision disabled)"
    )
    openai_context_window: int | None = Field(
        default=1047576, description="OpenAI context window in tokens"
    )
    openai_max_output_tokens: int | None = Field(
        default=32768, description="OpenAI max output tokens"
    )

    # Anthropic configuration
    anthropic_api_key: SecretStr | None = Field(
        default_factory=lambda: (
            SecretStr(v) if (v := os.environ.get("ANTHROPIC_API_KEY")) else None
        ),
        description="Anthropic API key",
    )
    anthropic_chat_model: str = Field(
        default="claude-sonnet-4-5", description="Anthropic chat model"
    )
    anthropic_extraction_model: str | None = Field(
        default=None, description="Anthropic extraction model (falls back to chat model if not set)"
    )
    anthropic_vision_model: str | None = Field(
        default=None, description="Anthropic vision model (None = vision disabled)"
    )
    anthropic_context_window: int | None = Field(
        default=200000, description="Anthropic context window in tokens"
    )
    anthropic_max_output_tokens: int | None = Field(
        default=64000, description="Anthropic max output tokens"
    )

    # Gemini configuration
    gemini_api_key: SecretStr | None = Field(
        default_factory=lambda: SecretStr(v) if (v := os.environ.get("GEMINI_API_KEY")) else None,
        description="Gemini API key",
    )
    gemini_chat_model: str = Field(default="gemini-2.5-pro", description="Gemini chat model")
    gemini_extraction_model: str | None = Field(
        default=None, description="Gemini extraction model (falls back to chat model if not set)"
    )
    gemini_vision_model: str | None = Field(
        default=None, description="Gemini vision model (None = vision disabled)"
    )
    gemini_context_window: int | None = Field(
        default=1048576, description="Gemini context window in tokens"
    )
    gemini_max_output_tokens: int | None = Field(
        default=65536, description="Gemini max output tokens"
    )

    # Vision-specific output-token caps. Per-provider (matches the
    # existing <provider>_max_output_tokens convention) so a deployment
    # mixing providers can tune per-backend. Default 8192 covers ~95%
    # of pages comfortably and stops the 65,536-token runaway observed
    # on qwen3-vl:30b. Truncated pages (finish_reason='length') get
    # accepted with content + VISION_PAGES_TRUNCATED counter in v1;
    # v2 region-split is gated on vision_split_on_truncation below.
    ollama_vision_max_output_tokens: int | None = Field(
        default=8192,
        description=(
            "Per-call output token cap for Ollama vision model. "
            "None = unbounded (not recommended; runaway risk)."
        ),
    )
    openai_vision_max_output_tokens: int | None = Field(
        default=8192,
        description="Per-call output token cap for OpenAI vision model.",
    )
    anthropic_vision_max_output_tokens: int | None = Field(
        default=8192,
        description="Per-call output token cap for Anthropic vision model.",
    )
    gemini_vision_max_output_tokens: int | None = Field(
        default=8192,
        description="Per-call output token cap for Gemini vision model.",
    )
    vision_split_on_truncation: bool = Field(
        default=False,
        description=(
            "v2 hook: split truncated pages top/bottom and re-describe "
            "each half. Off in v1 — truncated pages are accepted and "
            "counted via the VISION_PAGES_TRUNCATED quality counter."
        ),
    )
    vision_image_dpi: int = Field(
        default=150,
        ge=72,
        le=300,
        description=(
            "DPI for rendering PDF pages to PNG before vision processing. "
            "Same value drives both the bytes sent to the vision LLM and "
            "the PNG persisted under "
            "``{data_dir}/databases/<db>/images/<source_id>/page_{N}.png`` "
            "for UI previews. 72 = screen DPI (smaller files, less detail); "
            "150 = balanced default; 300 = print DPI (sharpest, ~4x disk)."
        ),
    )

    # LLM behavior
    ai_max_tokens: int = Field(default=65536, description="Maximum tokens per response")
    ai_context_window: int = Field(
        default=8192,
        ge=8192,
        description="LLM context window in tokens (minimum 8192 for 16GB VRAM)",
    )
    ai_temperature: float = Field(
        default=0.3,
        ge=0.0,
        le=2.0,
        description="LLM temperature for chat interactions",
    )
    extraction_temperature: float = Field(
        default=0.1,
        ge=0.0,
        le=2.0,
        description="LLM temperature for extraction and structured output (lower = more deterministic)",
    )

    # Extraction-specific limits
    extraction_max_tokens: int = Field(
        default=32768,
        gt=0,
        description="Maximum output tokens for extraction LLM calls",
    )

    # Extraction examples
    extraction_examples_enabled: bool = Field(
        default=True,
        description="Enable domain-specific extraction examples in prompts",
    )
    extraction_examples_max_chars: int = Field(
        default=800,
        ge=100,
        description="Maximum characters for formatted domain examples",
    )
    thinking_for_chat: bool = Field(default=True, description="Use thinking for chat operations")
    thinking_for_tools: bool = Field(
        default=False, description="Use thinking for tool call operations"
    )
    thinking_for_extraction: bool = Field(
        default=False, description="Use thinking for entity extraction"
    )
    thinking_auto_detect: bool = Field(
        default=False, description="Auto-detect thinking support for models"
    )

    # Streaming
    chat_interactive_streaming: bool = Field(
        default=True, description="Enable streaming for interactive chat"
    )
    stream_chunk_timeout: float = Field(
        default=120.0, ge=1.0, description="Max seconds between streaming chunks before aborting"
    )
    llm_request_timeout: float = Field(
        default=300.0,
        ge=1.0,
        description=(
            "Bounded per-request timeout (seconds) for LangChain LLM providers. "
            "Protects non-queued callers (chat_sync, direct provider calls) from "
            "hanging indefinitely on an upstream network stall."
        ),
    )

    # Health check
    ollama_health_check_timeout: float = Field(
        default=5.0, ge=0.1, description="Ollama health check timeout in seconds"
    )
    ollama_recovery_delay: float = Field(
        default=0.2,
        ge=0.0,
        description="Delay (seconds) after an Ollama reasoning-mode failure before retrying without reasoning",
    )

    # Queue settings
    enable_llm_queueing: bool = Field(default=True, description="Enable LLM request queueing")
    llm_max_retries: int = Field(default=3, ge=0, description="Max retries for LLM operations")
    llm_max_concurrent: int = Field(default=1, ge=1, description="Max concurrent LLM requests")
    llm_reserved_interactive: int = Field(
        default=0, ge=0, description="Reserved slots for interactive requests"
    )
    llm_enable_priority: bool = Field(default=True, description="Enable priority queueing")

    # Cost tracking
    enable_token_cost_tracking: bool = Field(default=True, description="Track token costs")
    token_cost_input_per_million: float = Field(
        default=5.0, ge=0.0, description="Input token cost per million"
    )
    token_cost_output_per_million: float = Field(
        default=15.0, ge=0.0, description="Output token cost per million"
    )

    # Spend caps (2026-05-19): bound LLM token spend per source and per day.
    # Checked at the provider boundary (chunk_extraction_service.py, the chat
    # handler, and the CLI extraction path) — when exceeded, the next LLM call
    # raises LLMSpendCapExceededError (permanent, no queue retry) so a
    # pathological source can't rack up thousands of dollars overnight. Both
    # caps disabled by default (None) — operators opt in via settings.yaml.
    # Per-source tracking is in-memory; the DAILY counter is persisted
    # per-database in llm_daily_spend (keyed by UTC date, 2026-05-25) so a
    # worker crash-loop cannot re-arm a set daily budget on restart. The window
    # rolls automatically at UTC midnight.
    max_tokens_per_source: int | None = Field(
        default=None,
        ge=1,
        description=(
            "Maximum total tokens (input + output, summed across all "
            "chunks) any single source is allowed to consume during "
            "extraction. The next LLM call after this cap is reached "
            "raises LLMSpendCapExceededError (permanent), so the source "
            "is marked failed instead of continuing to bill. Tracked "
            "in-memory (per worker process). Default None disables the "
            "per-source cap."
        ),
    )
    max_tokens_per_day: int | None = Field(
        default=None,
        ge=1,
        description=(
            "Maximum total tokens (input + output) consumed within a "
            "UTC-day window, scoped per database. Once reached, every "
            "subsequent LLM call raises LLMSpendCapExceededError "
            "(permanent) until the UTC-day rolls over. The running total "
            "is persisted (llm_daily_spend) so it survives worker "
            "restarts — a crash-loop cannot reset it. With one active "
            "database (the common case) this is a worker-wide daily "
            "budget. Default None disables the daily cap."
        ),
    )

    model_config = ConfigDict(extra="forbid")

    @field_validator("openai_base_url")
    @classmethod
    def _validate_openai_base_url(cls, v: str) -> str:
        """Reject OpenAI base URLs that target cloud-metadata endpoints."""
        from chaoscypher_core.utils.url_safety import validate_url_safety

        if not validate_url_safety(v):
            msg = f"openai_base_url rejected by safety policy: {v!r}"
            raise ValueError(msg)
        return v

    # API keys use SecretStr so they redact in repr() and logs. A
    # field_serializer round-trips the plaintext to disk so the
    # settings.yaml read-write cycle works (same pattern as
    # LexiconSettings.token). Without it, model_dump(mode="json") masks
    # the value to "**********" and ConfigManager would persist the mask,
    # corrupting the stored key. API responses stay masked separately via
    # app_config.mask_settings_dict.
    @field_serializer("openai_api_key", "anthropic_api_key", "gemini_api_key", when_used="always")
    def _serialize_api_keys(self, v: SecretStr | None) -> str | None:
        """Unwrap SecretStr to its raw value (or None) for persistence."""
        return v.get_secret_value() if v is not None else None

    @property
    def primary_ollama_url(self) -> str:
        """Return the base URL of the first enabled Ollama instance.

        Falls back to the first instance regardless of enabled state, then
        to a localhost default — but ``ollama_instances`` is seeded with a
        default entry so the fallback should never trigger in practice.
        """
        for inst in self.ollama_instances:
            if inst.enabled:
                return inst.base_url
        if self.ollama_instances:
            return self.ollama_instances[0].base_url
        return "http://host.docker.internal:11434"


# ============================================================================
# Batching Settings (Extracted from main app)
# ============================================================================


class BatchingSettings(BaseModel):
    """Batch processing configuration.

    Used by engine for embedding generation and other batch operations.
    """

    # Embedding
    embedding_batch_size: int = Field(
        default=512, ge=16, description="Texts per concurrent embedding dispatch"
    )
    embedding_concurrency: int = Field(
        default=4, ge=1, description="Concurrent embedding batch requests"
    )
    embedding_api_batch_size: int = Field(
        default=64,
        ge=1,
        description="Texts per single embedding API call (provider-level chunking)",
    )

    # Discovery
    discovery_batch: int = Field(default=1000, ge=1, description="Entity discovery batch size")

    # Internal bulk fetch (commit, indexing — NOT user-facing pagination)
    chunk_fetch_limit: int = Field(
        default=100_000, ge=1000, description="Max chunks to fetch in internal bulk operations"
    )

    # Export
    export_page_size: int = Field(
        default=500, ge=50, description="Page size for paginated export data collection"
    )

    # Summarization
    summarize_chunk_page_size: int = Field(
        default=200,
        ge=1,
        description="Page size when fetching chunks for the summarize tool",
    )

    # Graph analysis limits
    graph_analysis_node_limit: int = Field(
        default=1_500_000, ge=1000, description="Max nodes to load for graph analytics"
    )
    graph_analysis_edge_limit: int = Field(
        default=4_000_000, ge=1000, description="Max edges to load for graph analytics"
    )

    # ------------------------------------------------------------------
    # App-side batch fields (previously the separate app-only ``BatchSettings``
    # class, now unioned into core ``BatchingSettings`` — Tier 2 schema
    # unification). ``BatchSettings`` remains a back-compat alias in app_config.
    # The four overlap fields above (discovery_batch, export_page_size,
    # graph_analysis_node_limit, graph_analysis_edge_limit) carried identical
    # defaults on both sides, so they are not duplicated here.
    # ------------------------------------------------------------------

    # Upload limits
    max_upload_files: int = Field(
        default=20, description="Maximum files allowed in a single batch upload"
    )
    max_upload_bytes: int = Field(
        default=5 * 1024 * 1024 * 1024,
        description=(
            "Maximum upload size in bytes per file (default 5 GB). Applies to "
            "the cortex upload endpoint, URL import, MCP uploads, and the "
            "rendered nginx config (Jinja templates read this value). The "
            "parser-bound caps in `LoaderSettings.max_disk_bytes` (default "
            "500 MB) gate in-process parsers (PDF/CSV/DOCX/text) separately — "
            "in-process parsers can OOM on multi-GB files even when the "
            "upload itself fits, so the two caps are intentionally distinct. "
            "Video and audio loaders stream via ffmpeg and are bounded by "
            "this upload cap, not the parser cap."
        ),
    )
    upload_content_type_allowlist: list[str] = Field(
        default_factory=lambda: [
            "application/pdf",
            "text/plain",
            "text/markdown",
            "text/html",
            "application/json",
            "application/x-ndjson",  # JSONL — newline-delimited JSON
            "application/jsonl",  # alt MIME some clients send for .jsonl
            "application/xml",
            "text/xml",
            "text/csv",
            "application/zip",
            "application/x-zip-compressed",
            "application/gzip",
            "application/x-tar",
            "image/png",
            "image/jpeg",
            "image/webp",
            "image/gif",
            "application/epub+zip",  # EPUB e-books (epub_loader.py)
            "application/msword",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            # NB: ``application/octet-stream`` is intentionally NOT in
            # the default — see field description below.
        ],
        description=(
            "Allowed content types for source uploads. Set to ['*'] to "
            "disable the check.\n\n"
            "``application/octet-stream`` is intentionally NOT in the "
            "default list because it defeats the allowlist (any binary "
            "the browser doesn't recognise uploads as octet-stream, so "
            "including it accepts every binary). Operators who need "
            "binary uploads can add it via ``settings.yaml``."
        ),
    )
    max_connected_edges: int = Field(
        default=2000,
        ge=1,
        description="Safety cap for connected-nodes edge query",
    )
    bulk_request_max_operations: int = Field(
        default=500,
        ge=1,
        description=(
            "Maximum operations allowed in a single BulkRequest. Higher values "
            "let operators batch more in one API call; lower values bound the "
            "memory footprint per request."
        ),
    )
    embedding_wave_size: int = Field(
        default=2_000,
        ge=1,
        description=(
            "Cost / resource-exhaustion fix (2026-05-25): max chunks the "
            "embedding stage holds in memory per wave. The embedding handler "
            "keyset-paginates unembedded chunks and embeds one wave at a time, "
            "so a multi-GB document cannot materialize every chunk (+content) "
            "at once and OOM the worker. Peak memory is bounded by this value; "
            "embedding still happens in full — only the working set is capped. "
            "Independent of the engine-level embedding_batch_size (the "
            "per-dispatch fan-out inside one wave)."
        ),
    )

    # PDF processing
    pdf_ocr_batch: int = Field(default=64, description="OCR batch size for PDF processing")
    pdf_layout_batch: int = Field(default=64, description="Layout analysis batch size")
    pdf_page_batch: int = Field(default=64, description="Page processing batch size")

    # VLM Processing
    vlm_concurrency: int = Field(default=64, description="Vision-Language Model concurrency")

    # Graph operations
    edge_list_limit: int = Field(
        default=10000, description="Max edges to load for graph operations"
    )

    # Caching
    template_name_cache_size: int = Field(
        default=1000, description="Max entries in template name cache"
    )

    # Queue display
    queue_max_depth_display: int = Field(default=1000, description="Max queue depth for UI display")

    # Background handler yield cadence (queue handlers yield to the event loop between batches)
    quality_score_batch_size: int = Field(
        default=100,
        ge=1,
        description="Batch size for async quality-score recalculation loops (yields to event loop between batches)",
    )
    template_embedding_batch_size: int = Field(
        default=50,
        ge=1,
        description="Batch size for template embedding regeneration loops (yields to event loop between batches)",
    )

    # SQLite
    sqlite_cache_size_kb: int = Field(
        default=64000, description="SQLite cache size in KB (negative for KB, positive for pages)"
    )

    # Request size — outer HTTP body limit for non-upload routes. Upload
    # endpoints (POST /api/v1/sources, /sources/batch, /lexicon/upload,
    # /exports/import) are exempted by the body-size middleware and
    # enforce ``max_upload_bytes`` (default 5 GB) themselves during
    # streaming. 128 MB clears the largest existing per-route cap
    # (MCP submit_chunk_extraction at 10 MB) by ~12x and leaves headroom
    # for plausible JSON round-trips (the entities-list response is
    # ~30 MB today), while staying ~80x tighter than the pre-2026-05-22
    # default of 10 GB.
    max_request_body_mb: int = Field(
        default=128, description="Maximum request body size in MB for non-upload routes"
    )

    # TLS certificate upload
    tls_cert_max_size: int = Field(
        default=1_048_576, description="Maximum TLS certificate/key file size in bytes (1 MB)"
    )

    # File upload streaming
    upload_chunk_size: int = Field(
        default=1_048_576,
        ge=65536,
        description="Chunk size in bytes for streaming file uploads (default 1 MB)",
    )
    upload_max_concurrent: int = Field(
        default=2,
        ge=1,
        description=(
            "Maximum concurrent streaming uploads allowed across the process. "
            "Caps disk/memory consumption from browser-DNS-rebinding drive-by "
            "attacks or runaway parallel imports. Additional uploads beyond this "
            "cap wait for a slot."
        ),
    )
    upload_disk_headroom_bytes: int = Field(
        default=1024 * 1024 * 1024,
        ge=0,
        description=(
            "Extra free-disk-space headroom required above max_upload_bytes "
            "before accepting an upload. Guards against filling the disk when "
            "a 10 GB max is configured but only 500 MB is free (default 1 GB)."
        ),
    )
    search_index_pending_batch_size: int = Field(
        default=500,
        ge=1,
        description="Batch size for pending_search_index drain (neuron search_sweep.py).",
    )
    graphrag_edge_query_limit: int = Field(
        default=50,
        ge=1,
        description="Max edges per direction loaded by graphrag tool handlers.",
    )
    quality_score_max_tracked_errors: int = Field(
        default=100,
        ge=10,
        description="Max errors retained by the quality-score handler before discarding.",
    )
    quality_score_max_returned_errors: int = Field(
        default=10,
        ge=1,
        description="Max errors returned to the client in the quality-score response.",
    )

    # Frontend-facing batch defaults (PR3-A) — exposed via /api/v1/settings/public
    bulk_operation_size: int = Field(
        default=50,
        ge=1,
        description="Frontend default bulk-operation batch size.",
    )
    polling_max_attempts: int = Field(
        default=60,
        ge=1,
        description="Frontend max poll attempts.",
    )
    polling_wait_ms: int = Field(
        default=1_000,
        ge=10,
        description="Frontend poll interval (ms).",
    )
    export_max_attempts: int = Field(
        default=120,
        ge=1,
        description="Frontend export-poll max attempts.",
    )
    import_max_attempts: int = Field(
        default=180,
        ge=1,
        description="Frontend import-poll max attempts.",
    )
    graph_source_page_size: int = Field(
        default=200,
        ge=1,
        description="Graph canvas source list page size.",
    )
    frontend_upload_timeout_ms: int = Field(
        default=120_000,
        ge=1_000,
        description="SPA upload XHR timeout.",
    )
    frontend_batch_upload_timeout_ms: int = Field(
        default=300_000,
        ge=1_000,
        description="SPA batch upload XHR timeout.",
    )

    model_config = ConfigDict(extra="forbid")


# ============================================================================
# Extraction Settings (LLM extraction retries & quality)
# ============================================================================


class ExtractionSettings(BaseModel):
    """Configuration for LLM-based structured extraction.

    Controls retry backoff, quality thresholds, health check pauses,
    and loop detection used by StructuredExtractor during entity extraction.
    """

    quality_issue_threshold: float = Field(
        default=0.3,
        ge=0.0,
        le=1.0,
        description="Ratio of entities with quality issues that triggers a retry (0.0-1.0)",
    )
    llm_backoff_max_seconds: int = Field(
        default=30, ge=1, description="Maximum backoff delay between retries"
    )
    llm_backoff_multiplier: int = Field(
        default=5, ge=1, description="Multiplier for calculating backoff (5s, 10s, 15s...)"
    )
    llm_healthy_pause_seconds: float = Field(
        default=2.0, ge=0, description="Brief pause when provider is healthy but returned empty"
    )

    # Loop detection thresholds (streaming and post-hoc parsing)
    loop_max_out_of_bounds: int = Field(
        default=3,
        ge=1,
        description="Maximum consecutive out-of-bounds relationship indices before aborting",
    )
    loop_max_source_type_repeat: int = Field(
        default=10,
        ge=2,
        description="Maximum consecutive identical (source, type) relationship pairs before aborting. "
        "Must be high enough to avoid false positives on dense entities.",
    )
    loop_max_property_repeat: int = Field(
        default=5,
        ge=2,
        description="Maximum consecutive identical (entity_index, property_key) P| lines before aborting. "
        "Catches degenerate property loops where the LLM repeats the same property endlessly.",
    )
    loop_max_entity_count: int = Field(
        default=50,
        ge=10,
        le=200,
        description="Maximum entities per chunk group before aborting stream. "
        "Catches runaway entity hallucination where the LLM invents nonsensical entities.",
    )
    loop_invalid_relationship_rate_warmup: int = Field(
        default=10,
        ge=1,
        le=100,
        description=(
            "Number of relationship lines that must be observed before the "
            "invalid-rate detector can fire. Prevents false positives on "
            "small chunks where one bad line out of three would otherwise "
            "trip a 33% threshold."
        ),
    )
    loop_invalid_relationship_rate_threshold: float = Field(
        default=0.5,
        gt=0.0,
        le=1.0,
        description=(
            "Fraction of relationship lines with out-of-bounds entity indices "
            "above which the stream is aborted. Catches the failure mode where "
            "the model produces hundreds of invalid lines interleaved with a "
            "few valid ones — the consecutive-streak detector misses these "
            "because the runs aren't long enough to trigger, but the high "
            "overall rate (e.g. 336 invalid out of 352 total seen in real "
            "imports) is unambiguously degenerate."
        ),
    )

    # Post-extraction relationship limits (code-enforced caps)
    max_relationship_ratio: float = Field(
        default=8.0,
        ge=0.5,
        le=20.0,
        description="Maximum relationships as a multiple of entity count. Acts as a safety net "
        "against LLM runaway output — most valid networks stay well below this.",
    )
    max_entity_degree: int = Field(
        default=25,
        ge=2,
        le=100,
        description="Maximum relationships per entity (as source or target combined). Safety net "
        "for runaway output — protagonists in dense narratives can legitimately have 20+ edges.",
    )
    max_same_source_type: int = Field(
        default=12,
        ge=1,
        le=50,
        description="Maximum relationships with the same (source_index, type) pair",
    )

    # Evidence validation mode
    evidence_validation_mode: str = Field(
        default="standard",
        description="Evidence validation strictness: strict (full name match), "
        "standard (significant word match + rel type keyword), "
        "narrative (one entity name or rel type keyword — for pronoun-heavy text), "
        "relaxed (valid sent_ref only)",
    )

    # Edge type constraint behavior
    strict_edge_type_constraints: bool = Field(
        default=False,
        description="When True, relationships with types not matching any domain template "
        "are dropped. When False (default), unmatched types pass through without "
        "source/target constraint validation.",
    )

    # Filtering mode preset
    extraction_filtering_mode: str = Field(
        default="balanced",
        description="Filtering mode preset controlling extraction quality filters (scale 0-5). "
        "Options: maximum (5, all filters + drop mismatches), strict (4, strict evidence), "
        "balanced (3, default with fall-throughs), lenient (2, forgiving for prose), "
        "minimal (1, most filters disabled), unfiltered (0, dedup only).",
    )

    # Direction-correction toggle (Phase 4, 2026-05-08)
    enable_direction_correction: bool = Field(
        default=True,
        description=(
            "Phase 4 (2026-05-08): when True (default), relationships whose "
            "source/target violate domain type constraints are silently "
            "swapped (current behavior). When False, the relationship is "
            "dropped instead. RELATIONSHIPS_DIRECTION_CORRECTED counter "
            "increments either way — it measures wrong-direction LLM "
            "emission rate independent of how we handle it."
        ),
    )

    # Orphan-protection toggle (Phase 4, 2026-05-08)
    protect_orphans: bool = Field(
        default=False,
        description=(
            "Phase 4 (2026-05-08): when False (default), entities with no "
            "relationships are dropped before commit (classic behavior). When "
            "True, orphan entities are kept. Polarity is intentional: "
            "'protect_orphans=True' means keep them. Per-source and domain "
            "overrides take precedence over this global default."
        ),
    )

    # Phase 6 (2026-05-08): inverse-relationship toggle.
    # When True (default), the commit step auto-creates reverse edges for every
    # directed relationship that appears in the domain's inverse_relationships
    # map. When False, no inverse edges are created regardless of domain config.
    # Per-source override (nullable boolean on sources table) takes precedence
    # over the global default; domain config comes between the two.
    enable_inverse_relationships: bool = Field(
        default=True,
        description=(
            "Phase 6 (2026-05-08): when True (default), directed relationships "
            "are mirrored as inverse edges according to the domain's "
            "inverse_relationships map. When False, no inverse edges are ever "
            "created. 3-layer cascade: per-source (nullable) → domain config → "
            "this global default."
        ),
    )

    # Structural entity filtering
    filter_structural_entities: bool = Field(
        default=True,
        description="Filter out structural entities (chapters, sections, etc.) during cross-chunk processing",
    )

    # Semantic deduplication
    semantic_dedup_threshold: float = Field(
        default=0.95,
        ge=0.5,
        le=1.0,
        description="Cosine similarity threshold for semantic entity deduplication (higher = stricter)",
    )

    # Alias parsing
    minimum_alias_length: int = Field(
        default=2,
        ge=1,
        le=10,
        description="Minimum character length for an alias to be accepted during parsing",
    )

    # Plausibility thresholds for implausible entity filtering
    plausibility_threshold: float = Field(
        default=0.40,
        ge=0.0,
        le=1.0,
        description="Score below which named-type entities are rejected as implausible (0.0-1.0)",
    )
    plausibility_threshold_non_named: float = Field(
        default=0.15,
        ge=0.0,
        le=1.0,
        description="Score below which non-named-type entities are rejected (0.0-1.0)",
    )

    # Visual content adjustments
    visual_content_plausibility_factor: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Multiplier applied to plausibility thresholds for visual content chunks "
        "(prefixed with '[Visual Content]'). 0.5 = halve thresholds, 0.0 = skip filtering, "
        "1.0 = no adjustment. Domains can override via extraction_limits.",
    )

    # Evidence validation word matching
    min_significant_word_length: int = Field(
        default=4,
        ge=2,
        le=10,
        description="Minimum character length for a word to count as 'significant' in evidence matching",
    )

    # Loop detector: relationship count cap and same-pair cap
    # (Phase 3, CLAUDE.md "zero hardcoded config values")
    loop_max_relationship_multiplier: float = Field(
        default=4.0,
        gt=0.0,
        le=20.0,
        description=(
            "Maximum relationships as a multiple of max_entity_count for the streaming "
            "loop detector. loop_max_relationship_count = max_entity_count * this value."
        ),
    )
    loop_max_same_pair: int = Field(
        default=6,
        ge=1,
        le=50,
        description=(
            "Maximum relationships with the same (source_index, target_index) pair "
            "allowed before the streaming loop detector aborts the stream."
        ),
    )

    # Empty-output retry threshold
    # (Phase 3, CLAUDE.md "zero hardcoded config values")
    empty_output_retry_min_chars: int = Field(
        default=200,
        ge=1,
        description=(
            "Minimum chunk characters below which an empty LLM extraction output is "
            "accepted as-is. Above this threshold, empty output is treated as a "
            "retryable transient error (model glitch, RECITATION soft-stop, etc.)."
        ),
    )

    # Deduplication thresholds
    # (Phase 3, CLAUDE.md "zero hardcoded config values")
    dedup_type_partition_cutoff: int = Field(
        default=50,
        ge=1,
        description=(
            "Minimum entity count to trigger type-partitioned comparison in semantic "
            "deduplication. Below this cutoff all entities are compared in one group."
        ),
    )
    dedup_no_overlap_boost: float = Field(
        default=0.08,
        ge=0.0,
        le=0.5,
        description=(
            "Extra similarity required when entity names share no significant words. "
            "Prevents merging semantically-similar but distinct entities (e.g. two "
            "Italian cities) whose embeddings are close but names don't overlap."
        ),
    )
    dedup_borderline_penalty: float = Field(
        default=0.05,
        ge=0.0,
        le=0.5,
        description=(
            "Confidence penalty applied to entities merged within 0.10 of the "
            "similarity threshold (borderline merges). Applied as a reduction to "
            "the merged entity confidence score."
        ),
    )

    # Phase 6 (2026-05-08): configurable system prompt.
    # Lifted from the hardcoded SYSTEM_PROMPT constant in prompts.py so operators
    # can tailor the extraction persona without touching source code.
    # Domain configs can override via extraction_overrides.system_prompt.
    system_prompt: str = Field(
        default=(
            "You are an expert at extracting structured knowledge from text. "
            "Output ONLY the requested format with no additional text."
        ),
        description=(
            "System prompt sent with every LLM extraction call. "
            "Changing this can adjust extraction persona and behaviour globally. "
            "Per-domain overrides take precedence when set via "
            "extraction_overrides.system_prompt in the domain config."
        ),
    )

    # Domain detection + entity-description tuning (previously app-only, now
    # unioned into the single core class — see Tier 2 schema unification).
    # These previously lived as magic literals in adapters/llm/schema/extractor.py
    # and mcp/extraction.py before being lifted into ExtractionSettings.
    domain_detection_sample_count: int = Field(
        default=5,
        ge=1,
        description="Number of chunk samples used for domain auto-detection.",
    )
    domain_detection_sample_chars: int = Field(
        default=2000,
        ge=100,
        description="Chars per sample for domain auto-detection.",
    )
    entity_desc_min_length: int = Field(
        default=10,
        ge=1,
        description="Minimum entity description length (below this is rejected as low quality).",
    )
    entity_desc_incomplete_threshold: int = Field(
        default=20,
        ge=1,
        description=(
            "Entity description length under this is flagged as 'incomplete' "
            "for retry-pass enrichment."
        ),
    )
    research_context_window_chars: int = Field(
        default=2000,
        ge=100,
        description="Chars of document context included in chat/research prompts.",
    )

    model_config = ConfigDict(extra="forbid")


# ============================================================================
# Chat Settings (Tool calling limits)
# ============================================================================


class ChatSettings(BaseModel):
    """Chat tool-calling configuration.

    Controls iteration limits for multi-step tool calling in chat.
    Used by both Cortex (streaming chat) and CLI (direct chat).
    """

    max_tool_iterations: int = Field(
        default=10, ge=1, description="Maximum rounds of tool calling before forcing final response"
    )
    max_total_tool_calls: int = Field(
        default=25, ge=1, description="Maximum total tool calls across all iterations"
    )
    max_tools: int = Field(
        default=14,
        ge=1,
        description="Maximum tools to include per chat (prevents context overflow)",
    )
    enable_response_validation: bool = Field(
        default=True,
        description="Run a post-response LLM call to verify answer relevance (adds ~1-3s latency after streaming)",
    )
    tools_token_estimate: int = Field(
        default=2000,
        ge=0,
        description="Estimated tokens consumed by tool schemas in chat context (used for budget calculation)",
    )
    tool_approval: Literal["always-ask", "ask-on-write", "never-ask"] = Field(
        default="never-ask",
        description=(
            "Approval requirement for tool calls emitted by the chat LLM. "
            "'always-ask' requires user confirmation on every tool call; "
            "'ask-on-write' requires confirmation only for mutating tools "
            "(create/update/delete/add_document/remove_document/finalize_extraction); "
            "'never-ask' runs tools automatically. Current code always runs "
            "tools (never-ask behavior); the other modes are wired for a future "
            "UI approval flow. Defense-in-depth: the system prompt instructs "
            "the model to treat retrieved chunks as untrusted data regardless "
            "of mode, so prompt-injection alone cannot trigger a write without "
            "also bypassing the system prompt."
        ),
    )
    mutating_tools: list[str] = Field(
        default_factory=lambda: [
            "create_node",
            "update_node",
            "delete_node",
            "create_edge",
            "delete_edge",
            "create_template",
            "delete_template",
            "add_document",
            "remove_document",
            "finalize_extraction",
            "submit_chunk_extraction",
        ],
        description=(
            "Tool names that the 'ask-on-write' approval mode treats as "
            "mutating. Override this list to customize which tools auto-run."
        ),
    )
    log_message_preview_chars: int = Field(
        default=200,
        ge=10,
        description="Max chars of message content included in debug logs.",
    )
    citation_search_key_chars: int = Field(
        default=60,
        ge=10,
        description="Length of the quote-search key in citation matching.",
    )
    citation_min_quote_chars: int = Field(
        default=40,
        ge=1,
        description="Min chars for a quote to be considered citation-eligible.",
    )
    citation_min_match_chars: int = Field(
        default=12,
        ge=1,
        description="Min chars for a candidate string to be a citation match.",
    )

    model_config = ConfigDict(extra="forbid")


# ============================================================================
# MCP Settings
# ============================================================================


class MCPSettings(BaseModel):
    """MCP server configuration."""

    mode: Literal["read", "write"] = Field(
        default="read",
        description="Tool access mode: 'read' for read-only, 'write' for full access",
    )
    auto_extract: bool = Field(
        default=False,
        description="Run server-side LLM entity extraction after indexing. "
        "When false (default), the MCP client drives extraction itself.",
    )
    completed_history_limit: int = Field(
        default=20,
        ge=1,
        description="Max completed MCP documents to retain in in-memory history before evicting oldest",
    )
    max_extraction_payload_bytes: int = Field(
        default=10 * 1024 * 1024,
        ge=1024,
        description="Max combined UTF-8 byte size of entities_text + relationships_text "
        "accepted by submit_chunk_extraction. Rejected as PAYLOAD_TOO_LARGE above this cap.",
    )
    extraction_rate_limit_per_minute: int = Field(
        default=100,
        ge=1,
        description="Max submit_chunk_extraction calls per source_id per 60s sliding window.",
    )
    confirmation_required_default: bool = Field(
        default=True,
        description="Server-wide default for the domain confirmation gate. "
        "When true, an auto-detected (unforced) domain parks the source as "
        "'awaiting_confirmation' until confirmed. Per-call add_document "
        "auto_confirm=true overrides this for a single upload.",
    )

    model_config = ConfigDict(extra="forbid")


# ============================================================================
# Source processing Settings (Extracted from main app)
# ============================================================================


# Module-level guards for the deprecation warning fired by
# ``SourceProcessingSettings._warn_if_deprecated_max_file_size_gb``. We track
# this here (rather than on the class) so repeated constructions of the model
# inside a single process emit the warning at most once.
_DEFAULT_MAX_FILE_SIZE_GB = 100
_max_file_size_gb_deprecation_warned: bool = False


def _reset_max_file_size_gb_deprecation_warning() -> None:
    """Reset the deprecation-warned flag (test helper).

    Allows tests that exercise the deprecation path to assert the warning is
    emitted on the first construction without bleeding state between tests.
    Not part of the public API.
    """
    global _max_file_size_gb_deprecation_warned
    _max_file_size_gb_deprecation_warned = False


class SourceProcessingSettings(BaseModel):
    """Source processing configuration.

    Engine needs entity deduplication settings for ExtractionService.
    """

    # File size limits
    #
    # Deprecated as of 2026-05-06 (F12): the canonical upload cap is
    # ``settings.batching.max_upload_bytes``. The field is retained so that
    # existing ``settings.yaml`` files continue to parse under
    # ``extra="forbid"``, and so the per-test fixtures that set it on a
    # MagicMock keep working — but the value is no longer consulted by the
    # upload pipeline (file uploads, URL fetch, MCP all share
    # ``max_upload_bytes`` now). A startup-time deprecation warning fires
    # whenever the field is set to a non-default value (see the
    # ``_warn_if_deprecated_max_file_size_gb`` validator below).
    source_processing_max_file_size_gb: int = Field(
        default=100,
        ge=1,
        description=(
            "DEPRECATED (2026-05-06, F12): legacy maximum file size in GB. "
            "Replaced by batching.max_upload_bytes (the unified upload cap used "
            "by both file uploads and URL fetches). The value is no longer "
            "consulted by the upload pipeline. Retained only for backwards "
            "compatibility with existing settings.yaml files; setting it to a "
            "non-default value triggers a one-shot startup warning pointing to "
            "the replacement key (see _warn_if_deprecated_max_file_size_gb)."
        ),
    )

    # Entity deduplication
    entity_deduplication_mode: str = Field(
        default="semantic", description="Deduplication mode: exact | semantic"
    )
    entity_deduplication_similarity_threshold: float = Field(
        default=0.90,
        ge=0.0,
        le=1.0,
        description="Similarity threshold for semantic deduplication (0.0-1.0)",
    )
    entity_max_description_length: int = Field(
        default=8000,
        ge=500,
        description="Max characters for merged entity descriptions to prevent runaway growth during deduplication",
    )

    # Web scraping
    web_scraper_max_length: int = Field(
        default=5000, gt=0, description="Maximum content length for web scraper extraction"
    )

    # Analysis defaults
    auto_extract_entities: bool = Field(default=True, description="Auto-analyze source processing")
    source_processing_analysis_depth: str = Field(
        default="full", description="Analysis depth: quick | full"
    )
    source_processing_chunk_overlap: int = Field(
        default=500, ge=0, description="Chunk overlap (characters)"
    )
    source_processing_chunking_strategy: str = Field(
        default="hierarchical", description="Chunking strategy: hierarchical | fixed"
    )
    relationship_confidence_threshold: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Minimum confidence for extracted relationships",
    )

    # Type-aware deduplication
    dedup_require_type_compatibility: bool = Field(
        default=True,
        description="Only merge same-name entities when types are compatible",
    )
    dedup_type_compatibility_map: dict[str, list[str]] = Field(
        default={},
        description="Custom type compatibility groups, e.g. {'Person': ['Character', 'Individual']}",
    )

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def _warn_if_deprecated_max_file_size_gb(self) -> SourceProcessingSettings:
        """Emit a one-shot warning when the deprecated GB cap is set explicitly.

        ``source_processing_max_file_size_gb`` was the legacy file-upload cap
        before the upload pipeline was unified on ``batching.max_upload_bytes``
        (F12, 2026-05-06). The field is retained so that existing
        ``settings.yaml`` files still parse under ``extra="forbid"``, but the
        value is no longer consulted by the upload pipeline. We log a
        deprecation hint when the user has it set to anything other than the
        default so the next sweep of their settings file removes it cleanly.

        The warning is gated by a module-level flag so the test suite (which
        constructs this model thousands of times with the legacy value) does
        not spam the log on every construction.
        """
        global _max_file_size_gb_deprecation_warned
        if (
            self.source_processing_max_file_size_gb != _DEFAULT_MAX_FILE_SIZE_GB
            and not _max_file_size_gb_deprecation_warned
        ):
            structlog.get_logger(__name__).warning(
                "source_processing_max_file_size_gb_deprecated",
                configured_value_gb=self.source_processing_max_file_size_gb,
                replacement="batching.max_upload_bytes",
                hint=(
                    "source_processing_max_file_size_gb is deprecated as of "
                    "2026-05-06 (F12) and is no longer honored. Set "
                    "batching.max_upload_bytes instead (default 5 GB). "
                    "Remove the deprecated key from settings.yaml to silence "
                    "this warning."
                ),
            )
            _max_file_size_gb_deprecation_warned = True
        return self


# ============================================================================
# Normalizer Settings (Content Cleaning)
# ============================================================================


class NormalizerSettings(BaseModel):
    """Content normalization and cleaning configuration.

    Controls the content normalization pipeline including encoding fixes,
    OCR artifact removal, and output formatting.

    Used by ContentNormalizerService for document cleaning.
    """

    # Encoding & Unicode
    enable_encoding_fix: bool = Field(
        default=True, description="Fix encoding issues (mojibake) using ftfy"
    )
    enable_unicode_normalize: bool = Field(
        default=True, description="Normalize Unicode to NFC form"
    )

    # Whitespace & Control Characters
    enable_whitespace_normalize: bool = Field(
        default=True, description="Normalize whitespace (multiple spaces, tabs, etc.)"
    )
    enable_control_char_removal: bool = Field(
        default=True, description="Remove control characters (except newlines)"
    )

    # OCR Cleaning
    enable_ocr_cleaning: bool = Field(default=True, description="Apply OCR artifact cleaning")
    enable_duplicate_removal: bool = Field(
        default=True, description="Remove duplicate paragraphs/lines"
    )

    # Output Transformation
    enable_markdown_normalize: bool = Field(
        default=True, description="Normalize output to consistent Markdown format"
    )

    # Thresholds
    min_line_length: int = Field(default=5, ge=1, description="Minimum line length to keep (chars)")
    min_alpha_ratio: float = Field(
        default=0.4,
        ge=0.0,
        le=1.0,
        description="Minimum ratio of alphabetic characters for valid text",
    )
    gibberish_threshold: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Threshold for gibberish detection (lower = stricter)",
    )
    duplicate_similarity_threshold: float = Field(
        default=0.85,
        ge=0.0,
        le=1.0,
        description="Similarity threshold for duplicate paragraph detection",
    )

    # Output Format
    target_format: str = Field(
        default="markdown",
        description="Target output format: markdown | text",
    )

    # ftfy options (Phase 3, CLAUDE.md "zero hardcoded config values")
    ftfy_fix_character_width: bool = Field(
        default=True,
        description=(
            "Pass fix_character_width=True to ftfy.fix_text. Converts fullwidth and "
            "halfwidth Latin/ASCII characters to their standard equivalents."
        ),
    )
    ftfy_fix_line_breaks: bool = Field(
        default=True,
        description=(
            "Pass fix_line_breaks=True to ftfy.fix_text. Converts unusual line-ending "
            "sequences (e.g. NEL, vertical tab) to standard Unix newlines."
        ),
    )

    # OCR page-artifact detection thresholds
    # (Phase 3, CLAUDE.md "zero hardcoded config values")
    ocr_page_artifact_min_repeats: int = Field(
        default=3,
        ge=2,
        description=(
            "Minimum number of times a short line must appear before it is treated "
            "as a repeated OCR artifact (header/footer). Equivalent to the previous "
            "hardcoded `count > 2` comparison (i.e. appearing 3+ times)."
        ),
    )
    ocr_page_artifact_max_line_length: int = Field(
        default=30,
        ge=1,
        description=(
            "Maximum character length for a line to be considered a repeated artifact. "
            "Lines longer than this are never added to the artifact set even if they "
            "repeat the minimum number of times."
        ),
    )
    ocr_page_artifact_candidate_max_length: int = Field(
        default=50,
        ge=1,
        description=(
            "Maximum character length for a line to be counted as a candidate "
            "artifact occurrence. Lines longer than this are excluded from the "
            "repeat-count tally entirely."
        ),
    )

    # Phase 6 (2026-05-08): expose trafilatura extraction settings so operators
    # can tune precision/recall and image/comment handling per deployment.
    web_trafilatura_favor_precision: bool = Field(
        default=True,
        description=(
            "Phase 6 (2026-05-08): pass favor_precision to trafilatura.extract. "
            "True (default) prefers precision over recall — less content but higher "
            "signal-to-noise. Set False to favour recall (more content, more noise)."
        ),
    )
    web_trafilatura_include_images: bool = Field(
        default=False,
        description=(
            "Phase 6 (2026-05-08): pass include_images to trafilatura.extract. "
            "False (default) strips <img> alt text. Set True to include image "
            "descriptions (increases text volume, useful for documentation)."
        ),
    )
    web_trafilatura_include_comments: bool = Field(
        default=False,
        description=(
            "Phase 6 (2026-05-08): pass include_comments to trafilatura.extract. "
            "False (default) strips comment sections. Set True for forums/blogs "
            "where discussion is part of the knowledge."
        ),
    )
    web_basic_strip_tags: list[str] = Field(
        default_factory=lambda: ["nav", "footer", "header", "aside"],
        description=(
            "Phase 6 (2026-05-08): HTML tags stripped by the basic BeautifulSoup "
            "fallback extractor in WebCleaner. Defaults to the previous hardcoded "
            "list [nav, footer, header, aside]. Extend this list to strip additional "
            "chrome elements when trafilatura is unavailable."
        ),
    )

    model_config = ConfigDict(extra="forbid")


# ============================================================================
# Database Settings (SQLite Connection & Retry)
# ============================================================================


class DatabaseSettings(BaseModel):
    """SQLite database connection and retry configuration.

    Performance-tuned for multi-process writes (Cortex + Neuron workers).
    """

    connection_timeout_secs: int = Field(
        default=60, ge=1, description="Seconds to wait for SQLite locks"
    )
    busy_timeout_ms: int = Field(
        default=60000, ge=1000, description="Milliseconds for busy_timeout PRAGMA"
    )
    cache_size_kb: int = Field(default=64000, ge=1000, description="SQLite cache size in KB")
    pool_size: int = Field(
        default=20, ge=0, description="Number of persistent connections in the pool"
    )
    max_overflow: int = Field(
        default=40,
        ge=-1,
        description="Max additional connections beyond pool_size (-1 for unlimited)",
    )
    pool_timeout: int = Field(
        default=60, ge=1, description="Seconds to wait for a connection from the pool"
    )
    commit_max_retries: int = Field(
        default=5, ge=1, description="Max commit retry attempts on SQLITE_BUSY"
    )
    commit_base_delay_secs: float = Field(
        default=1.0, gt=0, description="Base delay for commit retry backoff (doubles each retry)"
    )
    strict_schema_drift: bool = Field(
        default=True,
        description=(
            "When true (default), refuse to boot if Alembic's compare_metadata reports "
            "any diff between the live DB and SQLModel.metadata after "
            "run_startup_migrations(). Self-hosted operators see a clear startup error "
            "('container won't start, drift at column X') rather than silent corruption "
            "on the first request that hits the renamed/missing column. "
            "Set to false to revert to lenient drift handling: startup logs "
            "'schema_drift_detected' and continues. Useful during incremental migrations "
            "or when benign textual drift from legacy migrations is known "
            "(see KNOWN_DRIFT in test_roundtrip.py)."
        ),
    )

    model_config = ConfigDict(extra="forbid")


# ============================================================================
# Migration Settings (startup auto-apply behaviour)
# ============================================================================
def _env_auto_apply_destructive() -> bool:
    """Default for auto_apply_destructive, honouring the env override.

    Mirrors the per-field os.getenv convention used throughout this module
    (e.g. CHAOSCYPHER_LLM_PROVIDER). Truthy unless explicitly disabled.
    """
    return os.getenv("CHAOSCYPHER_AUTO_APPLY_DESTRUCTIVE", "true").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


class MigrationsSettings(BaseModel):
    """Startup migration auto-apply behaviour."""

    auto_apply_destructive: bool = Field(
        default_factory=_env_auto_apply_destructive,
        description=(
            "When true (default), startup auto-applies ALL pending migrations "
            "including destructive (tier=manual) ones, after a verified backup. "
            "When false, safe/data-changing migrations still auto-apply but "
            "destructive ones gate to the maintenance flow. Env override: "
            "CHAOSCYPHER_AUTO_APPLY_DESTRUCTIVE (0/false/no/off disables)."
        ),
    )

    model_config = ConfigDict(extra="forbid")


# ============================================================================
# Pagination Settings
# ============================================================================


class PaginationSettings(BaseModel):
    """Pagination configuration for list operations.

    Used by services for consistent pagination across the application.
    """

    default_page_size: int = Field(default=50, ge=1, le=1000, description="Default items per page")
    default_list_limit: int = Field(default=100, ge=1, description="Default limit for list queries")
    max_page_size: int = Field(default=1000, ge=1, le=10000, description="Maximum items per page")
    # Canvas caps (2026-05-19): pre-launch P0. The previous defaults
    # (100k nodes / 300k edges) materialised hundreds of MB of JSON on
    # a single endpoint that runs inside the FastAPI event loop — every
    # other /api/ request stalled until serialisation completed. The
    # canvas renderer struggles past ~5k nodes anyway; operators with
    # legitimate larger views can raise the cap explicitly via
    # settings.yaml.
    canvas_max_nodes: int = Field(
        default=5_000, ge=1000, description="Max nodes for graph canvas bulk endpoint"
    )
    canvas_max_edges: int = Field(
        default=15_000, ge=1000, description="Max edges for graph canvas bulk endpoint"
    )
    max_citation_page_size: int = Field(
        default=100, ge=1, description="Maximum page size for citations"
    )
    workflow_history_limit: int = Field(
        default=10, ge=1, description="Workflow history retention limit"
    )
    trigger_history_limit: int = Field(
        default=10, ge=1, description="Trigger history retention limit"
    )
    stats_max_executions: int = Field(
        default=100,
        ge=100,
        le=10000,
        description="Maximum executions to fetch for stats calculation",
    )
    export_page_size: int = Field(
        default=500,
        ge=1,
        le=10000,
        description="Page size used when paginating sources during export",
    )
    graph_list_page_size: int = Field(
        default=1000,
        ge=1,
        le=10000,
        description="Page size used when listing graph nodes/edges in service handlers",
    )
    log_tail_lines: int = Field(
        default=2000,
        ge=100,
        le=100000,
        description="Default number of log lines returned by the logs service",
    )
    default_search_results: int = Field(
        default=10,
        ge=1,
        le=1000,
        description="Default result limit for search operations (keyword, semantic, hybrid)",
    )
    extraction_tasks_page_size: int = Field(
        default=20,
        ge=1,
        le=1000,
        description="Default page size for extraction task listings",
    )
    workflow_executions_fetch_limit: int = Field(
        default=10_000,
        ge=1,
        description="Max workflow execution records returned by the bulk fetch endpoint.",
    )

    model_config = ConfigDict(extra="forbid")


# ============================================================================
# Graph Settings (Commit & Template Defaults)
# ============================================================================


class GraphSettings(BaseModel):
    """Graph commit and template configuration.

    Centralizes system template IDs, default relationship types,
    and inverse relationship mappings used during source commits.
    """

    # Default templates
    default_node_template: str = Field(
        default="system_template_item",
        description="Fallback node template when no specific type matches",
    )
    default_edge_template: str = Field(
        default="system_template_link",
        description="Fallback edge template for relationships",
    )

    # Default relationship
    default_relationship_type: str = Field(
        default="related_to",
        description="Default relationship type when none specified",
    )
    sourced_from_label: str = Field(
        default="sourced from",
        description="Edge label linking entities to their source document",
    )

    # Inverse relationship map — loaded from domain configs at extraction time.
    # This field exists for user overrides via settings.yaml; the default is empty
    # because each domain .jsonld provides its own inverse_relationships section.
    inverse_relationship_map: dict[str, str] = Field(
        default={},
        description="User overrides for inverse relationship types (domain configs provide defaults)",
    )

    # Export limits
    export_max_graph_items: int = Field(
        default=100000, ge=1000, description="Maximum nodes/edges to export in a single operation"
    )

    model_config = ConfigDict(extra="forbid")


# ============================================================================
# GraphRAG Settings
# ============================================================================


class GraphRAGSettings(BaseModel):
    """GraphRAG search tuning parameters.

    Controls the behavior of the graphrag_search tool, which fuses
    Personalized PageRank graph traversal with vector/keyword search.
    """

    seed_similarity_threshold: float = Field(
        default=0.3,
        ge=0.0,
        le=1.0,
        description="Minimum cosine similarity for an entity to qualify as a PPR seed",
    )
    ppr_top_k: int = Field(
        default=20,
        ge=1,
        description="Number of top PPR-scored entities to include in graph context",
    )
    ppr_damping: float = Field(
        default=0.85,
        ge=0.0,
        le=1.0,
        description="PPR damping factor (higher = more exploration away from seeds)",
    )
    max_triples: int = Field(
        default=200,
        ge=10,
        description="Maximum triples to include in graph context summary",
    )
    vector_overfetch_multiplier: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Multiplier for vector search overfetch (fetch N*multiplier, filter to N seeds)",
    )
    max_graph_nodes: int = Field(
        default=50000,
        ge=1000,
        description="Maximum graph nodes to load for PPR (skip PPR if graph exceeds this)",
    )

    model_config = ConfigDict(extra="forbid")


# ============================================================================
# Search Settings
# ============================================================================


class SearchSettings(BaseModel):
    """Search repository configuration.

    Used by SearchRepository for vector search operations.
    """

    max_search_results: int = Field(
        default=100, ge=1, description="Maximum search results to return"
    )
    vector_dimensions: int = Field(
        default=1024,
        ge=1,
        description="Embedding vector dimensions (configurable per model)",
    )
    max_elements: int = Field(
        default=100000, ge=1000, description="Maximum number of vectors in vector search index"
    )
    fulltext_language: str = Field(
        default="en", description="Language for full-text search indexing"
    )
    enable_auto_embedding: bool = Field(
        default=True, description="Automatically generate embeddings for new content"
    )
    min_similarity_threshold: float = Field(
        default=0.55,
        ge=0.0,
        le=1.0,
        description="Minimum cosine similarity for vector search results",
    )
    enable_vector_search: bool = Field(default=True, description="Enable vector similarity search")

    # ---- Re-ranking settings (CrossEncoder) ----
    enable_rerank: bool = Field(
        default=True,
        description="Use CrossEncoder to re-rank search_chunks results by relevance",
    )
    rerank_model_name: str = Field(
        default="Alibaba-NLP/gte-reranker-modernbert-base",
        description="HuggingFace cross-encoder model ID for reranking (149M params, ~600MB)",
    )
    rerank_candidate_multiplier: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Fetch limit * this multiplier candidates from search, then rerank down to limit",
    )
    rerank_cache_dir: str | None = Field(
        default=None,
        description="Directory to cache reranking models (None = HuggingFace Hub default via HF_HOME)",
    )
    rerank_min_candidates: int = Field(
        default=15,
        ge=5,
        le=100,
        description="Minimum candidates to fetch for reranking, regardless of requested limit",
    )
    search_chunks_min_limit: int = Field(
        default=5,
        ge=1,
        le=50,
        description="Minimum chunks returned to the LLM from search_chunks",
    )
    result_preview_chars: int = Field(
        default=100,
        ge=10,
        description="Max chars for search result label previews.",
    )

    # Frontend-facing search defaults (PR3-A) — exposed via /api/v1/settings/public
    default_result_limit: int = Field(
        default=10,
        ge=1,
        description="Frontend search default result count.",
    )
    omnibar_entity_limit: int = Field(
        default=10,
        ge=1,
        description="Omnibar entity result cap.",
    )
    omnibar_source_limit: int = Field(
        default=5,
        ge=1,
        description="Omnibar source result cap.",
    )
    debounce_ms: int = Field(
        default=300,
        ge=10,
        description="Search input debounce (ms).",
    )

    model_config = ConfigDict(extra="forbid")


# ============================================================================
# Archive Settings (Documentation Archive Loader)
# ============================================================================


class ArchiveSettings(BaseModel):
    """Archive loader configuration.

    Controls extraction limits, format detection, and handler behavior
    for documentation archive source processing (ZIP/TAR.GZ files).
    """

    # Extraction limits
    max_extracted_size_mb: int = Field(
        default=500,
        ge=10,
        le=5000,
        description="Maximum extracted archive size in MB",
    )
    max_files: int = Field(
        default=10000,
        ge=100,
        le=100000,
        description="Maximum number of files to extract from archive",
    )

    # Ignore patterns (critical for mixed archives like React apps)
    ignore_dirs: list[str] = Field(
        default=[
            "node_modules",
            ".git",
            "__pycache__",
            "venv",
            ".venv",
            "dist",
            "build",
            ".next",
            ".nuxt",
            "coverage",
        ],
        description="Directories to skip during processing",
    )
    ignore_files: list[str] = Field(
        default=[".DS_Store", "Thumbs.db", "*.pyc"],
        description="File patterns to skip during processing",
    )

    # Sphinx HTML settings
    html_content_selectors: list[str] = Field(
        default=["div[itemprop='articleBody']", "div.body", "div.document"],
        description="CSS selectors for HTML content extraction (priority order)",
    )
    html_strip_selectors: list[str] = Field(
        default=["a.headerlink"],
        description="CSS selectors for elements to strip from HTML",
    )

    # Markdown settings
    strip_frontmatter: bool = Field(
        default=True,
        description="Strip YAML/TOML frontmatter from markdown files",
    )

    # OpenAPI settings
    openapi_chunk_by_operation: bool = Field(
        default=True,
        description="Create separate chunks per API operation",
    )
    openapi_include_schemas: bool = Field(
        default=True,
        description="Include component schemas as separate chunk",
    )

    # Archive handler walk depth
    # (Phase 3, CLAUDE.md "zero hardcoded config values")
    max_walk_depth: int = Field(
        default=5,
        ge=1,
        description=(
            "Maximum directory depth when walking an extracted archive looking for "
            "a nested documentation root (e.g. mkdocs.yml / index.html inside "
            "docs/_build/html/). Applies to MarkdownHandler and SphinxHTMLHandler. "
            "Equivalent to the previous hardcoded MAX_WALK_DEPTH = 5."
        ),
    )

    # Markdown handler detection thresholds
    # (Phase 3, CLAUDE.md "zero hardcoded config values")
    markdown_min_files: int = Field(
        default=5,
        ge=1,
        description=(
            "Minimum number of Markdown files required for a candidate directory to "
            "pass the MarkdownHandler detection filter. Equivalent to the previous "
            "hardcoded `md_count < 5` check."
        ),
    )
    markdown_min_confidence: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description=(
            "Minimum detection confidence score (0.0-1.0) required for a candidate "
            "directory to pass the MarkdownHandler detection filter. Equivalent to "
            "the previous hardcoded `confidence < 0.5` check."
        ),
    )

    model_config = ConfigDict(extra="forbid")


# ============================================================================
# Loader Settings
# ============================================================================


class LoaderSettings(BaseModel):
    """Configuration knobs for individual source loaders.

    Covers Whisper transcription (audio/video loaders) and CSV dialect
    detection. Archive-handler walk depth and Markdown detection thresholds
    live in :class:`ArchiveSettings` since they are archive-specific.

    Phase 3 (2026-05-08): lifted hardcoded literals here per the
    "zero hardcoded config values" rule in CLAUDE.md.
    """

    # Whisper transcription settings (audio + video loaders)
    whisper_model_size: str = Field(
        default="base",
        description=(
            "faster-whisper model size used for audio/video transcription. "
            "Common values: tiny, base, small, medium, large. Larger models are "
            "more accurate but slower and require more RAM. "
            "Equivalent to the previous hardcoded WhisperModel('base', ...)."
        ),
    )
    whisper_device: str = Field(
        default="cpu",
        description=(
            "Device to run faster-whisper on. Use 'cpu' (default) for "
            "GPU-free deployments or 'cuda' / 'auto' when a CUDA GPU is available. "
            "Equivalent to the previous hardcoded device='cpu'."
        ),
    )
    whisper_timeout_seconds: int = Field(
        default=600,
        gt=0,
        description=(
            "Hard timeout in seconds for ffmpeg audio conversion / audio extraction. "
            "Prevents a crafted media file from wedging the worker indefinitely. "
            "600 s (10 min) covers any legitimate multi-GB audio/video file. "
            "Equivalent to the previous hardcoded timeout=600."
        ),
    )

    # CSV dialect detection settings
    csv_dialect_sample_bytes: int = Field(
        default=8192,
        gt=0,
        description=(
            "Number of bytes fed to csv.Sniffer for delimiter detection. "
            "8 KiB is the convention used by csvkit, pandas, etc.: large enough "
            "to be statistically meaningful, small enough to avoid reading a full "
            "1-GB CSV twice. Equivalent to the previous hardcoded _SAMPLE_BYTES = 8192."
        ),
    )

    # Encoding detection settings (Phase 4, 2026-05-08)
    encoding_chardet_min_input_size: int = Field(
        default=32,
        ge=1,
        description=(
            "Phase 4 (2026-05-08): minimum input size in bytes for "
            "charset-normalizer + chardet detection. Files smaller than this "
            "skip statistical detection and fall through to Latin-1. Default "
            "lowered from the previous hardcoded 256 to support short legacy-"
            "encoded files (cp1251 Cyrillic / Shift-JIS Japanese)."
        ),
    )
    encoding_chardet_confidence_threshold: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        description=(
            "Phase 4 (2026-05-08): if charset-normalizer's top result has "
            "coherence below this threshold, run chardet as a second-opinion "
            "detector and prefer the higher-confidence result. The coherence "
            "score from charset-normalizer ranges from 0.0 (uncertain) to 1.0 "
            "(highly confident). 0.7 is a conservative threshold that fires "
            "chardet for short inputs where charset-normalizer's statistical "
            "model has too few bytes to be reliable."
        ),
    )

    # File-size guard (2026-05-19): pre-launch P0 to prevent worker OOM
    # when a single multi-GB upload hits a non-streaming parser
    # (pypdf, python-docx, full-text read). nginx caps the upload at the
    # request boundary (10 GB) and FastAPI streams to disk safely, but
    # the loader stage materialises the staged file into RAM at 5-10x
    # disk size for PDF/DOCX. This cap stops the worker from being
    # OOM-killed mid-source — the user gets a typed 400 instead of a
    # stuck `extracting` row. Set None to disable (e.g. trusted-input
    # local CLI runs); production deployments should keep the default.
    max_disk_bytes: int | None = Field(
        default=500 * 1024 * 1024,
        ge=1,
        description=(
            "Maximum on-disk size in bytes that a single file is allowed "
            "to have before it reaches a loader. Files larger than this "
            "are rejected with a LoaderFileTooLargeError BEFORE the "
            "heavyweight parser is invoked, so a malicious or accidental "
            "multi-GB upload cannot OOM the worker. Default 500 MiB. "
            "Set to None to disable the cap (single-user trusted "
            "deployments only)."
        ),
    )

    # PDF loader settings (Phase 5b, 2026-05-08)
    pdf_max_pages: int | None = Field(
        default=None,
        ge=1,
        description=(
            "Phase 5b (2026-05-08): maximum number of pages to extract from a "
            "single PDF. When set and the PDF exceeds this cap, pages beyond "
            "the limit are silently skipped and a loader_warnings entry is "
            "attached describing the truncation. Default None (no cap). "
            "Prevents a 10,000-page PDF from wedging a worker indefinitely."
        ),
    )

    # Vision sampling settings (Wave 4-5, 2026-05-23)
    vision_quick_sample_max_pages: int = Field(
        default=20,
        ge=1,
        description=(
            "Wave 4-5 (2026-05-23): cap on the number of pages the vision-LLM "
            "stage processes when ``extraction_depth='quick'``. The work-queue "
            "builder selects cover + last page + N evenly-spaced body pages "
            "and trims to this cap. Default 20 is tight enough that a "
            "400-page book runs in seconds (5%% of pages), but loose enough "
            "to give a representative sample. The pages not selected are "
            "counted via VISION_PAGES_SAMPLED_QUICK_MODE so the Processing "
            "tab can show 'Quick mode: 20 of 400 pages processed' rather "
            "than mistaking the skip for a partial failure. Has no effect "
            "when ``extraction_depth='full'`` — every image page is "
            "processed."
        ),
    )

    # Cost / resource-exhaustion backstop (2026-05-25 review pass 2):
    # per-source full-mode vision-page ceiling. Full mode enqueues one
    # OP_VISION_PAGE LLM task per image page; without a ceiling a pathological
    # multi-thousand-page PDF explodes into thousands of vision-LLM calls. When
    # the page count exceeds this value the source is hard-failed before any
    # vision job is created (zero vision spend). Generous by default (covers
    # large books) so it only trips on clearly-pathological inputs. Has no
    # effect in quick mode — that is already capped by
    # vision_quick_sample_max_pages.
    vision_max_pages: int = Field(
        default=2_000,
        ge=1,
        description=(
            "Maximum image pages a single source may fan out into in full-mode "
            "vision (one OP_VISION_PAGE task each). Sources over this ceiling "
            "are failed before any vision call. Quick mode is capped separately "
            "by vision_quick_sample_max_pages, so this only affects full mode."
        ),
    )

    # OpenAPI loader settings (Phase 5c, 2026-05-08)
    openapi_max_schema_depth: int = Field(
        default=4,
        ge=1,
        description=(
            "Phase 5c (2026-05-08): maximum recursion depth for the OpenAPI "
            "schema walker (_expand_schema). Schemas nested beyond this depth "
            "are summarised as '<schema (depth N reached)>' to prevent "
            "unbounded expansion on deeply nested or self-referential specs. "
            "Default 4 covers the vast majority of real-world API schemas "
            "without runaway recursion."
        ),
    )

    # Phase 6 (2026-05-08): skip-list configurability for archive handlers.
    markdown_skip_files: list[str] = Field(
        default_factory=lambda: ["CHANGELOG.md", "CONTRIBUTING.md", "CODE_OF_CONDUCT.md"],
        description=(
            "Phase 6 (2026-05-08): filenames skipped by the markdown archive handler "
            "(in addition to .DS_Store and Thumbs.db). Defaults match the previous "
            "hardcoded SKIP_FILES set. Extend to omit additional boilerplate files."
        ),
    )
    markdown_skip_dirs: list[str] = Field(
        default_factory=lambda: [
            "node_modules",
            ".git",
            "__pycache__",
            "venv",
            ".venv",
            "dist",
            "build",
            ".next",
            ".nuxt",
            "coverage",
            ".cache",
            ".idea",
            ".vscode",
        ],
        description=(
            "Directories the Markdown archive handler skips during walk. "
            "Phase 7 audit-remediation (2026-05-09): lifted from the "
            "MarkdownHandler.SKIP_DIRS ClassVar so operators can override "
            "(e.g., to index a docs archive that legitimately stores "
            "content under a default-skipped directory)."
        ),
    )
    sphinx_skip_patterns: list[str] = Field(
        default_factory=lambda: [
            "genindex.html",
            "search.html",
            "searchindex.js",
            "objects.inv",
            "_static/*",
            "_sources/*",
            "_modules/*",
            "_images/*",
            ".buildinfo",
        ],
        description=(
            "File patterns the Sphinx archive handler skips. Phase 7 "
            "audit-remediation (2026-05-09): lifted from the "
            "SphinxHandler.SKIP_PATTERNS ClassVar."
        ),
    )
    archive_skip_dirs: list[str] = Field(
        default_factory=lambda: [
            "node_modules",
            ".git",
            "__pycache__",
            "venv",
            ".venv",
            "dist",
            "build",
            ".next",
            ".nuxt",
            "coverage",
            ".cache",
            ".idea",
            ".vscode",
        ],
        description=(
            "Phase 6 (2026-05-08): directory names skipped by the generic archive "
            "handler. Defaults match the previous hardcoded SKIP_DIRS set."
        ),
    )
    archive_skip_extensions: list[str] = Field(
        default_factory=lambda: [
            ".exe",
            ".dll",
            ".so",
            ".dylib",
            ".pyc",
            ".pyo",
            ".whl",
            ".egg",
            ".egg-info",
            ".class",
            ".jar",
            ".war",
            ".map",
            ".min.js",
            ".min.css",
            ".lock",
        ],
        description=(
            "Phase 6 (2026-05-08): file extensions skipped by the generic archive "
            "handler. Defaults match the previous hardcoded SKIP_EXTENSIONS set."
        ),
    )
    archive_skip_files: list[str] = Field(
        default_factory=lambda: [
            ".DS_Store",
            "Thumbs.db",
            ".gitignore",
            ".gitattributes",
            ".npmrc",
            ".yarnrc",
            "package-lock.json",
            "yarn.lock",
            "pnpm-lock.yaml",
        ],
        description=(
            "Phase 6 (2026-05-08): exact filenames skipped by the generic archive "
            "handler. Defaults match the previous hardcoded SKIP_FILES set."
        ),
    )

    model_config = ConfigDict(extra="forbid")


# ============================================================================
# Chunking Settings (Extracted from main app)
# ============================================================================


class ChunkingSettings(BaseModel):
    """Hierarchical chunking configuration.

    Used by ExtractionService for text chunking.

    Research-based defaults (GraphRAG paper, RAG best practices 2025):
    - 900-char small chunks (~225 tokens) for balanced RAG retrieval and extraction
    - 4 chunks per group (~800-900 tokens after overlap) for entity extraction
    - 150-char overlap (~16%) within optimal 10-20% range
    - Larger chunks improve entity co-occurrence and relationship discovery
      while remaining well within Qwen3 30B's 32K context window
    """

    # Small chunks (for RAG retrieval and extraction grouping)
    # tier=basic: these are the 3 knobs most users ever need to touch
    small_chunk_size: int = Field(
        default=900,
        ge=100,
        description="Small chunk size (characters, ~225 tokens)",
        json_schema_extra={"tier": "basic"},
    )
    small_chunk_overlap: int = Field(
        default=150,
        ge=0,
        description="Small chunk overlap (characters)",
        json_schema_extra={"tier": "basic"},
    )
    min_chunk_size: int = Field(
        default=100,
        ge=0,
        description=(
            "Coalesce threshold (characters). Chunks shorter than this are "
            "merged into a neighbor instead of being emitted as their own "
            "row, so natural-prose imports (dialogue, transitions, short "
            "paragraphs) keep all content reaching extraction. Set to 0 to "
            "disable coalescing entirely."
        ),
        json_schema_extra={"tier": "advanced"},
    )
    max_chunk_size: int = Field(
        default=1100,
        ge=100,
        description="Maximum chunk size (hard limit)",
        json_schema_extra={"tier": "advanced"},
    )
    respect_boundaries: bool = Field(
        default=True,
        description="Break at sentence/paragraph boundaries",
        json_schema_extra={"tier": "advanced"},
    )

    # Hierarchical grouping (for entity extraction)
    # 4 chunks x 900 chars = 3600 chars (~900 tokens) — good balance of context and precision
    group_size: int = Field(
        default=4,
        ge=1,
        description="How many small chunks per group",
        json_schema_extra={"tier": "basic"},
    )
    group_overlap: int = Field(
        default=1,
        ge=0,
        description="Overlap between groups (sliding window)",
        json_schema_extra={"tier": "advanced"},
    )

    # Token-budget group sizing for extraction (replaces fixed group_size for extraction)
    target_group_tokens: int = Field(
        default=900,
        ge=200,
        le=4000,
        description="Target content tokens per extraction group. Groups pack chunks until this budget is reached.",
    )

    # Expected output tokens per chunk during entity extraction (initial pass only)
    # Conservative estimate based on real-world data: avg ~1,500, max ~5,000 per chunk
    # Using 2,000 (~33% above average) for initial pass context estimation
    output_tokens_per_chunk: int = Field(
        default=2000,
        ge=500,
        le=10000,
        description="Expected output tokens per chunk for context utilization estimation",
    )

    # Extraction density (domain may override)
    default_extraction_density: float = Field(
        default=1.0,
        ge=0.5,
        le=2.0,
        description="Default extraction density factor. Domains may override. Higher = more entities per chunk.",
    )

    # Text normalization
    normalize_newlines: bool = Field(
        default=True,
        description="Convert single newlines to spaces (recommended for books/PDFs)",
    )

    # Structural noise removal
    normalize_remove_structural_noise: bool = Field(
        default=True,
        description="Pre-strip TOC blocks, repeated headers, structural markers before extraction",
    )

    # Phase 5a: citation correctness
    preserve_original_text_for_citations: bool = Field(
        default=True,
        description=(
            "When True (default), the raw loader output for each source is written to "
            "``<data_dir>/sources/<source_id>/original.txt`` before normalization so "
            "that chunk char offsets can be recomputed against the original upload "
            "rather than the post-cleaner text. Disable this flag for large-corpus "
            "deployments where per-source disk overhead is a concern — chunks will "
            "still be produced correctly but citation_offset_method will be 'exact' "
            "relative to cleaned text (the pre-5a behaviour), not the upload."
        ),
        json_schema_extra={"tier": "advanced"},
    )

    # Phase 7 audit-remediation: quick-mode group cap (P1 #5, 2026-05-09)
    quick_mode_max_groups: int = Field(
        default=5,
        ge=1,
        description=(
            "Number of hierarchical groups sampled in quick-mode extraction. "
            "Phase 7 audit-remediation (2026-05-09): lifted from hardcoded 5 "
            "in utils/chunk.py:878,950."
        ),
        json_schema_extra={"tier": "advanced"},
    )

    # Cost / resource-exhaustion backstop (2026-05-25 review pass 2):
    # per-source full-mode fan-out ceiling. Full-mode extraction enqueues one
    # OP_EXTRACT_CHUNK LLM task per chunk-group; without a ceiling a single
    # pathological upload (default max_upload_bytes is 5 GB) can explode into
    # millions of tasks. When the group count exceeds this value the source is
    # hard-failed before any chunk task is enqueued (zero LLM spend). The
    # default is generous (~36 MB of text / ~10000 LLM calls) so it only trips
    # on clearly-pathological inputs, not real documents — raise it for a
    # genuinely huge single document, or split the document.
    max_groups_per_source: int = Field(
        default=10_000,
        ge=1,
        description=(
            "Maximum hierarchical groups a single source may fan out into in "
            "full-mode extraction (one OP_EXTRACT_CHUNK task each). Sources "
            "over this ceiling are failed before any LLM call. Quick mode is "
            "already capped by quick_mode_max_groups, so this only affects "
            "full mode."
        ),
        json_schema_extra={"tier": "advanced"},
    )

    model_config = ConfigDict(extra="forbid")


# ============================================================================
# Embedding Settings
# ============================================================================


class EmbeddingSettings(BaseModel):
    """Embedding provider configuration.

    Controls which embedding provider and model are used for generating
    vector embeddings throughout the platform (indexing, search, deduplication).

    Supports local CPU inference, Ollama, and cloud providers (OpenAI, Gemini).
    The API key is independent from the chat LLM API key, allowing different
    providers for chat vs. embeddings.
    """

    provider: str = Field(
        default="local",
        description="Embedding provider: local, ollama, openai, gemini",
    )
    model: str = Field(
        default="Qwen/Qwen3-Embedding-0.6B",
        description="Provider-specific model name",
    )
    api_key: SecretStr | None = Field(
        default=None,
        description="API key for cloud embedding providers (independent from chat)",
    )
    api_base: str | None = Field(
        default=None,
        description="Custom API endpoint override",
    )
    is_configured: bool = Field(
        default=False,
        description="Whether embedding has been explicitly configured via setup wizard",
    )
    ollama_instance_id: str = Field(
        default="default",
        description="Ollama instance ID for embedding requests",
    )
    max_text_length: int = Field(
        default=16000,
        ge=100,
        description="Maximum text length (chars) before truncation",
    )
    default_ollama_model: str = Field(
        default="qwen3-embedding:0.6b",
        description="Default Ollama embedding model when none is explicitly configured.",
    )
    allow_model_download: bool = Field(
        default=False,
        description=(
            "Permit the Neuron warmup task to download the local embedding "
            "model from HuggingFace on first boot when the cache is empty. "
            "Default False so a fresh install makes zero outbound HF calls "
            "until the operator opts in."
        ),
    )

    model_config = ConfigDict(extra="forbid")

    @field_serializer("api_key", when_used="always")
    def _serialize_api_key(self, v: SecretStr | None) -> str | None:
        """Unwrap SecretStr to its raw value (or None) for persistence.

        Same rationale as LLMSettings._serialize_api_keys.
        """
        return v.get_secret_value() if v is not None else None


class ExportSettings(BaseModel):
    """CCX export package metadata defaults."""

    export_package_name: str | None = Field(
        default=None, description="Package name for CCX exports (e.g. 'owner/my-package')"
    )
    export_version: str = Field(default="1.0.0", description="Default package version")
    export_license: str = Field(default="CC-BY-SA-4.0", description="Default package license")
    export_author: str | None = Field(default=None, description="Default package author")
    export_description: str | None = Field(default=None, description="Default package description")
    export_tags: list[str] = Field(default_factory=list, description="Default package tags")
    export_derived_from: dict[str, Any] = Field(
        default_factory=dict, description="Provenance metadata"
    )
    export_dependencies: dict[str, Any] = Field(
        default_factory=dict, description="Package dependencies"
    )

    model_config = ConfigDict(extra="forbid")


# ============================================================================
# CLI Settings (CLI-specific defaults)
# ============================================================================


class CLISettings(BaseModel):
    """CLI-specific configuration defaults.

    Controls CLI server ports and Ollama interaction timeouts used
    by ``chaoscypher serve`` / ``chaoscypher run`` and the CLI context.
    """

    api_port: int = Field(
        default=8081,
        ge=1,
        le=65535,
        description="Default port for the CLI local API server (serve/run commands)",
    )
    ollama_connect_timeout: int = Field(
        default=2,
        ge=1,
        description="Timeout in seconds for checking Ollama connectivity",
    )
    ollama_pull_timeout: int = Field(
        default=600,
        ge=30,
        description="Timeout in seconds for pulling Ollama models",
    )

    # CLI tunables (timeouts, page sizes, concurrency, file I/O) — previously
    # the app-only ``CliSettings`` class, now unioned into core ``CLISettings``
    # (Tier 2 schema unification). ``CliSettings`` remains a back-compat alias
    # in app_config.
    ollama_connect_timeout_seconds: int = Field(
        default=3,
        ge=1,
        description="Timeout for Ollama connectivity probes (cli health/setup commands).",
    )
    setup_ollama_test_timeout_seconds: int = Field(
        default=5,
        ge=1,
        description="Timeout for Ollama connection test in setup wizard.",
    )
    api_test_timeout_seconds: int = Field(
        default=15,
        ge=1,
        description="Timeout for external API key validation (OpenAI, Anthropic, Gemini) in setup.",
    )
    health_check_workers: int = Field(
        default=2,
        ge=1,
        le=16,
        description="Thread pool size for parallel CLI health checks.",
    )
    list_page_size: int = Field(
        default=50,
        ge=1,
        description="Default page size for `node list` / `link list` / etc.",
    )
    search_default_limit: int = Field(
        default=20,
        ge=1,
        description="Default result count for CLI search commands.",
    )
    edge_batch_size: int = Field(
        default=100,
        ge=1,
        description="Page size for fetching edges during node get/delete operations.",
    )
    download_chunk_size_bytes: int = Field(
        default=8_192,
        ge=512,
        description="Buffer size for streaming downloads (cli/utils/files.py).",
    )
    serve_default_page_size: int = Field(
        default=50,
        ge=1,
        description="Default page size for `chaoscypher serve` /nodes and /edges endpoints.",
    )

    model_config = ConfigDict(extra="forbid")


# ============================================================================
# Compose Settings
# ============================================================================


class ComposeSettings(BaseModel):
    """Configuration for the package composition runtime."""

    process_terminate_timeout: int = Field(
        default=5,
        ge=1,
        description="Seconds to wait for a spawned compose server to exit gracefully "
        "before force-killing it",
    )

    model_config = ConfigDict(extra="forbid")


# ============================================================================
# Analysis Settings
# ============================================================================


class AnalysisSettings(BaseModel):
    """Analysis sampling configuration.

    Mirrors ``app_config.AnalysisSettings`` so the standalone Engine /
    MCP path can read ``analysis.quick_sample_size`` from
    ``EngineSettings`` directly. ``settings.yaml``'s ``analysis.*``
    block is the single source of truth for both Cortex and MCP.
    """

    quick_sample_size: int = Field(
        default=5,
        ge=1,
        le=100,
        description="Number of groups to sample when analysis_depth='quick'. "
        "Evenly-distributed across the document.",
    )
    extraction_max_input_chars: int = Field(
        default=8000, description="Maximum input characters for extraction"
    )

    model_config = ConfigDict(extra="forbid")


# ============================================================================
# Web Settings (HTTP / web-fetch behavior)
# ============================================================================


class WebSettings(BaseModel):
    """HTTP / web-fetch behavior (used by adapters/web/search.py and HTTP plugins)."""

    fetch_timeout_seconds: float = Field(
        default=30.0,
        gt=0,
        description="HTTP timeout for web scraping (adapters/web/search.py).",
    )
    workflow_http_default_timeout_seconds: float = Field(
        default=60.0,
        gt=0,
        description="Default HTTP timeout for workflow http_request plugin invocations.",
    )
    max_redirects: int = Field(
        default=10,
        ge=0,
        le=100,
        description=(
            "Max redirect hops the web fetcher will follow before bailing. "
            "Security-sensitive: higher values increase SSRF surface."
        ),
    )
    content_type_detection_window_bytes: int = Field(
        default=2048,
        ge=128,
        le=1_048_576,
        description=(
            "Bytes/chars at the head of a fetched/uploaded payload used for "
            "content-type sniffing (consumed by source normalizer + web_cleaner)."
        ),
    )

    model_config = ConfigDict(extra="forbid")


# ============================================================================
# Backoff Settings (Exponential backoff)
# ============================================================================


class BackoffSettings(BaseModel):
    """Exponential backoff configuration."""

    retry_delays: list[float] = Field(
        default=[2.0, 4.0, 8.0, 16.0],
        description="Backoff delays in seconds for each retry attempt",
    )
    max_seconds: int = Field(default=30, description="Maximum backoff delay in seconds")
    llm_backoff_multiplier: int = Field(
        default=5, description="Multiplier for LLM backoff calculation"
    )
    sqlite_base_delay: float = Field(default=1.0, description="Base delay for SQLite retry backoff")
    queue_poller_error_delay: float = Field(
        default=1.0,
        ge=0.0,
        description="Pause (seconds) after a queue poller error before re-entering the poll loop",
    )
    exponential_multiplier: float = Field(
        default=2.0,
        gt=1.0,
        le=10.0,
        description=(
            "Base of exponential backoff growth. 2.0 = doubling each attempt; "
            "1.5 = 50% growth (gentler). Used by utils/retry.py and "
            "adapters/sqlite/safe_session.py."
        ),
    )

    model_config = ConfigDict(extra="forbid")


# ============================================================================
# Retry Settings
# ============================================================================


class RetrySettings(BaseModel):
    """Retry configuration."""

    llm_max_retries: int = Field(default=3, description="Max retries for LLM operations")
    llm_worker_max_tries: int = Field(
        default=5, description="Max tries for LLM worker jobs (includes retries)"
    )
    operations_worker_max_tries: int = Field(
        default=5, description="Max tries for operations worker jobs"
    )
    ai_executor_retries: int = Field(default=2, description="AI executor retry attempts")
    http_max_retries: int = Field(default=4, description="Max retries for HTTP requests")
    sqlite_max_attempts: int = Field(default=5, description="Max attempts for SQLite operations")
    extraction_chunk_max: int = Field(
        default=2, description="Max retries for chunk extraction (3 total attempts)"
    )
    extraction_finalize_max: int = Field(
        default=2, description="Max retries for extraction finalization (3 total attempts)"
    )
    structured_extraction_max: int = Field(
        default=5, description="Max retries for structured LLM extraction"
    )
    embedding_max_retries: int = Field(
        default=3,
        ge=0,
        description="Max retries for embedding provider calls (adapters/embedding/_retry.py).",
    )
    embedding_initial_backoff_seconds: float = Field(
        default=0.5,
        gt=0,
        description="Initial backoff before first embedding-call retry.",
    )
    workflow_rename_max_attempts: int = Field(
        default=10,
        ge=1,
        description="Max retries for workflow rename collisions (services/workflows/management/io.py).",
    )
    ollama_drain_max_iterations: int = Field(
        default=10,
        ge=1,
        description="Max poll iterations when draining an Ollama instance during shutdown.",
    )

    model_config = ConfigDict(extra="forbid")


# ============================================================================
# Quality Settings (Quality dashboard / analysis tuning)
# ============================================================================


class QualitySettings(BaseModel):
    """Quality dashboard / analysis tuning."""

    top_sources_count: int = Field(
        default=5,
        ge=1,
        description="Number of top-scored sources shown on the quality dashboard.",
    )
    top_cited_entities_limit: int = Field(
        default=10,
        ge=1,
        description="Max entities returned by the top-cited-entities query.",
    )
    llm_outlier_std_dev_threshold: float = Field(
        default=2.0,
        gt=0,
        description=(
            "Standard deviations above the mean LLM call duration that flag "
            "an outlier in analytics/llm_metrics.py."
        ),
    )

    model_config = ConfigDict(extra="forbid")


# ============================================================================
# Main Engine Settings
# ============================================================================


class EngineSettings(BaseModel):
    """Complete settings for chaoscypher-engine.

    Main application constructs this from its own Settings instance
    and passes to engine services.

    Example:
        # In main app
        from chaoscypher_engine import EngineSettings

        engine_settings = EngineSettings(
            current_database=settings.current_database,
            llm=LLMSettings(
                chat_provider=settings.llm.chat_provider,
                ollama_chat_model=settings.llm.ollama_chat_model,
                # ... all other LLM settings
            ),
            batching=BatchingSettings(
                # ... batching settings
            ),
            source_processing=SourceProcessingSettings(
                entity_deduplication_mode=settings.source_processing.entity_deduplication_mode,
                # ... other source_processing settings
            ),
            chunking=ChunkingSettings(
                small_chunk_size=settings.chunking.small_chunk_size,
                # ... other chunking settings
            )
        )

    """

    # Database context
    current_database: str = Field(default="default", description="Current database name")

    # Nested settings groups
    paths: PathSettings = Field(
        default_factory=PathSettings, description="Path configuration (XDG-compliant)"
    )
    llm: LLMSettings = Field(default_factory=LLMSettings, description="LLM provider configuration")
    batching: BatchingSettings = Field(
        default_factory=BatchingSettings, description="Batch processing configuration"
    )
    source_processing: SourceProcessingSettings = Field(
        default_factory=SourceProcessingSettings, description="Source processing configuration"
    )
    extraction: ExtractionSettings = Field(
        default_factory=ExtractionSettings,
        description="LLM extraction retry and quality configuration",
    )
    chunking: ChunkingSettings = Field(
        default_factory=ChunkingSettings, description="Chunking configuration"
    )
    analysis: AnalysisSettings = Field(
        default_factory=AnalysisSettings,
        description="Analysis sampling configuration (e.g. quick-mode sample size)",
    )
    normalizer: NormalizerSettings = Field(
        default_factory=NormalizerSettings, description="Content normalization configuration"
    )
    pagination: PaginationSettings = Field(
        default_factory=PaginationSettings, description="Pagination configuration"
    )
    database: DatabaseSettings = Field(
        default_factory=DatabaseSettings, description="SQLite connection and retry configuration"
    )
    migrations: MigrationsSettings = Field(
        default_factory=MigrationsSettings,
        description="Startup migration auto-apply behaviour",
    )
    search: SearchSettings = Field(
        default_factory=SearchSettings, description="Search repository configuration"
    )
    embedding: EmbeddingSettings = Field(
        default_factory=EmbeddingSettings, description="Embedding provider configuration"
    )
    graphrag: GraphRAGSettings = Field(
        default_factory=GraphRAGSettings, description="GraphRAG search configuration"
    )
    graph: GraphSettings = Field(
        default_factory=GraphSettings, description="Graph commit and template configuration"
    )
    archive: ArchiveSettings = Field(
        default_factory=ArchiveSettings, description="Archive loader configuration"
    )
    loader: LoaderSettings = Field(
        default_factory=LoaderSettings,
        description="Loader-level knobs (Whisper model/device/timeout, CSV sniffer)",
    )
    chat: ChatSettings = Field(
        default_factory=ChatSettings, description="Chat tool-calling configuration"
    )
    mcp: MCPSettings = Field(default_factory=MCPSettings, description="MCP server settings")
    export: ExportSettings = Field(
        default_factory=ExportSettings, description="CCX export package metadata defaults"
    )
    compose: ComposeSettings = Field(
        default_factory=ComposeSettings, description="Compose runtime configuration"
    )
    cli: CLISettings = Field(
        default_factory=CLISettings, description="CLI-specific defaults (ports, timeouts)"
    )
    web: WebSettings = Field(default_factory=WebSettings, description="HTTP / web-fetch behavior")
    backoff: BackoffSettings = Field(
        default_factory=BackoffSettings, description="Exponential backoff configuration"
    )
    retries: RetrySettings = Field(default_factory=RetrySettings, description="Retry configuration")
    quality: QualitySettings = Field(
        default_factory=QualitySettings, description="Quality dashboard / analysis tuning"
    )

    model_config = ConfigDict(extra="forbid")


# ============================================================================
# Export
# ============================================================================

__all__ = [
    "AnalysisSettings",
    "ArchiveSettings",
    "BackoffSettings",
    "BatchingSettings",
    "CLISettings",
    "ChatSettings",
    "ChunkingSettings",
    "ComposeSettings",
    "DatabaseSettings",
    "EmbeddingSettings",
    "EngineSettings",
    "ExportSettings",
    "ExtractionSettings",
    "GraphRAGSettings",
    "GraphSettings",
    "LLMSettings",
    "LexiconSettings",
    "LoaderSettings",
    "MCPSettings",
    "MigrationsSettings",
    "NormalizerSettings",
    "OllamaInstance",
    "PaginationSettings",
    "PathSettings",
    "QualitySettings",
    "RetrySettings",
    "SearchSettings",
    "SourceProcessingSettings",
    "WebSettings",
]
