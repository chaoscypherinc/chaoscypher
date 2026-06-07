// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent, act } from '@testing-library/react';
import { Routes, Route } from 'react-router';
import { installApiClientMock } from '../../test/mocks/apiClient';
import { makeWrapper } from '../../test/renderWithProviders';
import SourcesPage from '../Sources';
import { apiClient } from '../../services/api/client';
import * as useSourcesMod from '../../services/api/useSources';
import * as sourceProcessingMod from '../../services/api/sourceProcessing';

vi.mock('../../services/api/client', () => installApiClientMock());

const mockedApiClient = apiClient as unknown as ReturnType<
  typeof installApiClientMock
>['apiClient'];

// ---------------------------------------------------------------------------
// Shared source fixtures
// ---------------------------------------------------------------------------

const PAGINATED_SOURCES = {
  data: [
    {
      id: 's1',
      title: 'Migrated Source',
      status: 'committed',
      source_type: 'pdf',
      created_at: '2026-05-20T00:00:00Z',
      file_size: 1234,
    },
  ],
  pagination: { total: 1, page: 1, page_size: 50, total_pages: 1, has_next: false, has_prev: false },
};

/** A source parked at awaiting_confirmation with a confident domain ranking. */
const AWAITING_SOURCE_RAW = {
  id: 'src-await-1',
  title: 'paper.pdf',
  status: 'awaiting_confirmation',
  source_type: 'pdf',
  created_at: '2026-05-28T00:00:00Z',
  file_size: 2048,
  confirmation_required: true,
  detection_ranking: [
    { domain: 'science', score: 4.2 },
    { domain: 'general', score: 1.6 },
  ],
  detection_confidence: 0.82,
  detection_low_confidence: false,
  proposed_extraction_options: { analysis_depth: 'full', filtering_mode: 'balanced' },
};

const AWAITING_PAGINATED = {
  data: [AWAITING_SOURCE_RAW],
  pagination: { total: 1, page: 1, page_size: 50, total_pages: 1, has_next: false, has_prev: false },
};

