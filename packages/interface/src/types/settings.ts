// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

// Settings domain type definitions for Chaos Cypher frontend

// Ollama instance for multi-instance load balancing
export interface OllamaInstance {
  id: string;
  name: string;
  base_url: string;
  enabled: boolean;
  healthy: boolean;
  last_health_check?: string | null;
  last_error?: string | null;
}

export interface Settings {
  app_name: string;
  current_database: string;
  data_dir: string;
  dark_mode: boolean;
  auto_enable: boolean;
  /** First-run wizard completion flag. False until every wizard step is done. */
  setup_completed: boolean;
  custom_settings: Record<string, unknown>;

  // LLM Settings (Nested)
  llm: {
    chat_provider: string;

    // Ollama instances. Always non-empty: the backend seeds a default
    // instance pointed at the Docker host. Multi-GPU users can add more
    // entries to enable load balancing.
    ollama_instances: OllamaInstance[];
    ollama_load_balancing?: 'round_robin' | 'least_loaded' | 'random';

    // Ollama - Shared model settings
    ollama_chat_model: string;
    ollama_extraction_model?: string | null;
    ollama_vision_model?: string | null;
    ollama_num_batch?: number | null;
    ollama_num_ctx?: number;
    ollama_num_parallel?: number | null;
    ollama_num_thread?: number | null;

    // Ollama - Configuration mode
    ollama_config_mode?: 'quick' | 'advanced';
    ollama_quick_preset?: string | null;

    // OpenAI
    openai_api_key?: string | null;
    openai_base_url: string;
    openai_chat_model: string;
    openai_extraction_model?: string | null;
    openai_vision_model?: string | null;
    openai_context_window?: number | null;
    openai_max_output_tokens?: number | null;

    // Anthropic
    anthropic_api_key?: string | null;
    anthropic_chat_model: string;
    anthropic_extraction_model?: string | null;
    anthropic_vision_model?: string | null;
    anthropic_context_window?: number | null;
    anthropic_max_output_tokens?: number | null;

    // Gemini
    gemini_api_key?: string | null;
    gemini_chat_model: string;
    gemini_extraction_model?: string | null;
    gemini_vision_model?: string | null;
    gemini_context_window?: number | null;
    gemini_max_output_tokens?: number | null;

    // General LLM Settings
    ai_max_tokens: number;
    ai_context_window?: number;
    ai_temperature: number;
    extraction_max_tokens?: number;

    // Thinking Mode
    enable_thinking: boolean;
    thinking_for_chat: boolean;
    thinking_for_tools: boolean;

    // Performance

    // Queue Configuration
    enable_llm_queueing: boolean;
    llm_max_retries: number;
    llm_max_concurrent: number;
    llm_reserved_interactive: number;
    llm_enable_priority: boolean;

    // Cost Tracking
    enable_token_cost_tracking: boolean;
    token_cost_input_per_million: number;
    token_cost_output_per_million: number;
  };

  // Queue Settings (Nested)
  queue: {
    queue_host: string;
    queue_port: number;
    queue_database: number;
    queue_password?: string | null;
    queue_ssl: boolean;
  };

  // Embedding Settings (Nested)
  embedding: {
    provider: string;
    model: string;
    api_key?: string | null;
    api_base?: string | null;
    ollama_instance_id: string;
    max_text_length: number;
  };

  // Search Settings (Nested)
  search: {
    max_search_results: number;
    enable_vector_search: boolean;
    vector_dimensions: number;
    fulltext_language: string;
    enable_auto_embedding: boolean;
  };

  // Source Processing Settings (Nested)
  source_processing: {
    max_file_size_gb: number;
    auto_analyze: boolean;
    analysis_depth: string;
    chunk_overlap: number;
    chunking_strategy: string;
    relationship_confidence_threshold: number;
  };

  // Chunking Settings (Nested) - Always provided by backend with defaults
  chunking: {
    small_chunk_size: number;
    small_chunk_overlap: number;
    min_chunk_size: number;
    max_chunk_size: number;
    respect_boundaries: boolean;
    group_size: number;  // Default: 4 (research shows ~600 tokens optimal)
    group_overlap: number;
    output_tokens_per_chunk: number;  // Default: 2000 (conservative estimate for initial pass)
    default_extraction_density?: number; // Default: 1.0 (domains may override)
  };

  // NLP Settings (Nested)
  nlp: {
    nlp_enable_spacy_ner: boolean;
    nlp_enable_dependency_parsing: boolean;
    nlp_enable_semantic_embeddings: boolean;
    nlp_semantic_model: string;
    nlp_similarity_threshold: number;
  };

  // Export Settings (Nested)
  export: {
    export_package_name?: string | null;
    export_version: string;
    export_license: string;
    export_author?: string | null;
    export_description?: string | null;
    export_tags: string[];
    export_derived_from: Record<string, unknown>;
    export_dependencies: Record<string, unknown>;
  };

  // Chat Settings (tool-calling behavior)
  // Optional because older servers / the current cortex settings response may
  // not expose the `chat` group yet — the field lives in core.ChatSettings.
  // Frontend defaults to "never-ask" when the field is missing.
  chat?: {
    max_tool_iterations?: number;
    max_total_tool_calls?: number;
    max_tools?: number;
    tools_token_estimate?: number;
    tool_approval?: 'always-ask' | 'ask-on-write' | 'never-ask';
    mutating_tools?: string[];
  };

  // Workflow Settings
  workflow_history_limit: number;
  trigger_history_limit: number;

  // Backup Settings
  backup: {
    enabled: boolean;
    interval: string;
    retention_count: number;
    backup_dir: string;
  };

