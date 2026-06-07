// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../client', () => ({
  apiClient: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  },
}));

vi.mock('../../../utils/logger', () => ({
  logger: {
    error: vi.fn(),
    info: vi.fn(),
    warn: vi.fn(),
  },
}));

import { apiClient } from '../client';
import { logger } from '../../../utils/logger';
import { sourceProcessingApi } from '../sourceProcessing';
import type {
  SourceChunk,
  SourceChunkListResponse,
  ExtractionTask,
  ExtractionTaskListResponse,
  ExtractionTaskStats,
  ExtractionChartTask,
  FilteringLog,
  SourceStats,
  PaginatedSourcesResponse,
  SourceSummary,
} from '../../../types';
import type { ExtractionDomain, ChunkRerunResponse, ChunkAttemptsListResponse, ChunkAttemptDetail } from '../sourceProcessing';

// ============================================================================
// Helpers
// ============================================================================

const mockGet = apiClient.get as ReturnType<typeof vi.fn>;
const mockPost = apiClient.post as ReturnType<typeof vi.fn>;
const mockDelete = apiClient.delete as ReturnType<typeof vi.fn>;

function makeResponse<T>(data: T) {
  return { data, status: 200, headers: new Headers() };
}

// ============================================================================
// Minimal fixture factories
// ============================================================================

function makeChunk(override?: Partial<SourceChunk>): SourceChunk {
  return {
    id: 'chunk-1',
    source_id: 'src-1',
    chunk_index: 0,
    content: 'hello',
    status: 'extracted',
    created_at: '2026-01-01T00:00:00Z',
    ...override,
  } as unknown as SourceChunk;
}

function makeSourceSummary(override?: Partial<SourceSummary>): SourceSummary {
  return {
    id: 'src-1',
    title: 'Test Source',
    filename: 'test.pdf',
    status: 'committed',
    created_at: '2026-01-01T00:00:00Z',
    source_type: 'pdf',
    file_type: 'pdf',
    file_size: 1024,
    chunk_count: 5,
    enabled: true,
    ...override,
  } as unknown as SourceSummary;
}

// ============================================================================
// getChunks
// ============================================================================

describe('sourceProcessingApi.getChunks', () => {
  beforeEach(() => vi.clearAllMocks());

  it('calls GET /sources/{id}/chunks and returns response.data', async () => {
    const payload: SourceChunkListResponse = { data: [makeChunk()], total: 1 } as unknown as SourceChunkListResponse;
    mockGet.mockResolvedValue(makeResponse(payload));

    const result = await sourceProcessingApi.getChunks('src-1');

    expect(mockGet).toHaveBeenCalledWith('/sources/src-1/chunks', { params: undefined });
    expect(result).toEqual(payload);
  });

  it('passes pagination params to the request', async () => {
    const payload: SourceChunkListResponse = { data: [], total: 0 } as unknown as SourceChunkListResponse;
    mockGet.mockResolvedValue(makeResponse(payload));

    await sourceProcessingApi.getChunks('src-2', { page: 2, page_size: 20, status: 'extracted' });

    expect(mockGet).toHaveBeenCalledWith('/sources/src-2/chunks', {
      params: { page: 2, page_size: 20, status: 'extracted' },
    });
  });
});

// ============================================================================
// getChunk
// ============================================================================

describe('sourceProcessingApi.getChunk', () => {
  beforeEach(() => vi.clearAllMocks());

  it('calls GET /sources/{id}/chunks/{chunkId} and returns response.data', async () => {
    const chunk = makeChunk();
    mockGet.mockResolvedValue(makeResponse(chunk));

    const result = await sourceProcessingApi.getChunk('src-1', 'chunk-1');

    expect(mockGet).toHaveBeenCalledWith('/sources/src-1/chunks/chunk-1');
    expect(result).toEqual(chunk);
  });
});

// ============================================================================
// getExtractionTasks
// ============================================================================

describe('sourceProcessingApi.getExtractionTasks', () => {
  beforeEach(() => vi.clearAllMocks());

  it('calls GET /sources/{id}/extraction/tasks and returns response.data', async () => {
    const payload: ExtractionTaskListResponse = { data: [] } as unknown as ExtractionTaskListResponse;
    mockGet.mockResolvedValue(makeResponse(payload));

    const result = await sourceProcessingApi.getExtractionTasks('src-1');

    expect(mockGet).toHaveBeenCalledWith('/sources/src-1/extraction/tasks', { params: undefined });
    expect(result).toEqual(payload);
  });

  it('passes params when provided', async () => {
    const payload: ExtractionTaskListResponse = { data: [] } as unknown as ExtractionTaskListResponse;
    mockGet.mockResolvedValue(makeResponse(payload));

    await sourceProcessingApi.getExtractionTasks('src-1', { page: 1, page_size: 10, include_content: true });

    expect(mockGet).toHaveBeenCalledWith('/sources/src-1/extraction/tasks', {
      params: { page: 1, page_size: 10, include_content: true },
    });
  });
});

