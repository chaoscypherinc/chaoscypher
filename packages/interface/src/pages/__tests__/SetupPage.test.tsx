// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, it, expect, vi } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { Routes, Route } from 'react-router';
import { installApiClientMock } from '../../test/mocks/apiClient';
import { fakeSettings, makeWrapper } from '../../test/renderWithProviders';
import SetupPage from '../SetupPage';
import AccountStep from '../SetupPage/AccountStep';
import {
  embeddingStepHasMinimumInput,
  llmStepHasMinimumInput,
} from '../SetupPage/wizardHelpers';
import type { Settings } from '../../types';
import { settingsApi } from '../../services/api/settings';

vi.mock('../../services/api/client', () => installApiClientMock());

describe('SetupPage', () => {
  it('renders without throwing', async () => {
    const { container } = render(
      <Routes>
        <Route path="/setup" element={<SetupPage />} />
      </Routes>,
      { wrapper: makeWrapper({ initialEntries: ['/setup'] }) },
    );
    await waitFor(() => expect(container).toBeTruthy());
  });

  it('renders the three-step wizard outline', async () => {
    render(
      <Routes>
        <Route path="/setup" element={<SetupPage />} />
      </Routes>,
      { wrapper: makeWrapper({ initialEntries: ['/setup'] }) },
    );
    await waitFor(() => expect(screen.getByText('Account')).toBeInTheDocument());
    expect(screen.getByText('LLM Provider')).toBeInTheDocument();
    expect(screen.getByText('Embeddings')).toBeInTheDocument();
    // Tool Approval used to be its own step; it now lives at the bottom of
    // the LLM Provider tab, so it's not a stepper label anymore.
    expect(screen.queryByText('Tool Approval')).not.toBeInTheDocument();
  });

  it('shows the account form on initial mount with no Skip button', async () => {
    render(
      <Routes>
        <Route path="/setup" element={<SetupPage />} />
      </Routes>,
      { wrapper: makeWrapper({ initialEntries: ['/setup'] }) },
    );
    await waitFor(() => expect(screen.getByLabelText(/^username/i)).toBeInTheDocument());
    expect(screen.getByLabelText(/^password/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/confirm password/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /create account/i })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^skip$/i })).not.toBeInTheDocument();
  });
});

describe('llmStepHasMinimumInput', () => {
  const withLlm = (overrides: Partial<Settings['llm']>): Settings => ({
    ...fakeSettings,
    llm: { ...fakeSettings.llm, ...overrides },
  });

  it('ollama: requires URL, VRAM preset, AND chat model', () => {
    const enabledInstance = [
      { id: '1', name: 'p', base_url: 'http://h:11434', enabled: true, healthy: true },
    ];

    // Missing everything
    expect(
      llmStepHasMinimumInput(
        withLlm({ chat_provider: 'ollama', ollama_instances: [], ollama_chat_model: '' }),
      ),
    ).toBe(false);

    // Has URL only
    expect(
      llmStepHasMinimumInput(
        withLlm({
          chat_provider: 'ollama',
          ollama_instances: enabledInstance,
          ollama_chat_model: '',
          ollama_quick_preset: null,
        }),
      ),
    ).toBe(false);

    // Has URL + chat model, missing preset
    expect(
      llmStepHasMinimumInput(
        withLlm({
          chat_provider: 'ollama',
          ollama_instances: enabledInstance,
          ollama_chat_model: 'phi4:14b',
          ollama_quick_preset: null,
        }),
      ),
    ).toBe(false);

    // Has URL + preset, missing chat model
    expect(
      llmStepHasMinimumInput(
        withLlm({
          chat_provider: 'ollama',
          ollama_instances: enabledInstance,
          ollama_chat_model: '',
          ollama_quick_preset: 'vram_16gb',
        }),
      ),
    ).toBe(false);

    // All three: passes
    expect(
      llmStepHasMinimumInput(
        withLlm({
          chat_provider: 'ollama',
          ollama_instances: enabledInstance,
          ollama_chat_model: 'phi4:14b',
          ollama_quick_preset: 'vram_16gb',
        }),
      ),
    ).toBe(true);
  });

  it('openai: requires api key + chat model', () => {
    expect(
      llmStepHasMinimumInput(
        withLlm({ chat_provider: 'openai', openai_api_key: '', openai_chat_model: 'gpt-4' }),
      ),
    ).toBe(false);
    expect(
      llmStepHasMinimumInput(
        withLlm({ chat_provider: 'openai', openai_api_key: 'sk-x', openai_chat_model: '' }),
      ),
    ).toBe(false);
    expect(
      llmStepHasMinimumInput(
        withLlm({ chat_provider: 'openai', openai_api_key: 'sk-x', openai_chat_model: 'gpt-4' }),
      ),
    ).toBe(true);
  });
});

