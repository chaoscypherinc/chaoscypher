// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { useState, useCallback, useMemo } from 'react';
import { useNavigate, useSearchParams } from 'react-router';
import {
  Box,
  Alert,
  LinearProgress,
  Paper,
  Checkbox,
  Typography,
  Button,
} from '@mui/material';
import DeleteIcon from '@mui/icons-material/Delete';
import { LoadingState } from '../../components/LoadingState';
import { ghostButtonSx, ghostErrorAlertSx } from '../../theme/ghostStyles';
import { ChaosCypherPalette } from '../../theme/palette';
import { sourcesApi } from '../../services/api/sources';
import { chatApi } from '../../services/api/chat';
import { getApiErrorMessage } from '../../utils/errors';
import type { UnifiedSource } from '../../types';
import { useSourcePause } from '../../hooks/useSourcePause';
import { useSourcesData } from './hooks/useSourcesData';
import { useSourcesUpload } from '../../hooks/useSourcesUpload';
import type { UploadCallbackMeta } from '../../hooks/useSourcesUpload';
import { useSourcesSelection } from './hooks/useSourcesSelection';
import { SourcesHeader } from './SourcesHeader';
import { SourcesFilters } from './SourcesFilters';
import { SourcesTable } from './SourcesTable';
import { ProcessingProgress } from './ProcessingProgress';
import { UploadDialog } from '../../components/UploadDialog';
import { UploadWizard } from '../../components/UploadWizard';
import { DeleteDialog } from './dialogs/DeleteDialog';
import { BulkDeleteDialog } from './dialogs/BulkDeleteDialog';
import { ConfirmExtractionDialog } from './dialogs/ConfirmExtractionDialog';
import { BulkConfirmDialog } from './dialogs/BulkConfirmDialog';
import type { BulkConfirmItem, BulkConfirmError } from './dialogs/BulkConfirmDialog';
import { useConfirmExtraction } from '../../services/api/useSources';
import { sourceProcessingApi } from '../../services/api/sourceProcessing';
import type { ConfirmExtractionOptions } from '../../services/api/sourceProcessing';
import { BulkProgressDialog } from '../../components/BulkProgressDialog';
import type { BulkProgress } from '../../components/BulkProgressDialog';
import { logger } from '../../utils/logger';
import { useNotification } from '../../contexts/useNotification';

