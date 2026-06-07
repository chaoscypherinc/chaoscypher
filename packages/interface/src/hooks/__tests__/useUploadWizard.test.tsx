// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Tests for `useUploadWizard` — the upfront domain-confirmation upload wizard
 * orchestration hook.
 *
 * Covers (spec §5 UI bullet):
 *   - the targeted poll: keeps polling GET /sources/{id} at the wizard cadence
 *     until the eager `detection_proposal` (surfaced as proposed_extraction_options)
 *     is populated, then advances to 'review';
 *   - the hard timeout: if the proposal never lands, the wizard closes (chip fallback);
 *   - the override fast-path: a specific domain skips polling entirely;
 *   - skipped-duplicate: no wizard;
 *   - confirm → state-aware confirm endpoint, then close.
 *
 * Mocks at the `apiClient` layer so the real service modules + query hooks run
 * unchanged. Fake timers drive `refetchInterval`.
 */

import React from 'react';
import { renderHook, act } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

import { installApiClientMock } from '../../test/mocks/apiClient';
import {
  useUploadWizard,
  WIZARD_POLL_MS,
  WIZARD_ANALYZE_TIMEOUT_MS,
  type WizardUploadParams,
} from '../useUploadWizard';

vi.mock('../../services/api/client', () => installApiClientMock());
vi.mock('../../utils/logger', () => ({
  logger: { error: vi.fn(), info: vi.fn(), warn: vi.fn() },
}));

import { apiClient } from '../../services/api/client';

const mockedApiClient = apiClient as unknown as ReturnType<
  typeof installApiClientMock
>['apiClient'];

// ── Helpers ───────────────────────────────────────────────────────────────

