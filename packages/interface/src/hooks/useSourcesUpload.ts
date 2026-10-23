// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { useState, useCallback, useEffect, useRef } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { sourcesApi, getRecommendedNormalization } from '../services/api/sources';
import { buildUrlImportOptions } from '../services/api/sourceProcessing';
import { SITES_QUERY_KEY, sitesApi } from '../services/api/sites';
import { getApiErrorMessage, isAbortError } from '../utils/errors';
import { logger } from '../utils/logger';
import { useAppConfig } from '../contexts/useAppConfig';
import { useUploadWizard } from './useUploadWizard';
import type { UseUploadWizardReturn } from './useUploadWizard';

/** Format a byte count as a human-readable size for upload-cap UI. */
function formatUploadCap(bytes: number): string {
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(bytes % 1024 ** 3 === 0 ? 0 : 1)} GB`;
  if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(0)} MB`;
  return `${bytes} B`;
}

// ── Callback meta types ───────────────────────────────────────────────────

/** Metadata attached to upload error / info callbacks for special handling. */
export interface UploadCallbackMeta {
  /** ID of the relevant source (existing sibling when skipped_duplicate=true). */
  sourceId?: string;
  /** 'retry' signals that the user should be offered a navigate-to-source action. */
  action?: 'retry';
}

type OnError = (message: string, meta?: UploadCallbackMeta) => void;
type OnInfo = (message: string, meta?: UploadCallbackMeta) => void;

// ── Return type ───────────────────────────────────────────────────────────

interface UseSourcesUploadReturn {
  uploading: boolean;
  uploadProgress: number;
  importingUrl: boolean;
  selectedFiles: File[];
  extractEntities: boolean;
  analysisDepth: 'quick' | 'full';
  enableNormalization: boolean;
  enableVision: boolean;
  /** The chosen packs; `null` leaves them to detection (`[]` = the floor alone). */
  selectedPacks: string[] | null;
  filteringMode: string;
  contentFiltering: boolean;
  skipDuplicates: boolean;
  setExtractEntities: (value: boolean) => void;
  setAnalysisDepth: (value: 'quick' | 'full') => void;
  setEnableNormalization: (value: boolean) => void;
  setEnableVision: (value: boolean) => void;
  setSelectedPacks: (packs: string[] | null) => void;
  setFilteringMode: (value: string) => void;
  setContentFiltering: (value: boolean) => void;
  setSkipDuplicates: (value: boolean) => void;
  handleFilesSelected: (files: File[]) => void;
  handleUploadConfirm: () => Promise<void>;
  handleUrlImport: (url: string) => Promise<void>;
  /** Import a site: every page its discovery finds but the `exclude`d ones. */
  handleSiteImport: (url: string, exclude: string[]) => Promise<void>;
  cancelUpload: () => void;
  clearSelection: () => void;
  removeFile: (index: number) => void;
  /**
   * The upfront domain-confirmation upload wizard (single-file only). A
   * single-file Import routes through `wizard.start()`: auto-domain engages
   * detection → Analyzing poll → inline review; a specific domain takes the
   * override fast-path. Render `<UploadWizard wizard={wizard} … />` at the
   * entry point. Batch + URL imports never touch the wizard.
   */
  wizard: UseUploadWizardReturn;
}