// ============================================================================
// getExtractionTask
// ============================================================================

describe('sourceProcessingApi.getExtractionTask', () => {
  beforeEach(() => vi.clearAllMocks());

  it('calls GET /sources/{id}/extraction/tasks/{taskId} and returns response.data', async () => {
    const task: ExtractionTask = { id: 'task-1', source_id: 'src-1', status: 'completed' } as unknown as ExtractionTask;
    mockGet.mockResolvedValue(makeResponse(task));

    const result = await sourceProcessingApi.getExtractionTask('src-1', 'task-1');

    expect(mockGet).toHaveBeenCalledWith('/sources/src-1/extraction/tasks/task-1');
    expect(result).toEqual(task);
  });
});

// ============================================================================
// getExtractionTaskStats
// ============================================================================

describe('sourceProcessingApi.getExtractionTaskStats', () => {
  beforeEach(() => vi.clearAllMocks());

  it('calls GET /sources/{id}/extraction/stats and returns response.data', async () => {
    const stats: ExtractionTaskStats = { total: 5, completed: 4 } as unknown as ExtractionTaskStats;
    mockGet.mockResolvedValue(makeResponse(stats));

    const result = await sourceProcessingApi.getExtractionTaskStats('src-1');

    expect(mockGet).toHaveBeenCalledWith('/sources/src-1/extraction/stats');
    expect(result).toEqual(stats);
  });
});

// ============================================================================
// getExtractionTasksForCharts
// ============================================================================

describe('sourceProcessingApi.getExtractionTasksForCharts', () => {
  beforeEach(() => vi.clearAllMocks());

  it('calls GET /sources/{id}/extraction/charts and returns response.data', async () => {
    const tasks: ExtractionChartTask[] = [{ id: 'chart-1' } as unknown as ExtractionChartTask];
    mockGet.mockResolvedValue(makeResponse(tasks));

    const result = await sourceProcessingApi.getExtractionTasksForCharts('src-1');

    expect(mockGet).toHaveBeenCalledWith('/sources/src-1/extraction/charts');
    expect(result).toEqual(tasks);
  });
});

// ============================================================================
// getExtractionFilteringLog
// ============================================================================

describe('sourceProcessingApi.getExtractionFilteringLog', () => {
  beforeEach(() => vi.clearAllMocks());

  it('calls GET /sources/{id}/extraction/filteringlog and returns response.data', async () => {
    const log: FilteringLog = { entries: [] } as unknown as FilteringLog;
    mockGet.mockResolvedValue(makeResponse(log));

    const result = await sourceProcessingApi.getExtractionFilteringLog('src-1');

    expect(mockGet).toHaveBeenCalledWith('/sources/src-1/extraction/filteringlog');
    expect(result).toEqual(log);
  });

  it('returns null when the request throws (error branch)', async () => {
    mockGet.mockRejectedValue(new Error('Not Found'));

    const result = await sourceProcessingApi.getExtractionFilteringLog('src-1');

    expect(result).toBeNull();
  });
});

// ============================================================================
// getStats
// ============================================================================

describe('sourceProcessingApi.getStats', () => {
  beforeEach(() => vi.clearAllMocks());

  it('calls GET /sources/{id}/stats and returns response.data', async () => {
    const stats: SourceStats = { total_chunks: 10 } as unknown as SourceStats;
    mockGet.mockResolvedValue(makeResponse(stats));

    const result = await sourceProcessingApi.getStats('src-1');

    expect(mockGet).toHaveBeenCalledWith('/sources/src-1/stats');
    expect(result).toEqual(stats);
  });
});

// ============================================================================
// listDomains
// ============================================================================

describe('sourceProcessingApi.listDomains', () => {
  beforeEach(() => vi.clearAllMocks());

  it('calls GET /sources/domains and returns the domains array', async () => {
    const domains: ExtractionDomain[] = [
      { name: 'general', description: 'General', builtin: true },
    ];
    mockGet.mockResolvedValue(makeResponse({ domains }));

    const result = await sourceProcessingApi.listDomains();

    expect(mockGet).toHaveBeenCalledWith('/sources/domains');
    expect(result).toEqual(domains);
  });
});

