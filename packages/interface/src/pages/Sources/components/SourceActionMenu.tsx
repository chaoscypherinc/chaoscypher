// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Source Action Menu Component
 *
 * Context menu for per-source actions, triggered via the 3-dot icon button.
 * Shows different menu items depending on whether the source is active
 * (view, export, chat, graph, toggle, delete) or processing (pause/resume,
 * stop, delete). Uses the shared {@link useMenuState} hook for anchor state.
 */

import { useState } from 'react';
import {
  Menu,
  MenuItem,
  ListItemIcon,
  Divider,
  CircularProgress,
} from '@mui/material';
import VisibilityIcon from '@mui/icons-material/Visibility';
import VisibilityOffIcon from '@mui/icons-material/VisibilityOff';
import ChatIcon from '@mui/icons-material/Chat';
import DeleteIcon from '@mui/icons-material/Delete';
import StopIcon from '@mui/icons-material/Stop';
import DownloadIcon from '@mui/icons-material/Download';
import HubIcon from '@mui/icons-material/Hub';
import PauseIcon from '@mui/icons-material/Pause';
import PlayArrowIcon from '@mui/icons-material/PlayArrow';
import RestartAltIcon from '@mui/icons-material/RestartAlt';
import AutorenewIcon from '@mui/icons-material/Autorenew';
import type { UnifiedSource } from '../../../types';
import { REEXTRACTABLE_STATUSES } from '../../../types';
import { useMenuState } from '../../../hooks';
import { useConfirmDialog } from '../../../hooks/useConfirmDialog';
import { dataApi } from '../../../services/api/data';
import { sourcesApi } from '../../../services/api/sources';
import { useNotification } from '../../../contexts/useNotification';
import { getApiErrorMessage } from '../../../utils/errors';
import { logger } from '../../../utils/logger';
import ConfirmDialog from '../../../components/ConfirmDialog';

/** Statuses that indicate a source is currently being processed. */
const PROCESSING_STATUSES = ['pending', 'indexing', 'vision_pending', 'extracting', 'mcp_extracting', 'committing'];

interface SourceActionMenuProps {
  /** Callback to view source details. */
  onRowClick: (source: UnifiedSource) => void;
  /** Callback to stop processing. */
  onStop: (source: UnifiedSource) => void;
  /** Callback to delete source. */
  onDelete: (source: UnifiedSource) => void;
  /** Callback to toggle source enabled/disabled. */
  onToggleEnabled: (source: UnifiedSource) => void;
  /** Callback to chat with source (optional). */
  onChatWithSource?: (source: UnifiedSource) => void;
  /** Callback to view source in graph (optional). */
  onViewInGraph?: (source: UnifiedSource) => void;
  /** Callback to pause source processing (optional). */
  onPauseSource?: (source: UnifiedSource) => void;
  /** Callback to resume source processing (optional). */
  onResumeSource?: (source: UnifiedSource) => void;
  /** Callback invoked after a successful retry (optional). */
  onRetrySource?: (source: UnifiedSource) => void;
  /** Callback invoked after a successful re-extract (optional). */
  onReextractSource?: (source: UnifiedSource) => void;
}

interface SourceActionMenuReturn {
  /** Open the menu for a specific source, anchored to the click event. */
  openMenu: (event: React.MouseEvent<HTMLElement>, source: UnifiedSource) => void;
  /** The rendered Menu element (include in JSX). */
  menuElement: React.ReactNode;
}

/**
 * Hook that manages source action menu state and renders the Menu.
 *
 * Returns an `openMenu` function for the 3-dot button and a `menuElement`
 * to include in the component tree. This pattern keeps the menu state
 * co-located with its rendering while using {@link useMenuState} for
 * anchor management.
 */