export function useSourcesUpload(
  onUploadComplete: () => Promise<void>,
  onError: OnError,
  onInfo?: OnInfo,
): UseSourcesUploadReturn {
  const { batch_max_upload_bytes: maxUploadBytes } = useAppConfig();
  const queryClient = useQueryClient();
  const wizard = useUploadWizard();
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [importingUrl, setImportingUrl] = useState(false);
  const abortControllerRef = useRef<AbortController | null>(null);
  const progressResetTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (progressResetTimer.current) clearTimeout(progressResetTimer.current);
    };
  }, []);
  const [selectedFiles, setSelectedFiles] = useState<File[]>([]);
  const [extractEntities, setExtractEntities] = useState(true);
  const [analysisDepth, setAnalysisDepth] = useState<'quick' | 'full'>('full');
  const [enableNormalization, setEnableNormalization] = useState(true);
  const [enableVision, setEnableVision] = useState(true);
  const [selectedPacks, setSelectedPacks] = useState<string[] | null>(null);
  const [filteringMode, setFilteringMode] = useState<string>('');
  const [contentFiltering, setContentFiltering] = useState(true);
  const [skipDuplicates, setSkipDuplicates] = useState(false);

  // Update normalization setting based on current files
  const updateNormalization = useCallback((files: File[]) => {
    const shouldNormalize = files.some(file => getRecommendedNormalization(file.name));
    setEnableNormalization(shouldNormalize);
  }, []);

  const handleFilesSelected = useCallback((files: File[]) => {
    if (files.length === 0) return;

    // Filter out files exceeding the operator-configured upload cap (see
    // BatchSettings.max_upload_bytes — default 5 GB; rendered nginx config
    // reads the same setting via Jinja).
    const oversized = files.filter(f => f.size > maxUploadBytes);
    const valid = files.filter(f => f.size <= maxUploadBytes);

    if (oversized.length > 0) {
      const names = oversized.map(f => f.name).join(', ');
      onError(`Files exceed ${formatUploadCap(maxUploadBytes)} limit and were not added: ${names}`);
    }

    if (valid.length === 0) return;

    setSelectedFiles(prev => {
      // Merge new files, avoiding duplicates by filename
      const existingNames = new Set(prev.map(f => f.name));
      const newFiles = valid.filter(f => !existingNames.has(f.name));
      const merged = [...prev, ...newFiles];
      // Update normalization based on merged files
      updateNormalization(merged);
      return merged;
    });
  }, [maxUploadBytes, updateNormalization, onError]);

  const removeFile = useCallback((index: number) => {
    setSelectedFiles(prev => {
      const updated = prev.filter((_, i) => i !== index);
      updateNormalization(updated);
      return updated;
    });
  }, [updateNormalization]);

  const cancelUpload = useCallback(() => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
  }, []);

  const handleUploadConfirm = useCallback(async () => {
    if (selectedFiles.length === 0) return;

    const controller = new AbortController();
    abortControllerRef.current = controller;

    try {
      setUploading(true);
      setUploadProgress(0);

      if (selectedFiles.length === 1) {
        // Single file → the upfront domain-confirmation wizard owns the upload.
        // It POSTs /sources (engaging detection for auto-domain, or sending
        // auto_confirm for a forced domain), then either polls for the eager
        // proposal (auto) or fast-paths (forced). Batch + URL paths below are
        // out of wizard scope and keep the chip fallback.
        const file = selectedFiles[0];
        setUploadProgress(100);

        const { outcome, source: result } = await wizard.start({
          file,
          extractEntities,
          analysisDepth,
          enableNormalization,
          enableVision,
          filteringMode,
          contentFiltering,
          skipDuplicates,
          packs: selectedPacks,
          signal: controller.signal,
        });

        // Skipped duplicate → surface the same actionable feedback the chip
        // flow uses (the wizard hook owns the upload but not these callbacks).
        if (outcome === 'skipped' && result) {
          if (result.existing_status === 'error') {
            onError(
              `"${file.name}" was previously uploaded but errored. Open the existing source to retry.`,
              { sourceId: result.id, action: 'retry' },
            );
          } else {
            const status = result.existing_status ?? 'unknown';
            onInfo?.(
              `"${file.name}" matches an existing source (${status}); upload skipped.`,
              { sourceId: result.id },
            );
          }
        }

        // 'error' is surfaced inline by the wizard's error phase — don't
        // double-report. Refresh the list (the new source is visible) and
        // clear the picker for every outcome; the wizard's analyzing/review
        // dialogs live on their own state and stay open for the 'wizard' path.
        await onUploadComplete();
        setSelectedFiles([]);
        if (progressResetTimer.current) clearTimeout(progressResetTimer.current);
        progressResetTimer.current = setTimeout(() => setUploadProgress(0), 1000);
        return;
      } else {
        // Batch upload
        const total = selectedFiles.length;

        const result = await sourcesApi.uploadBatch(
          selectedFiles,
          extractEntities,
          analysisDepth,
          enableNormalization,
          selectedPacks,
          enableVision,
          filteringMode || undefined,
          contentFiltering,
          skipDuplicates,
        );

        setUploadProgress((result.uploaded / total) * 100);

        // Count skipped duplicates among the batch results
        const skippedFiles = result.files.filter(f => f.skipped_duplicate);
        const skippedCount = skippedFiles.length;

        if (result.errors.length > 0) {
          const uploadedActual = result.uploaded - skippedCount;
          const summary = skippedCount > 0
            ? `Uploaded ${uploadedActual} files, ${skippedCount} skipped (duplicate), ${result.failed} failed`
            : `Uploaded ${result.uploaded} files, ${result.failed} failed`;
          onError(summary);
        } else if (skippedCount > 0 && skippedCount === result.files.length) {
          // All files were duplicates
          onInfo?.(`All ${skippedCount} file(s) match existing sources; uploads skipped.`);
        } else if (skippedCount > 0) {
          onInfo?.(`${skippedCount} file(s) skipped as duplicates.`);
        }
      }

      // Reload sources list
      await onUploadComplete();

      // Reset state and progress
      setSelectedFiles([]);
      if (progressResetTimer.current) clearTimeout(progressResetTimer.current);
      progressResetTimer.current = setTimeout(() => setUploadProgress(0), 1000);
    } catch (err) {
      if (isAbortError(err)) {
        onError('Upload cancelled');
      } else {
        logger.error('Upload failed:', err);
        onError('Upload failed: ' + getApiErrorMessage(err));
      }
    } finally {
      abortControllerRef.current = null;
      setUploading(false);
    }
  }, [
    selectedFiles,
    selectedPacks,
    extractEntities,
    analysisDepth,
    enableNormalization,
    enableVision,
    filteringMode,
    contentFiltering,
    skipDuplicates,
    onUploadComplete,
    onError,
    onInfo,
    wizard,
  ]);

  const handleUrlImport = useCallback(async (url: string) => {
    if (!url) return;

    try {
      setImportingUrl(true);

      // The fetch is queued: the worker creates the source (or skips a
      // duplicate) when it runs, and the sources list shows the outcome.
      await sourcesApi.importUrl(
        url,
        extractEntities,
        analysisDepth,
        enableNormalization,
        selectedPacks,
        filteringMode || undefined,
        contentFiltering,
        skipDuplicates,
      );

      // Reload sources list
      await onUploadComplete();
    } catch (err) {
      logger.error('URL import failed:', err);
      onError('URL import failed: ' + (getApiErrorMessage(err)));
    } finally {
      setImportingUrl(false);
    }
  }, [
    selectedPacks,
    extractEntities,
    analysisDepth,
    enableNormalization,
    filteringMode,
    contentFiltering,
    skipDuplicates,
    onUploadComplete,
    onError,
  ]);

  const handleSiteImport = useCallback(async (url: string, exclude: string[]) => {
    try {
      setImportingUrl(true);
      await sitesApi.create(url, exclude, {
        ...buildUrlImportOptions({
          extractEntities,
          analysisDepth,
          enableNormalization,
          enableVision,
          packs: selectedPacks,
          filteringMode,
          contentFiltering,
          skipDuplicates,
        }),
        // A site is many pages: each extracts with the packs detected for it
        // (or the ones chosen here) instead of waiting for a confirmation.
        auto_confirm: true,
      });
      await queryClient.invalidateQueries({ queryKey: SITES_QUERY_KEY });
      onInfo?.(`Importing the site at ${url}; its pages appear under one row in Sources.`);
      await onUploadComplete();
    } catch (err) {
      logger.error('Site import failed:', err);
      onError('Site import failed: ' + getApiErrorMessage(err));
    } finally {
      setImportingUrl(false);
    }
  }, [
    selectedPacks,
    extractEntities,
    analysisDepth,
    enableNormalization,
    enableVision,
    filteringMode,
    contentFiltering,
    skipDuplicates,
    queryClient,
    onUploadComplete,
    onError,
    onInfo,
  ]);

  const clearSelection = useCallback(() => {
    setSelectedFiles([]);
    setEnableNormalization(true);
    setSelectedPacks(null);
  }, []);

  return {
    uploading,
    uploadProgress,
    importingUrl,
    selectedFiles,
    extractEntities,
    analysisDepth,
    enableNormalization,
    enableVision,
    selectedPacks,
    filteringMode,
    contentFiltering,
    skipDuplicates,
    setExtractEntities,
    setAnalysisDepth,
    setEnableNormalization,
    setEnableVision,
    setSelectedPacks,
    setFilteringMode,
    setContentFiltering,
    setSkipDuplicates,
    handleFilesSelected,
    handleUploadConfirm,
    handleUrlImport,
    handleSiteImport,
    cancelUpload,
    clearSelection,
    removeFile,
    wizard,
  };
}