// ============================================================================
// importUrl
// ============================================================================

describe('sourceProcessingApi.importUrl', () => {
  beforeEach(() => vi.clearAllMocks());

  it('posts to /sources/url with defaults and returns response.data', async () => {
    const source = { id: 'src-1', status: 'pending' };
    mockPost.mockResolvedValue(makeResponse(source));

    const result = await sourceProcessingApi.importUrl('https://example.com');

    expect(mockPost).toHaveBeenCalledWith('/sources/url', {
      url: 'https://example.com',
      extract_entities: true,
      analysis_depth: 'full',
      enable_normalization: false,
      domain: null,
      filtering_mode: null,
      content_filtering: true,
      skip_duplicates: false,
    });
    expect(result).toEqual(source);
  });

  it('uses provided domain and filteringMode when given', async () => {
    const source = { id: 'src-2', status: 'pending' };
    mockPost.mockResolvedValue(makeResponse(source));

    await sourceProcessingApi.importUrl(
      'https://example.com',
      false,
      'quick',
      true,
      'science',
      'strict',
      false,
      true,
    );

    expect(mockPost).toHaveBeenCalledWith('/sources/url', {
      url: 'https://example.com',
      extract_entities: false,
      analysis_depth: 'quick',
      enable_normalization: true,
      domain: 'science',
      filtering_mode: 'strict',
      content_filtering: false,
      skip_duplicates: true,
    });
  });

  it('sends null for empty domain and filteringMode', async () => {
    const source = { id: 'src-3', status: 'pending' };
    mockPost.mockResolvedValue(makeResponse(source));

    await sourceProcessingApi.importUrl('https://example.com', true, 'full', false, '', '');

    const callArg = mockPost.mock.calls[0][1] as Record<string, unknown>;
    expect(callArg.domain).toBeNull();
    expect(callArg.filtering_mode).toBeNull();
  });
});

// ============================================================================
// upload
// ============================================================================

describe('sourceProcessingApi.upload', () => {
  beforeEach(() => vi.clearAllMocks());

  it('posts FormData to /sources and returns response.data', async () => {
    const source = { id: 'src-1', status: 'pending' };
    mockPost.mockResolvedValue(makeResponse(source));

    const file = new File(['content'], 'test.pdf', { type: 'application/pdf' });
    const result = await sourceProcessingApi.upload(file);

    expect(mockPost).toHaveBeenCalledTimes(1);
    const [url, body, config] = mockPost.mock.calls[0] as [string, FormData, Record<string, unknown>];
    expect(url).toBe('/sources');
    expect(body).toBeInstanceOf(FormData);
    expect((body as FormData).get('file')).toBe(file);
    expect((body as FormData).get('extract_entities')).toBe('true');
    expect((body as FormData).get('analysis_depth')).toBe('full');
    expect((body as FormData).get('enable_normalization')).toBe('false');
    expect((body as FormData).get('content_filtering')).toBe('true');
    expect((config as Record<string, unknown>).timeout).toBe(120_000);
    expect(result).toEqual(source);
  });

  it('appends domain and filteringMode when provided', async () => {
    mockPost.mockResolvedValue(makeResponse({ id: 'src-2' }));
    const file = new File(['x'], 'a.txt', { type: 'text/plain' });

    await sourceProcessingApi.upload(file, true, 'full', false, 'science', undefined, null, 'strict');

    const body = mockPost.mock.calls[0][1] as FormData;
    expect(body.get('domain')).toBe('science');
    expect(body.get('filtering_mode')).toBe('strict');
  });

  it('appends enable_vision when provided', async () => {
    mockPost.mockResolvedValue(makeResponse({ id: 'src-3' }));
    const file = new File(['x'], 'a.pdf', { type: 'application/pdf' });

    await sourceProcessingApi.upload(file, true, 'full', false, undefined, undefined, true);

    const body = mockPost.mock.calls[0][1] as FormData;
    expect(body.get('enable_vision')).toBe('true');
  });

  it('does NOT append enable_vision when it is null', async () => {
    mockPost.mockResolvedValue(makeResponse({ id: 'src-4' }));
    const file = new File(['x'], 'a.pdf', { type: 'application/pdf' });

    await sourceProcessingApi.upload(file, true, 'full', false, undefined, undefined, null);

    const body = mockPost.mock.calls[0][1] as FormData;
    expect(body.get('enable_vision')).toBeNull();
  });

  it('appends skip_duplicates when true', async () => {
    mockPost.mockResolvedValue(makeResponse({ id: 'src-5' }));
    const file = new File(['x'], 'a.pdf', { type: 'application/pdf' });

    await sourceProcessingApi.upload(
      file, true, 'full', false, undefined, undefined, null, undefined, undefined, true, true,
    );

    const body = mockPost.mock.calls[0][1] as FormData;
    expect(body.get('skip_duplicates')).toBe('true');
  });

  it('does NOT append skip_duplicates when false', async () => {
    mockPost.mockResolvedValue(makeResponse({ id: 'src-6' }));
    const file = new File(['x'], 'a.pdf', { type: 'application/pdf' });

    await sourceProcessingApi.upload(file);

    const body = mockPost.mock.calls[0][1] as FormData;
    expect(body.get('skip_duplicates')).toBeNull();
  });
});

