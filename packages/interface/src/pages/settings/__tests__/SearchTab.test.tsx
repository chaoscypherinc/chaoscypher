// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Tests for SearchTab.tsx after its migration to TanStack Query.
 *
 * The index status read (`useIndexStatus`) and the rebuild action
 * (`useRebuildIndexes`) now flow through `../hooks/useSearchIndex`, which call
 * the `search` service module. We mock that service and render inside
 * `makeWrapper`. `EmbeddingProviderConfig` is a heavy child that does its own
 * fetching; it's stubbed so these tests focus on the rebuild + mismatch logic.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import SearchTab from '../SearchTab';
import { makeWrapper } from '../../../test/renderWithProviders';
import type { Settings } from '../../../types';
import type { IndexStatus, RebuildResult } from '../../../services/api/search';

const getIndexStatus = vi.fn<() => Promise<IndexStatus>>();
const rebuildIndexes = vi.fn<() => Promise<RebuildResult>>();

vi.mock('../../../services/api/search', () => ({
  searchApi: {
    getIndexStatus: () => getIndexStatus(),
    rebuildIndexes: () => rebuildIndexes(),
  },
}));

// EmbeddingProviderConfig fetches its own data; stub it out of these tests.
vi.mock('../components/EmbeddingProviderConfig', () => ({
  default: () => <div data-testid="embedding-provider-config" />,
}));

function makeStatus(over: Partial<IndexStatus> = {}): IndexStatus {
  return {
    needs_rebuild: false,
    embedding_model: 'nomic-embed-text',
    vector_dimensions: 768,
    fulltext: { document_count: 10 },
    vector: { vector_count: 10, dimensions: 768 },
    ...over,
  };
}

function makeSettings(over: Partial<Settings> = {}): Settings {
  return {
    embedding: { provider: 'ollama', model: 'nomic-embed-text', ollama_instance_id: '', max_text_length: 0 },
    search: {
      max_search_results: 20,
      enable_vector_search: false,
      vector_dimensions: 768,
      fulltext_language: 'english',
      enable_auto_embedding: false,
    },
    ...over,
  } as unknown as Settings;
}

function renderTab(settings: Settings = makeSettings()) {
  const setSettings = vi.fn();
  render(<SearchTab settings={settings} setSettings={setSettings} />, {
    wrapper: makeWrapper(),
  });
  return { setSettings };
}

beforeEach(() => {
  vi.clearAllMocks();
  getIndexStatus.mockResolvedValue(makeStatus());
  rebuildIndexes.mockResolvedValue({ status: 'ok', total_nodes: 5, nodes_with_embeddings: 4, chunks_indexed: 3 });
});

describe('SearchTab', () => {
  it('reads index status on mount and shows no mismatch chip when model matches', async () => {
    renderTab();
    await waitFor(() => expect(getIndexStatus).toHaveBeenCalledTimes(1));
    // The warning chip uses the exact label "mismatch detected"; absent here.
    expect(screen.queryByText('mismatch detected')).not.toBeInTheDocument();
  });

  it('shows the mismatch chip when the index status reports needs_rebuild', async () => {
    getIndexStatus.mockResolvedValue(makeStatus({ needs_rebuild: true }));
    renderTab();
    await waitFor(() => expect(screen.getByText('mismatch detected')).toBeInTheDocument());
  });

  it('detects a local mismatch when the settings model differs from the index model', async () => {
    getIndexStatus.mockResolvedValue(makeStatus({ embedding_model: 'old-model', vector_dimensions: 768 }));
    renderTab(makeSettings({ embedding: { provider: 'ollama', model: 'new-model', ollama_instance_id: '', max_text_length: 0 } } as Partial<Settings>));
    await waitFor(() => expect(screen.getByText('mismatch detected')).toBeInTheDocument());
  });

  it('runs a rebuild and shows the synchronous success summary', async () => {
    renderTab();
    await waitFor(() => expect(getIndexStatus).toHaveBeenCalled());

    fireEvent.click(screen.getByRole('button', { name: /^rebuild$/i }));

    await waitFor(() => expect(rebuildIndexes).toHaveBeenCalledTimes(1));
    await waitFor(() => {
      expect(screen.getByText(/rebuilt successfully/i)).toBeInTheDocument();
    });
    expect(screen.getByText(/5 nodes/i)).toBeInTheDocument();
  });

  it('shows the queued message when the rebuild returns a task_id', async () => {
    rebuildIndexes.mockResolvedValue({ task_id: 'task-123' });
    renderTab();
    await waitFor(() => expect(getIndexStatus).toHaveBeenCalled());

    fireEvent.click(screen.getByRole('button', { name: /^rebuild$/i }));

    await waitFor(() => {
      expect(screen.getByText(/rebuild queued \(task task-123\)/i)).toBeInTheDocument();
    });
  });

  it('shows an error alert (server message) when the rebuild fails', async () => {
    rebuildIndexes.mockRejectedValue(new Error('rebuild boom'));
    renderTab();
    await waitFor(() => expect(getIndexStatus).toHaveBeenCalled());

    fireEvent.click(screen.getByRole('button', { name: /^rebuild$/i }));

    // getApiErrorMessage surfaces the Error message; falls back to a generic
    // string only when there's no extractable message.
    await waitFor(() => {
      expect(screen.getByText(/rebuild boom/i)).toBeInTheDocument();
    });
  });

  it('falls back to the generic message when the error has no message', async () => {
    // Empty-message error -> getApiErrorMessage returns '' -> generic fallback.
    rebuildIndexes.mockRejectedValue(new Error(''));
    renderTab();
    await waitFor(() => expect(getIndexStatus).toHaveBeenCalled());

    fireEvent.click(screen.getByRole('button', { name: /^rebuild$/i }));

    await waitFor(() => {
      expect(screen.getByText(/rebuild failed\. please try again/i)).toBeInTheDocument();
    });
  });
});
