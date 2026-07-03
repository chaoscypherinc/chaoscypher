// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { useState } from 'react';
import { useParams, useNavigate, useSearchParams } from 'react-router';
import {
  Box,
  Typography,
  Tabs,
  Tab,
  Alert,
  Button,
  LinearProgress,
} from '@mui/material';
import { LoadingState } from '../../components/LoadingState';
import {
  ghostErrorAlertSx,
  ghostTabsSx,
} from '../../theme/ghostStyles';
import ArrowBackIcon from '@mui/icons-material/ArrowBack';
import DashboardIcon from '@mui/icons-material/Dashboard';
import PauseIcon from '@mui/icons-material/Pause';
import AutorenewIcon from '@mui/icons-material/Autorenew';
import ViewListIcon from '@mui/icons-material/ViewList';
import AccountTreeIcon from '@mui/icons-material/AccountTree';
import MoveToInboxIcon from '@mui/icons-material/MoveToInbox';
import { isSourceProcessing, isSourceIndexed, isSourceExtracted } from '../../types';
import { useRecoveryThresholds } from '../../config/recoveryThresholds';
import { useSourcePause } from '../../hooks/useSourcePause';
import { useNotification } from '../../contexts/useNotification';
import { useSourceDetail } from './hooks/useSourceDetail';
import { OverviewTab } from './components/OverviewTab';
import { ChunksTab } from './components/ChunksTab';
import { ExtractionTab } from './components/ExtractionTab';
import { SourcePageHeader } from './components/header/SourcePageHeader';
import { McpExtractionBanner } from './components/banners/McpExtractionBanner';
import { ProcessingBanner } from './components/banners/ProcessingBanner';
import { DeleteSourceDialog } from './components/DeleteSourceDialog';
import { RecoveryEventsPanel } from './components/RecoveryEventsPanel';

