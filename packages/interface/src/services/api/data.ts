// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { apiClient } from './client';
import { BATCH_CONFIG } from '../../constants/config';

interface ExportOptions {
  includeTemplates?: boolean;
  includeKnowledge?: boolean;
  includeLenses?: boolean;
  includeWorkflows?: boolean;
  includeSources?: boolean;
  includeEmbeddings?: boolean;
  lensId?: string;
}

interface ExportTaskResponse {
  task_id: string;
  status: string;
  message: string;
}

interface ExportResult {
  filename: string;
  content: string; // base64 encoded ZIP
  size_bytes: number;
}

interface TaskStatusResponse {
  status: string;
  error?: string;
}

export const dataApi = {
  export: async (options: ExportOptions = {}, signal?: AbortSignal): Promise<Blob> => {
    const params: Record<string, boolean | string> = {
      include_templates: options.includeTemplates ?? true,
      include_knowledge: options.includeKnowledge ?? true,
      include_lenses: options.includeLenses ?? true,
      include_workflows: options.includeWorkflows ?? true,
      include_sources: options.includeSources ?? true,
      include_embeddings: options.includeEmbeddings ?? false,
    };

    // Add lens_id if provided
    if (options.lensId) {
      params.lens_id = options.lensId;
    }

    // Step 1: Queue export operation
    const queueResponse = await apiClient.post<ExportTaskResponse>('/exports', {}, { params, signal });
    const taskId = queueResponse.data.task_id;

    // Step 2: Poll for completion
    let attempts = 0;

    while (attempts < BATCH_CONFIG.EXPORT_MAX_ATTEMPTS) {
      if (signal?.aborted) throw new DOMException('Aborted', 'AbortError');
      await new Promise(resolve => setTimeout(resolve, BATCH_CONFIG.POLLING_WAIT_MS));
      attempts++;

      // Check task status
      const statusResponse = await apiClient.get<TaskStatusResponse>(`/queue/tasks/${taskId}`, { signal });
      const status = statusResponse.data.status;

      if (status === 'completed') {
        // Get the result
        const resultResponse = await apiClient.get<{ result: ExportResult }>(`/queue/tasks/${taskId}/result`, { signal });
        const result = resultResponse.data.result;

        // Decode base64 content to binary
        const binaryString = atob(result.content);
        const bytes = new Uint8Array(binaryString.length);
        for (let i = 0; i < binaryString.length; i++) {
          bytes[i] = binaryString.charCodeAt(i);
        }

        // Create blob from binary data
        return new Blob([bytes], { type: 'application/zip' });
      } else if (status === 'failed') {
        throw new Error(statusResponse.data.error || 'Export failed');
      }
    }

    throw new Error('Export timeout - operation did not complete in time');
  },

  exportBySource: async (sourceIds: string[], signal?: AbortSignal): Promise<Blob> => {
    // Step 1: Queue source-filtered export
    const queueResponse = await apiClient.post<ExportTaskResponse>(
      '/exports/by_sources',
      sourceIds,
      { params: { include_templates: true, include_embeddings: false }, signal },
    );
    const taskId = queueResponse.data.task_id;

    // Step 2: Poll for completion
    let attempts = 0;

    while (attempts < BATCH_CONFIG.EXPORT_MAX_ATTEMPTS) {
      if (signal?.aborted) throw new DOMException('Aborted', 'AbortError');
      await new Promise(resolve => setTimeout(resolve, BATCH_CONFIG.POLLING_WAIT_MS));
      attempts++;

      const statusResponse = await apiClient.get<TaskStatusResponse>(`/queue/tasks/${taskId}`, { signal });
      const status = statusResponse.data.status;

      if (status === 'completed') {
        const resultResponse = await apiClient.get<{ result: ExportResult }>(`/queue/tasks/${taskId}/result`, { signal });
        const result = resultResponse.data.result;

        const binaryString = atob(result.content);
        const bytes = new Uint8Array(binaryString.length);
        for (let i = 0; i < binaryString.length; i++) {
          bytes[i] = binaryString.charCodeAt(i);
        }

        return new Blob([bytes], { type: 'application/zip' });
      } else if (status === 'failed') {
        throw new Error(statusResponse.data.error || 'Export failed');
      }
    }

    throw new Error('Export timeout - operation did not complete in time');
  },

  import: async (file: File, merge: boolean = false, signal?: AbortSignal) => {
    const formData = new FormData();
    formData.append('file', file);

    // Step 1: Queue import operation
    const queueResponse = await apiClient.post<ExportTaskResponse>('/exports/import', formData, {
      params: { merge },
      headers: { 'Content-Type': 'multipart/form-data' },
      signal,
    });

    const taskId = queueResponse.data.task_id;

    // Step 2: Poll for completion
    let attempts = 0;

    while (attempts < BATCH_CONFIG.IMPORT_MAX_ATTEMPTS) {
      if (signal?.aborted) throw new DOMException('Aborted', 'AbortError');
      await new Promise(resolve => setTimeout(resolve, BATCH_CONFIG.POLLING_WAIT_MS));
      attempts++;

      // Check task status
      const statusResponse = await apiClient.get<TaskStatusResponse>(`/queue/tasks/${taskId}`, { signal });
      const status = statusResponse.data.status;

      if (status === 'completed') {
        // Get the result
        const resultResponse = await apiClient.get(`/queue/tasks/${taskId}/result`, { signal });
        return resultResponse.data.result;
      } else if (status === 'failed') {
        throw new Error(statusResponse.data.error || 'Import failed');
      }
    }

    throw new Error('Import timeout - operation did not complete in time');
  },
};
