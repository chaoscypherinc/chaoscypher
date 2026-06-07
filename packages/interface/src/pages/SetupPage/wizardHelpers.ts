// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Wizard helpers — pure predicates against the live `Settings` object.
 *
 * The wizard renders the existing Settings page tab components
 * (`LLMProviderTab`, `EmbeddingProviderConfig`) so its working state is
 * just `Settings`. These predicates gate the Continue button per step.
 */

import type { Settings } from '../../types';

/**
 * Secret fields that must never be written to sessionStorage in cleartext.
 *
 * The wizard persists its in-progress `Settings` draft so a browser refresh
 * restores edits — but API keys / passwords would otherwise sit readable in
 * storage until Finish. They are stripped before persisting and re-merged
 * from the loaded context settings on rehydrate, so Finish still PATCHes the
 * real values rather than blanks. Listed as ``[section, field]`` pairs.
 */
const WIZARD_SECRET_FIELDS: ReadonlyArray<readonly [section: keyof Settings, field: string]> = [
  ['llm', 'openai_api_key'],
  ['llm', 'anthropic_api_key'],
  ['llm', 'gemini_api_key'],
  ['embedding', 'api_key'],
  ['queue', 'queue_password'],
];

/**
 * Return a copy of `settings` with every {@link WIZARD_SECRET_FIELDS} entry
 * removed. Does not mutate the input. Call before serialising the wizard
 * draft to sessionStorage.
 */
export function stripWizardSecrets(settings: Settings): Settings {
  // unknown-cast: we index dynamic (section, field) pairs that the Settings
  // type can't express as a uniform record. Never `any`.
  const clone = { ...settings } as unknown as Record<string, unknown>;
  for (const [section, field] of WIZARD_SECRET_FIELDS) {
    const slice = clone[section];
    if (slice && typeof slice === 'object') {
      const copy = { ...(slice as Record<string, unknown>) };
      delete copy[field];
      clone[section] = copy;
    }
  }
  return clone as unknown as Settings;
}

/**
 * Re-merge secret fields from `context` into a stripped `draft`.
 *
 * `draft` edits win for every non-secret field; the secrets come from
 * `context` (the loaded server settings) since they were never persisted.
 * Does not mutate either input.
 */
export function mergeWizardSecretsFromContext(draft: Settings, context: Settings): Settings {
  const merged = { ...draft } as unknown as Record<string, unknown>;
  const ctx = context as unknown as Record<string, unknown>;
  for (const [section, field] of WIZARD_SECRET_FIELDS) {
    const ctxSlice = ctx[section] as Record<string, unknown> | undefined;
    if (!ctxSlice || !(field in ctxSlice)) continue;
    const draftSlice = { ...((merged[section] as Record<string, unknown> | undefined) ?? {}) };
    draftSlice[field] = ctxSlice[field];
    merged[section] = draftSlice;
  }
  return merged as unknown as Settings;
}

/** True when step 1 has the minimum input needed to advance via Continue.
 *
 * Ollama additionally requires a VRAM preset to be selected so the user
 * actively confirms which size class they want — that drives the chat
 * model, context window, and other tuning parameters.
 */
export function llmStepHasMinimumInput(settings: Settings): boolean {
  const llm = settings.llm;
  switch (llm.chat_provider) {
    case 'ollama':
      return (
        llm.ollama_instances.some((i) => i.enabled && i.base_url.trim().length > 0) &&
        Boolean(llm.ollama_quick_preset && llm.ollama_quick_preset.trim().length > 0) &&
        llm.ollama_chat_model.trim().length > 0
      );
    case 'openai':
      return Boolean(llm.openai_api_key && llm.openai_chat_model.trim().length > 0);
    case 'anthropic':
      return Boolean(llm.anthropic_api_key && llm.anthropic_chat_model.trim().length > 0);
    case 'gemini':
      return Boolean(llm.gemini_api_key && llm.gemini_chat_model.trim().length > 0);
    default:
      return false;
  }
}

/** True when step 2 has the minimum input needed to advance via Continue.
 *
 * Ollama embedding additionally requires at least one enabled Ollama
 * instance with a non-empty URL — even if the user picked OpenAI for
 * chat (which would have skipped the Ollama URL field on step 1), the
 * embedding adapter still needs a reachable Ollama server.
 */
export function embeddingStepHasMinimumInput(settings: Settings): boolean {
  const e = settings.embedding;
  if (!e || !e.model || e.model.trim().length === 0) return false;
  if (settings.search.vector_dimensions <= 0) return false;
  if (e.provider === 'openai' || e.provider === 'gemini') {
    return Boolean(e.api_key && e.api_key.trim().length > 0);
  }
  if (e.provider === 'ollama') {
    return settings.llm.ollama_instances.some(
      (i) => i.enabled && i.base_url.trim().length > 0,
    );
  }
  return true;
}