export function useSourceActionMenu({
  onRowClick,
  onStop,
  onDelete,
  onToggleEnabled,
  onChatWithSource,
  onViewInGraph,
  onPauseSource,
  onResumeSource,
  onRetrySource,
  onReextractSource,
}: SourceActionMenuProps): SourceActionMenuReturn {
  const menu = useMenuState();
  const [menuSource, setMenuSource] = useState<UnifiedSource | null>(null);
  const [exportingSourceId, setExportingSourceId] = useState<string | null>(null);
  const reextractDialog = useConfirmDialog<UnifiedSource>();
  const { notify } = useNotification();

  const openMenu = (event: React.MouseEvent<HTMLElement>, source: UnifiedSource) => {
    event.stopPropagation();
    setMenuSource(source);
    menu.open(event);
  };

  const closeMenu = () => {
    menu.close();
    setMenuSource(null);
  };

  const handleRetrySource = async (source: UnifiedSource) => {
    try {
      await sourcesApi.retrySource(source.id);
      notify('Source queued for retry', 'success');
      if (onRetrySource) {
        onRetrySource(source);
      }
    } catch (err) {
      logger.error('Retry source failed:', err);
      notify(getApiErrorMessage(err), 'error');
    }
  };

  const handleReextractConfirm = async () => {
    const source = reextractDialog.data;
    if (!source) return;
    await reextractDialog.confirm(async () => {
      try {
        await sourcesApi.reextractSource(source.id);
        notify('Source queued for re-extraction', 'success');
        if (onReextractSource) {
          onReextractSource(source);
        }
      } catch (err) {
        logger.error('Re-extract source failed:', err);
        notify(getApiErrorMessage(err), 'error');
      }
    });
  };

  const handleExportSource = async (source: UnifiedSource) => {
    try {
      setExportingSourceId(source.id);
      const blob = await dataApi.exportBySource([source.id]);

      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      const safeName = source.title.replace(/[^a-zA-Z0-9_-]/g, '_');
      a.download = `${safeName}_export.ccx`;
      document.body.appendChild(a);
      a.click();
      window.URL.revokeObjectURL(url);
      document.body.removeChild(a);
    } catch (error) {
      logger.error('Source export failed:', error);
    } finally {
      setExportingSourceId(null);
    }
  };

  const openReextractDialog = (source: UnifiedSource) => {
    reextractDialog.open(source);
  };

  const menuElement = (
    <>
      <Menu
        anchorEl={menu.anchorEl}
        open={menu.isOpen}
        onClose={closeMenu}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'right' }}
        transformOrigin={{ vertical: 'top', horizontal: 'right' }}
      >
        {menuSource && menuSource.stage === 'active'
          ? renderActiveMenuItems(
              menuSource,
              closeMenu,
              exportingSourceId,
              handleExportSource,
              openReextractDialog,
              onRowClick,
              onToggleEnabled,
              onDelete,
              onChatWithSource,
              onViewInGraph,
            )
          : menuSource
            ? renderProcessingMenuItems(
                menuSource,
                closeMenu,
                onStop,
                onDelete,
                handleRetrySource,
                openReextractDialog,
                onPauseSource,
                onResumeSource,
              )
            : null}
      </Menu>
      <ConfirmDialog
        open={reextractDialog.isOpen}
        title="Re-extract source?"
        message="This will discard the current extraction and re-run the LLM. Continue?"
        confirmLabel={reextractDialog.isConfirming ? 'Re-extracting...' : 'Re-extract'}
        cancelLabel="Cancel"
        confirmColor="warning"
        onConfirm={handleReextractConfirm}
        onCancel={reextractDialog.close}
      />
    </>
  );

  return { openMenu, menuElement };
}

/** Menu items for active/completed sources. */
function renderActiveMenuItems(
  source: UnifiedSource,
  closeMenu: () => void,
  exportingSourceId: string | null,
  handleExportSource: (source: UnifiedSource) => void,
  openReextractDialog: (source: UnifiedSource) => void,
  onRowClick: (source: UnifiedSource) => void,
  onToggleEnabled: (source: UnifiedSource) => void,
  onDelete: (source: UnifiedSource) => void,
  onChatWithSource?: (source: UnifiedSource) => void,
  onViewInGraph?: (source: UnifiedSource) => void,
): React.ReactNode[] {
  const items: React.ReactNode[] = [
    <MenuItem key="view" onClick={() => { onRowClick(source); closeMenu(); }}>
      <ListItemIcon><VisibilityIcon fontSize="small" /></ListItemIcon>
      View Details
    </MenuItem>,
    <MenuItem
      key="export"
      disabled={exportingSourceId === source.id}
      onClick={() => { handleExportSource(source); closeMenu(); }}
    >
      <ListItemIcon>
        {exportingSourceId === source.id
          ? <CircularProgress size={18} />
          : <DownloadIcon fontSize="small" />}
      </ListItemIcon>
      {exportingSourceId === source.id ? 'Exporting...' : 'Export Source'}
    </MenuItem>,
  ];

  if (onChatWithSource) {
    items.push(
      <MenuItem key="chat" onClick={() => { onChatWithSource(source); closeMenu(); }}>
        <ListItemIcon><ChatIcon fontSize="small" /></ListItemIcon>
        Chat with Source
      </MenuItem>,
    );
  }

  if (onViewInGraph) {
    items.push(
      <MenuItem key="graph" onClick={() => { onViewInGraph(source); closeMenu(); }}>
        <ListItemIcon><HubIcon fontSize="small" /></ListItemIcon>
        View in Graph
      </MenuItem>,
    );
  }

  // Re-extract — committed sources are eligible (audit fix #F49). Distinct
  // from Retry: throws away the cached extraction, costs LLM tokens.
  if (REEXTRACTABLE_STATUSES.has(source.status)) {
    items.push(
      <MenuItem
        key="reextract"
        onClick={() => { openReextractDialog(source); closeMenu(); }}
      >
        <ListItemIcon>
          <AutorenewIcon fontSize="small" sx={{ color: 'warning.main' }} />
        </ListItemIcon>
        Re-extract
      </MenuItem>,
    );
  }

  items.push(
    <Divider key="div1" />,
    <MenuItem key="toggle" onClick={() => { onToggleEnabled(source); closeMenu(); }}>
      <ListItemIcon>
        {source.active?.enabled !== false
          ? <VisibilityOffIcon fontSize="small" />
          : <VisibilityIcon fontSize="small" />}
      </ListItemIcon>
      {source.active?.enabled !== false ? 'Disable' : 'Enable'}
    </MenuItem>,
    <Divider key="div2" />,
    <MenuItem key="delete" onClick={() => { onDelete(source); closeMenu(); }} sx={{ color: 'error.main' }}>
      <ListItemIcon><DeleteIcon fontSize="small" color="error" /></ListItemIcon>
      Delete
    </MenuItem>,
  );

  return items;
}

