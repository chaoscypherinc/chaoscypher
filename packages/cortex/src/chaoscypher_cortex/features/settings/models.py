# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Settings Models.

Pydantic DTOs for settings operations.

The ``MaskedSettingsResponse`` DTO is the one deliberately-loose response
model in this module. ``Settings`` is dynaconf-backed with many nested
groups and permits arbitrary user-supplied keys via ``settings.yaml``
overrides; ``GET /settings`` / ``POST /settings/reset`` return
``Settings.model_dump()`` after ``mask_settings_dict()`` masks secret
fields in place. Pinning a strict schema would either lie about the
payload or force the handler to prune arbitrary user keys, so the model
uses ``extra='allow'`` and documents each known top-level group while
still giving FastAPI/OpenAPI a named reference.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator


# ============================================================================
# Masked Settings Payload
# ============================================================================


class MaskedSettingsResponse(BaseModel):
    """Full settings dump with secret fields masked.

    Returned by ``GET /api/v1/settings`` and ``POST /api/v1/settings/reset``.
    Uses ``extra='allow'`` because the ``Settings`` model is dynaconf-backed
    and users may add arbitrary keys via ``settings.yaml`` overrides — a
    strict schema would either drop those keys silently or misrepresent the
    payload. The fields documented below are the known top-level groups;
    anything else passes through unchanged.
    """

    model_config = ConfigDict(extra="allow")

    current_database: str | None = Field(
        default=None, description="Name of the currently active database"
    )
    dark_mode: bool | None = Field(default=None, description="UI dark mode preference")


class LoggingLevelResponse(BaseModel):
    """Current logging level response."""

    level: str
    numeric_level: int
    available_levels: list[str]


class LoggingLevelRequest(BaseModel):
    """Set logging level request."""

    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class SetLoggingLevelResponse(BaseModel):
    """Response from setting logging level."""

    success: bool
    old_level: str
    new_level: str
    message: str


class ResetResponse(BaseModel):
    """Generic reset operation response."""

    success: bool = True
    data: dict[str, Any]


# ============================================================================
# VRAM Preset Models
# ============================================================================


class VRAMPresetResponse(BaseModel):
    """VRAM preset data for API responses."""

    name: str
    display_name: str
    description: str
    vram_gb: int
    gpu_examples: list[str]
    version: str
    author: str
    builtin: bool
    ollama_settings: dict[str, Any]
    llm_settings: dict[str, Any]


class PresetListResponse(BaseModel):
    """Response containing all available VRAM presets."""

    presets: list[VRAMPresetResponse]
    count: int


class ApplyPresetRequest(BaseModel):
    """Request to apply a VRAM preset."""

    preset_id: str


class ApplyPresetResponse(BaseModel):
    """Response after applying a preset."""

    success: bool
    preset_id: str
    preset_name: str
    settings_updated: dict[str, Any]
    message: str
    # 2026-05-21: surface configured-but-not-pulled models inline so the
    # UI can toast immediately on apply, without waiting for the next
    # useLLMHealth refetch (~30s).
    missing_models: list[str] = Field(default_factory=list)


# ============================================================================
# Ollama Verification Models
# ============================================================================


class OllamaVerifyRequest(BaseModel):
    """Request to verify an Ollama URL."""

    url: str
    timeout: int | None = None  # Uses settings.timeouts.ollama_verify_timeout if not provided


class OllamaVerifyResponse(BaseModel):
    """Response from Ollama URL verification."""

    success: bool
    message: str
    version: str | None = None
    models: list[str] | None = None
    model_count: int | None = None
    response_time_ms: int | None = None
    error_type: str | None = None


class LLMVerifyRequest(BaseModel):
    """Request to verify a cloud LLM provider's API key connectivity.

    For Ollama use ``OllamaVerifyRequest`` / ``POST /settings/ollama/verify``
    instead — Ollama needs a URL, not a key.
    """

    provider: str  # "openai" | "anthropic" | "gemini"
    api_key: SecretStr


class LLMVerifyResponse(BaseModel):
    """Response from cloud LLM provider verification."""

    success: bool
    message: str
    provider: str


