// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Global Upload Dialog State Hook
 *
 * Manages the upload dialog lifecycle: open/close state, domain loading,
 * extraction capacity settings, file upload orchestration, and error display.
 * Extracted from Layout to keep the shell component thin.
 */

import { useState, useEffect, useCallback, useMemo } from 'react';
import { useNavigate } from 'react-router';
import { sourcesApi } from '../services/api/sources';
import type { ExtractionDomain } from '../services/api/sources';
import { settingsApi } from '../services/api/settings';
import { useSourcesUpload } from './useSourcesUpload';
import type { UploadCallbackMeta } from './useSourcesUpload';
import { useNotification } from '../contexts/useNotification';
import { logger } from '../utils/logger';

interface ExtractionCapacity {
  contextWindow: number;
  groupSize: number;
  inputPerChunk: number;
  outputPerChunk: number;
}

interface UseUploadDialogStateReturn {
  /** Whether the upload dialog is visible */
  uploadDialogOpen: boolean;
  /** Open the upload dialog */
  openUploadDialog: () => void;
  /** Close the upload dialog */
  closeUploadDialog: () => void;
  /** Current upload error message, or null */
  uploadError: string | null;
  /** Clear the upload error */
  clearUploadError: () => void;
  /** Available extraction domains */
  domains: ExtractionDomain[];
  /** LLM extraction capacity settings */
  extractionCapacity: ExtractionCapacity;
  /** Upload hook return value (file state, handlers, etc.) */
  uploadHook: ReturnType<typeof useSourcesUpload>;
  /** Context value for UploadDialogContext provider */
  uploadDialogCtx: { openUploadDialog: () => void };
}

/**
 * Hook that encapsulates all upload dialog state and side effects.
 *
 * Loads domains and extraction capacity from the API when the dialog opens,
 * delegates file handling to `useSourcesUpload`, and exposes a stable context
 * value for the `UploadDialogContext` provider.
 */
export function useUploadDialogState(): UseUploadDialogStateReturn {
  const navigate = useNavigate();
  const { notify } = useNotification();
  const [uploadDialogOpen, setUploadDialogOpen] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [domains, setDomains] = useState<ExtractionDomain[]>([]);
  const [extractionCapacity, setExtractionCapacity] = useState<ExtractionCapacity>({
    contextWindow: 8192,
    groupSize: 4,
    inputPerChunk: 150,
    outputPerChunk: 2000,
  });

  const openUploadDialog = useCallback(() => setUploadDialogOpen(true), []);
  const closeUploadDialog = useCallback(() => setUploadDialogOpen(false), []);
  const clearUploadError = useCallback(() => setUploadError(null), []);

  const uploadDialogCtx = useMemo(() => ({
    openUploadDialog,
  }), [openUploadDialog]);

  // Error callback: shows an actionable toast for skipped_duplicate+error cases.
  const handleUploadError = useCallback(
    (msg: string, meta?: UploadCallbackMeta) => {
      if (meta?.action === 'retry' && meta.sourceId) {
        const sourceId = meta.sourceId;
        notify(msg, 'warning', {
          label: 'Open source',
          onClick: () => navigate(`/sources/${sourceId}`),
        });
      } else {
        setUploadError(msg);
      }
    },
    [navigate, notify],
  );

  // Info callback: neutral toast for non-error duplicates.
  const handleUploadInfo = useCallback(
    (msg: string, _meta?: UploadCallbackMeta) => {
      notify(msg, 'info');
    },
    [notify],
  );

  const uploadHook = useSourcesUpload(
    useCallback(async () => {
      setUploadDialogOpen(false);
    }, []),
    handleUploadError,
    handleUploadInfo,
  );

  // Load domains + extraction capacity once when dialog opens
  useEffect(() => {
    if (!uploadDialogOpen) return;
    sourcesApi.listDomains().then(setDomains).catch((err) => {
      logger.error('Failed to load domains:', err);
    });
    settingsApi.get().then((settings) => {
      const llm = settings.llm as { ollama_num_ctx?: number; ai_context_window?: number } | undefined;
      const chunking = settings.chunking as
        | { group_size?: number; small_chunk_size?: number; output_tokens_per_chunk?: number }
        | undefined;
      const contextWindow = llm?.ollama_num_ctx || llm?.ai_context_window || 8192;
      const groupSize = chunking?.group_size || 4;
      const inputPerChunk = Math.floor((chunking?.small_chunk_size || 600) / 4);
      const outputPerChunk = chunking?.output_tokens_per_chunk || 2000;
      setExtractionCapacity({ contextWindow, groupSize, inputPerChunk, outputPerChunk });
    }).catch((err) => {
      logger.error('Failed to load extraction capacity:', err);
    });
  }, [uploadDialogOpen]);

  return {
    uploadDialogOpen,
    openUploadDialog,
    closeUploadDialog,
    uploadError,
    clearUploadError,
    domains,
    extractionCapacity,
    uploadHook,
    uploadDialogCtx,
  };
}
