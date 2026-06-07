// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import type { ReactNode } from 'react';
import { describe, it, expect, vi, beforeEach, type Mock } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { Source } from '../../types';

// ---------------------------------------------------------------------------
// Mocks
//
// `getRecommendedNormalization` lives in the same module
// (`../services/api/sources`) and is mocked alongside `sourcesApi`. The real
// `../utils/errors` helpers are pure (no heavy deps) so we use them directly.
// ---------------------------------------------------------------------------

type UploadFn = (
  file: File,
  extractEntities?: boolean,
  analysisDepth?: 'quick' | 'full',
  enableNormalization?: boolean,
  domain?: string,
  onUploadProgress?: (progress: number) => void,
  enableVision?: boolean | null,
  filteringMode?: string,
  signal?: AbortSignal,
  contentFiltering?: boolean,
  skipDuplicates?: boolean,
  autoConfirm?: boolean,
) => Promise<Source>;

interface BatchResult {
  uploaded: number;
  failed: number;
  files: Source[];
  errors: { filename: string; error: string }[];
}

type UploadBatchFn = (
  files: File[],
  extractEntities?: boolean,
  analysisDepth?: 'quick' | 'full',
  enableNormalization?: boolean,
  domain?: string,
  enableVision?: boolean | null,
  filteringMode?: string,
  contentFiltering?: boolean,
  skipDuplicates?: boolean,
) => Promise<BatchResult>;

type ImportUrlFn = (
  url: string,
  extractEntities?: boolean,
  analysisDepth?: 'quick' | 'full',
  enableNormalization?: boolean,
  domain?: string,
  filteringMode?: string,
  contentFiltering?: boolean,
  skipDuplicates?: boolean,
) => Promise<Source>;

vi.mock('../../services/api/sources', () => ({
  sourcesApi: {
    upload: vi.fn<UploadFn>(),
    uploadBatch: vi.fn<UploadBatchFn>(),
    importUrl: vi.fn<ImportUrlFn>(),
    // Used by the embedded upload wizard (useUploadWizard) for the targeted
    // poll + the state-aware confirm. Single-file auto-domain uploads enter
    // the wizard's analyzing poll, so `get` must resolve.
    get: vi.fn<(id: string) => Promise<Source>>(),
    confirmExtraction: vi.fn<(id: string, opts: unknown) => Promise<{ source_id: string; status: string }>>(),
  },
  getRecommendedNormalization: vi.fn<(filename: string) => boolean>(),
}));

vi.mock('../../utils/logger', () => ({
  logger: {
    error: vi.fn(),
    info: vi.fn(),
    warn: vi.fn(),
  },
}));

const MAX_UPLOAD_BYTES = 5 * 1024 * 1024 * 1024;

vi.mock('../../contexts/useAppConfig', () => ({
  useAppConfig: () => ({ batch_max_upload_bytes: MAX_UPLOAD_BYTES }),
}));

// ---------------------------------------------------------------------------
// Imports under test (after mocks)
// ---------------------------------------------------------------------------

import { sourcesApi, getRecommendedNormalization } from '../../services/api/sources';
import { logger } from '../../utils/logger';
import { useSourcesUpload } from '../useSourcesUpload';

const mockUpload = sourcesApi.upload as ReturnType<typeof vi.fn>;
const mockUploadBatch = sourcesApi.uploadBatch as ReturnType<typeof vi.fn>;
const mockImportUrl = sourcesApi.importUrl as ReturnType<typeof vi.fn>;
const mockGet = sourcesApi.get as ReturnType<typeof vi.fn>;
const mockConfirmExtraction = sourcesApi.confirmExtraction as ReturnType<typeof vi.fn>;
const mockGetRecommendedNormalization = getRecommendedNormalization as ReturnType<typeof vi.fn>;
const mockLoggerError = logger.error as ReturnType<typeof vi.fn>;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Build a minimal Source-shaped result for the fields the hook reads. */
function makeSource(overrides: Partial<Source> = {}): Source {
  return {
    id: 'src-1',
    skipped_duplicate: false,
    ...overrides,
  } as Source;
}