export default function SourcePage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const highlightChunkId = searchParams.get('highlight');
  const tabParam = searchParams.get('tab');
  const highlightChunkIndexParam = searchParams.get('highlight_chunk_index');
  // True for any URL that deep-links into the Chunks tab — either an explicit
  // ?tab=chunks, an id-style ?highlight=<chunkId>, or the legacy
  // ?highlight_chunk_index=N (note: ?highlight= is sent empty alongside the
  // index-style link, so an empty string must NOT count as a highlight).
  const deepLinksToChunks =
    tabParam === 'chunks' || !!highlightChunkId || highlightChunkIndexParam != null;

  const {
    source,
    stats,
    loading,
    loadError,
    actionError,
    clearActionError,
    extractionProgress,
    deleteSource,
    toggleEnabled,
    abortProcessing,
    resetToIndexed,
    finalizePartial,
    chatWithSource,
    retrySource,
    reExtract,
    reextractSource,
    refetch,
  } = useSourceDetail(id, navigate);

  const { pauseSource, resumeSource } = useSourcePause(refetch);
  const { warnThreshold: recoveryWarnThreshold, maxAttempts: recoveryMaxAttempts } =
    useRecoveryThresholds();

  // F12 — surface in-flight action state to users. The page-scoped
  // mutations (retry / pause / resume / re-extract) are hand-rolled
  // callbacks rather than TanStack `useMutation` instances, so no
  // built-in `isPending` exists. A single page-level flag is enough to
  // (a) render a thin progress strip and (b) be passed to descendants
  // that want to disable their buttons while a write is mid-flight.
  const [pendingAction, setPendingAction] = useState<string | null>(null);
  const runAction = async (label: string, fn: () => Promise<void>): Promise<void> => {
    setPendingAction(label);
    try {
      await fn();
    } finally {
      setPendingAction(null);
    }
  };

  const handlePause = async (): Promise<void> => {
    if (!source) return;
    await runAction('pause', () => pauseSource(source.id));
  };
  const handleResume = async (): Promise<void> => {
    if (!source) return;
    await runAction('resume', () => resumeSource(source.id));
  };

  const { notify } = useNotification();
  const handleRetry = async (): Promise<void> => {
    await runAction('retry', async () => {
      try {
        await retrySource();
        notify('Source queued for retry', 'success');
      } catch {
        // actionError is set inside retrySource; surface it via the existing error banner
      }
    });
  };
  const handleReextract = async (): Promise<void> => {
    await runAction('reextract', async () => {
      try {
        await reextractSource();
        notify('Source queued for re-extraction', 'success');
      } catch {
        // actionError is set inside reextractSource; surface via banner.
      }
    });
  };

  // Tab state — start on the Chunks tab (index 1) when the URL deep-links
  // into it on first mount.
  const [tab, setTab] = useState(deepLinksToChunks ? 1 : 0);

  // SourcePage stays mounted when a "View chunk" button
  // navigates to a new ?highlight=…&tab=chunks URL (same route), so the
  // mount-only initializer above isn't enough. Sync the tab to a fresh
  // deep-link by adjusting state during render (React's recommended
  // alternative to a setState-in-effect), keyed on the deep-link params so a
  // later manual tab switch isn't undone on the next render.
  const deepLinkKey = `${tabParam ?? ''}|${highlightChunkId ?? ''}|${highlightChunkIndexParam ?? ''}`;
  const [prevDeepLinkKey, setPrevDeepLinkKey] = useState(deepLinkKey);
  if (deepLinkKey !== prevDeepLinkKey) {
    setPrevDeepLinkKey(deepLinkKey);
    if (deepLinksToChunks) setTab(1);
  }

  // Delete confirmation
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);

  // Loading state
  if (loading) {
    return <LoadingState message="Loading source..." fullPage />;
  }

  // Not found state
  if (!source) {
    return (
      <Box>
        <Alert severity="error" sx={ghostErrorAlertSx}>Source not found</Alert>
        <Button startIcon={<ArrowBackIcon />} onClick={() => navigate('/sources')} sx={{ mt: 2 }}>
          Back to Sources
        </Button>
      </Box>
    );
  }

  // Determine which tabs are available
  const isImported = source.source_type === 'imported';
  const showChunksTab = isSourceIndexed(source);
  // Imported sources have no local extraction pipeline — the Extraction tab
  // reads source_entities, which is always empty for an import (the entities
  // came pre-formed in the CCX package and live in the graph). Cut the
  // confusing empty tab; the imported banner below points to the Graph view.
  const showExtractionTab = isSourceExtracted(source) && !isImported;

  return (
    <Box>
      <SourcePageHeader
        source={source}
        onBack={() => navigate('/sources')}
        onToggleEnabled={toggleEnabled}
        onChat={chatWithSource}
        onAbort={abortProcessing}
        onDelete={() => setDeleteDialogOpen(true)}
        onViewInGraph={() => navigate(`/graph?source_ids=${source.id}`)}
        onPause={handlePause}
        onResume={handleResume}
        onRetry={handleRetry}
        onReExtract={reExtract}
        onReextract={handleReextract}
      />
      {/* In-flight mutation indicator (F12). Thin progress bar above the
          banners so users see immediate feedback after clicking retry /
          pause / resume / re-extract — prevents the "did my click do
          anything?" double-submit pattern. */}
      {pendingAction && (
        <LinearProgress
          aria-label={`Action in progress: ${pendingAction}`}
          sx={{ mb: 2 }}
        />
      )}
      {/* MCP Extraction Progress Banner */}
      {source.status === 'mcp_extracting' && (
        <McpExtractionBanner
          source={source}
          onReset={resetToIndexed}
          onFinalize={finalizePartial}
        />
      )}
      {/* Paused Banner */}
      {source.is_paused && (
        <Alert severity="warning" sx={{ mb: 2 }} icon={<PauseIcon />}
          action={<Button color="inherit" size="small" onClick={handleResume}>Resume</Button>}
        >
          <strong>Processing paused</strong> (was {source.status}).
          {source.paused_reason && <> Reason: {source.paused_reason}.</>}
        </Alert>
      )}
      {/* Recovery exhausted banner (error state, no further auto-recovery) */}
      {source.status === 'error' && source.error_stage === 'recovery_exhausted' && (
        <Alert
          severity="error"
          sx={{ mb: 2 }}
          icon={<AutorenewIcon />}
          action={
            <Button color="inherit" size="small" onClick={handleRetry}>
              Retry
            </Button>
          }
        >
          <strong>Recovery exhausted</strong> after {source.recovery_attempts ?? recoveryMaxAttempts} attempts.
          {' '}Automatic recovery has stopped. Click &ldquo;Retry&rdquo; to manually restart the source.
        </Alert>
      )}
      {/* Recovery warning banner (repeated recoveries but source is still running) */}
      {(source.recovery_attempts ?? 0) >= recoveryWarnThreshold && source.status !== 'error' && (
        <Alert
          severity="warning"
          sx={{ mb: 2 }}
          icon={<AutorenewIcon />}
        >
          This source has been auto-recovered <strong>{source.recovery_attempts}</strong> time{source.recovery_attempts === 1 ? '' : 's'}.
          {' '}If recovery continues, the source will be marked errored after {recoveryMaxAttempts} attempts.
          {' '}Expand the panel below to see what fired.
        </Alert>
      )}
      {/* Recovery audit-trail panel: collapsible, fetches on expand only */}
      <RecoveryEventsPanel
        sourceId={source.id}
        recoveryAttempts={source.recovery_attempts ?? 0}
      />
      {/* Progress Banner (during internal processing, hidden when paused) */}
      {isSourceProcessing(source) && source.status !== 'mcp_extracting' && !source.is_paused && (
        <ProcessingBanner source={source} extractionProgress={extractionProgress} />
      )}
      {/* Error Banner */}
      {source.status === 'error' && source.error_message && (
        <Alert severity="error" sx={{ ...ghostErrorAlertSx, mb: 2 }}>
          <Typography variant="subtitle2">
            Error during {source.error_stage || 'processing'}:
          </Typography>
          <Typography variant="body2">{source.error_message}</Typography>
        </Alert>
      )}
      {(loadError || actionError) && (
        <Alert
          severity="error"
          onClose={() => { clearActionError(); }}
          sx={{ ...ghostErrorAlertSx, mb: 2 }}
        >
          {actionError || loadError}
        </Alert>
      )}
      {/* Imported-source banner: an import has no local extraction pipeline,
          so the Overview's pipeline stages and the (hidden) Extraction tab
          don't apply. Set expectations + point at the Graph view where the
          imported entities actually live. */}
      {isImported && (
        <Alert
          severity="info"
          icon={<MoveToInboxIcon />}
          sx={{ mb: 2 }}
          action={
            <Button
              color="inherit"
              size="small"
              onClick={() => navigate(`/graph?source_ids=${source.id}`)}
            >
              View in graph
            </Button>
          }
        >
          <strong>Imported source.</strong> This graph came from a CCX package, not a local
          extraction run, so there are no per-chunk extraction artifacts to show. Its entities and
          relationships live in the knowledge graph — open the Graph view to explore them.
        </Alert>
      )}
      {/* Tabs */}
      <Box sx={{ mb: 2, borderBottom: '1px solid rgba(255, 255, 255, 0.06)' }}>
        <Tabs value={tab} onChange={(_, newValue) => setTab(newValue)} sx={ghostTabsSx}>
          <Tab icon={<DashboardIcon sx={{ fontSize: 18 }} />} iconPosition="start" label="Overview" />
          <Tab
            icon={<ViewListIcon sx={{ fontSize: 18 }} />}
            iconPosition="start"
            label={`Chunks (${source.chunk_count})`}
            disabled={!showChunksTab}
          />
          {showExtractionTab && (
            <Tab
              icon={<AccountTreeIcon sx={{ fontSize: 18 }} />}
              iconPosition="start"
              label={`Extraction (${source.extraction_entities_count})`}
            />
          )}
        </Tabs>
      </Box>
      {/* Tab Content */}
      {tab === 0 && (
        <OverviewTab
          source={source}
          stats={stats}
          onNavigateToExtraction={showExtractionTab ? () => setTab(2) : undefined}
        />
      )}
      {tab === 1 && showChunksTab && (
        <ChunksTab source={source} highlightChunkId={highlightChunkId} />
      )}
      {tab === 2 && showExtractionTab && (
        <ExtractionTab
          sourceId={source.id}
          entitiesCount={source.extraction_entities_count}
          relationshipsCount={source.extraction_relationships_count}
          templatesCount={source.commit_templates_created}
        />
      )}
      {/* Delete Confirmation Dialog */}
      <DeleteSourceDialog
        open={deleteDialogOpen}
        source={source}
        onClose={() => setDeleteDialogOpen(false)}
        onConfirm={() => {
          deleteSource();
          setDeleteDialogOpen(false);
        }}
      />
    </Box>
  );
}