class LLMHealthResponse(BaseModel):
    """Health snapshot for the currently-selected LLM chat provider.

    Drives the frontend's action-gating UX: when ``verified`` is False,
    the app shell shows a "Configure your LLM" banner and disables the
    Import button + chat input. The same state is enforced server-side
    by the import and chat-send endpoints (409 ``llm_not_verified``).
    """

    provider: str  # "ollama" | "openai" | "anthropic" | "gemini"
    configured: bool  # Minimum config fields are populated for the selected provider
    verified: bool  # At least one successful verify has been recorded this process
    last_verified_at: str | None = None  # ISO-8601 UTC timestamp, None if never verified
    missing_models: list[str] = Field(
        default_factory=list,
        description=(
            "Configured Ollama models (chat / extraction / vision) not present on "
            "any reachable Ollama instance. Empty for cloud providers. The Add "
            "Source button and chat input gate on this being empty in addition "
            "to ``verified``."
        ),
    )


# ============================================================================
# Cloud Model Registry Models
# ============================================================================


class CloudModelPricing(BaseModel):
    """Pricing information for a cloud model."""

    input_per_million: float
    output_per_million: float


class CloudModelInfo(BaseModel):
    """Information about a single cloud LLM model."""

    id: str
    display_name: str
    context_window: int
    max_output_tokens: int
    supports_vision: bool = False
    supports_tools: bool = False
    recommended: bool = False
    pricing: CloudModelPricing | None = None
    notes: str | None = None


class CloudProviderInfo(BaseModel):
    """Information about a cloud LLM provider."""

    display_name: str
    models: list[CloudModelInfo]


class CloudModelsResponse(BaseModel):
    """Response containing all cloud models grouped by provider."""

    providers: dict[str, CloudProviderInfo]


# ============================================================================
# Settings Update Response Models
# ============================================================================


class SettingsWarning(BaseModel):
    """A warning generated during a settings update."""

    field: str
    message: str
    severity: Literal["warning", "info"] = "warning"


# Security-/startup-sensitive keys that must NOT be mutated through the generic
# settings PATCH. ``dev_mode`` disables the edge-auth-token check (trusting any
# X-Auth-User header) and bricks the container on the next restart when
# CHAOSCYPHER_ALLOW_DEV_MODE is unset; the ``local_auth`` secrets/paths are owned
# by the auth setup flow and the on-disk secret files. The frontend round-trips
# the full settings object on save, so these are silently stripped, not 422'd.
_PROTECTED_TOP_LEVEL_KEYS = frozenset({"dev_mode"})
_PROTECTED_LOCAL_AUTH_FIELDS = frozenset(
    {"edge_auth_token", "edge_auth_token_path", "session_secret_path", "credentials_path"}
)


class SettingsUpdateRequest(BaseModel):
    """Typed request body for PATCH /settings.

    The allowlist of accepted top-level keys is derived at validation
    time from ``Settings.model_fields``, so it cannot drift from the real
    settings schema. Unknown keys (typos, stale references from old
    refactors) are rejected with an actionable message instead of being
    silently no-op'd by ``ConfigManager.update_settings``.

    Per-field validation inside each nested group is deferred to the
    settings manager, which runs a full ``Settings`` model validation on
    the merged result.
    """

    model_config = ConfigDict(extra="allow")

    @model_validator(mode="before")
    @classmethod
    def _reject_unknown_keys(cls, data: Any) -> Any:
        """Reject unknown top-level keys and strip security-sensitive ones."""
        if not isinstance(data, dict):
            return data
        # Imported here to avoid a module-load cycle via app_config.
        from chaoscypher_core.app_config import Settings

        allowed = set(Settings.model_fields)
        unknown = sorted(set(data) - allowed)
        if unknown:
            msg = f"Unknown settings keys: {unknown}. Allowed top-level keys: {sorted(allowed)}"
            raise ValueError(msg)

        # Drop protected keys so PATCH can never change them.
        sanitized = {k: v for k, v in data.items() if k not in _PROTECTED_TOP_LEVEL_KEYS}
        local_auth = sanitized.get("local_auth")
        if isinstance(local_auth, dict):
            sanitized["local_auth"] = {
                k: v for k, v in local_auth.items() if k not in _PROTECTED_LOCAL_AUTH_FIELDS
            }
        return sanitized


class SettingsUpdateResponse(BaseModel):
    """Response from updating settings, with optional warnings."""

    settings: dict[str, Any]
    warnings: list[SettingsWarning] = []


# ============================================================================
# Ollama Model Management Models
# ============================================================================


class OllamaModelDetails(BaseModel):
    """Details about an Ollama model's architecture."""

    parameter_size: str | None = None
    quantization_level: str | None = None
    family: str | None = None
    format: str | None = None


class OllamaModelInfo(BaseModel):
    """Information about a single Ollama model."""

    name: str
    size: int = 0
    modified_at: str | None = None
    digest: str | None = None
    details: OllamaModelDetails | None = None