describe('embeddingStepHasMinimumInput', () => {
  const withEmbed = (
    embedOverrides: Partial<Settings['embedding']>,
    searchOverrides: Partial<Settings['search']> = {},
  ): Settings => ({
    ...fakeSettings,
    embedding: { ...fakeSettings.embedding, ...embedOverrides },
    search: { ...fakeSettings.search, vector_dimensions: 1024, ...searchOverrides },
  });

  it('rejects when model is empty', () => {
    expect(embeddingStepHasMinimumInput(withEmbed({ provider: 'local', model: '' }))).toBe(false);
  });

  it('rejects when dimensions is non-positive', () => {
    expect(
      embeddingStepHasMinimumInput(
        withEmbed({ provider: 'local', model: 'foo' }, { vector_dimensions: 0 }),
      ),
    ).toBe(false);
  });

  it.each(['openai', 'gemini'] as const)('%s: requires api_key', (provider) => {
    expect(
      embeddingStepHasMinimumInput(
        withEmbed({ provider, model: 'text-embedding-3-small', api_key: null }),
      ),
    ).toBe(false);
    expect(
      embeddingStepHasMinimumInput(
        withEmbed({ provider, model: 'text-embedding-3-small', api_key: 'sk-x' }),
      ),
    ).toBe(true);
  });

  it('local: passes with model + dimensions', () => {
    expect(
      embeddingStepHasMinimumInput(
        withEmbed({ provider: 'local', model: 'Qwen/Qwen3-Embedding-0.6B' }),
      ),
    ).toBe(true);
  });
});

describe('AccountStep — Network access section', () => {
  it('defaults the switch to OFF when the access hint reports loopback', async () => {
    const spy = vi
      .spyOn(settingsApi, 'getAccessHint')
      .mockResolvedValueOnce({ request_host: 'localhost', is_loopback: true });
    try {
      render(<AccountStep onComplete={vi.fn()} />, { wrapper: makeWrapper() });
      const checkbox = await screen.findByLabelText(/allow access from other devices/i);
      await waitFor(() => expect(checkbox).not.toBeChecked());
    } finally {
      spy.mockRestore();
    }
  });

  it('defaults the switch to ON when the access hint reports non-loopback', async () => {
    const spy = vi
      .spyOn(settingsApi, 'getAccessHint')
      .mockResolvedValueOnce({ request_host: '192.168.1.20', is_loopback: false });
    try {
      render(<AccountStep onComplete={vi.fn()} />, { wrapper: makeWrapper() });
      const checkbox = await screen.findByLabelText(/allow access from other devices/i);
      await waitFor(() => expect(checkbox).toBeChecked());
    } finally {
      spy.mockRestore();
    }
  });

  it('reveals the advanced chip-input when expanded', async () => {
    const spy = vi
      .spyOn(settingsApi, 'getAccessHint')
      .mockResolvedValueOnce({ request_host: 'localhost', is_loopback: true });
    try {
      render(<AccountStep onComplete={vi.fn()} />, { wrapper: makeWrapper() });
      const advanced = await screen.findByText(/advanced: allow specific hosts only/i);
      fireEvent.click(advanced);
      await waitFor(() => {
        expect(screen.getByPlaceholderText(/add host/i)).toBeInTheDocument();
      });
    } finally {
      spy.mockRestore();
    }
  });
});