function renderPage() {
  return render(
    <Routes>
      <Route path="/sources" element={<SourcesPage />} />
    </Routes>,
    { wrapper: makeWrapper({ initialEntries: ['/sources'] }) },
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Default GET router used by most tests. */
function makeGetRouter(sourcesData: unknown) {
  return (url: string) => {
    if (url === '/sources') return Promise.resolve({ data: sourcesData });
    if (url === '/sources/domains')
      return Promise.resolve({ data: { domains: [{ name: 'science', description: 'Science', builtin: true }] } });
    if (url === '/settings') return Promise.resolve({ data: {} });
    if (url === '/llm/stats') return Promise.resolve({ data: {} });
    return Promise.resolve({ data: {} });
  };
}

// ---------------------------------------------------------------------------
// Baseline smoke tests (pre-existing)
// ---------------------------------------------------------------------------

describe('SourcesPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders without throwing', async () => {
    const { container } = renderPage();
    await waitFor(() => expect(container).toBeTruthy());
  });

  it('loads and lists sources via the migrated TanStack Query hook', async () => {
    mockedApiClient.get.mockImplementation((url: string) =>
      url === '/sources' ? Promise.resolve({ data: PAGINATED_SOURCES }) : Promise.resolve({ data: {} }),
    );

    renderPage();

    expect(await screen.findByText('Migrated Source')).toBeTruthy();
  });

  it('shows the empty state when no sources come back', async () => {
    mockedApiClient.get.mockImplementation((url: string) =>
      url === '/sources'
        ? Promise.resolve({
            data: { data: [], pagination: { total: 0, page: 1, page_size: 50, total_pages: 0, has_next: false, has_prev: false } },
          })
        : Promise.resolve({ data: {} }),
    );

    renderPage();

    expect(await screen.findByText(/No sources found/i)).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// Single-confirm flow
// ---------------------------------------------------------------------------

describe('SourcesPage — single confirm flow', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('clicking the Confirm domain chip opens ConfirmExtractionDialog', async () => {
    mockedApiClient.get.mockImplementation(makeGetRouter(AWAITING_PAGINATED));

    renderPage();

    // Wait for the row to appear.
    const chip = await screen.findByRole('button', { name: /confirm domain/i });
    expect(chip).toBeTruthy();

    fireEvent.click(chip);

    // The dialog should now be open — it contains the domain ranking picks.
    await waitFor(() => {
      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });

    // The top-ranked domain 'science' should be pre-selected (multiple occurrences are ok).
    expect(screen.getAllByText('science').length).toBeGreaterThan(0);
  });

  it('confirming in ConfirmExtractionDialog calls useConfirmExtraction.mutateAsync with sourceId + options and closes the dialog', async () => {
    mockedApiClient.get.mockImplementation(makeGetRouter(AWAITING_PAGINATED));

    // Mock the mutation: resolve immediately, capture arguments.
    const mutateAsync = vi.fn<() => Promise<{ source_id: string; status: string }>>().mockResolvedValue({
      source_id: 'src-await-1',
      status: 'queued',
    });
    vi.spyOn(useSourcesMod, 'useConfirmExtraction').mockReturnValue({
      mutateAsync,
      mutate: vi.fn(),
      isPending: false,
      isError: false,
      isSuccess: false,
      error: null,
      data: undefined,
      reset: vi.fn(),
      variables: undefined,
      context: undefined,
      failureCount: 0,
      failureReason: null,
      isIdle: true,
      isPaused: false,
      status: 'idle',
      submittedAt: 0,
    } as unknown as ReturnType<typeof useSourcesMod.useConfirmExtraction>);

    renderPage();

    // Click the confirm chip to open the dialog.
    const chip = await screen.findByRole('button', { name: /confirm domain/i });
    fireEvent.click(chip);

    await waitFor(() => {
      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });

    // Click the "Confirm" button inside the dialog.
    const confirmBtn = screen.getByRole('button', { name: /^confirm$/i });
    await act(async () => {
      fireEvent.click(confirmBtn);
    });

    await waitFor(() => {
      expect(mutateAsync).toHaveBeenCalledTimes(1);
    });

    // Assert the call shape: { sourceId, options }
    const callArgs = mutateAsync.mock.calls[0] as unknown as [{ sourceId: string; options: { domain?: string } }];
    const [callArg] = callArgs;
    expect(callArg.sourceId).toBe('src-await-1');
    // The dialog pre-selects the top-ranked domain (science).
    expect(callArg.options.domain).toBe('science');

    // After success the dialog should close (the role="dialog" disappears).
    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });
  });
});

// ---------------------------------------------------------------------------
// Upfront upload wizard (entry point 1: Sources page)
//
// The decisive "single-file Import opens the wizard" behavior is exercised at
// the shared seam in useSourcesUpload.test.tsx (handleUploadConfirm →
// wizard.start → analyzing/review). Here we assert the Sources page mounts the
// UploadWizard wired to that shared hook, so the wizard surface is reachable
// from this entry point.
// ---------------------------------------------------------------------------

describe('SourcesPage — upload wizard entry point', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('opens the upload dialog (the wizard entry) from the Add Source action', async () => {
    mockedApiClient.get.mockImplementation((url: string) => {
      if (url === '/sources') {
        return Promise.resolve({
          data: { data: [], pagination: { total: 0, page: 1, page_size: 50, total_pages: 0, has_next: false, has_prev: false } },
        });
      }
      if (url === '/sources/domains') {
        return Promise.resolve({ data: { domains: [{ name: 'science', description: 'Science', builtin: true }] } });
      }
      // The Add Source button is gated on a verified LLM.
      if (url === '/settings/llm/health') {
        return Promise.resolve({ data: { verified: true, missing_models: [] } });
      }
      return Promise.resolve({ data: {} });
    });

    renderPage();

    // Open the upload dialog — the wizard's step 1 (file select) entry point.
    const addBtn = await screen.findByRole('button', { name: /add source/i });
    await waitFor(() => expect(addBtn).not.toBeDisabled());
    fireEvent.click(addBtn);

    // The Add Source dialog (the wizard's front door) opens with its
    // drop-zone — text that only exists inside the dialog.
    expect(
      await screen.findByText(/Drop files here or click to browse/i),
    ).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Bulk-confirm flow
// ---------------------------------------------------------------------------

describe('SourcesPage — bulk confirm flow', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('"Confirm Selected" bulk action is visible when an awaiting source is selected', async () => {
    mockedApiClient.get.mockImplementation(makeGetRouter(AWAITING_PAGINATED));

    renderPage();

    // Wait for the row.
    await screen.findByRole('button', { name: /confirm domain/i });

    // There are 2 table checkboxes (header + row). Click the row one (index 1).
    const checkboxes = screen.getAllByRole('checkbox');
    // The first checkbox with data-indeterminate is the header; click the row checkbox.
    const rowCheckbox = checkboxes.find(
      (cb) => !(cb as HTMLInputElement).indeterminate && cb.getAttribute('data-indeterminate') !== 'true',
    ) ?? checkboxes[checkboxes.length - 1];
    fireEvent.click(rowCheckbox);

    // The bulk action bar should appear with "Confirm Selected".
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /confirm selected/i })).toBeInTheDocument();
    });
  });

  it('"Confirm Selected" is NOT shown when no selected source is awaiting_confirmation', async () => {
    // Use a source that is committed (active), not awaiting_confirmation.
    mockedApiClient.get.mockImplementation(makeGetRouter(PAGINATED_SOURCES));

    renderPage();

    // Wait for the row.
    await screen.findByText('Migrated Source');

    // Find the row-level checkbox (last checkbox = row checkbox since header is hidden at 0 selected).
    const checkboxes = screen.getAllByRole('checkbox');
    fireEvent.click(checkboxes[checkboxes.length - 1]);

    // Bulk delete bar appears (selectedCount > 0) but "Confirm Selected" must not.
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /delete selected/i })).toBeInTheDocument();
    });
    expect(screen.queryByRole('button', { name: /confirm selected/i })).not.toBeInTheDocument();
  });

  it('clicking "Confirm Selected" opens BulkConfirmDialog listing the awaiting source', async () => {
    mockedApiClient.get.mockImplementation(makeGetRouter(AWAITING_PAGINATED));

    renderPage();

    await screen.findByRole('button', { name: /confirm domain/i });

    // Select the row checkbox (last checkbox in list).
    const checkboxes = screen.getAllByRole('checkbox');
    fireEvent.click(checkboxes[checkboxes.length - 1]);

    // Click "Confirm Selected".
    const bulkBtn = await screen.findByRole('button', { name: /confirm selected/i });
    fireEvent.click(bulkBtn);

    // BulkConfirmDialog should open and list the source (may appear in both row and dialog).
    await waitFor(() => {
      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });
    expect(screen.getAllByText('paper.pdf').length).toBeGreaterThan(0);
  });

  it('confirming bulk dialog calls sourceProcessingApi.bulkConfirmExtraction with string[] of source_ids', async () => {
    mockedApiClient.get.mockImplementation(makeGetRouter(AWAITING_PAGINATED));

    // Spy on the module-level bulkConfirmExtraction.
    const bulkConfirm = vi.spyOn(sourceProcessingMod.sourceProcessingApi, 'bulkConfirmExtraction')
      .mockResolvedValue({
        confirmed: 1,
        failed: 0,
        results: [{ source_id: 'src-await-1', ok: true }],
      });

    renderPage();

    await screen.findByRole('button', { name: /confirm domain/i });

    // Select the row checkbox (last checkbox in list).
    const checkboxes = screen.getAllByRole('checkbox');
    fireEvent.click(checkboxes[checkboxes.length - 1]);

    // Open bulk dialog.
    const bulkBtn = await screen.findByRole('button', { name: /confirm selected/i });
    fireEvent.click(bulkBtn);

    await waitFor(() => {
      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });

    // Click "Confirm All" inside the dialog.
    const confirmAllBtn = screen.getByRole('button', { name: /confirm all/i });
    await act(async () => {
      fireEvent.click(confirmAllBtn);
    });

    await waitFor(() => {
      expect(bulkConfirm).toHaveBeenCalledTimes(1);
    });

    // CRITICAL: the argument must be a string[] of source_ids, NOT an array of objects.
    const [sourceIds] = bulkConfirm.mock.calls[0] as [string[]];
    expect(Array.isArray(sourceIds)).toBe(true);
    expect(sourceIds).toEqual(['src-await-1']);
    // Ensure we did NOT accidentally pass objects like { source_id: '…' }.
    expect(typeof sourceIds[0]).toBe('string');
  });

  it('partial-failure result surfaces per-item errors and dialog stays open', async () => {
    mockedApiClient.get.mockImplementation(makeGetRouter({
      data: [
        AWAITING_SOURCE_RAW,
        {
          id: 'src-await-2',
          title: 'other.pdf',
          status: 'awaiting_confirmation',
          source_type: 'pdf',
          created_at: '2026-05-28T01:00:00Z',
          file_size: 1024,
          confirmation_required: true,
          detection_ranking: [{ domain: 'legal', score: 3.1 }],
          detection_low_confidence: false,
        },
      ],
      pagination: { total: 2, page: 1, page_size: 50, total_pages: 1, has_next: false, has_prev: false },
    }));

    // One succeeds, one fails.
    vi.spyOn(sourceProcessingMod.sourceProcessingApi, 'bulkConfirmExtraction')
      .mockResolvedValue({
        confirmed: 1,
        failed: 1,
        results: [
          { source_id: 'src-await-1', ok: true },
          { source_id: 'src-await-2', ok: false, error: 'Not awaiting confirmation' },
        ],
      });

    renderPage();

    // Wait for both rows.
    await screen.findByText('paper.pdf');
    await screen.findByText('other.pdf');

    // Find table-scoped checkboxes (inside <table>), then click all row-level ones.
    const checkboxes = screen.getAllByRole('checkbox');
    const tableCheckboxes = checkboxes.filter((cb) => cb.closest('table') !== null);
    // tableCheckboxes[0] is the header (select-all) checkbox; [1] and [2] are row checkboxes.
    // Click both row checkboxes to select both sources.
    for (const cb of tableCheckboxes.slice(1)) {
      fireEvent.click(cb);
    }

    // Open bulk dialog.
    const bulkBtn = await screen.findByRole('button', { name: /confirm selected/i });
    fireEvent.click(bulkBtn);

    await waitFor(() => {
      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });

    // Confirm all.
    const confirmAllBtn = screen.getByRole('button', { name: /confirm all/i });
    await act(async () => {
      fireEvent.click(confirmAllBtn);
    });

    // The per-item error message must appear (in dialog or BulkProgressDialog).
    await waitFor(() => {
      expect(screen.getAllByText(/not awaiting confirmation/i).length).toBeGreaterThan(0);
    });

    // Dialog stays open because there were failures.
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });
});
