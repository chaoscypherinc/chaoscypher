// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { makeWrapper } from '../../../test/renderWithProviders';
import { cacheApi } from '../../../services/api/cache';
import type { CacheOverview, CacheStats } from '../../../services/api/cache';
import CacheAccordion from '../CacheAccordion';

vi.mock('../../../services/api/cache', () => ({
  cacheApi: { getOverview: vi.fn(), update: vi.fn(), clear: vi.fn() },
}));

const api = vi.mocked(cacheApi);

function stats(name: string, overrides: Partial<CacheStats> = {}): CacheStats {
  return {
    name,
    enabled: true,
    require_hit: false,
    entries: 42,
    bytes: 1024 * 1024,
    max_bytes: 2 * 1024 ** 3,
    hits: 30,
    misses: 10,
    bypasses: 2,
    hit_rate: 0.75,
    latency_saved_ms: 3_725_000,
    by_call_site: {
      extraction: { hits: 30, misses: 10, bypasses: 2, latency_saved_ms: 3_725_000 },
    },
    ...overrides,
  };
}

function overview(): CacheOverview {
  return {
    dir: '/data/cache/llm',
    caches: [stats('completions'), stats('embeddings', { latency_saved_ms: 65_000, by_call_site: {} })],
  };
}

async function renderExpanded() {
  const user = userEvent.setup();
  render(<CacheAccordion />, { wrapper: makeWrapper() });
  await user.click(screen.getByRole('button', { name: /cache/i }));
  return user;
}

describe('CacheAccordion', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.getOverview.mockResolvedValue(overview());
  });

  it('renders both cache cards from the overview', async () => {
    await renderExpanded();
    const completions = await screen.findByTestId('cache-card-completions');
    const embeddings = screen.getByTestId('cache-card-embeddings');

    expect(screen.getByText('Pinned answers and embeddings served again without the model')).toBeInTheDocument();
    expect(within(completions).getByText('Completions')).toBeInTheDocument();
    expect(within(completions).getByText('42')).toBeInTheDocument();
    expect(within(completions).getByText('75%')).toBeInTheDocument();
    expect(within(completions).getAllByText('1h 2m 5s').length).toBeGreaterThan(0);
    expect(within(completions).getByText('1 MB / 2 GB')).toBeInTheDocument();
    expect(within(completions).getByRole('table', { name: /by call site/i })).toBeInTheDocument();
    expect(within(completions).getByText('extraction')).toBeInTheDocument();

    expect(within(embeddings).getByText('Embeddings')).toBeInTheDocument();
    expect(within(embeddings).getByText('1m 5s')).toBeInTheDocument();
    expect(within(embeddings).queryByRole('table')).toBeNull();
  });

  it('toggling a cache PATCHes its enabled flag', async () => {
    api.update.mockResolvedValue(overview());
    const user = await renderExpanded();
    await user.click(await screen.findByRole('switch', { name: /completions cache enabled/i }));

    await waitFor(() => expect(api.update).toHaveBeenCalledWith({ completions: { enabled: false } }));
  });

  it('Clear asks first: cancel calls nothing', async () => {
    const user = await renderExpanded();
    const card = await screen.findByTestId('cache-card-embeddings');
    await user.click(within(card).getByRole('button', { name: 'Clear' }));

    const dialog = await screen.findByRole('dialog');
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }));

    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(api.clear).not.toHaveBeenCalled();
  });

  it('Clear then confirm POSTs the clear and refreshes the overview', async () => {
    api.clear.mockResolvedValue({ name: 'embeddings', entries_removed: 42 });
    const user = await renderExpanded();
    const card = await screen.findByTestId('cache-card-embeddings');
    await user.click(within(card).getByRole('button', { name: 'Clear' }));

    const dialog = await screen.findByRole('dialog');
    await user.click(within(dialog).getByRole('button', { name: 'Clear' }));

    await waitFor(() => expect(api.clear).toHaveBeenCalledWith('embeddings'));
    await waitFor(() => expect(api.getOverview).toHaveBeenCalledTimes(2));
  });

  it('shows the error alert when the overview fails to load', async () => {
    api.getOverview.mockRejectedValue(new Error('cache unavailable'));
    await renderExpanded();

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('cache unavailable');
    expect(screen.queryByTestId('cache-card-completions')).toBeNull();
  });
});
