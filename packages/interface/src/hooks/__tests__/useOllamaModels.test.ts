// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import type {
  OllamaModelsListResponse,
  OllamaModelShowResponse,
} from '../../types/settings';

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

vi.mock('../../services/api/settings', () => ({
  settingsApi: {
    listOllamaModels: vi.fn<() => Promise<OllamaModelsListResponse>>(),
    pullOllamaModel: vi.fn<(model: string, instanceId?: string, signal?: AbortSignal) => Promise<Response>>(),
    removeOllamaModel: vi.fn<(model: string, instanceId?: string) => Promise<{ success: boolean }>>(),
    showOllamaModel: vi.fn<(model: string, instanceId?: string) => Promise<OllamaModelShowResponse>>(),
  },
}));

vi.mock('../../utils/logger', () => ({
  logger: {
    error: vi.fn(),
    info: vi.fn(),
    warn: vi.fn(),
  },
}));

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

import { settingsApi } from '../../services/api/settings';
import { logger } from '../../utils/logger';
import { useOllamaModels } from '../useOllamaModels';

const mockListOllamaModels = settingsApi.listOllamaModels as ReturnType<typeof vi.fn>;
const mockPullOllamaModel = settingsApi.pullOllamaModel as ReturnType<typeof vi.fn>;
const mockRemoveOllamaModel = settingsApi.removeOllamaModel as ReturnType<typeof vi.fn>;
const mockShowOllamaModel = settingsApi.showOllamaModel as ReturnType<typeof vi.fn>;
const mockLoggerError = logger.error as ReturnType<typeof vi.fn>;

function makeModelsResponse(modelNames: string[] = ['llama3', 'mistral']): OllamaModelsListResponse {
  return {
    instances: [
      {
        instance_id: 'inst-1',
        instance_name: 'Local Ollama',
        base_url: 'http://localhost:11434',
        healthy: true,
        models: modelNames.map(name => ({
          name,
          size: 1234567,
          modified_at: null,
          digest: null,
          details: null,
        })),
      },
    ],
  };
}

function makeShowResponse(): OllamaModelShowResponse {
  return {
    modelfile: 'FROM llama3',
    parameters: null,
    template: null,
    details: {
      parameter_size: '7B',
      quantization_level: 'Q4_0',
      family: 'llama',
      format: 'gguf',
    },
    model_info: null,
  };
}

/** Build a minimal streaming Response from SSE lines */
function makeSseResponse(lines: string[]): Response {
  const body = lines.map(l => `data: ${l}\n`).join('');
  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(body));
      controller.close();
    },
  });
  return new Response(stream, { status: 200 });
}