/** Menu items for processing/queued/error sources. */
function renderProcessingMenuItems(
  source: UnifiedSource,
  closeMenu: () => void,
  onStop: (source: UnifiedSource) => void,
  onDelete: (source: UnifiedSource) => void,
  onRetrySource: (source: UnifiedSource) => void,
  openReextractDialog: (source: UnifiedSource) => void,
  onPauseSource?: (source: UnifiedSource) => void,
  onResumeSource?: (source: UnifiedSource) => void,
): React.ReactNode[] {
  const items: React.ReactNode[] = [];

  // Retry — only for errored sources
  if (source.status === 'error') {
    items.push(
      <MenuItem key="retry" onClick={() => { onRetrySource(source); closeMenu(); }}>
        <ListItemIcon><RestartAltIcon fontSize="small" color="primary" /></ListItemIcon>
        Retry
      </MenuItem>,
    );
  }

  // Re-extract — distinct from Retry (audit fix #F49). Available on any
  // status that has produced extraction artifacts (or could). Costs LLM
  // tokens, so we surface it with a confirm dialog at the call site.
  if (REEXTRACTABLE_STATUSES.has(source.status)) {
    items.push(
      <MenuItem
        key="reextract"
        onClick={() => { openReextractDialog(source); closeMenu(); }}
      >
        <ListItemIcon>
          <AutorenewIcon fontSize="small" sx={{ color: 'warning.main' }} />
        </ListItemIcon>
        Re-extract
      </MenuItem>,
    );
  }

  // Pause / Resume toggle
  if (source.is_paused && onResumeSource) {
    items.push(
      <MenuItem key="resume" onClick={() => { onResumeSource(source); closeMenu(); }}>
        <ListItemIcon><PlayArrowIcon fontSize="small" color="success" /></ListItemIcon>
        Resume Processing
      </MenuItem>,
    );
  } else if (!source.is_paused && onPauseSource && PROCESSING_STATUSES.includes(source.status)) {
    items.push(
      <MenuItem key="pause" onClick={() => { onPauseSource(source); closeMenu(); }}>
        <ListItemIcon><PauseIcon fontSize="small" sx={{ color: 'warning.main' }} /></ListItemIcon>
        Pause Processing
      </MenuItem>,
    );
  }

  // Stop processing
  if (PROCESSING_STATUSES.includes(source.status) && !source.is_paused) {
    items.push(
      <MenuItem key="stop" onClick={() => { onStop(source); closeMenu(); }}>
        <ListItemIcon><StopIcon fontSize="small" /></ListItemIcon>
        Stop Processing
      </MenuItem>,
    );
  }

  items.push(
    <Divider key="div" />,
    <MenuItem key="delete" onClick={() => { onDelete(source); closeMenu(); }} sx={{ color: 'error.main' }}>
      <ListItemIcon><DeleteIcon fontSize="small" color="error" /></ListItemIcon>
      Delete
    </MenuItem>,
  );

  return items;
}
