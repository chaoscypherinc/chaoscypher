// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// Mock BATCH_CONFIG with tiny values so polling loops run fast in tests.
vi.mock('../../../constants/config', () => ({
  BATCH_CONFIG: {
    BULK_OPERATION_SIZE: 10,
    POLLING_MAX_ATTEMPTS: 5,
    POLLING_WAIT_MS: 0,
    EXPORT_MAX_ATTEMPTS: 3,
    IMPORT_MAX_ATTEMPTS: 3,
  },
}));

vi.mock('../client', () => ({
  apiClient: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  },
}));

import { apiClient } from '../client';
import { dataApi } from '../data';

const mockGet = apiClient.get as ReturnType<typeof vi.fn>;
const mockPost = apiClient.post as ReturnType<typeof vi.fn>;

// Helper: create a minimal valid base64 ZIP content (single byte 0x00)
const FAKE_BASE64 = btoa('\x00');

function makeTaskQueuedResponse(taskId: string) {
  return { data: { task_id: taskId, status: 'queued', message: 'ok' } };
}

function makeStatusResponse(status: string, error?: string) {
  return { data: { status, ...(error !== undefined ? { error } : {}) } };
}

function makeResultResponse(base64Content: string) {
  return {
    data: {
      result: {
        filename: 'export.zip',
        content: base64Content,
        size_bytes: 1,
      },
    },
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  // Use fake timers so setTimeout in polling resolves instantly.
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

// Helper that runs the async fn while advancing fake timers concurrently.
// Returns a tuple [promise, settle] where settle() drains all pending timers.
// This avoids "unhandled rejection" warnings: the caller attaches .catch/.rejects
// before the timers fire, ensuring errors are always handled.
function withTimers<T>(fn: () => Promise<T>): { result: Promise<T>; flush: () => Promise<void> } {
  const result = fn();
  const flush = async () => {
    await vi.runAllTimersAsync();
  };
  return { result, flush };
}

// Convenience wrapper for success paths.
async function runWithTimers<T>(fn: () => Promise<T>): Promise<T> {
  const { result, flush } = withTimers(fn);
  await flush();
  return result;
}

// For rejection paths: attach a rejection handler BEFORE flushing timers
// so the error is never unhandled.
async function rejectWithTimers<T>(fn: () => Promise<T>): Promise<unknown> {
  const { result, flush } = withTimers(fn);
  // Attach rejection handler immediately so the error is never unhandled.
  const caught = result.then(
    () => { throw new Error('Expected rejection but promise resolved'); },
    (err: unknown) => err,
  );
  await flush();
  return caught;
}

// ---------------------------------------------------------------------------
// dataApi.export
// ---------------------------------------------------------------------------
describe('dataApi.export', () => {
  it('POSTs to /exports with default params and returns a Blob on completion', async () => {
    mockPost.mockResolvedValueOnce(makeTaskQueuedResponse('task-1'));
    mockGet
      .mockResolvedValueOnce(makeStatusResponse('running'))
      .mockResolvedValueOnce(makeStatusResponse('completed'))
      .mockResolvedValueOnce(makeResultResponse(FAKE_BASE64));

    const blob = await runWithTimers(() => dataApi.export());

    expect(mockPost).toHaveBeenCalledWith(
      '/exports',
      {},
      expect.objectContaining({
        params: expect.objectContaining({
          include_templates: true,
          include_knowledge: true,
          include_lenses: true,
          include_workflows: true,
          include_sources: true,
          include_embeddings: false,
        }),
      }),
    );
    expect(blob).toBeInstanceOf(Blob);
    expect(blob.type).toBe('application/zip');
  });

  it('includes lens_id in params when lensId option is provided', async () => {
    mockPost.mockResolvedValueOnce(makeTaskQueuedResponse('task-2'));
    mockGet
      .mockResolvedValueOnce(makeStatusResponse('completed'))
      .mockResolvedValueOnce(makeResultResponse(FAKE_BASE64));

    await runWithTimers(() => dataApi.export({ lensId: 'lens-abc' }));

    expect(mockPost).toHaveBeenCalledWith(
      '/exports',
      {},
      expect.objectContaining({
        params: expect.objectContaining({ lens_id: 'lens-abc' }),
      }),
    );
  });

  it('respects explicit options (includeEmbeddings true, includeTemplates false)', async () => {
    mockPost.mockResolvedValueOnce(makeTaskQueuedResponse('task-3'));
    mockGet
      .mockResolvedValueOnce(makeStatusResponse('completed'))
      .mockResolvedValueOnce(makeResultResponse(FAKE_BASE64));

    await runWithTimers(() => dataApi.export({ includeEmbeddings: true, includeTemplates: false }));

    expect(mockPost).toHaveBeenCalledWith(
      '/exports',
      {},
      expect.objectContaining({
        params: expect.objectContaining({
          include_embeddings: true,
          include_templates: false,
        }),
      }),
    );
  });

  it('throws with server error message when status is failed', async () => {
    mockPost.mockResolvedValueOnce(makeTaskQueuedResponse('task-err'));
    mockGet.mockResolvedValueOnce(makeStatusResponse('failed', 'disk full'));

    const err = await rejectWithTimers(() => dataApi.export());
    expect(err).toBeInstanceOf(Error);
    expect((err as Error).message).toBe('disk full');
  });

  it('throws generic error when status is failed with no error message', async () => {
    mockPost.mockResolvedValueOnce(makeTaskQueuedResponse('task-noerr'));
    mockGet.mockResolvedValueOnce(makeStatusResponse('failed'));

    const err = await rejectWithTimers(() => dataApi.export());
    expect((err as Error).message).toBe('Export failed');
  });

  it('throws timeout error when EXPORT_MAX_ATTEMPTS exhausted', async () => {
    mockPost.mockResolvedValueOnce(makeTaskQueuedResponse('task-timeout'));
    // Always returns 'running' — never completes.
    mockGet.mockResolvedValue(makeStatusResponse('running'));

    const err = await rejectWithTimers(() => dataApi.export());
    expect((err as Error).message).toBe('Export timeout - operation did not complete in time');
  });

  it('throws AbortError when signal is already aborted before first poll', async () => {
    mockPost.mockResolvedValueOnce(makeTaskQueuedResponse('task-abort'));
    const ctrl = new AbortController();
    ctrl.abort();

    const err = await rejectWithTimers(() => dataApi.export({}, ctrl.signal));
    expect(err).toBeInstanceOf(DOMException);
    expect((err as DOMException).name).toBe('AbortError');
  });

  it('decodes base64 content correctly into Blob bytes', async () => {
    // Encode 3 known bytes
    const knownBytes = new Uint8Array([0x50, 0x4b, 0x03]); // "PK\x03" (ZIP magic)
    let binaryStr = '';
    for (const b of knownBytes) binaryStr += String.fromCharCode(b);
    const b64 = btoa(binaryStr);

    mockPost.mockResolvedValueOnce(makeTaskQueuedResponse('task-bytes'));
    mockGet
      .mockResolvedValueOnce(makeStatusResponse('completed'))
      .mockResolvedValueOnce(makeResultResponse(b64));

    const blob = await runWithTimers(() => dataApi.export());
    const buf = await blob.arrayBuffer();
    const result = new Uint8Array(buf);

    expect(result[0]).toBe(0x50);
    expect(result[1]).toBe(0x4b);
    expect(result[2]).toBe(0x03);
  });

  it('polls the correct task-status and result URLs', async () => {
    mockPost.mockResolvedValueOnce(makeTaskQueuedResponse('task-urls'));
    mockGet
      .mockResolvedValueOnce(makeStatusResponse('completed'))
      .mockResolvedValueOnce(makeResultResponse(FAKE_BASE64));

    await runWithTimers(() => dataApi.export());

    expect(mockGet).toHaveBeenNthCalledWith(1, '/queue/tasks/task-urls', expect.any(Object));
    expect(mockGet).toHaveBeenNthCalledWith(2, '/queue/tasks/task-urls/result', expect.any(Object));
  });
});

// ---------------------------------------------------------------------------
// dataApi.exportBySource
// ---------------------------------------------------------------------------
describe('dataApi.exportBySource', () => {
  it('POSTs sourceIds array to /exports/by_sources and returns a Blob on completion', async () => {
    mockPost.mockResolvedValueOnce(makeTaskQueuedResponse('by-src-1'));
    mockGet
      .mockResolvedValueOnce(makeStatusResponse('completed'))
      .mockResolvedValueOnce(makeResultResponse(FAKE_BASE64));

    const sourceIds = ['src-a', 'src-b'];
    const blob = await runWithTimers(() => dataApi.exportBySource(sourceIds));

    expect(mockPost).toHaveBeenCalledWith(
      '/exports/by_sources',
      sourceIds,
      expect.objectContaining({
        params: { include_templates: true, include_embeddings: false },
      }),
    );
    expect(blob).toBeInstanceOf(Blob);
    expect(blob.type).toBe('application/zip');
  });

  it('throws with server error when status is failed', async () => {
    mockPost.mockResolvedValueOnce(makeTaskQueuedResponse('by-src-err'));
    mockGet.mockResolvedValueOnce(makeStatusResponse('failed', 'source not found'));

    const err = await rejectWithTimers(() => dataApi.exportBySource(['s1']));
    expect((err as Error).message).toBe('source not found');
  });

  it('throws generic Export failed when status is failed with no message', async () => {
    mockPost.mockResolvedValueOnce(makeTaskQueuedResponse('by-src-noerr'));
    mockGet.mockResolvedValueOnce(makeStatusResponse('failed'));

    const err = await rejectWithTimers(() => dataApi.exportBySource(['s1']));
    expect((err as Error).message).toBe('Export failed');
  });

  it('throws timeout error when EXPORT_MAX_ATTEMPTS exhausted', async () => {
    mockPost.mockResolvedValueOnce(makeTaskQueuedResponse('by-src-timeout'));
    mockGet.mockResolvedValue(makeStatusResponse('running'));

    const err = await rejectWithTimers(() => dataApi.exportBySource(['s1']));
    expect((err as Error).message).toBe('Export timeout - operation did not complete in time');
  });

  it('throws AbortError when signal is aborted', async () => {
    mockPost.mockResolvedValueOnce(makeTaskQueuedResponse('by-src-abort'));
    const ctrl = new AbortController();
    ctrl.abort();

    const err = await rejectWithTimers(() => dataApi.exportBySource(['s1'], ctrl.signal));
    expect(err).toBeInstanceOf(DOMException);
    expect((err as DOMException).name).toBe('AbortError');
  });

  it('polls multiple times before completion', async () => {
    mockPost.mockResolvedValueOnce(makeTaskQueuedResponse('by-src-multi'));
    mockGet
      .mockResolvedValueOnce(makeStatusResponse('running'))
      .mockResolvedValueOnce(makeStatusResponse('running'))
      .mockResolvedValueOnce(makeStatusResponse('completed'))
      .mockResolvedValueOnce(makeResultResponse(FAKE_BASE64));

    await runWithTimers(() => dataApi.exportBySource(['s1']));

    // 3 status polls + 1 result fetch = 4 GET calls
    expect(mockGet).toHaveBeenCalledTimes(4);
  });
});

// ---------------------------------------------------------------------------
// dataApi.import
// ---------------------------------------------------------------------------
describe('dataApi.import', () => {
  function makeFile(name = 'data.zip', type = 'application/zip'): File {
    return new File(['content'], name, { type });
  }

  it('POSTs FormData to /exports/import with merge=false by default and returns result', async () => {
    mockPost.mockResolvedValueOnce(makeTaskQueuedResponse('imp-1'));
    mockGet
      .mockResolvedValueOnce(makeStatusResponse('completed'))
      .mockResolvedValueOnce({ data: { result: { imported_nodes: 5 } } });

    const file = makeFile();
    const result = await runWithTimers(() => dataApi.import(file));

    expect(mockPost).toHaveBeenCalledWith(
      '/exports/import',
      expect.any(FormData),
      expect.objectContaining({
        params: { merge: false },
        headers: { 'Content-Type': 'multipart/form-data' },
      }),
    );
    expect(result).toEqual({ imported_nodes: 5 });
  });

  it('passes merge=true when specified', async () => {
    mockPost.mockResolvedValueOnce(makeTaskQueuedResponse('imp-merge'));
    mockGet
      .mockResolvedValueOnce(makeStatusResponse('completed'))
      .mockResolvedValueOnce({ data: { result: { merged: true } } });

    const file = makeFile();
    await runWithTimers(() => dataApi.import(file, true));

    expect(mockPost).toHaveBeenCalledWith(
      '/exports/import',
      expect.any(FormData),
      expect.objectContaining({ params: { merge: true } }),
    );
  });

  it('appends the file to FormData under the key "file"', async () => {
    let capturedFormData: FormData | null = null;
    mockPost.mockImplementationOnce((_url: string, body: unknown) => {
      capturedFormData = body as FormData;
      return Promise.resolve(makeTaskQueuedResponse('imp-fd'));
    });
    mockGet
      .mockResolvedValueOnce(makeStatusResponse('completed'))
      .mockResolvedValueOnce({ data: { result: null } });

    const file = makeFile('upload.zip');
    await runWithTimers(() => dataApi.import(file));

    expect(capturedFormData).not.toBeNull();
    const formFile = capturedFormData!.get('file');
    expect(formFile).toBeInstanceOf(File);
    expect((formFile as File).name).toBe('upload.zip');
  });

  it('throws with server error message when import task fails', async () => {
    mockPost.mockResolvedValueOnce(makeTaskQueuedResponse('imp-err'));
    mockGet.mockResolvedValueOnce(makeStatusResponse('failed', 'bad archive'));

    const err = await rejectWithTimers(() => dataApi.import(makeFile()));
    expect((err as Error).message).toBe('bad archive');
  });

  it('throws generic Import failed when failed with no error message', async () => {
    mockPost.mockResolvedValueOnce(makeTaskQueuedResponse('imp-noerr'));
    mockGet.mockResolvedValueOnce(makeStatusResponse('failed'));

    const err = await rejectWithTimers(() => dataApi.import(makeFile()));
    expect((err as Error).message).toBe('Import failed');
  });

  it('throws timeout error when IMPORT_MAX_ATTEMPTS exhausted', async () => {
    mockPost.mockResolvedValueOnce(makeTaskQueuedResponse('imp-timeout'));
    mockGet.mockResolvedValue(makeStatusResponse('running'));

    const err = await rejectWithTimers(() => dataApi.import(makeFile()));
    expect((err as Error).message).toBe('Import timeout - operation did not complete in time');
  });

  it('throws AbortError when signal is already aborted', async () => {
    mockPost.mockResolvedValueOnce(makeTaskQueuedResponse('imp-abort'));
    const ctrl = new AbortController();
    ctrl.abort();

    const err = await rejectWithTimers(() => dataApi.import(makeFile(), false, ctrl.signal));
    expect(err).toBeInstanceOf(DOMException);
    expect((err as DOMException).name).toBe('AbortError');
  });

  it('polls using the correct task-status URL', async () => {
    mockPost.mockResolvedValueOnce(makeTaskQueuedResponse('imp-url'));
    mockGet
      .mockResolvedValueOnce(makeStatusResponse('completed'))
      .mockResolvedValueOnce({ data: { result: {} } });

    await runWithTimers(() => dataApi.import(makeFile()));

    expect(mockGet).toHaveBeenNthCalledWith(1, '/queue/tasks/imp-url', expect.any(Object));
    expect(mockGet).toHaveBeenNthCalledWith(2, '/queue/tasks/imp-url/result', expect.any(Object));
  });
});