// ============================================================================
// uploadBatch
// ============================================================================

describe('sourceProcessingApi.uploadBatch', () => {
  beforeEach(() => vi.clearAllMocks());

  it('posts FormData to /sources/batch and returns response.data', async () => {
    const batchResult = { uploaded: 2, failed: 0, files: [], errors: [] };
    mockPost.mockResolvedValue(makeResponse(batchResult));

    const files = [
      new File(['a'], 'a.pdf', { type: 'application/pdf' }),
      new File(['b'], 'b.pdf', { type: 'application/pdf' }),
    ];
    const result = await sourceProcessingApi.uploadBatch(files);

    expect(mockPost).toHaveBeenCalledTimes(1);
    const [url, body, config] = mockPost.mock.calls[0] as [string, FormData, Record<string, unknown>];
    expect(url).toBe('/sources/batch');
    expect(body).toBeInstanceOf(FormData);
    expect((config as Record<string, unknown>).timeout).toBe(300_000);
    expect(result).toEqual(batchResult);
  });

  it('appends domain, vision, filteringMode, skipDuplicates when provided', async () => {
    mockPost.mockResolvedValue(makeResponse({ uploaded: 1, failed: 0, files: [], errors: [] }));
    const files = [new File(['x'], 'x.pdf', { type: 'application/pdf' })];

    await sourceProcessingApi.uploadBatch(files, true, 'quick', true, 'science', true, 'strict', false, true);

    const body = mockPost.mock.calls[0][1] as FormData;
    expect(body.get('domain')).toBe('science');
    expect(body.get('enable_vision')).toBe('true');
    expect(body.get('filtering_mode')).toBe('strict');
    expect(body.get('skip_duplicates')).toBe('true');
  });
});

// ============================================================================
// cancelProcessing
// ============================================================================

describe('sourceProcessingApi.cancelProcessing', () => {
  beforeEach(() => vi.clearAllMocks());

  it('calls DELETE /sources/{id}/processing and returns { success: true }', async () => {
    mockDelete.mockResolvedValue(makeResponse(null));

    const result = await sourceProcessingApi.cancelProcessing('src-1');

    expect(mockDelete).toHaveBeenCalledWith('/sources/src-1/processing');
    expect(result).toEqual({ success: true });
  });
});

// ============================================================================
// getExtraction
// ============================================================================

describe('sourceProcessingApi.getExtraction', () => {
  beforeEach(() => vi.clearAllMocks());

  it('calls GET /sources/{id}/extraction and returns response.data', async () => {
    const status = {
      timing: { estimated_remaining_seconds: 30, elapsed_seconds: 10, avg_chunk_time_seconds: 2 },
    };
    mockGet.mockResolvedValue(makeResponse(status));

    const result = await sourceProcessingApi.getExtraction('src-1');

    expect(mockGet).toHaveBeenCalledWith('/sources/src-1/extraction');
    expect(result).toEqual(status);
  });
});

// ============================================================================
// getEntities
// ============================================================================

describe('sourceProcessingApi.getEntities', () => {
  beforeEach(() => vi.clearAllMocks());

  it('calls GET /sources/{id}/entities with default pagination params', async () => {
    const payload = { entities: [], pagination: { page: 1, total: 0 } };
    mockGet.mockResolvedValue(makeResponse(payload));

    const result = await sourceProcessingApi.getEntities('src-1');

    expect(mockGet).toHaveBeenCalledWith('/sources/src-1/entities', {
      params: { page: 1, per_page: 50, sort_by: 'default', sort_order: 'desc' },
    });
    expect(result).toEqual(payload);
  });

  it('passes custom pagination params', async () => {
    mockGet.mockResolvedValue(makeResponse({ entities: [], pagination: {} }));

    await sourceProcessingApi.getEntities('src-1', 3, 10, 'name', 'asc');

    expect(mockGet).toHaveBeenCalledWith('/sources/src-1/entities', {
      params: { page: 3, per_page: 10, sort_by: 'name', sort_order: 'asc' },
    });
  });
});