function wrap({ children }: { children: React.ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

function makeFile(name = 'doc.pdf'): File {
  return new File(['x'], name, { type: 'application/pdf' });
}

function makeParams(overrides: Partial<WizardUploadParams> = {}): WizardUploadParams {
  return {
    file: makeFile(),
    extractEntities: true,
    analysisDepth: 'full',
    enableNormalization: true,
    enableVision: true,
    filteringMode: '',
    contentFiltering: true,
    skipDuplicates: false,
    domain: '__auto__',
    ...overrides,
  };
}

/** Minimal SourceResponse-shaped row for the fields the hook reads. */
function makeSource(overrides: Record<string, unknown> = {}) {
  return {
    id: 'src-1',
    filename: 'doc.pdf',
    title: 'Doc',
    status: 'indexing',
    skipped_duplicate: false,
    ...overrides,
  };
}

const PROPOSAL = {
  ranking: [
    { domain: 'legal', score: 8.2 },
    { domain: 'generic', score: 2.1 },
  ],
  confidence: 8.2,
  detected_domain: 'legal',
  low_confidence: false,
};

beforeEach(() => {
  vi.clearAllMocks();
});

afterEach(() => {
  vi.useRealTimers();
});

describe('useUploadWizard', () => {
  it('starts idle with no source or error', () => {
    const { result } = renderHook(() => useUploadWizard(), { wrapper: wrap });
    expect(result.current.phase).toBe('idle');
    expect(result.current.source).toBeNull();
    expect(result.current.error).toBeNull();
    expect(result.current.confirming).toBe(false);
  });

  // ── Override fast-path ─────────────────────────────────────────────────

  it('override fast-path: a specific domain sends auto_confirm=true and skips polling', async () => {
    mockedApiClient.post.mockResolvedValue({ data: makeSource() });

    const { result } = renderHook(() => useUploadWizard(), { wrapper: wrap });

    let outcome: string | undefined;
    await act(async () => {
      outcome = (await result.current.start(makeParams({ domain: 'legal' }))).outcome;
    });

    expect(outcome).toBe('fast-path');
    expect(result.current.phase).toBe('idle');
    // Upload POSTed once; GET /sources/{id} never polled.
    expect(mockedApiClient.post).toHaveBeenCalledTimes(1);
    const form = mockedApiClient.post.mock.calls[0][1] as FormData;
    expect(form.get('domain')).toBe('legal');
    expect(form.get('auto_confirm')).toBe('true');
    expect(
      mockedApiClient.get.mock.calls.filter((c) => c[0] === '/sources/src-1'),
    ).toHaveLength(0);
  });

  it('auto domain does NOT send a forced domain or auto_confirm', async () => {
    mockedApiClient.post.mockResolvedValue({ data: makeSource() });
    mockedApiClient.get.mockResolvedValue({ data: makeSource() });

    const { result } = renderHook(() => useUploadWizard(), { wrapper: wrap });
    await act(async () => {
      await result.current.start(makeParams({ domain: '__auto__' }));
    });

    const form = mockedApiClient.post.mock.calls[0][1] as FormData;
    expect(form.get('domain')).toBeNull();
    expect(form.get('auto_confirm')).toBeNull();
  });

  // ── Skipped duplicate ──────────────────────────────────────────────────

  it('skipped duplicate: no wizard, returns "skipped"', async () => {
    mockedApiClient.post.mockResolvedValue({
      data: makeSource({ skipped_duplicate: true, existing_status: 'committed' }),
    });

    const { result } = renderHook(() => useUploadWizard(), { wrapper: wrap });
    let outcome: string | undefined;
    await act(async () => {
      outcome = (await result.current.start(makeParams())).outcome;
    });

    expect(outcome).toBe('skipped');
    expect(result.current.phase).toBe('idle');
  });

  // ── Upload failure ─────────────────────────────────────────────────────

  it('surfaces an upload error and enters the error phase', async () => {
    mockedApiClient.post.mockRejectedValue(new Error('network down'));

    const { result } = renderHook(() => useUploadWizard(), { wrapper: wrap });
    let outcome: string | undefined;
    await act(async () => {
      outcome = (await result.current.start(makeParams())).outcome;
    });

    expect(outcome).toBe('error');
    expect(result.current.phase).toBe('error');
    expect(result.current.error).toContain('Upload failed');
  });

  // ── Poll until proposal ────────────────────────────────────────────────

  it('polls until the detection proposal lands, then advances to review', async () => {
    vi.useFakeTimers();
    mockedApiClient.post.mockResolvedValue({ data: makeSource() });

    // First two GETs: no proposal yet. Third: proposal present.
    let getCalls = 0;
    mockedApiClient.get.mockImplementation((url: string) => {
      if (url === '/sources/src-1') {
        getCalls += 1;
        const withProposal = getCalls >= 3;
        return Promise.resolve({
          data: makeSource({
            status: 'indexing',
            proposed_extraction_options: withProposal ? PROPOSAL : null,
            detection_ranking: withProposal ? PROPOSAL.ranking : [],
            detection_low_confidence: withProposal ? false : null,
          }),
        });
      }
      return Promise.resolve({ data: {} });
    });

    const { result } = renderHook(() => useUploadWizard(), { wrapper: wrap });

    await act(async () => {
      await result.current.start(makeParams());
    });
    expect(result.current.phase).toBe('analyzing');

    // Advance through poll cycles until the proposal arrives.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(WIZARD_POLL_MS * 3);
    });

    await vi.waitFor(() => expect(result.current.phase).toBe('review'));
    expect(result.current.source?.id).toBe('src-1');
    expect(result.current.source?.detection_ranking).toEqual(PROPOSAL.ranking);
  });

  it('advances to review immediately when the proposal is already present on the first poll', async () => {
    vi.useFakeTimers();
    mockedApiClient.post.mockResolvedValue({ data: makeSource() });
    mockedApiClient.get.mockResolvedValue({
      data: makeSource({
        proposed_extraction_options: PROPOSAL,
        detection_ranking: PROPOSAL.ranking,
        detection_low_confidence: false,
      }),
    });

    const { result } = renderHook(() => useUploadWizard(), { wrapper: wrap });
    await act(async () => {
      await result.current.start(makeParams());
    });
    // Let the initial poll fetch resolve.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(WIZARD_POLL_MS);
    });

    await vi.waitFor(() => expect(result.current.phase).toBe('review'));
  });

  // ── Hard timeout → chip fallback ───────────────────────────────────────

  it('times out and closes gracefully when no proposal ever lands', async () => {
    vi.useFakeTimers();
    mockedApiClient.post.mockResolvedValue({ data: makeSource() });
    // Always returns a source WITHOUT a proposal.
    mockedApiClient.get.mockResolvedValue({
      data: makeSource({ status: 'indexing', proposed_extraction_options: null }),
    });

    const { result } = renderHook(() => useUploadWizard(), { wrapper: wrap });
    await act(async () => {
      await result.current.start(makeParams());
    });
    expect(result.current.phase).toBe('analyzing');

    // Advance well past the hard timeout.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(WIZARD_ANALYZE_TIMEOUT_MS + WIZARD_POLL_MS * 2);
    });

    await vi.waitFor(() => expect(result.current.phase).toBe('idle'));
    expect(result.current.source).toBeNull();
  });

  // ── Confirm ────────────────────────────────────────────────────────────

  it('confirm hits the confirmation endpoint and closes the wizard', async () => {
    vi.useFakeTimers();
    mockedApiClient.post.mockImplementation((url: string) => {
      if (url === '/sources') return Promise.resolve({ data: makeSource() });
      // confirmation endpoint
      return Promise.resolve({ data: { source_id: 'src-1', status: 'indexed' } });
    });
    mockedApiClient.get.mockResolvedValue({
      data: makeSource({
        proposed_extraction_options: PROPOSAL,
        detection_ranking: PROPOSAL.ranking,
      }),
    });

    const { result } = renderHook(() => useUploadWizard(), { wrapper: wrap });
    await act(async () => {
      await result.current.start(makeParams());
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(WIZARD_POLL_MS);
    });
    await vi.waitFor(() => expect(result.current.phase).toBe('review'));

    await act(async () => {
      await result.current.confirm({ domain: 'legal', analysis_depth: 'full' });
    });

    expect(result.current.phase).toBe('idle');
    const confirmCall = mockedApiClient.post.mock.calls.find(
      (c) => c[0] === '/sources/src-1/confirmation',
    );
    expect(confirmCall).toBeDefined();
    expect(confirmCall?.[1]).toMatchObject({ domain: 'legal' });
  });

  it('cancel resets the wizard from review back to idle', async () => {
    vi.useFakeTimers();
    mockedApiClient.post.mockResolvedValue({ data: makeSource() });
    mockedApiClient.get.mockResolvedValue({
      data: makeSource({ proposed_extraction_options: PROPOSAL, detection_ranking: PROPOSAL.ranking }),
    });

    const { result } = renderHook(() => useUploadWizard(), { wrapper: wrap });
    await act(async () => {
      await result.current.start(makeParams());
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(WIZARD_POLL_MS);
    });
    await vi.waitFor(() => expect(result.current.phase).toBe('review'));

    act(() => result.current.cancel());
    expect(result.current.phase).toBe('idle');
    expect(result.current.source).toBeNull();
  });
});
