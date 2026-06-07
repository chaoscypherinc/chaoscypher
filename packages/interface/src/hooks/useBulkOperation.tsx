// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { useState, useCallback } from 'react';
import { apiClient, isApiError } from '../services/api/client';
import { BATCH_CONFIG } from '../constants/config';
import { BulkProgressDialog } from '../components/BulkProgressDialog';
import type { BulkProgress } from '../components/BulkProgressDialog';
import { logger } from '../utils/logger';

interface BulkOperation {
  operation: 'create' | 'update' | 'delete';
  data: Record<string, unknown>;
}

interface BulkOperationResult {
  success: number;
  failed: number;
  results: Array<{
    operation_index: number;
    operation: string;
    id: string;
    status: string;
  }>;
  errors: Array<{
    operation_index: number;
    error: string;
  }>;
}

interface UseBulkOperationReturn {
  execute: (resource: string, operations: BulkOperation[]) => Promise<BulkOperationResult>;
  progress: BulkProgress;
  ProgressDialog: React.FC;
}

/**
 * Custom hook for bulk operations with progress tracking
 *
 * Usage:
 * ```tsx
 * const { execute, ProgressDialog } = useBulkOperation();
 *
 * const handleBulkDelete = async () => {
 *   const operations = selectedIds.map(id => ({ operation: 'delete', data: { id } }));
 *   await execute('nodes', operations);
 * };
 *
 * return (
 *   <>
 *     <Button onClick={handleBulkDelete}>Delete Selected</Button>
 *     <ProgressDialog />
 *   </>
 * );
 * ```
 */
export function useBulkOperation(): UseBulkOperationReturn {
  const [progress, setProgress] = useState<BulkProgress>({
    open: false,
    current: 0,
    total: 0,
    status: 'Processing...',
    errors: [],
    isComplete: false,
  });

  const execute = useCallback(
    async (
      resource: string, // 'nodes', 'edges', 'templates'
      operations: BulkOperation[]
    ): Promise<BulkOperationResult> => {
      setProgress({
        open: true,
        current: 0,
        total: operations.length,
        status: `Preparing ${operations.length} operations...`,
        errors: [],
        isComplete: false,
      });

      try {
        const batchSize = BATCH_CONFIG.BULK_OPERATION_SIZE;
        const allResults: BulkOperationResult[] = [];

        for (let i = 0; i < operations.length; i += batchSize) {
          const batch = operations.slice(i, i + batchSize);
          const batchEnd = Math.min(i + batchSize, operations.length);

          setProgress((prev) => ({
            ...prev,
            current: i,
            status: `Processing ${i + 1}-${batchEnd} of ${operations.length}...`,
          }));

          // Make bulk API call - batch endpoints now return 202 with task_id
          const response = await apiClient.post(`/${resource}/batch`, {
            operations: batch,
          });

          // Check if response is async (has task_id) or sync (has success/failed)
          if (response.data.task_id) {
            // Async response - poll for results
            const taskId = response.data.task_id;
            setProgress((prev) => ({
              ...prev,
              status: `Waiting for batch ${i + 1}-${batchEnd} to complete...`,
            }));

            // Poll for task completion
            let result: BulkOperationResult | null = null;
            let attempts = 0;
            const maxAttempts = BATCH_CONFIG.POLLING_MAX_ATTEMPTS;

            while (attempts < maxAttempts && !result) {
              await new Promise((resolve) => setTimeout(resolve, BATCH_CONFIG.POLLING_WAIT_MS));
              attempts++;

              try {
                // Check task status
                const statusResponse = await apiClient.get(`/queue/tasks/${taskId}`);

                if (statusResponse.data.status === 'completed') {
                  // Get result - API wraps it in {"result": ...}
                  const resultResponse = await apiClient.get(`/queue/tasks/${taskId}/result`);
                  result = resultResponse.data.result || resultResponse.data;
                } else if (statusResponse.data.status === 'failed') {
                  throw new Error(statusResponse.data.error || 'Batch operation failed');
                }
              } catch (pollError) {
                logger.error('Error polling task:', pollError);
                // Continue polling unless it's a definitive error
                if (isApiError(pollError) && pollError.response?.status === 404) {
                  throw pollError;
                }
              }
            }

            if (!result) {
              throw new Error('Batch operation timeout - task did not complete in time');
            }

            allResults.push(result);
          } else {
            // Sync response (backward compatibility)
            allResults.push(response.data as BulkOperationResult);
          }

          // Update progress
          setProgress((prev) => ({
            ...prev,
            current: batchEnd,
          }));

          // Yield to UI thread to keep interface responsive
          await new Promise((resolve) => setTimeout(resolve, 0));
        }

        // Combine results from all batches
        const combinedResult: BulkOperationResult = {
          success: allResults.reduce((sum, r) => sum + (r.success || 0), 0),
          failed: allResults.reduce((sum, r) => sum + (r.failed || 0), 0),
          results: allResults.flatMap((r) => r.results || []),
          errors: allResults.flatMap((r) => r.errors || []),
        };

        setProgress((prev) => ({
          ...prev,
          current: operations.length,
          status: `Complete! ${combinedResult.success} succeeded, ${combinedResult.failed} failed`,
          errors: combinedResult.errors || [],
          isComplete: true,
        }));

        // If no errors, close dialog after brief delay
        // If there are errors, keep dialog open so user can see them
        if (combinedResult.failed === 0) {
          setTimeout(() => {
            setProgress((prev) => ({ ...prev, open: false }));
          }, 1500);
        }

        return combinedResult;
      } catch (error) {
        const message = isApiError(error)
          ? error.detail || error.message
          : error instanceof Error ? error.message : 'Unknown error';
        setProgress((prev) => ({
          ...prev,
          status: `Error: ${message}`,
        }));

        // Keep dialog open to show error
        setTimeout(() => {
          setProgress((prev) => ({ ...prev, open: false }));
        }, 3000);

        throw error;
      }
    },
    []
  );

  const handleClose = useCallback(() => {
    setProgress((prev) => ({ ...prev, open: false }));
  }, []);

  // Thin wrapper that renders the extracted component with current state
  const ProgressDialogWrapper: React.FC = () => (
    <BulkProgressDialog progress={progress} onClose={handleClose} />
  );

  return { execute, progress, ProgressDialog: ProgressDialogWrapper };
}
