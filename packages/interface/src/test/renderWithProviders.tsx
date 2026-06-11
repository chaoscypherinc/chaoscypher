// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Test render helper that mirrors `App.tsx`'s provider stack so tests
 * exercise the same context wiring as production.
 *
 * Wraps children in:
 *   MemoryRouter → ThemeProvider (minimal dark theme) → AuthProvider
 *     → NotificationProvider → SettingsProvider (fake settings)
 *
 * Usage:
 *   render(
 *     <Routes>
 *       <Route path="/nodes/:nodeId" element={<NodeDetailPage />} />
 *     </Routes>,
 *     { wrapper: makeWrapper({ initialEntries: ['/nodes/node-1'] }) },
 *   );
 */

import type { ReactNode } from 'react';
import { MemoryRouter } from 'react-router';
import { ThemeProvider, createTheme, CssBaseline } from '@mui/material';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AuthProvider } from '../contexts/AuthContext';
import { NotificationProvider } from '../contexts/NotificationContext';
import { SettingsProvider } from '../contexts/SettingsContext';
import type { Settings } from '../types';

const theme = createTheme({ palette: { mode: 'dark' } });

/**
 * Minimal settings object for tests. Every field of the Settings interface
 * is populated with a zero-value default. Individual tests that depend on a
 * specific setting value should build their own override from this base.
 */
export const fakeSettings: Settings = {
  app_name: 'Chaos Cypher Test',
  current_database: 'test-db',
  data_dir: '/tmp/chaoscypher-test',
  dark_mode: true,
  auto_enable: false,
  setup_completed: true,
  custom_settings: {},
  llm: {
    chat_provider: 'ollama',
    ollama_instances: [],
    ollama_chat_model: 'llama3',
    openai_base_url: '',
    openai_chat_model: '',
    anthropic_chat_model: '',
    gemini_chat_model: '',
    ai_max_tokens: 2048,
    ai_temperature: 0.7,
    enable_thinking: false,
    thinking_for_chat: false,
    thinking_for_tools: false,
    enable_llm_queueing: false,
    llm_max_retries: 3,
    llm_max_concurrent: 1,
    llm_reserved_interactive: 0,
    llm_enable_priority: false,
    enable_token_cost_tracking: false,
    token_cost_input_per_million: 0,
    token_cost_output_per_million: 0,
  },
  queue: {
    queue_host: 'localhost',
    queue_port: 6379,
    queue_database: 0,
    queue_ssl: false,
  },
  embedding: {
    provider: 'ollama',
    model: '',
    ollama_instance_id: '',
    max_text_length: 0,
  },
  search: {
    max_search_results: 20,
    enable_vector_search: false,
    vector_dimensions: 0,
    fulltext_language: 'english',
    enable_auto_embedding: false,
  },
  source_processing: {
    max_file_size_gb: 1,
    auto_analyze: false,
    analysis_depth: 'standard',
    chunk_overlap: 0,
    chunking_strategy: 'default',
    relationship_confidence_threshold: 0,
  },
  chunking: {
    small_chunk_size: 900,
    small_chunk_overlap: 0,
    min_chunk_size: 100,
    max_chunk_size: 2000,
    respect_boundaries: true,
    group_size: 4,
    group_overlap: 1,
    output_tokens_per_chunk: 2000,
  },
  nlp: {
    nlp_enable_spacy_ner: false,
    nlp_enable_dependency_parsing: false,
    nlp_enable_semantic_embeddings: false,
    nlp_semantic_model: '',
    nlp_similarity_threshold: 0,
  },
  export: {
    export_version: '1.0.0',
    export_license: 'AGPL-3.0-only',
    export_tags: [],
    export_derived_from: {},
    export_dependencies: {},
  },
  workflow_history_limit: 100,
  trigger_history_limit: 100,
  backup: {
    enabled: false,
    interval: 'daily',
    retention_count: 7,
    backup_dir: '/tmp/backups',
  },
};

interface WrapperOptions {
  initialEntries?: string[];
}

/**
 * Build a wrapper component for `render({ wrapper })`.
 * Default initial entry is `/` so most page tests need no options.
 */
export function makeWrapper(options: WrapperOptions = {}): React.FC<{ children: ReactNode }> {
  const { initialEntries = ['/'] } = options;
  const noopRefresh = async () => {};
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return function Wrapper({ children }) {
    return (
      <MemoryRouter initialEntries={initialEntries}>
        <ThemeProvider theme={theme}>
          <CssBaseline />
          <QueryClientProvider client={queryClient}>
            <AuthProvider>
              <NotificationProvider>
                <SettingsProvider settings={fakeSettings} refreshSettings={noopRefresh}>
                  {children}
                </SettingsProvider>
              </NotificationProvider>
            </AuthProvider>
          </QueryClientProvider>
        </ThemeProvider>
      </MemoryRouter>
    );
  };
}