// ============================================================================
// getRelationships
// ============================================================================

describe('sourceProcessingApi.getRelationships', () => {
  beforeEach(() => vi.clearAllMocks());

  it('calls GET /sources/{id}/relationships with default page params', async () => {
    const payload = { relationships: [], pagination: { page: 1, total: 0 } };
    mockGet.mockResolvedValue(makeResponse(payload));

    const result = await sourceProcessingApi.getRelationships('src-1');

    expect(mockGet).toHaveBeenCalledWith('/sources/src-1/relationships', {
      params: { page: 1, per_page: 50 },
    });
    expect(result).toEqual(payload);
  });

  it('passes custom page params', async () => {
    mockGet.mockResolvedValue(makeResponse({ relationships: [], pagination: {} }));

    await sourceProcessingApi.getRelationships('src-1', 2, 25);

    expect(mockGet).toHaveBeenCalledWith('/sources/src-1/relationships', {
      params: { page: 2, per_page: 25 },
    });
  });
});

// ============================================================================
// getTemplates
// ============================================================================

describe('sourceProcessingApi.getTemplates', () => {
  beforeEach(() => vi.clearAllMocks());

  it('calls GET /sources/{id}/templates with default pagination', async () => {
    const payload = { templates: [], pagination: { page: 1, total: 0 } };
    mockGet.mockResolvedValue(makeResponse(payload));

    const result = await sourceProcessingApi.getTemplates('src-1');

    expect(mockGet).toHaveBeenCalledWith('/sources/src-1/templates', {
      params: { page: 1, per_page: 50 },
    });
    expect(result).toEqual(payload);
  });
});

// ============================================================================
// listUnified
// ============================================================================

describe('sourceProcessingApi.listUnified', () => {
  beforeEach(() => vi.clearAllMocks());

  it('returns mapped UnifiedSources sorted by created_at descending', async () => {
    const older = makeSourceSummary({ id: 'src-old', created_at: '2026-01-01T00:00:00Z' });
    const newer = makeSourceSummary({ id: 'src-new', created_at: '2026-06-01T00:00:00Z' });
    const paginatedResponse: PaginatedSourcesResponse = {
      data: [older, newer],
      total: 2,
    } as unknown as PaginatedSourcesResponse;

    mockGet.mockResolvedValue(makeResponse(paginatedResponse));

    const result = await sourceProcessingApi.listUnified();

    expect(mockGet).toHaveBeenCalledWith('/sources', {
      params: {
        search: undefined,
        source_type: undefined,
        page_size: 200,
      },
    });
    expect(result[0].id).toBe('src-new');
    expect(result[1].id).toBe('src-old');
  });

  it('maps committed sources to stage=active', async () => {
    const committed = makeSourceSummary({ id: 'src-c', status: 'committed' });
    const paginatedResponse: PaginatedSourcesResponse = {
      data: [committed],
      total: 1,
    } as unknown as PaginatedSourcesResponse;

    mockGet.mockResolvedValue(makeResponse(paginatedResponse));

    const result = await sourceProcessingApi.listUnified();

    expect(result[0].stage).toBe('active');
  });

  it('maps indexing sources to stage=processing', async () => {
    const indexing = makeSourceSummary({ id: 'src-i', status: 'indexing' });
    const paginatedResponse: PaginatedSourcesResponse = {
      data: [indexing],
      total: 1,
    } as unknown as PaginatedSourcesResponse;

    mockGet.mockResolvedValue(makeResponse(paginatedResponse));

    const result = await sourceProcessingApi.listUnified();

    expect(result[0].stage).toBe('processing');
  });

  it('maps pending sources to stage=queued', async () => {
    const pending = makeSourceSummary({ id: 'src-p', status: 'pending' });
    const paginatedResponse: PaginatedSourcesResponse = {
      data: [pending],
      total: 1,
    } as unknown as PaginatedSourcesResponse;

    mockGet.mockResolvedValue(makeResponse(paginatedResponse));

    const result = await sourceProcessingApi.listUnified();

    expect(result[0].stage).toBe('queued');
  });

  it('filters by stage when stage param is provided', async () => {
    const committed = makeSourceSummary({ id: 'src-c', status: 'committed' });
    const indexing = makeSourceSummary({ id: 'src-i', status: 'indexing' });
    const paginatedResponse: PaginatedSourcesResponse = {
      data: [committed, indexing],
      total: 2,
    } as unknown as PaginatedSourcesResponse;

    mockGet.mockResolvedValue(makeResponse(paginatedResponse));

    const result = await sourceProcessingApi.listUnified({ stage: 'active' });

    expect(result).toHaveLength(1);
    expect(result[0].id).toBe('src-c');
  });

  it('does not filter when stage=all', async () => {
    const committed = makeSourceSummary({ id: 'src-c', status: 'committed' });
    const indexing = makeSourceSummary({ id: 'src-i', status: 'indexing' });
    const paginatedResponse: PaginatedSourcesResponse = {
      data: [committed, indexing],
      total: 2,
    } as unknown as PaginatedSourcesResponse;

    mockGet.mockResolvedValue(makeResponse(paginatedResponse));

    const result = await sourceProcessingApi.listUnified({ stage: 'all' });

    expect(result).toHaveLength(2);
  });

  it('passes search and source_type params to GET /sources', async () => {
    mockGet.mockResolvedValue(makeResponse({ data: [], total: 0 }));

    await sourceProcessingApi.listUnified({ search: 'test', source_type: 'pdf' });

    expect(mockGet).toHaveBeenCalledWith('/sources', {
      params: {
        search: 'test',
        source_type: 'pdf',
        page_size: 200,
      },
    });
  });

  it('returns empty array and calls logger.error when the request throws', async () => {
    const err = new Error('Network failure');
    mockGet.mockRejectedValue(err);

    const result = await sourceProcessingApi.listUnified();

    expect(result).toEqual([]);
    expect(logger.error).toHaveBeenCalledWith('Failed to fetch sources:', err);
  });

  it('populates active fields for committed source (isActive branch)', async () => {
    const committed = makeSourceSummary({
      id: 'src-c',
      status: 'committed',
      extraction_entities_count: 10,
      extraction_relationships_count: 5,
      commit_nodes_created: 3,
    } as unknown as SourceSummary);
    mockGet.mockResolvedValue(makeResponse({ data: [committed], total: 1 }));

    const [unified] = await sourceProcessingApi.listUnified();

    expect(unified.active).toBeDefined();
    expect(unified.ingestion).toBeUndefined();
  });

  it('populates ingestion fields for non-committed source (isActive=false branch)', async () => {
    const indexing = makeSourceSummary({ id: 'src-i', status: 'indexing' });
    mockGet.mockResolvedValue(makeResponse({ data: [indexing], total: 1 }));

    const [unified] = await sourceProcessingApi.listUnified();

    expect(unified.ingestion).toBeDefined();
    expect(unified.active).toBeUndefined();
  });
});

