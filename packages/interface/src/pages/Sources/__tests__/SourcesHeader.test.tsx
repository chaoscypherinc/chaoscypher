// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { SourcesHeader } from '../SourcesHeader';
import * as useLLMHealthMod from '../../../hooks/useLLMHealth';

function renderWithClient(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

describe('SourcesHeader — missing_models gates Add Source', () => {
  it('disables Add Source when verified but missing_models non-empty', () => {
    vi.spyOn(useLLMHealthMod, 'useLLMHealth').mockReturnValue({
      data: {
        provider: 'ollama',
        configured: true,
        verified: true,
        last_verified_at: '2026-05-21T20:00:00Z',
        missing_models: ['qwen3:30b-instruct'],
      },
      isLoading: false,
      error: null,
    } as unknown as ReturnType<typeof useLLMHealthMod.useLLMHealth>);

    renderWithClient(
      <SourcesHeader
        loading={false}
        uploading={false}
        onRefresh={() => {}}
        onUploadClick={() => {}}
      />,
    );

    const button = screen.getByRole('button', { name: /add source/i });
    expect(button).toBeDisabled();
  });

  it('enables Add Source when verified and missing_models empty', () => {
    vi.spyOn(useLLMHealthMod, 'useLLMHealth').mockReturnValue({
      data: {
        provider: 'ollama',
        configured: true,
        verified: true,
        last_verified_at: '2026-05-21T20:00:00Z',
        missing_models: [],
      },
      isLoading: false,
      error: null,
    } as unknown as ReturnType<typeof useLLMHealthMod.useLLMHealth>);

    renderWithClient(
      <SourcesHeader
        loading={false}
        uploading={false}
        onRefresh={() => {}}
        onUploadClick={() => {}}
      />,
    );

    expect(screen.getByRole('button', { name: /add source/i })).toBeEnabled();
  });
});
