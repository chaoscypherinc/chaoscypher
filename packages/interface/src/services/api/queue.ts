// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Queue polling helpers.
 *
 * The backend moved several heavy operations to queue + 202 during the
 * 2026-04-18 API review: ``/reset/knowledge``, ``/reset/all``,
 * ``/cleanup/orphans``, and ``/graph/cleanup`` now dispatch to the Neuron
 * worker and return a ``task_id`` instead of blocking on the request.
 * This module centralises the "poll until complete, return the result"
 * pattern so each feature wrapper doesn't re-implement it.
 */

import { apiClient } from './client';
import { BATCH_CONFIG } from '../../constants/config';

interface TaskStatusResponse {
  status: string;
  error?: string;
}

interface QueuedResponse {
  task_id: string;
  status?: string;
  message?: string;
}

interface PollOptions {
  /** AbortSignal used to cancel the poll (e.g. on component unmount). */
  signal?: AbortSignal;
  /**
   * Max polling attempts before aborting with a timeout error. Default is
   * pulled from ``BATCH_CONFIG.EXPORT_MAX_ATTEMPTS`` since the shortest
   * heavy operation (cleanup) completes well under an export's budget.
   */
  maxAttempts?: number;
  /** Milliseconds to wait between poll attempts. */
  intervalMs?: number;
}

/**
 * Poll ``/queue/tasks/{task_id}`` until the task reaches a terminal state,
 * then fetch and return the final result from ``/queue/tasks/{task_id}/result``.
 *
 * @throws Error when the task reports ``status: "failed"``.
 * @throws DOMException("AbortError") when ``signal`` aborts.
 * @throws Error("Queue task timeout — operation did not complete in time")
 *         when ``maxAttempts`` is exhausted.
 */
async function pollTaskResult<T>(
  taskId: string,
  options: PollOptions = {},
): Promise<T> {
  const maxAttempts = options.maxAttempts ?? BATCH_CONFIG.EXPORT_MAX_ATTEMPTS;
  const intervalMs = options.intervalMs ?? BATCH_CONFIG.POLLING_WAIT_MS;
  const signal = options.signal;

  let attempts = 0;
  while (attempts < maxAttempts) {
    if (signal?.aborted) throw new DOMException('Aborted', 'AbortError');
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
    attempts++;

    const statusResponse = await apiClient.get<TaskStatusResponse>(
      `/queue/tasks/${taskId}`,
      { signal },
    );
    const status = statusResponse.data.status;

    if (status === 'completed') {
      const resultResponse = await apiClient.get<{ result: T }>(
        `/queue/tasks/${taskId}/result`,
        { signal },
      );
      return resultResponse.data.result;
    }
    if (status === 'failed' || status === 'cancelled') {
      throw new Error(
        statusResponse.data.error || `Queue task ${status} without a reason`,
      );
    }
  }

  throw new Error('Queue task timeout — operation did not complete in time');
}

/**
 * Helper for endpoints that return a queued response: POST the request,
 * read the ``task_id``, then poll for the final result.
 *
 * Example:
 *   ``const stats = await enqueueAndWait<ResetStats>(() => apiClient.post('/settings/reset/knowledge'));``
 */
export async function enqueueAndWait<T>(
  enqueue: () => Promise<{ data: QueuedResponse }>,
  options: PollOptions = {},
): Promise<T> {
  const queued = await enqueue();
  const taskId = queued.data.task_id;
  if (!taskId) {
    throw new Error('Queued response did not contain a task_id');
  }
  return pollTaskResult<T>(taskId, options);
}