// ============================================================================
// retrySource
// ============================================================================

describe('sourceProcessingApi.retrySource', () => {
  beforeEach(() => vi.clearAllMocks());

  it('calls POST /sources/{id}/retry and returns response.data', async () => {
    const source = { id: 'src-1', status: 'pending' };
    mockPost.mockResolvedValue(makeResponse(source));

    const result = await sourceProcessingApi.retrySource('src-1');

    expect(mockPost).toHaveBeenCalledWith('/sources/src-1/retry');
    expect(result).toEqual(source);
  });
});

// ============================================================================
// reextractSource
// ============================================================================

describe('sourceProcessingApi.reextractSource', () => {
  beforeEach(() => vi.clearAllMocks());

  it('calls POST /sources/{id}/re_extract and returns response.data', async () => {
    const source = { id: 'src-1', status: 'extracting' };
    mockPost.mockResolvedValue(makeResponse(source));

    const result = await sourceProcessingApi.reextractSource('src-1');

    expect(mockPost).toHaveBeenCalledWith('/sources/src-1/re_extract');
    expect(result).toEqual(source);
  });
});

// ============================================================================
// triggerExtraction
// ============================================================================

describe('sourceProcessingApi.triggerExtraction', () => {
  beforeEach(() => vi.clearAllMocks());

  it('calls POST /sources/{id}/extraction with default empty options and returns response.data', async () => {
    const resp = { source_id: 'src-1', status: 'extracting' };
    mockPost.mockResolvedValue(makeResponse(resp));

    const result = await sourceProcessingApi.triggerExtraction('src-1');

    expect(mockPost).toHaveBeenCalledWith('/sources/src-1/extraction', {});
    expect(result).toEqual(resp);
  });

  it('passes extraction options when provided', async () => {
    mockPost.mockResolvedValue(makeResponse({ source_id: 'src-1', status: 'extracting' }));

    await sourceProcessingApi.triggerExtraction('src-1', {
      analysis_depth: 'quick',
      domain: 'science',
      force: true,
      filtering_mode: 'strict',
      content_filtering: false,
    });

    expect(mockPost).toHaveBeenCalledWith('/sources/src-1/extraction', {
      analysis_depth: 'quick',
      domain: 'science',
      force: true,
      filtering_mode: 'strict',
      content_filtering: false,
    });
  });
});

