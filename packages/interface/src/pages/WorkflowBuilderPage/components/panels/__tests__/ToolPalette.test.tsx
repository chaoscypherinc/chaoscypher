// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * ToolPalette smoke tests.
 *
 * Pins the palette's behaviour across the TanStack Query migration: it loads
 * system tools and groups them into category accordions, filters by the search
 * box, and on load failure falls back to the bundled sample tools while showing
 * a warning. Mocks at the apiClient layer so the real service modules and query
 * hooks run unchanged.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { installApiClientMock } from '../../../../../test/mocks/apiClient';
import { makeWrapper } from '../../../../../test/renderWithProviders';
import { ToolPalette } from '../ToolPalette';
import { apiClient } from '../../../../../services/api/client';

vi.mock('../../../../../services/api/client', () => installApiClientMock());

const mockedApiClient = apiClient as unknown as ReturnType<
  typeof installApiClientMock
>['apiClient'];

const AI_TOOL = {
  id: 'ai.prompt',
  category: 'ai',
  icon: null,
  name: 'AI Prompt',
  description: 'Run an AI prompt',
  input_schema: {},
  output_schema: {},
  version: '1.0.0',
  is_active: true,
};

function renderPalette() {
  return render(<ToolPalette onClose={() => {}} />, { wrapper: makeWrapper() });
}

describe('ToolPalette', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('loads system tools and renders their category accordion', async () => {
    mockedApiClient.get.mockImplementation((url: string) => {
      if (url === '/tools/system') return Promise.resolve({ data: [AI_TOOL] });
      return Promise.resolve({ data: {} });
    });
    renderPalette();

    // Category accordion header for the AI tool.
    expect(await screen.findByText('AI Tools')).toBeTruthy();
  });

  it('filters tools by the search query', async () => {
    mockedApiClient.get.mockImplementation((url: string) => {
      if (url === '/tools/system') return Promise.resolve({ data: [AI_TOOL] });
      return Promise.resolve({ data: {} });
    });
    renderPalette();

    await screen.findByText('AI Tools');

    const search = screen.getByPlaceholderText('Search tools...');
    fireEvent.change(search, { target: { value: 'no-such-tool' } });

    await waitFor(() => {
      expect(screen.getByText('No tools match your search.')).toBeTruthy();
    });
  });

  it('falls back to sample tools and warns on load failure', async () => {
    mockedApiClient.get.mockImplementation((url: string) => {
      if (url === '/tools/system') return Promise.reject(new Error('boom'));
      return Promise.resolve({ data: {} });
    });
    renderPalette();

    // Warning alert surfaces.
    expect(await screen.findByText('Failed to load tools')).toBeTruthy();
    // Sample data still renders the AI Tools category.
    expect(await screen.findByText('AI Tools')).toBeTruthy();
  });
});
