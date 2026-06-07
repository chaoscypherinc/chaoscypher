// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, it, expect } from 'vitest';
import { stripWizardSecrets, mergeWizardSecretsFromContext } from '../wizardHelpers';
import { fakeSettings } from '../../../test/renderWithProviders';
import type { Settings } from '../../../types';

/** fakeSettings with every wizard-handled secret populated. */
function withSecrets(): Settings {
  return {
    ...fakeSettings,
    llm: {
      ...fakeSettings.llm,
      openai_api_key: 'sk-openai-SECRET',
      anthropic_api_key: 'sk-anthropic-SECRET',
      gemini_api_key: 'gm-SECRET',
    },
    embedding: { ...fakeSettings.embedding, api_key: 'emb-SECRET' },
    queue: { ...fakeSettings.queue, queue_password: 'pw-SECRET' },
  };
}

describe('stripWizardSecrets', () => {
  it('removes every secret field', () => {
    const stripped = stripWizardSecrets(withSecrets());
    expect(stripped.llm.openai_api_key).toBeUndefined();
    expect(stripped.llm.anthropic_api_key).toBeUndefined();
    expect(stripped.llm.gemini_api_key).toBeUndefined();
    expect(stripped.embedding.api_key).toBeUndefined();
    expect(stripped.queue.queue_password).toBeUndefined();
  });

  it('leaves non-secret fields intact', () => {
    const original = withSecrets();
    const stripped = stripWizardSecrets(original);
    expect(stripped.llm.chat_provider).toBe(original.llm.chat_provider);
    expect(stripped.llm.openai_chat_model).toBe(original.llm.openai_chat_model);
    expect(stripped.embedding.provider).toBe(original.embedding.provider);
    expect(stripped.queue.queue_host).toBe(original.queue.queue_host);
  });

  it('does not mutate the input', () => {
    const original = withSecrets();
    stripWizardSecrets(original);
    expect(original.llm.openai_api_key).toBe('sk-openai-SECRET');
    expect(original.embedding.api_key).toBe('emb-SECRET');
    expect(original.queue.queue_password).toBe('pw-SECRET');
  });

  it('serialised output contains no secret values', () => {
    const json = JSON.stringify(stripWizardSecrets(withSecrets()));
    expect(json).not.toContain('SECRET');
  });
});

describe('mergeWizardSecretsFromContext', () => {
  it('restores secret fields from context into a stripped draft', () => {
    const context = withSecrets();
    // Draft = user edited a non-secret field; secrets stripped before persist.
    const draft = stripWizardSecrets({
      ...withSecrets(),
      llm: { ...withSecrets().llm, openai_chat_model: 'gpt-edited' },
    });

    const merged = mergeWizardSecretsFromContext(draft, context);

    // Secrets come back from context.
    expect(merged.llm.openai_api_key).toBe('sk-openai-SECRET');
    expect(merged.llm.anthropic_api_key).toBe('sk-anthropic-SECRET');
    expect(merged.embedding.api_key).toBe('emb-SECRET');
    expect(merged.queue.queue_password).toBe('pw-SECRET');
    // The user's non-secret edit is preserved.
    expect(merged.llm.openai_chat_model).toBe('gpt-edited');
  });

  it('round-trips: strip -> JSON -> parse -> merge restores secrets', () => {
    const context = withSecrets();
    const persisted = JSON.parse(JSON.stringify(stripWizardSecrets(context))) as Settings;

    const merged = mergeWizardSecretsFromContext(persisted, context);

    expect(merged.llm.gemini_api_key).toBe('gm-SECRET');
    expect(merged.queue.queue_password).toBe('pw-SECRET');
  });

  it('does not mutate the draft input', () => {
    const draft = stripWizardSecrets(withSecrets());
    mergeWizardSecretsFromContext(draft, withSecrets());
    expect(draft.llm.openai_api_key).toBeUndefined();
  });
});