// ============================================================================
// getLlmStats
// ============================================================================

describe('sourceProcessingApi.getLlmStats', () => {
  beforeEach(() => vi.clearAllMocks());

  it('calls GET /llm/stats and returns response.data', async () => {
    const stats = { data: { queue_depth: 5 } };
    mockGet.mockResolvedValue(makeResponse(stats));

    const result = await sourceProcessingApi.getLlmStats();

    expect(mockGet).toHaveBeenCalledWith('/llm/stats');
    expect(result).toEqual(stats);
  });
});

// ============================================================================
// rerunChunk
// ============================================================================

describe('sourceProcessingApi.rerunChunk', () => {
  beforeEach(() => vi.clearAllMocks());

  it('calls POST /sources/{id}/chunks/{index}/rerun and returns response.data', async () => {
    const rerunResp: ChunkRerunResponse = {
      chunk_task_id: 'ct-1',
      queue_task_id: 'qt-1',
      attempt_number: 2,
      source_status: 'extracting',
    };
    mockPost.mockResolvedValue(makeResponse(rerunResp));

    const result = await sourceProcessingApi.rerunChunk('src-1', 0);

    expect(mockPost).toHaveBeenCalledWith('/sources/src-1/chunks/0/rerun');
    expect(result).toEqual(rerunResp);
  });
});

// ============================================================================
// listChunkAttempts
// ============================================================================

describe('sourceProcessingApi.listChunkAttempts', () => {
  beforeEach(() => vi.clearAllMocks());

  it('calls GET /sources/{id}/chunks/{index}/attempts and returns response.data', async () => {
    const listResp: ChunkAttemptsListResponse = { data: [] };
    mockGet.mockResolvedValue(makeResponse(listResp));

    const result = await sourceProcessingApi.listChunkAttempts('src-1', 3);

    expect(mockGet).toHaveBeenCalledWith('/sources/src-1/chunks/3/attempts');
    expect(result).toEqual(listResp);
  });
});

// ============================================================================
// getChunkAttempt
// ============================================================================

describe('sourceProcessingApi.getChunkAttempt', () => {
  beforeEach(() => vi.clearAllMocks());

  it('calls GET /sources/{id}/chunks/{index}/attempts/{attemptId} and returns response.data', async () => {
    const detail: ChunkAttemptDetail = {
      id: 'att-1',
      chunk_task_id: 'ct-1',
      attempt_number: 1,
      snapshotted_at: '2026-01-01T00:00:00Z',
      started_at: null,
      completed_at: null,
      entity_count: 0,
      relationship_count: 0,
      invalid_relationship_count: 0,
      finish_reason: null,
      aborted_by_loop: null,
      llm_duration_ms: null,
      input_tokens: null,
      output_tokens: null,
      input_text_length: null,
      llm_response_length: null,
      error_message: null,
      error_type: null,
      input_text: null,
      llm_response_json: null,
      raw_entities: null,
      raw_relationships: null,
      filtering_log: null,
      chunk_sentences: null,
    };
    mockGet.mockResolvedValue(makeResponse(detail));

    const result = await sourceProcessingApi.getChunkAttempt('src-1', 0, 'att-1');

    expect(mockGet).toHaveBeenCalledWith('/sources/src-1/chunks/0/attempts/att-1');
    expect(result).toEqual(detail);
  });
});

// ============================================================================
// getChunksByIds
// ============================================================================

describe('sourceProcessingApi.getChunksByIds', () => {
  beforeEach(() => vi.clearAllMocks());

  it('calls GET /sources/{id}/chunks/batch with ids joined by comma and returns response.data', async () => {
    const payload = { chunks: [{ id: 'c1', content: 'hello', chunk_index: 0 }] };
    mockGet.mockResolvedValue(makeResponse(payload));

    const result = await sourceProcessingApi.getChunksByIds('src-1', ['c1', 'c2', 'c3']);

    expect(mockGet).toHaveBeenCalledWith('/sources/src-1/chunks/batch', {
      params: { ids: 'c1,c2,c3' },
    });
    expect(result).toEqual(payload);
  });

  it('handles empty id array by sending empty string', async () => {
    mockGet.mockResolvedValue(makeResponse({ chunks: [] }));

    await sourceProcessingApi.getChunksByIds('src-1', []);

    expect(mockGet).toHaveBeenCalledWith('/sources/src-1/chunks/batch', {
      params: { ids: '' },
    });
  });
});