export default function SourcesPage() {
  const navigate = useNavigate();
  const { notify } = useNotification();

  // Filter state
  const [stageFilter, setStageFilter] = useState<'all' | 'queued' | 'processing' | 'active'>('all');
  const [searchParams] = useSearchParams();
  const [statusFilter, setStatusFilter] = useState(searchParams.get('status') ?? '');
  const [typeFilter, setTypeFilter] = useState('');
  const [searchQuery, setSearchQuery] = useState('');

  // Sort state
  const [sortField, setSortField] = useState<'created_at' | 'size'>('created_at');
  const [sortDirection, setSortDirection] = useState<'asc' | 'desc'>('desc');

  // Selection state
  const selection = useSourcesSelection();

  // Dialog state
  const [uploadDialogOpen, setUploadDialogOpen] = useState(false);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [bulkDeleteDialogOpen, setBulkDeleteDialogOpen] = useState(false);
  const [bulkDeleting, setBulkDeleting] = useState(false);

  // Selected source for dialogs
  const [selectedSource, setSelectedSource] = useState<UnifiedSource | null>(null);

  // Error state
  const [actionError, setActionError] = useState<string | null>(null);

  // Domain-confirmation flow
  const [confirmDialogOpen, setConfirmDialogOpen] = useState(false);
  const [confirmSource, setConfirmSource] = useState<UnifiedSource | null>(null);
  const [bulkConfirmOpen, setBulkConfirmOpen] = useState(false);
  const [bulkConfirmErrors, setBulkConfirmErrors] = useState<BulkConfirmError[]>([]);
  const [bulkConfirmSubmitting, setBulkConfirmSubmitting] = useState(false);
  const [bulkProgress, setBulkProgress] = useState<BulkProgress>({
    open: false, current: 0, total: 0, status: '', errors: [], isComplete: false,
  });
  const confirmMutation = useConfirmExtraction();

  // Data hook
  const { sources, loading, error, domains, queueStats, extractionCapacity, qualityScores, refresh, clearError } = useSourcesData({
    stage: stageFilter,
    status: statusFilter,
    source_type: typeFilter,
    search: searchQuery,
  });

  // Pause/resume actions
  const { pauseSource, resumeSource } = useSourcePause(refresh);

  const handlePauseSource = useCallback(async (source: UnifiedSource) => {
    try {
      await pauseSource(source.id);
    } catch (e) {
      setActionError(getApiErrorMessage(e));
    }
  }, [pauseSource]);

  const handleResumeSource = useCallback(async (source: UnifiedSource) => {
    try {
      await resumeSource(source.id);
    } catch (e) {
      setActionError(getApiErrorMessage(e));
    }
  }, [resumeSource]);

  // Upload error handler — shows an actionable toast when a skipped_duplicate
  // errored source is detected (action=retry), falling back to inline alert.
  const handleUploadError = useCallback(
    (message: string, meta?: UploadCallbackMeta) => {
      if (meta?.action === 'retry' && meta.sourceId) {
        const sourceId = meta.sourceId;
        notify(message, 'warning', {
          label: 'Open source',
          onClick: () => navigate(`/sources/${sourceId}`),
        });
      } else {
        setActionError(message);
      }
    },
    [navigate, notify],
  );

  // Upload info handler — shows a neutral info toast for non-error duplicates.
  const handleUploadInfo = useCallback(
    (message: string, _meta?: UploadCallbackMeta) => {
      notify(message, 'info');
    },
    [notify],
  );

  // Upload hook
  const uploadHook = useSourcesUpload(
    async () => {
      await refresh();
      setUploadDialogOpen(false);
    },
    handleUploadError,
    handleUploadInfo,
  );

  // Sorted sources (client-side sorting)
  const sortedSources = useMemo(() => {
    return [...sources].sort((a, b) => {
      if (sortField === 'created_at') {
        const dateA = new Date(a.created_at).getTime();
        const dateB = new Date(b.created_at).getTime();
        return sortDirection === 'asc' ? dateA - dateB : dateB - dateA;
      } else {
        return sortDirection === 'asc' ? a.size - b.size : b.size - a.size;
      }
    });
  }, [sources, sortField, sortDirection]);

  // Clear selection when filters change
  const handleStageChange = useCallback(
    (value: 'all' | 'queued' | 'processing' | 'active') => {
      setStageFilter(value);
      selection.deselectAll();
    },
    [selection]
  );

  const handleStatusChange = useCallback(
    (value: string) => {
      setStatusFilter(value);
      selection.deselectAll();
    },
    [selection]
  );

  const handleTypeChange = useCallback(
    (value: string) => {
      setTypeFilter(value);
      selection.deselectAll();
    },
    [selection]
  );

  const handleSearchChange = useCallback(
    (value: string) => {
      setSearchQuery(value);
      selection.deselectAll();
    },
    [selection]
  );

  // Sort handler
  const handleSortChange = useCallback(
    (field: 'created_at' | 'size', direction: 'asc' | 'desc') => {
      setSortField(field);
      setSortDirection(direction);
    },
    []
  );

  // Bulk delete handler
  const handleBulkDeleteConfirm = useCallback(async () => {
    const selectedSources = selection.getSelectedSources(sortedSources);
    if (selectedSources.length === 0) return;

    setBulkDeleting(true);
    setActionError(null);

    try {
      for (const source of selectedSources) {
        await sourcesApi.delete(source.id);
      }

      selection.deselectAll();
      setBulkDeleteDialogOpen(false);
      await refresh();
    } catch (err) {
      logger.error('Bulk delete failed:', err);
      setActionError('Bulk delete failed: ' + (getApiErrorMessage(err)));
    } finally {
      setBulkDeleting(false);
    }
  }, [selection, sortedSources, refresh]);

  // Select all handler for table
  const handleSelectAll = useCallback(() => {
    selection.toggleAll(sortedSources);
  }, [selection, sortedSources]);

  // Action handlers
  const handleRowClick = useCallback(
    (source: UnifiedSource) => {
      // Unified route - all sources use the same URL pattern
      navigate(`/sources/${source.id}`);
    },
    [navigate]
  );

  const handleStop = useCallback(
    async (source: UnifiedSource) => {
      try {
        setActionError(null);
        await sourcesApi.cancelProcessing(source.id);
        await refresh();
      } catch (err) {
        logger.error('Stop failed:', err);
        setActionError('Stop failed: ' + (getApiErrorMessage(err)));
      }
    },
    [refresh]
  );

  const handleDeleteClick = useCallback((source: UnifiedSource) => {
    setSelectedSource(source);
    setDeleteDialogOpen(true);
  }, []);

  const handleDeleteConfirm = useCallback(async () => {
    if (!selectedSource) return;

    try {
      setActionError(null);
      setDeleteDialogOpen(false);

      await sourcesApi.delete(selectedSource.id);

      await refresh();
      setSelectedSource(null);
    } catch (err) {
      logger.error('Delete failed:', err);
      setActionError('Delete failed: ' + (getApiErrorMessage(err)));
    }
  }, [selectedSource, refresh]);

  const handleToggleEnabled = useCallback(
    async (source: UnifiedSource) => {
      try {
        setActionError(null);
        const isCurrentlyEnabled = source.active?.enabled !== false;
        await sourcesApi.update(source.id, { enabled: !isCurrentlyEnabled });
        await refresh();
      } catch (err) {
        logger.error('Failed to toggle enabled status:', err);
        setActionError('Failed to toggle enabled status: ' + (getApiErrorMessage(err)));
      }
    },
    [refresh]
  );

  const handleChatWithSource = useCallback(
    async (source: UnifiedSource) => {
      try {
        const newChat = await chatApi.createChat({
          title: `Chat: ${source.title}`,
          source_ids: [source.id],
        });
        navigate(`/chat/${newChat.id}`);
      } catch (err) {
        logger.error('Failed to create scoped chat:', err);
        setActionError('Failed to create chat: ' + (getApiErrorMessage(err)));
      }
    },
    [navigate]
  );

  const handleViewInGraph = useCallback(
    (source: UnifiedSource) => {
      navigate(`/graph?source_ids=${source.id}`);
    },
    [navigate]
  );

  const handleRetrySource = useCallback(
    async (_source: UnifiedSource) => {
      await refresh();
    },
    [refresh]
  );

  const handleConfirmClick = useCallback((source: UnifiedSource) => {
    setConfirmSource(source);
    setConfirmDialogOpen(true);
  }, []);

  const handleConfirmSubmit = useCallback(
    async (options: ConfirmExtractionOptions) => {
      if (!confirmSource) return;
      try {
        setActionError(null);
        await confirmMutation.mutateAsync({ sourceId: confirmSource.id, options });
        setConfirmDialogOpen(false);
        setConfirmSource(null);
        await refresh();
      } catch (err) {
        setActionError('Confirm failed: ' + getApiErrorMessage(err));
      }
    },
    [confirmSource, confirmMutation, refresh],
  );

  // Bulk-confirm: per-item envelope. One 409/error never aborts the rest.
  const handleBulkConfirmAll = useCallback(
    async (items: BulkConfirmItem[]) => {
      setBulkConfirmSubmitting(true);
      setBulkConfirmErrors([]);
      setBulkProgress({ open: true, current: 0, total: items.length, status: 'Confirming...', errors: [], isComplete: false });
      try {
        const { results } = await sourceProcessingApi.bulkConfirmExtraction(
          items.map((it) => it.source_id),
        );
        const failed = results.filter((r) => !r.ok);
        setBulkConfirmErrors(
          failed.map((r) => ({ source_id: r.source_id, error: r.error ?? 'Unknown error' })),
        );
        setBulkProgress({
          open: true,
          current: items.length,
          total: items.length,
          status: failed.length === 0 ? 'All confirmed' : `${failed.length} failed`,
          errors: failed.map((r, i) => ({ operation_index: i, error: `${r.source_id}: ${r.error ?? 'error'}` })),
          isComplete: true,
        });
        if (failed.length === 0) {
          setBulkConfirmOpen(false);
          selection.deselectAll();
        }
        await refresh();
      } catch (err) {
        setActionError('Bulk confirm failed: ' + getApiErrorMessage(err));
        setBulkProgress((p) => ({ ...p, isComplete: true, status: 'Failed' }));
      } finally {
        setBulkConfirmSubmitting(false);
      }
    },
    [refresh, selection],
  );

  // Loading state
  if (loading && sources.length === 0) {
    return <LoadingState message="Loading sources..." fullPage />;
  }

  return (
    <Box>
      <SourcesHeader
        loading={loading}
        uploading={uploadHook.uploading}
        onRefresh={() => refresh()}
        onUploadClick={() => setUploadDialogOpen(true)}
      />
      {/* Search Bar with Sort/Filter - at top for quick access */}
      <SourcesFilters
        searchQuery={searchQuery}
        onSearchChange={handleSearchChange}
        stageFilter={stageFilter}
        statusFilter={statusFilter}
        typeFilter={typeFilter}
        sortField={sortField}
        sortDirection={sortDirection}
        onStageChange={handleStageChange}
        onStatusChange={handleStatusChange}
        onTypeChange={handleTypeChange}
        onSortChange={handleSortChange}
      />
      {/* Upload Progress Bar */}
      {uploadHook.uploading && uploadHook.uploadProgress > 0 && (
        <Box sx={{
          mb: 2
        }}>
          <LinearProgress variant="determinate" value={uploadHook.uploadProgress} />
        </Box>
      )}
      {/* Processing Progress Summary */}
      <ProcessingProgress sources={sources} queueStats={queueStats} />
      {/* Error display */}
      {(error || actionError) && (
        <Alert
          severity="error"
          onClose={() => {
            clearError();
            setActionError(null);
          }}
          sx={{ ...ghostErrorAlertSx, mb: 2 }}
        >
          {error || actionError}
        </Alert>
      )}
      {/* Bulk Action Bar - appears when items selected */}
      {selection.selectedCount > 0 && (
        <Paper sx={{ p: 1.5, mb: 2, display: 'flex', alignItems: 'center', gap: 2 }}>
          <Checkbox
            indeterminate={selection.selectedCount > 0 && selection.selectedCount < sortedSources.length}
            checked={selection.selectedCount === sortedSources.length}
            onChange={handleSelectAll}
          />
          <Typography variant="body2">
            {selection.selectedCount} of {sortedSources.length} selected
          </Typography>
          <Box sx={{ flexGrow: 1 }} />
          <Button
            variant="outlined"
            size="small"
            startIcon={<DeleteIcon />}
            onClick={() => setBulkDeleteDialogOpen(true)}
            sx={ghostButtonSx(ChaosCypherPalette.error)}
          >
            Delete Selected
          </Button>
          {selection.getSelectedSources(sortedSources).some((s) => s.status === 'awaiting_confirmation') && (
            <Button
              variant="outlined"
              size="small"
              onClick={() => setBulkConfirmOpen(true)}
              sx={ghostButtonSx(ChaosCypherPalette.primary)}
            >
              Confirm Selected
            </Button>
          )}
        </Paper>
      )}
      <SourcesTable
        sources={sortedSources}
        selectedIds={selection.selectedIds}
        onSelectionChange={selection.toggle}
        onSelectAll={handleSelectAll}
        onRowClick={handleRowClick}
        onStop={handleStop}
        onDelete={handleDeleteClick}
        onToggleEnabled={handleToggleEnabled}
        onChatWithSource={handleChatWithSource}
        onViewInGraph={handleViewInGraph}
        onPauseSource={handlePauseSource}
        onResumeSource={handleResumeSource}
        onRetrySource={handleRetrySource}
        onConfirmExtraction={handleConfirmClick}
        qualityScores={qualityScores}
      />
      {/* Dialogs */}
      <UploadDialog
        open={uploadDialogOpen}
        onClose={() => setUploadDialogOpen(false)}
        selectedFiles={uploadHook.selectedFiles}
        analysisDepth={uploadHook.analysisDepth}
        enableNormalization={uploadHook.enableNormalization}
        selectedDomain={uploadHook.selectedDomain}
        availableDomains={domains}
        onFilesSelected={uploadHook.handleFilesSelected}
        onAnalysisDepthChange={uploadHook.setAnalysisDepth}
        onNormalizationChange={uploadHook.setEnableNormalization}
        onDomainChange={uploadHook.setSelectedDomain}
        onConfirm={uploadHook.handleUploadConfirm}
        onClearSelection={uploadHook.clearSelection}
        onRemoveFile={uploadHook.removeFile}
        onUrlImport={uploadHook.handleUrlImport}
        importingUrl={uploadHook.importingUrl}
        extractEntities={uploadHook.extractEntities}
        onExtractEntitiesChange={uploadHook.setExtractEntities}
        enableVision={uploadHook.enableVision}
        onEnableVisionChange={uploadHook.setEnableVision}
        filteringMode={uploadHook.filteringMode}
        onFilteringModeChange={uploadHook.setFilteringMode}
        contentFiltering={uploadHook.contentFiltering}
        onContentFilteringChange={uploadHook.setContentFiltering}
        contextWindow={extractionCapacity.contextWindow}
        groupSize={extractionCapacity.groupSize}
        inputPerChunk={extractionCapacity.inputPerChunk}
        outputPerChunk={extractionCapacity.outputPerChunk}
        skipDuplicates={uploadHook.skipDuplicates}
        onSkipDuplicatesChange={uploadHook.setSkipDuplicates}
      />
      {/* Upfront domain-confirmation wizard (single-file uploads). This entry
          point owns its own `useSourcesUpload` instance (the app-shell Layout
          has its own); only the instance whose handleUploadConfirm ran goes
          non-idle and renders, so the Analyzing → Review flow takes over after
          a single-file Import here. Mirrors the dual-UploadDialog pattern. */}
      <UploadWizard
        wizard={uploadHook.wizard}
        availableDomains={domains}
        contextWindow={extractionCapacity.contextWindow}
        groupSize={extractionCapacity.groupSize}
        inputPerChunk={extractionCapacity.inputPerChunk}
        outputPerChunk={extractionCapacity.outputPerChunk}
      />
      <DeleteDialog
        open={deleteDialogOpen}
        source={selectedSource}
        onClose={() => {
          setDeleteDialogOpen(false);
          setSelectedSource(null);
        }}
        onConfirm={handleDeleteConfirm}
      />
      <BulkDeleteDialog
        open={bulkDeleteDialogOpen}
        sources={selection.getSelectedSources(sortedSources)}
        onClose={() => setBulkDeleteDialogOpen(false)}
        onConfirm={handleBulkDeleteConfirm}
        loading={bulkDeleting}
      />
      <ConfirmExtractionDialog
        open={confirmDialogOpen}
        source={confirmSource}
        availableDomains={domains}
        submitting={confirmMutation.isPending}
        onClose={() => { setConfirmDialogOpen(false); setConfirmSource(null); }}
        onConfirm={handleConfirmSubmit}
        contextWindow={extractionCapacity.contextWindow}
        groupSize={extractionCapacity.groupSize}
        inputPerChunk={extractionCapacity.inputPerChunk}
        outputPerChunk={extractionCapacity.outputPerChunk}
      />
      <BulkConfirmDialog
        open={bulkConfirmOpen}
        sources={selection.getSelectedSources(sortedSources).filter((s) => s.status === 'awaiting_confirmation')}
        submitting={bulkConfirmSubmitting}
        errors={bulkConfirmErrors}
        onClose={() => setBulkConfirmOpen(false)}
        onConfirmAll={handleBulkConfirmAll}
      />
      <BulkProgressDialog progress={bulkProgress} onClose={() => setBulkProgress((p) => ({ ...p, open: false }))} />
    </Box>
  );
}