/** Build a File of a given byte size and name/type. */
function makeFile(name = 'doc.pdf', sizeBytes = 1024, type = 'application/pdf'): File {
  const file = new File(['x'], name, { type });
  // jsdom File.size derives from contents; override for cap testing.
  Object.defineProperty(file, 'size', { value: sizeBytes });
  return file;
}

interface Callbacks {
  onUploadComplete: Mock<() => Promise<void>>;
  onError: Mock<(message: string, meta?: unknown) => void>;
  onInfo: Mock<(message: string, meta?: unknown) => void>;
}

function makeCallbacks(): Callbacks {
  return {
    onUploadComplete: vi.fn<() => Promise<void>>().mockResolvedValue(undefined),
    onError: vi.fn<(message: string, meta?: unknown) => void>(),
    onInfo: vi.fn<(message: string, meta?: unknown) => void>(),
  };
}

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

function renderUploadHook(cb: Callbacks) {
  return renderHook(
    () => useSourcesUpload(cb.onUploadComplete, cb.onError, cb.onInfo),
    { wrapper },
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockGetRecommendedNormalization.mockReturnValue(true);
  mockUpload.mockResolvedValue(makeSource());
  mockImportUrl.mockResolvedValue(makeSource());
  mockUploadBatch.mockResolvedValue({ uploaded: 0, failed: 0, files: [], errors: [] });
  // The embedded wizard polls `get` for an auto-domain single-file upload;
  // resolve to a proposal-ready source so it settles into review (the
  // wizard's own behavior is covered in useUploadWizard.test.tsx).
  mockGet.mockResolvedValue(
    makeSource({
      proposed_extraction_options: { ranking: [{ domain: 'generic', score: 1 }] },
      detection_ranking: [{ domain: 'generic', score: 1 }],
    } as Partial<Source>),
  );
  mockConfirmExtraction.mockResolvedValue({ source_id: 'src-1', status: 'indexed' });
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('useSourcesUpload', () => {
  // -------------------------------------------------------------------------
  // Initial state / return shape
  // -------------------------------------------------------------------------

  describe('initial state', () => {
    it('exposes sensible defaults', () => {
      const cb = makeCallbacks();
      const { result } = renderUploadHook(cb);

      expect(result.current.uploading).toBe(false);
      expect(result.current.uploadProgress).toBe(0);
      expect(result.current.importingUrl).toBe(false);
      expect(result.current.selectedFiles).toEqual([]);
      expect(result.current.extractEntities).toBe(true);
      expect(result.current.analysisDepth).toBe('full');
      expect(result.current.enableNormalization).toBe(true);
      expect(result.current.enableVision).toBe(true);
      expect(result.current.selectedDomain).toBe('__auto__');
      expect(result.current.filteringMode).toBe('');
      expect(result.current.contentFiltering).toBe(true);
      expect(result.current.skipDuplicates).toBe(false);
    });

    it('exposes all action functions', () => {
      const cb = makeCallbacks();
      const { result } = renderUploadHook(cb);

      expect(typeof result.current.handleFilesSelected).toBe('function');
      expect(typeof result.current.handleUploadConfirm).toBe('function');
      expect(typeof result.current.handleUrlImport).toBe('function');
      expect(typeof result.current.cancelUpload).toBe('function');
      expect(typeof result.current.clearSelection).toBe('function');
      expect(typeof result.current.removeFile).toBe('function');
    });
  });

  // -------------------------------------------------------------------------
  // Setters
  // -------------------------------------------------------------------------

  describe('setters', () => {
    it('updates each toggle/selection field', () => {
      const cb = makeCallbacks();
      const { result } = renderUploadHook(cb);

      act(() => result.current.setExtractEntities(false));
      act(() => result.current.setAnalysisDepth('quick'));
      act(() => result.current.setEnableNormalization(false));
      act(() => result.current.setEnableVision(false));
      act(() => result.current.setSelectedDomain('legal'));
      act(() => result.current.setFilteringMode('strict'));
      act(() => result.current.setContentFiltering(false));
      act(() => result.current.setSkipDuplicates(true));

      expect(result.current.extractEntities).toBe(false);
      expect(result.current.analysisDepth).toBe('quick');
      expect(result.current.enableNormalization).toBe(false);
      expect(result.current.enableVision).toBe(false);
      expect(result.current.selectedDomain).toBe('legal');
      expect(result.current.filteringMode).toBe('strict');
      expect(result.current.contentFiltering).toBe(false);
      expect(result.current.skipDuplicates).toBe(true);
    });
  });

  // -------------------------------------------------------------------------
  // handleFilesSelected
  // -------------------------------------------------------------------------

  describe('handleFilesSelected', () => {
    it('does nothing for an empty list', () => {
      const cb = makeCallbacks();
      const { result } = renderUploadHook(cb);

      act(() => result.current.handleFilesSelected([]));

      expect(result.current.selectedFiles).toEqual([]);
      expect(cb.onError).not.toHaveBeenCalled();
    });

    it('adds valid files and updates normalization recommendation', () => {
      const cb = makeCallbacks();
      const { result } = renderUploadHook(cb);
      const file = makeFile('notes.pdf');

      act(() => result.current.handleFilesSelected([file]));

      expect(result.current.selectedFiles).toHaveLength(1);
      expect(result.current.selectedFiles[0].name).toBe('notes.pdf');
      expect(mockGetRecommendedNormalization).toHaveBeenCalledWith('notes.pdf');
      expect(result.current.enableNormalization).toBe(true);
    });

    it('disables normalization when no file recommends it', () => {
      const cb = makeCallbacks();
      const { result } = renderUploadHook(cb);
      mockGetRecommendedNormalization.mockReturnValue(false);

      act(() => result.current.handleFilesSelected([makeFile('data.csv')]));

      expect(result.current.enableNormalization).toBe(false);
    });

    it('rejects oversized files and reports them via onError', () => {
      const cb = makeCallbacks();
      const { result } = renderUploadHook(cb);
      const big = makeFile('huge.pdf', MAX_UPLOAD_BYTES + 1);

      act(() => result.current.handleFilesSelected([big]));

      expect(result.current.selectedFiles).toEqual([]);
      expect(cb.onError).toHaveBeenCalledWith(
        expect.stringContaining('huge.pdf'),
      );
      expect(cb.onError.mock.calls[0][0]).toContain('limit');
    });

    it('keeps valid files while rejecting oversized ones in the same batch', () => {
      const cb = makeCallbacks();
      const { result } = renderUploadHook(cb);
      const ok = makeFile('ok.pdf', 1024);
      const big = makeFile('big.pdf', MAX_UPLOAD_BYTES + 1);

      act(() => result.current.handleFilesSelected([ok, big]));

      expect(result.current.selectedFiles).toHaveLength(1);
      expect(result.current.selectedFiles[0].name).toBe('ok.pdf');
      expect(cb.onError).toHaveBeenCalledTimes(1);
    });

    it('deduplicates files by filename when merging', () => {
      const cb = makeCallbacks();
      const { result } = renderUploadHook(cb);

      act(() => result.current.handleFilesSelected([makeFile('a.pdf')]));
      act(() => result.current.handleFilesSelected([makeFile('a.pdf'), makeFile('b.pdf')]));

      expect(result.current.selectedFiles.map(f => f.name)).toEqual(['a.pdf', 'b.pdf']);
    });
  });

  // -------------------------------------------------------------------------
  // removeFile / clearSelection
  // -------------------------------------------------------------------------

  describe('removeFile', () => {
    it('removes the file at the given index and re-evaluates normalization', () => {
      const cb = makeCallbacks();
      const { result } = renderUploadHook(cb);

      act(() => result.current.handleFilesSelected([makeFile('a.pdf'), makeFile('b.pdf')]));
      act(() => result.current.removeFile(0));

      expect(result.current.selectedFiles.map(f => f.name)).toEqual(['b.pdf']);
    });
  });

  describe('clearSelection', () => {
    it('clears files and resets normalization + domain', () => {
      const cb = makeCallbacks();
      const { result } = renderUploadHook(cb);

      act(() => result.current.handleFilesSelected([makeFile('a.pdf')]));
      act(() => result.current.setSelectedDomain('legal'));
      act(() => result.current.setEnableNormalization(false));

      act(() => result.current.clearSelection());

      expect(result.current.selectedFiles).toEqual([]);
      expect(result.current.enableNormalization).toBe(true);
      expect(result.current.selectedDomain).toBe('__auto__');
    });
  });

  // -------------------------------------------------------------------------
  // handleUploadConfirm — single file
  // -------------------------------------------------------------------------

  describe('handleUploadConfirm (single file — routed through the wizard)', () => {
    it('does nothing when no files are selected', async () => {
      const cb = makeCallbacks();
      const { result } = renderUploadHook(cb);

      await act(async () => {
        await result.current.handleUploadConfirm();
      });

      expect(mockUpload).not.toHaveBeenCalled();
      expect(mockUploadBatch).not.toHaveBeenCalled();
    });

    it('uploads the selected file with settings via the wizard (no batch)', async () => {
      const cb = makeCallbacks();
      const { result } = renderUploadHook(cb);

      act(() => result.current.handleFilesSelected([makeFile('doc.pdf')]));
      act(() => result.current.setSelectedDomain('legal'));
      act(() => result.current.setFilteringMode('strict'));

      await act(async () => {
        await result.current.handleUploadConfirm();
      });

      expect(mockUpload).toHaveBeenCalledTimes(1);
      expect(mockUploadBatch).not.toHaveBeenCalled();
      const args = mockUpload.mock.calls[0];
      expect((args[0] as File).name).toBe('doc.pdf');
      expect(args[1]).toBe(true);          // extractEntities
      expect(args[2]).toBe('full');        // analysisDepth
      expect(args[4]).toBe('legal');       // domain (not __auto__)
      expect(args[7]).toBe('strict');      // filteringMode
      // Override fast-path: a specific domain sends auto_confirm=true.
      expect(args[11]).toBe(true);
    });

    it('passes undefined domain + auto_confirm=false when __auto__ is selected', async () => {
      const cb = makeCallbacks();
      const { result } = renderUploadHook(cb);

      act(() => result.current.handleFilesSelected([makeFile('doc.pdf')]));

      await act(async () => {
        await result.current.handleUploadConfirm();
      });

      expect(mockUpload.mock.calls[0][4]).toBeUndefined(); // domain
      expect(mockUpload.mock.calls[0][11]).toBe(false);    // auto_confirm
    });

    it('passes undefined filteringMode when empty', async () => {
      const cb = makeCallbacks();
      const { result } = renderUploadHook(cb);

      act(() => result.current.handleFilesSelected([makeFile('doc.pdf')]));

      await act(async () => {
        await result.current.handleUploadConfirm();
      });

      expect(mockUpload.mock.calls[0][7]).toBeUndefined();
    });

    it('clears the selection and refreshes the list on success', async () => {
      const cb = makeCallbacks();
      const { result } = renderUploadHook(cb);
      act(() => result.current.handleFilesSelected([makeFile('doc.pdf')]));

      await act(async () => {
        await result.current.handleUploadConfirm();
      });

      expect(result.current.uploadProgress).toBe(100);
      expect(result.current.selectedFiles).toEqual([]);
      expect(cb.onUploadComplete).toHaveBeenCalledTimes(1);
    });

    it('does not surface an inline error when the wizard upload fails (the wizard shows it)', async () => {
      const cb = makeCallbacks();
      mockUpload.mockRejectedValue(new Error('network down'));

      const { result } = renderUploadHook(cb);
      act(() => result.current.handleFilesSelected([makeFile('doc.pdf')]));

      await act(async () => {
        await result.current.handleUploadConfirm();
      });

      // The wizard owns the error surface; the upload hook does not double-report.
      expect(cb.onError).not.toHaveBeenCalled();
      expect(result.current.selectedFiles).toEqual([]);
      expect(result.current.uploading).toBe(false);
    });

    it('opens the wizard: an auto-domain single-file Import drives phase to review', async () => {
      const cb = makeCallbacks();
      const { result } = renderUploadHook(cb);
      act(() => result.current.handleFilesSelected([makeFile('doc.pdf')]));

      expect(result.current.wizard.phase).toBe('idle');

      await act(async () => {
        await result.current.handleUploadConfirm();
      });

      // The shared wizard (used by both entry points) takes over. The default
      // mockGet resolves a proposal-ready source, so it lands on review.
      await waitFor(() => expect(result.current.wizard.phase).toBe('review'));
      expect(result.current.wizard.source?.id).toBe('src-1');
    });

    it('override fast-path: a specific domain skips the wizard (phase stays idle)', async () => {
      const cb = makeCallbacks();
      const { result } = renderUploadHook(cb);
      act(() => result.current.handleFilesSelected([makeFile('doc.pdf')]));
      act(() => result.current.setSelectedDomain('legal'));

      await act(async () => {
        await result.current.handleUploadConfirm();
      });

      // Fast-path: gate bypassed, no review.
      expect(result.current.wizard.phase).toBe('idle');
      expect(mockUpload.mock.calls[0][11]).toBe(true); // auto_confirm
    });
  });

  // -------------------------------------------------------------------------
  // handleUploadConfirm — skipped duplicate (single file)
  // -------------------------------------------------------------------------

  describe('handleUploadConfirm (single, skipped duplicate)', () => {
    it('fires onInfo for a non-error duplicate and refreshes', async () => {
      const cb = makeCallbacks();
      mockUpload.mockResolvedValue(
        makeSource({ skipped_duplicate: true, existing_status: 'committed', id: 'dup-9' }),
      );

      const { result } = renderUploadHook(cb);
      act(() => result.current.handleFilesSelected([makeFile('doc.pdf')]));

      await act(async () => {
        await result.current.handleUploadConfirm();
      });

      expect(cb.onInfo).toHaveBeenCalledWith(
        expect.stringContaining('matches an existing source'),
        { sourceId: 'dup-9' },
      );
      expect(cb.onError).not.toHaveBeenCalled();
      expect(cb.onUploadComplete).toHaveBeenCalledTimes(1);
      expect(result.current.selectedFiles).toEqual([]);
    });

    it('fires onError with retry action when the existing source errored', async () => {
      const cb = makeCallbacks();
      mockUpload.mockResolvedValue(
        makeSource({ skipped_duplicate: true, existing_status: 'error', id: 'dup-err' }),
      );

      const { result } = renderUploadHook(cb);
      act(() => result.current.handleFilesSelected([makeFile('doc.pdf')]));

      await act(async () => {
        await result.current.handleUploadConfirm();
      });

      expect(cb.onError).toHaveBeenCalledWith(
        expect.stringContaining('previously uploaded but errored'),
        { sourceId: 'dup-err', action: 'retry' },
      );
      expect(cb.onUploadComplete).toHaveBeenCalledTimes(1);
    });
  });

  // -------------------------------------------------------------------------
  // handleUploadConfirm — batch (multiple files)
  // -------------------------------------------------------------------------

  describe('handleUploadConfirm (batch)', () => {
    it('calls uploadBatch for multiple files and refreshes', async () => {
      const cb = makeCallbacks();
      mockUploadBatch.mockResolvedValue({ uploaded: 2, failed: 0, files: [makeSource(), makeSource()], errors: [] });

      const { result } = renderUploadHook(cb);
      act(() => result.current.handleFilesSelected([makeFile('a.pdf'), makeFile('b.pdf')]));

      await act(async () => {
        await result.current.handleUploadConfirm();
      });

      expect(mockUpload).not.toHaveBeenCalled();
      expect(mockUploadBatch).toHaveBeenCalledTimes(1);
      const files = mockUploadBatch.mock.calls[0][0] as File[];
      expect(files.map(f => f.name)).toEqual(['a.pdf', 'b.pdf']);
      expect(cb.onUploadComplete).toHaveBeenCalledTimes(1);
      expect(result.current.selectedFiles).toEqual([]);
    });

    it('surfaces a failure summary via onError when some uploads fail', async () => {
      const cb = makeCallbacks();
      mockUploadBatch.mockResolvedValue({
        uploaded: 1,
        failed: 1,
        files: [makeSource()],
        errors: [{ filename: 'b.pdf', error: 'boom' }],
      });

      const { result } = renderUploadHook(cb);
      act(() => result.current.handleFilesSelected([makeFile('a.pdf'), makeFile('b.pdf')]));

      await act(async () => {
        await result.current.handleUploadConfirm();
      });

      expect(cb.onError).toHaveBeenCalledWith(
        expect.stringContaining('failed'),
      );
    });

    it('reports all-duplicate batches via onInfo', async () => {
      const cb = makeCallbacks();
      const dup = makeSource({ skipped_duplicate: true });
      mockUploadBatch.mockResolvedValue({ uploaded: 2, failed: 0, files: [dup, dup], errors: [] });

      const { result } = renderUploadHook(cb);
      act(() => result.current.handleFilesSelected([makeFile('a.pdf'), makeFile('b.pdf')]));

      await act(async () => {
        await result.current.handleUploadConfirm();
      });

      expect(cb.onInfo).toHaveBeenCalledWith(
        expect.stringContaining('match existing sources'),
      );
    });

    it('reports a partial-duplicate batch via onInfo', async () => {
      const cb = makeCallbacks();
      const dup = makeSource({ skipped_duplicate: true });
      const fresh = makeSource({ skipped_duplicate: false });
      mockUploadBatch.mockResolvedValue({ uploaded: 2, failed: 0, files: [dup, fresh], errors: [] });

      const { result } = renderUploadHook(cb);
      act(() => result.current.handleFilesSelected([makeFile('a.pdf'), makeFile('b.pdf')]));

      await act(async () => {
        await result.current.handleUploadConfirm();
      });

      expect(cb.onInfo).toHaveBeenCalledWith(
        expect.stringContaining('skipped as duplicates'),
      );
    });
  });

  // -------------------------------------------------------------------------
  // handleUploadConfirm — abort + error paths
  // -------------------------------------------------------------------------

  describe('handleUploadConfirm (single-file abort / error → owned by the wizard)', () => {
    it('treats a single-file AbortError as a quiet cancel (wizard closes, no onError)', async () => {
      const cb = makeCallbacks();
      const abortErr = Object.assign(new Error('aborted'), { name: 'AbortError' });
      mockUpload.mockRejectedValue(abortErr);

      const { result } = renderUploadHook(cb);
      act(() => result.current.handleFilesSelected([makeFile('doc.pdf')]));

      await act(async () => {
        await result.current.handleUploadConfirm();
      });

      // The wizard catches the abort and resets; useSourcesUpload doesn't
      // surface "Upload cancelled" for the single-file path anymore.
      expect(cb.onError).not.toHaveBeenCalled();
      expect(result.current.uploading).toBe(false);
    });

    it('does not double-report a single-file upload failure (the wizard shows it)', async () => {
      const cb = makeCallbacks();
      mockUpload.mockRejectedValue(new Error('network down'));

      const { result } = renderUploadHook(cb);
      act(() => result.current.handleFilesSelected([makeFile('doc.pdf')]));

      await act(async () => {
        await result.current.handleUploadConfirm();
      });

      expect(cb.onError).not.toHaveBeenCalled();
      expect(result.current.uploading).toBe(false);
    });

    it('batch upload failures still surface via the catch block onError', async () => {
      const cb = makeCallbacks();
      mockUploadBatch.mockRejectedValue(new Error('batch boom'));

      const { result } = renderUploadHook(cb);
      act(() => result.current.handleFilesSelected([makeFile('a.pdf'), makeFile('b.pdf')]));

      await act(async () => {
        await result.current.handleUploadConfirm();
      });

      expect(mockLoggerError).toHaveBeenCalledWith('Upload failed:', expect.any(Error));
      expect(cb.onError).toHaveBeenCalledWith('Upload failed: batch boom');
      expect(result.current.uploading).toBe(false);
    });
  });

  // -------------------------------------------------------------------------
  // cancelUpload
  // -------------------------------------------------------------------------

  describe('cancelUpload', () => {
    it('aborts the in-flight upload signal', async () => {
      const cb = makeCallbacks();
      mockUpload.mockReturnValue(new Promise<Source>(() => {})); // never resolves

      const { result } = renderUploadHook(cb);
      act(() => result.current.handleFilesSelected([makeFile('doc.pdf')]));

      act(() => {
        void result.current.handleUploadConfirm();
      });

      await waitFor(() => expect(mockUpload).toHaveBeenCalledTimes(1));
      const signal = mockUpload.mock.calls[0][8] as AbortSignal;
      expect(signal.aborted).toBe(false);

      act(() => result.current.cancelUpload());

      expect(signal.aborted).toBe(true);
    });

    it('is a no-op when there is no in-flight upload', () => {
      const cb = makeCallbacks();
      const { result } = renderUploadHook(cb);

      expect(() => act(() => result.current.cancelUpload())).not.toThrow();
    });
  });

  // -------------------------------------------------------------------------
  // handleUrlImport
  // -------------------------------------------------------------------------

  describe('handleUrlImport', () => {
    it('does nothing for an empty url', async () => {
      const cb = makeCallbacks();
      const { result } = renderUploadHook(cb);

      await act(async () => {
        await result.current.handleUrlImport('');
      });

      expect(mockImportUrl).not.toHaveBeenCalled();
    });

    it('calls importUrl with current settings and refreshes on success', async () => {
      const cb = makeCallbacks();
      const { result } = renderUploadHook(cb);
      act(() => result.current.setSelectedDomain('legal'));

      await act(async () => {
        await result.current.handleUrlImport('https://example.com/doc');
      });

      expect(mockImportUrl).toHaveBeenCalledTimes(1);
      const args = mockImportUrl.mock.calls[0];
      expect(args[0]).toBe('https://example.com/doc');
      expect(args[4]).toBe('legal'); // domain
      expect(cb.onUploadComplete).toHaveBeenCalledTimes(1);
    });

    it('toggles importingUrl during the import', async () => {
      const cb = makeCallbacks();
      let resolveImport!: (s: Source) => void;
      mockImportUrl.mockReturnValue(new Promise<Source>(res => { resolveImport = res; }));

      const { result } = renderUploadHook(cb);

      act(() => {
        void result.current.handleUrlImport('https://example.com/doc');
      });

      await waitFor(() => expect(result.current.importingUrl).toBe(true));

      await act(async () => {
        resolveImport(makeSource());
      });

      await waitFor(() => expect(result.current.importingUrl).toBe(false));
    });

    it('fires onInfo for a non-error duplicate url', async () => {
      const cb = makeCallbacks();
      mockImportUrl.mockResolvedValue(
        makeSource({ skipped_duplicate: true, existing_status: 'committed', id: 'url-dup' }),
      );

      const { result } = renderUploadHook(cb);

      await act(async () => {
        await result.current.handleUrlImport('https://example.com/doc');
      });

      expect(cb.onInfo).toHaveBeenCalledWith(
        expect.stringContaining('matches an existing source'),
        { sourceId: 'url-dup' },
      );
      expect(cb.onUploadComplete).toHaveBeenCalledTimes(1);
    });

    it('fires onError with retry action for an errored duplicate url', async () => {
      const cb = makeCallbacks();
      mockImportUrl.mockResolvedValue(
        makeSource({ skipped_duplicate: true, existing_status: 'error', id: 'url-err' }),
      );

      const { result } = renderUploadHook(cb);

      await act(async () => {
        await result.current.handleUrlImport('https://example.com/doc');
      });

      expect(cb.onError).toHaveBeenCalledWith(
        expect.stringContaining('previously imported but errored'),
        { sourceId: 'url-err', action: 'retry' },
      );
    });

    it('logs and reports import failures', async () => {
      const cb = makeCallbacks();
      mockImportUrl.mockRejectedValue(new Error('bad url'));

      const { result } = renderUploadHook(cb);

      await act(async () => {
        await result.current.handleUrlImport('https://example.com/doc');
      });

      expect(mockLoggerError).toHaveBeenCalledWith('URL import failed:', expect.any(Error));
      expect(cb.onError).toHaveBeenCalledWith('URL import failed: bad url');
      expect(result.current.importingUrl).toBe(false);
    });
  });
});