class OllamaInstanceModels(BaseModel):
    """Models available on a single Ollama instance."""

    instance_id: str
    instance_name: str
    base_url: str
    healthy: bool
    models: list[OllamaModelInfo]


class OllamaModelsListResponse(BaseModel):
    """Response containing models across all Ollama instances."""

    instances: list[OllamaInstanceModels]


class OllamaModelPullRequest(BaseModel):
    """Request to pull a model to an Ollama instance."""

    model: str
    instance_id: str | None = None


class OllamaModelRemoveRequest(BaseModel):
    """Request to remove a model from an Ollama instance."""

    model: str
    instance_id: str | None = None


class OllamaModelShowResponse(BaseModel):
    """Detailed model information from Ollama /api/show."""

    modelfile: str | None = None
    parameters: str | None = None
    template: str | None = None
    details: OllamaModelDetails | None = None
    model_info: dict[str, Any] | None = None


# ============================================================================
# Local Embedding Model Management Models
# ============================================================================


class LocalEmbeddingModelInfo(BaseModel):
    """Information about a locally downloaded HuggingFace embedding model."""

    id: str
    name: str
    path: str


class LocalEmbeddingModelsResponse(BaseModel):
    """Response containing locally downloaded embedding models."""

    models: list[LocalEmbeddingModelInfo]


class LocalEmbeddingDownloadRequest(BaseModel):
    """Request to download a HuggingFace embedding model."""

    model: str


class LocalEmbeddingDownloadResponse(BaseModel):
    """Response after downloading a local embedding model."""

    model_name: str
    native_dimensions: int
    download_time_ms: int


# ============================================================================
# Curated Embedding Models Registry
# ============================================================================


class CuratedEmbeddingModelInfo(BaseModel):
    """A vetted local/Ollama embedding model with known characteristics."""

    name: str = Field(description="Human-readable model name")
    local: str = Field(description="HuggingFace local model identifier")
    ollama: str = Field(description="Ollama-specific model tag")
    dimensions: int = Field(description="Native embedding dimensions")
    mrl: bool = Field(description="Whether the model supports Matryoshka Representation Learning")
    default: bool = Field(default=False, description="Whether this is the default curated model")


class CloudEmbeddingModelInfo(BaseModel):
    """A cloud-provider embedding model definition."""

    name: str = Field(description="Human-readable model name")
    model: str = Field(description="Provider-specific model identifier")
    dimensions: int = Field(description="Native embedding dimensions")
    mrl: bool = Field(description="Whether the model supports Matryoshka Representation Learning")
    current: bool = Field(default=True, description="Whether this model is currently available")


class EmbeddingModelsResponse(BaseModel):
    """Curated and cloud embedding models used to populate selection UIs."""

    curated: list[CuratedEmbeddingModelInfo] = Field(
        description="Vetted local/Ollama embedding models with known dimensions",
    )
    cloud: dict[str, list[CloudEmbeddingModelInfo]] = Field(
        description="Cloud provider embedding models keyed by provider id (openai, gemini, ...)",
    )


# ============================================================================
# TLS Management Models
# ============================================================================


class TLSStatusResponse(BaseModel):
    """Current TLS enablement status."""

    enabled: bool = Field(
        description="True when both certificate and private key files are present on disk",
    )


class TLSEnableResponse(BaseModel):
    """Response returned after enabling TLS (self-signed or custom)."""

    status: Literal["enabled"] = Field(
        default="enabled",
        description="Always 'enabled' on success; error cases are signalled via HTTP status codes",
    )
    mode: Literal["self-signed", "custom"] = Field(
        description="Which TLS mode was activated: 'self-signed' (generated cert) or 'custom' (uploaded cert)",
    )


# ============================================================================
# Ollama Model Removal Models
# ============================================================================


class OllamaModelRemoveInstanceResult(BaseModel):
    """Per-instance result from an Ollama model removal request."""

    instance_id: str = Field(description="Ollama instance identifier the removal was attempted on")
    success: bool = Field(
        description="Whether the model was successfully removed from this instance"
    )
    error: str | None = Field(
        default=None,
        description="Human-readable error message when removal failed; omitted on success",
    )


class OllamaModelRemoveResponse(BaseModel):
    """Aggregated response from removing an Ollama model across one or more instances."""

    success: bool = Field(
        description="True when removal succeeded on every targeted instance, False otherwise",
    )
    results: list[OllamaModelRemoveInstanceResult] = Field(
        description="Per-instance removal results; one entry per targeted Ollama instance",
    )