  // Security Settings — Host header allow-list + external-access toggle
  security?: {
    allowed_hosts: string[];
    allow_external_access: boolean;
  };

  // LLM response and embedding caches (core CacheSettings). Read-only here:
  // the Cache accordion changes them through PATCH /cache.
  cache?: {
    dir: string | null;
    completions: CacheStoreSettings;
    embeddings: CacheStoreSettings;
  };

  // GraphRAG retrieval (core GraphRAGSettings). The GraphRAG accordion changes
  // the user-facing switches through PATCH /settings; the rest are levers the
  // benchmark sweeps.
  graphrag?: {
    /** Let relationships no cited sentence states into the chat context. Default false. */
    include_unevidenced_edges?: boolean;
    /** How graphrag_search decides a question's shape. */
    router?: 'off' | 'rules' | 'llm' | 'rules_then_llm';
    /** Lever values applied on top of the model's profile, per question shape (aggregate, path, global). */
    shape_levers?: Record<string, Record<string, unknown>>;
    /** Chunks the graph block cites that enter the chunk budget by right. */
    evidence_chunks?: number;
    /** Longest relation chain a walk between two named entities may have (1 to 4). */
    max_hops?: number;
    /** Walks a path question lists in all, across its pairs of named entities (default 6). */
    max_walks?: number;
    /** Rows an aggregate block lists per relation label before it counts the rest. */
    aggregate_per_label?: number;
    /** Put the description sentences that share a word with the question first. */
    question_aware_descriptions?: boolean;
    /** Search the chunk index for each part of a two-part question as well as the whole. */
    question_parts?: boolean;
    /** Characters kept of an entity description. */
    entity_description_chars?: number;
    /** Descriptions drop repeated sentences and end at a sentence boundary. */
    clean_descriptions?: boolean;
    [lever: string]: unknown;
  };
}

/** One cache's settings (core CacheStoreSettings). */
export interface CacheStoreSettings {
  enabled: boolean;
  max_bytes: number;
  require_hit: boolean;
}

// ========================================
// VRAM Preset Types
// ========================================

export interface VRAMPreset {
  name: string;
  display_name: string;
  description: string;
  vram_gb: number;
  gpu_examples: string[];
  version: string;
  author: string;
  builtin: boolean;
  ollama_settings: {
    ollama_chat_model: string;
    ollama_extraction_model?: string;
    ollama_vision_model?: string;
    ollama_num_ctx: number;
    ollama_num_batch?: number | null;
    ollama_num_parallel?: number | null;
    ollama_num_thread?: number | null;
  };
  llm_settings: {
    ai_context_window?: number;
    ai_max_tokens: number;
    extraction_max_tokens?: number;
    enable_thinking: boolean;
  };
}

export interface PresetListResponse {
  presets: VRAMPreset[];
  count: number;
}

export interface ApplyPresetResponse {
  success: boolean;
  preset_id: string;
  preset_name: string;
  settings_updated: Record<string, unknown>;
  message: string;
  missing_models: string[];
}

// ========================================
// Settings Update Response Types
// ========================================

export interface SettingsWarning {
  field: string;
  message: string;
  severity: 'warning' | 'info';
}

export interface SettingsUpdateResponse {
  settings: Settings;
  warnings: SettingsWarning[];
}

// ========================================
// Ollama Verification Types
// ========================================

export interface OllamaVerifyResponse {
  success: boolean;
  message: string;
  version?: string | null;
  models?: string[] | null;
  model_count?: number | null;
  response_time_ms?: number | null;
  error_type?: string | null;
}

export type LLMProvider = 'openai' | 'anthropic' | 'gemini';

export interface LLMVerifyResponse {
  success: boolean;
  message: string;
  provider: string;
}

export interface LLMHealthResponse {
  provider: string;
  configured: boolean;
  verified: boolean;
  last_verified_at?: string | null;
  /**
   * Configured Ollama models (chat / extraction / vision) not present on
   * any reachable Ollama instance. Empty for cloud providers. The Add
   * Source button and chat input gate on this being empty in addition
   * to `verified`.
   */
  missing_models: string[];
}

// ========================================
// Cloud Model Registry Types
// ========================================

export interface CloudModelPricing {
  input_per_million: number;
  output_per_million: number;
}

export interface CloudModelInfo {
  id: string;
  display_name: string;
  context_window: number;
  max_output_tokens: number;
  supports_vision?: boolean;
  supports_tools?: boolean;
  recommended?: boolean;
  pricing?: CloudModelPricing | null;
  notes?: string | null;
}

export interface CloudProviderInfo {
  display_name: string;
  models: CloudModelInfo[];
}

export interface CloudModelsResponse {
  providers: Record<string, CloudProviderInfo>;
}

// ============================================================================
// Ollama Model Management Types
// ============================================================================

export interface OllamaModelDetails {
  parameter_size: string | null;
  quantization_level: string | null;
  family: string | null;
  format: string | null;
}

export interface OllamaModelInfo {
  name: string;
  size: number;
  modified_at: string | null;
  digest: string | null;
  details: OllamaModelDetails | null;
}

export interface OllamaInstanceModels {
  instance_id: string;
  instance_name: string;
  base_url: string;
  healthy: boolean;
  models: OllamaModelInfo[];
}

export interface OllamaModelsListResponse {
  instances: OllamaInstanceModels[];
}

export interface OllamaModelShowResponse {
  modelfile: string | null;
  parameters: string | null;
  template: string | null;
  details: OllamaModelDetails | null;
  model_info: Record<string, unknown> | null;
}