// ============================================================================
// bulkConfirmExtraction
// ============================================================================

describe('sourceProcessingApi.bulkConfirmExtraction', () => {
  beforeEach(() => vi.clearAllMocks());

  it('POSTs { source_ids } to /sources/confirmation and returns the BulkConfirmExtractionResponse envelope', async () => {
    const responsePayload = {
      confirmed: 2,
      failed: 1,
      results: [
        { source_id: 'src-1', ok: true, error: null },
        { source_id: 'src-2', ok: true, error: null },
        { source_id: 'src-3', ok: false, error: 'source not awaiting confirmation' },
      ],
    };
    mockPost.mockResolvedValue(makeResponse(responsePayload));

    const result = await sourceProcessingApi.bulkConfirmExtraction(['src-1', 'src-2', 'src-3']);

    expect(mockPost).toHaveBeenCalledWith('/sources/confirmation', {
      source_ids: ['src-1', 'src-2', 'src-3'],
    });
    expect(result.confirmed).toBe(2);
    expect(result.failed).toBe(1);
    expect(result.results).toHaveLength(3);
    expect(result.results[0]).toEqual({ source_id: 'src-1', ok: true, error: null });
    expect(result.results[2]).toEqual({ source_id: 'src-3', ok: false, error: 'source not awaiting confirmation' });
  });

  it('sends an empty source_ids array when called with no ids', async () => {
    const responsePayload = { confirmed: 0, failed: 0, results: [] };
    mockPost.mockResolvedValue(makeResponse(responsePayload));

    const result = await sourceProcessingApi.bulkConfirmExtraction([]);

    expect(mockPost).toHaveBeenCalledWith('/sources/confirmation', { source_ids: [] });
    expect(result.confirmed).toBe(0);
    expect(result.failed).toBe(0);
    expect(result.results).toHaveLength(0);
  });
});

// ============================================================================
// mapSourceToUnified — awaiting_confirmation (domain confirmation gate)
// ============================================================================

describe('mapSourceToUnified — awaiting_confirmation', () => {
  beforeEach(() => vi.clearAllMocks());

  it('maps an awaiting_confirmation source to a queued stage (NOT bucketed as generic processing) and carries the detection proposal', async () => {
    mockGet.mockResolvedValue(
      makeResponse({
        data: [
          {
            id: 'src-await',
            title: 'paper.pdf',
            status: 'awaiting_confirmation',
            source_type: 'pdf',
            created_at: '2026-05-28T00:00:00Z',
            file_size: 2048,
            confirmation_required: true,
            extraction_confirmed_at: null,
            detection_confidence: 0.82,
            detection_ranking: [
              { domain: 'science', score: 4.2 },
              { domain: 'general', score: 1.1 },
            ],
            proposed_extraction_options: { analysis_depth: 'full', filtering_mode: 'balanced' },
          },
        ],
        pagination: { total: 1, page: 1, page_size: 50, total_pages: 1, has_next: false, has_prev: false },
      }),
    );

    const sources = await sourceProcessingApi.listUnified();

    expect(sources).toHaveLength(1);
    const s = sources[0];
    expect(s.status).toBe('awaiting_confirmation');
    // Not 'processing' — the spinner bucket would mislead. queued is the resting bucket.
    expect(s.stage).toBe('queued');
    expect(s.confirmation_required).toBe(true);
    // extraction_confirmed_at: null in the fixture → preserved as null (not coerced to undefined)
    expect(s.extraction_confirmed_at).toBeNull();
    expect(s.detection_confidence).toBe(0.82);
    expect(s.detection_ranking?.[0]).toEqual({ domain: 'science', score: 4.2 });
    expect(s.proposed_extraction_options?.filtering_mode).toBe('balanced');
  });

  it('flags low_confidence when ranking is empty / fallback', async () => {
    mockGet.mockResolvedValue(
      makeResponse({
        data: [
          {
            id: 'src-lowconf',
            title: 'noise.txt',
            status: 'awaiting_confirmation',
            source_type: 'txt',
            created_at: '2026-05-28T00:00:00Z',
            file_size: 10,
            confirmation_required: true,
            detection_confidence: 0.1,
            detection_low_confidence: true,
            detection_ranking: [],
            extraction_domain: 'generic',
          },
        ],
        pagination: { total: 1, page: 1, page_size: 50, total_pages: 1, has_next: false, has_prev: false },
      }),
    );

    const [s] = await sourceProcessingApi.listUnified();
    expect(s.detection_low_confidence).toBe(true);
    expect(s.detection_ranking).toEqual([]);
  });
});