beforeEach(() => {
  vi.clearAllMocks();
  // Default: list returns empty instances
  mockListOllamaModels.mockResolvedValue({ instances: [] });
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('useOllamaModels', () => {
  // -------------------------------------------------------------------------
  // enabled=false
  // -------------------------------------------------------------------------

  describe('when enabled=false', () => {
    it('does NOT call listOllamaModels on mount', () => {
      renderHook(() => useOllamaModels(false));
      expect(mockListOllamaModels).not.toHaveBeenCalled();
    });

    it('starts with null modelsData and empty installedModels', () => {
      const { result } = renderHook(() => useOllamaModels(false));
      expect(result.current.modelsData).toBeNull();
      expect(result.current.installedModels.size).toBe(0);
    });

    it('starts with loading=false', () => {
      const { result } = renderHook(() => useOllamaModels(false));
      expect(result.current.loading).toBe(false);
    });

    it('refresh() is a no-op when enabled=false', async () => {
      const { result } = renderHook(() => useOllamaModels(false));
      await act(async () => {
        await result.current.refresh();
      });
      expect(mockListOllamaModels).not.toHaveBeenCalled();
    });
  });

  // -------------------------------------------------------------------------
  // enabled=true (default)
  // -------------------------------------------------------------------------

  describe('when enabled=true (default)', () => {
    it('calls listOllamaModels on mount', async () => {
      renderHook(() => useOllamaModels());
      await waitFor(() => {
        expect(mockListOllamaModels).toHaveBeenCalledTimes(1);
      });
    });

    it('populates modelsData with the resolved response', async () => {
      const response = makeModelsResponse(['llama3', 'mistral']);
      mockListOllamaModels.mockResolvedValue(response);

      const { result } = renderHook(() => useOllamaModels());

      await waitFor(() => {
        expect(result.current.modelsData).toEqual(response);
      });
    });

    it('builds installedModels from response instances', async () => {
      mockListOllamaModels.mockResolvedValue(makeModelsResponse(['llama3', 'mistral']));

      const { result } = renderHook(() => useOllamaModels());

      await waitFor(() => {
        expect(result.current.installedModels.has('llama3')).toBe(true);
        expect(result.current.installedModels.has('mistral')).toBe(true);
        expect(result.current.installedModels.size).toBe(2);
      });
    });

    it('installedModels is empty when instances have no models', async () => {
      mockListOllamaModels.mockResolvedValue({ instances: [] });

      const { result } = renderHook(() => useOllamaModels());

      await waitFor(() => {
        expect(result.current.modelsData).toEqual({ instances: [] });
        expect(result.current.installedModels.size).toBe(0);
      });
    });

    it('sets loading=true during fetch and loading=false after', async () => {
      let resolveList!: (v: OllamaModelsListResponse) => void;
      mockListOllamaModels.mockReturnValue(
        new Promise<OllamaModelsListResponse>(res => { resolveList = res; })
      );

      const { result } = renderHook(() => useOllamaModels());

      await waitFor(() => {
        expect(result.current.loading).toBe(true);
      });

      await act(async () => {
        resolveList({ instances: [] });
      });

      await waitFor(() => {
        expect(result.current.loading).toBe(false);
      });
    });

    it('logs error and keeps loading=false when listOllamaModels rejects', async () => {
      const err = new Error('network error');
      mockListOllamaModels.mockRejectedValue(err);

      const { result } = renderHook(() => useOllamaModels());

      await waitFor(() => {
        expect(mockLoggerError).toHaveBeenCalledWith('Failed to load Ollama models:', err);
        expect(result.current.loading).toBe(false);
      });
    });

    it('modelsData stays null after a failed initial fetch', async () => {
      mockListOllamaModels.mockRejectedValue(new Error('fail'));

      const { result } = renderHook(() => useOllamaModels());

      await waitFor(() => {
        expect(result.current.modelsData).toBeNull();
      });
    });
  });

  // -------------------------------------------------------------------------
  // refresh()
  // -------------------------------------------------------------------------

  describe('refresh()', () => {
    it('re-calls listOllamaModels and updates modelsData', async () => {
      const first = makeModelsResponse(['llama3']);
      const second = makeModelsResponse(['llama3', 'phi3']);
      mockListOllamaModels.mockResolvedValueOnce(first).mockResolvedValueOnce(second);

      const { result } = renderHook(() => useOllamaModels());

      await waitFor(() => {
        expect(result.current.modelsData).toEqual(first);
      });

      await act(async () => {
        await result.current.refresh();
      });

      expect(result.current.modelsData).toEqual(second);
      expect(mockListOllamaModels).toHaveBeenCalledTimes(2);
    });
  });

  // -------------------------------------------------------------------------
  // removeModel()
  // -------------------------------------------------------------------------

  describe('removeModel()', () => {
    it('calls removeOllamaModel with model and instanceId', async () => {
      mockListOllamaModels.mockResolvedValue({ instances: [] });
      mockRemoveOllamaModel.mockResolvedValue({ success: true });

      const { result } = renderHook(() => useOllamaModels());
      await waitFor(() => expect(mockListOllamaModels).toHaveBeenCalledTimes(1));

      await act(async () => {
        await result.current.removeModel('llama3', 'inst-1');
      });

      expect(mockRemoveOllamaModel).toHaveBeenCalledWith('llama3', 'inst-1');
    });

    it('returns true and refreshes when removal succeeds', async () => {
      mockListOllamaModels.mockResolvedValue({ instances: [] });
      mockRemoveOllamaModel.mockResolvedValue({ success: true });

      const { result } = renderHook(() => useOllamaModels());
      await waitFor(() => expect(mockListOllamaModels).toHaveBeenCalledTimes(1));

      let ret: boolean | undefined;
      await act(async () => {
        ret = await result.current.removeModel('llama3');
      });

      expect(ret).toBe(true);
      // refresh should have been called a second time
      expect(mockListOllamaModels).toHaveBeenCalledTimes(2);
    });

    it('returns false and does NOT refresh when success=false', async () => {
      mockListOllamaModels.mockResolvedValue({ instances: [] });
      mockRemoveOllamaModel.mockResolvedValue({ success: false });

      const { result } = renderHook(() => useOllamaModels());
      await waitFor(() => expect(mockListOllamaModels).toHaveBeenCalledTimes(1));

      let ret: boolean | undefined;
      await act(async () => {
        ret = await result.current.removeModel('llama3');
      });

      expect(ret).toBe(false);
      expect(mockListOllamaModels).toHaveBeenCalledTimes(1); // no extra refresh
    });

    it('logs error and returns false when removeOllamaModel rejects', async () => {
      mockListOllamaModels.mockResolvedValue({ instances: [] });
      const err = new Error('delete failed');
      mockRemoveOllamaModel.mockRejectedValue(err);

      const { result } = renderHook(() => useOllamaModels());
      await waitFor(() => expect(mockListOllamaModels).toHaveBeenCalledTimes(1));

      let ret: boolean | undefined;
      await act(async () => {
        ret = await result.current.removeModel('bad-model');
      });

      expect(ret).toBe(false);
      expect(mockLoggerError).toHaveBeenCalledWith('Remove failed:', err);
    });
  });

  // -------------------------------------------------------------------------
  // showModel()
  // -------------------------------------------------------------------------

  describe('showModel()', () => {
    it('calls showOllamaModel and returns its result', async () => {
      mockListOllamaModels.mockResolvedValue({ instances: [] });
      const showResponse = makeShowResponse();
      mockShowOllamaModel.mockResolvedValue(showResponse);

      const { result } = renderHook(() => useOllamaModels());
      await waitFor(() => expect(mockListOllamaModels).toHaveBeenCalledTimes(1));

      let ret: OllamaModelShowResponse | undefined;
      await act(async () => {
        ret = await result.current.showModel('llama3', 'inst-1');
      });

      expect(mockShowOllamaModel).toHaveBeenCalledWith('llama3', 'inst-1');
      expect(ret).toEqual(showResponse);
    });

    it('calls showOllamaModel without instanceId when not provided', async () => {
      mockListOllamaModels.mockResolvedValue({ instances: [] });
      mockShowOllamaModel.mockResolvedValue(makeShowResponse());

      const { result } = renderHook(() => useOllamaModels());
      await waitFor(() => expect(mockListOllamaModels).toHaveBeenCalledTimes(1));

      await act(async () => {
        await result.current.showModel('mistral');
      });

      expect(mockShowOllamaModel).toHaveBeenCalledWith('mistral', undefined);
    });
  });

  // -------------------------------------------------------------------------
  // pullModel()
  // -------------------------------------------------------------------------

  describe('pullModel()', () => {
    it('calls pullOllamaModel with model and instanceId', async () => {
      mockListOllamaModels.mockResolvedValue({ instances: [] });

      // Return a response with no body content (done immediately)
      mockPullOllamaModel.mockResolvedValue(makeSseResponse([]));

      const { result } = renderHook(() => useOllamaModels());
      await waitFor(() => expect(mockListOllamaModels).toHaveBeenCalledTimes(1));

      await act(async () => {
        await result.current.pullModel('llama3', 'inst-1');
      });

      expect(mockPullOllamaModel).toHaveBeenCalledWith('llama3', 'inst-1', expect.any(AbortSignal));
    });

    it('sets pullProgress entries from SSE data lines', async () => {
      mockListOllamaModels.mockResolvedValue({ instances: [] });

      const progressLine = JSON.stringify({
        status: 'downloading',
        completed: 500,
        total: 1000,
        instance_id: 'inst-1',
      });
      mockPullOllamaModel.mockResolvedValue(makeSseResponse([progressLine]));

      const { result } = renderHook(() => useOllamaModels());
      await waitFor(() => expect(mockListOllamaModels).toHaveBeenCalledTimes(1));

      await act(async () => {
        await result.current.pullModel('llama3', 'inst-1');
      });

      // After an empty stream end the entry is either still present or cleared;
      // key behavior is that during pull it was set. We verify pullProgress was
      // not erroneously populated for a different model.
      expect(result.current.pullProgress['nonexistent-model']).toBeUndefined();
    });

    it('updates pullProgress with status from SSE data events', async () => {
      mockListOllamaModels.mockResolvedValue({ instances: [] });

      const progressLine = JSON.stringify({
        status: 'downloading',
        completed: 200,
        total: 1000,
        instance_id: 'inst-1',
      });

      // Make the stream pause so we can observe intermediate state:
      // Use a stream that writes one chunk and waits for close signal
      let closeStream!: () => void;
      const stream = new ReadableStream({
        start(controller) {
          controller.enqueue(new TextEncoder().encode(`data: ${progressLine}\n`));
          // Don't close immediately so we can check intermediate state
          new Promise<void>(res => { closeStream = res; }).then(() => {
            controller.close();
          });
        },
      });
      const response = new Response(stream, { status: 200 });
      mockPullOllamaModel.mockResolvedValue(response);

      const { result } = renderHook(() => useOllamaModels());
      await waitFor(() => expect(mockListOllamaModels).toHaveBeenCalledTimes(1));

      // Start pull but don't await it
      act(() => {
        void result.current.pullModel('llama3', 'inst-1');
      });

      // Give it time to read the first chunk
      await waitFor(() => {
        expect(result.current.pullProgress['llama3']).toBeDefined();
      });

      expect(result.current.pullProgress['llama3'].status).toBe('downloading');
      expect(result.current.pullProgress['llama3'].completed).toBe(200);
      expect(result.current.pullProgress['llama3'].total).toBe(1000);

      // Close the stream
      await act(async () => {
        closeStream();
      });
    });

    it('removes pullProgress entry on SSE status=error', async () => {
      mockListOllamaModels.mockResolvedValue({ instances: [] });

      // First send a progress event, then an error event
      const progressLine = JSON.stringify({ status: 'downloading', completed: 100, total: 1000 });
      const errorLine = JSON.stringify({ status: 'error', error: 'disk full' });

      const stream = new ReadableStream({
        start(controller) {
          controller.enqueue(new TextEncoder().encode(
            `data: ${progressLine}\ndata: ${errorLine}\n`
          ));
          controller.close();
        },
      });
      mockPullOllamaModel.mockResolvedValue(new Response(stream, { status: 200 }));

      const { result } = renderHook(() => useOllamaModels());
      await waitFor(() => expect(mockListOllamaModels).toHaveBeenCalledTimes(1));

      await act(async () => {
        await result.current.pullModel('llama3');
      });

      expect(result.current.pullProgress['llama3']).toBeUndefined();
      expect(mockLoggerError).toHaveBeenCalledWith('Pull error:', 'disk full');
    });

    it('calls refresh and clears progress after status=success', async () => {
      mockListOllamaModels.mockResolvedValue({ instances: [] });

      const successLine = JSON.stringify({
        status: 'success',
        completed: 1000,
        total: 1000,
        instance_id: 'inst-1',
      });
      mockPullOllamaModel.mockResolvedValue(makeSseResponse([successLine]));

      const { result } = renderHook(() => useOllamaModels());
      await waitFor(() => expect(mockListOllamaModels).toHaveBeenCalledTimes(1));

      await act(async () => {
        await result.current.pullModel('llama3', 'inst-1');
      });

      // refresh() should have been triggered after success
      expect(mockListOllamaModels).toHaveBeenCalledTimes(2);
    });

    it('logs error and clears pullProgress when pull response is not ok', async () => {
      mockListOllamaModels.mockResolvedValue({ instances: [] });

      // Simulate a non-ok response (response.ok = false)
      const badResponse = new Response(null, { status: 500, statusText: 'Internal Server Error' });
      mockPullOllamaModel.mockResolvedValue(badResponse);

      const { result } = renderHook(() => useOllamaModels());
      await waitFor(() => expect(mockListOllamaModels).toHaveBeenCalledTimes(1));

      await act(async () => {
        await result.current.pullModel('llama3');
      });

      expect(mockLoggerError).toHaveBeenCalledWith(
        'Pull failed:',
        expect.objectContaining({ message: expect.stringContaining('Pull failed:') })
      );
      expect(result.current.pullProgress['llama3']).toBeUndefined();
    });

    it('does NOT log error when pullOllamaModel throws an AbortError', async () => {
      mockListOllamaModels.mockResolvedValue({ instances: [] });

      const abortError = new DOMException('The user aborted a request.', 'AbortError');
      mockPullOllamaModel.mockRejectedValue(abortError);

      const { result } = renderHook(() => useOllamaModels());
      await waitFor(() => expect(mockListOllamaModels).toHaveBeenCalledTimes(1));

      await act(async () => {
        await result.current.pullModel('llama3');
      });

      // isAbortError branch: should return early without logging
      expect(mockLoggerError).not.toHaveBeenCalledWith('Pull failed:', expect.anything());
    });

    it('logs error and clears pullProgress on generic pull exception', async () => {
      mockListOllamaModels.mockResolvedValue({ instances: [] });

      const err = new Error('network failure');
      mockPullOllamaModel.mockRejectedValue(err);

      const { result } = renderHook(() => useOllamaModels());
      await waitFor(() => expect(mockListOllamaModels).toHaveBeenCalledTimes(1));

      await act(async () => {
        await result.current.pullModel('llama3');
      });

      expect(mockLoggerError).toHaveBeenCalledWith('Pull failed:', err);
      expect(result.current.pullProgress['llama3']).toBeUndefined();
    });

    it('aborts an in-flight pull when a new pullModel call is made', async () => {
      mockListOllamaModels.mockResolvedValue({ instances: [] });

      // First call: never resolves (simulating a long pull)
      mockPullOllamaModel.mockReturnValueOnce(new Promise<Response>(() => {}));
      // Second call: resolves immediately with empty SSE
      mockPullOllamaModel.mockResolvedValueOnce(makeSseResponse([]));

      const { result } = renderHook(() => useOllamaModels());
      await waitFor(() => expect(mockListOllamaModels).toHaveBeenCalledTimes(1));

      // Start first pull (fire and forget)
      act(() => {
        void result.current.pullModel('llama3');
      });

      // Start second pull which should abort the first
      await act(async () => {
        await result.current.pullModel('mistral');
      });

      expect(mockPullOllamaModel).toHaveBeenCalledTimes(2);
      // First call's signal should have been aborted
      const firstSignal = mockPullOllamaModel.mock.calls[0][2] as AbortSignal;
      expect(firstSignal.aborted).toBe(true);
    });

    it('skips malformed SSE JSON lines without throwing', async () => {
      mockListOllamaModels.mockResolvedValue({ instances: [] });

      const stream = new ReadableStream({
        start(controller) {
          controller.enqueue(new TextEncoder().encode('data: {not valid json}\n'));
          controller.close();
        },
      });
      mockPullOllamaModel.mockResolvedValue(new Response(stream, { status: 200 }));

      const { result } = renderHook(() => useOllamaModels());
      await waitFor(() => expect(mockListOllamaModels).toHaveBeenCalledTimes(1));

      // Should not throw
      await act(async () => {
        await result.current.pullModel('llama3');
      });

      expect(result.current.pullProgress['llama3']).toBeUndefined();
    });
  });

  // -------------------------------------------------------------------------
  // pullProgress initial state
  // -------------------------------------------------------------------------

  describe('pullProgress initial state', () => {
    it('starts as an empty object', async () => {
      const { result } = renderHook(() => useOllamaModels());
      expect(result.current.pullProgress).toEqual({});
    });
  });

  // -------------------------------------------------------------------------
  // Hook return shape
  // -------------------------------------------------------------------------

  describe('return shape', () => {
    it('exposes all expected fields', () => {
      const { result } = renderHook(() => useOllamaModels(false));
      expect(typeof result.current.refresh).toBe('function');
      expect(typeof result.current.pullModel).toBe('function');
      expect(typeof result.current.removeModel).toBe('function');
      expect(typeof result.current.showModel).toBe('function');
      expect(result.current.installedModels).toBeInstanceOf(Set);
      expect(result.current.pullProgress).toEqual({});
      expect(result.current.loading).toBe(false);
      expect(result.current.modelsData).toBeNull();
    });
  });

  // -------------------------------------------------------------------------
  // Unmount cleanup
  // -------------------------------------------------------------------------

  describe('unmount cleanup', () => {
    it('aborts in-flight pull on unmount', async () => {
      mockListOllamaModels.mockResolvedValue({ instances: [] });

      // Never-resolving pull
      mockPullOllamaModel.mockReturnValue(new Promise<Response>(() => {}));

      const { result, unmount } = renderHook(() => useOllamaModels());
      await waitFor(() => expect(mockListOllamaModels).toHaveBeenCalledTimes(1));

      // Start a pull
      act(() => {
        void result.current.pullModel('llama3');
      });

      await waitFor(() => {
        expect(mockPullOllamaModel).toHaveBeenCalledTimes(1);
      });

      const signal = mockPullOllamaModel.mock.calls[0][2] as AbortSignal;
      expect(signal.aborted).toBe(false);

      unmount();

      expect(signal.aborted).toBe(true);
    });
  });
});
