// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { useState } from 'react';
import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  IconButton,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
  Typography,
} from '@mui/material';
import AccountTreeIcon from '@mui/icons-material/AccountTree';
import AutorenewIcon from '@mui/icons-material/Autorenew';
import ChatIcon from '@mui/icons-material/Chat';
import CheckIcon from '@mui/icons-material/Check';
import DeleteIcon from '@mui/icons-material/Delete';
import MoreVertIcon from '@mui/icons-material/MoreVert';
import PauseIcon from '@mui/icons-material/Pause';
import PlayArrowIcon from '@mui/icons-material/PlayArrow';
import RestartAltIcon from '@mui/icons-material/RestartAlt';
import StopIcon from '@mui/icons-material/Stop';
import VisibilityOffIcon from '@mui/icons-material/VisibilityOff';
import { ghostButtonSx, ghostCancelBtnSx, ghostDialogPaperSx } from '../../../../theme/ghostStyles';
import { ChaosCypherPalette } from '../../../../theme/palette';
import type { Source } from '../../../../types';
import {
  REEXTRACTABLE_STATUSES,
  isSourceCommitted,
  isSourceIndexed,
  isSourceProcessing,
} from '../../../../types';

interface SourceActionsMenuProps {
  source: Source;
  onToggleEnabled: () => void;
  onChat: () => void;
  onAbort: () => void;
  onDelete: () => void;
  onViewInGraph?: () => void;
  onPause?: () => void;
  onResume?: () => void;
  onRetry?: () => void;
  onReExtract?: (force: boolean) => void;
  /**
   * Audit fix #F49 — explicit Re-extract via the dedicated re-extract
   * endpoint. When provided, the COMMITTED-source confirm dialog calls
   * this instead of the legacy ``triggerExtraction(force)`` path so the
   * server reliably clears the cached commit_payload (audit fix #F44).
   * Also enables the action on extracted/extracting/committing/error
   * statuses.
   */
  onReextract?: () => void;
}

/**
 * Three-dot actions menu for SourcePage. Shows
 * Enable/Disable (committed only), Chat (indexed+), Abort (processing),
 * Retry (error only), Re-extract (indexed or committed), and Delete.
 * Owns its own anchor state and the re-extract confirmation dialog.
 */
export function SourceActionsMenu({
  source,
  onToggleEnabled,
  onChat,
  onAbort,
  onDelete,
  onViewInGraph,
  onPause,
  onResume,
  onRetry,
  onReExtract,
  onReextract,
}: SourceActionsMenuProps) {
  const [anchor, setAnchor] = useState<null | HTMLElement>(null);
  const [reExtractConfirmOpen, setReExtractConfirmOpen] = useState(false);
  const close = () => setAnchor(null);

  const showViewInGraph = isSourceCommitted(source) && !!onViewInGraph;
  // First-extract on INDEXED still flows through onReExtract(false) for
  // backwards compat. Otherwise prefer the dedicated reextract endpoint.
  const canReextract =
    onReextract !== undefined && REEXTRACTABLE_STATUSES.has(source.status);
  const showReExtract =
    canReextract ||
    (onReExtract !== undefined &&
      (source.status === 'indexed' || source.status === 'committed'));
  const hasConditionalItems =
    isSourceCommitted(source) || isSourceIndexed(source) || isSourceProcessing(source) || !!source.is_paused || source.status === 'error';

  const handleReExtractClick = () => {
    close();
    if (source.status === 'indexed' && onReExtract) {
      // First extraction on an indexed-but-never-extracted source is not
      // a re-extract — bypass the warning dialog and just kick it off.
      onReExtract(false);
      return;
    }
    setReExtractConfirmOpen(true);
  };

  return (
    <>
      <IconButton aria-label="More actions" size="small" onClick={(e) => setAnchor(e.currentTarget)}>
        <MoreVertIcon />
      </IconButton>
      <Menu
        anchorEl={anchor}
        open={Boolean(anchor)}
        onClose={close}
        transformOrigin={{ horizontal: 'right', vertical: 'top' }}
        anchorOrigin={{ horizontal: 'right', vertical: 'bottom' }}
      >
        {isSourceCommitted(source) && (
          <MenuItem onClick={() => { onToggleEnabled(); close(); }}>
            <ListItemIcon>
              {source.enabled
                ? <VisibilityOffIcon fontSize="small" />
                : <CheckIcon fontSize="small" />
              }
            </ListItemIcon>
            <ListItemText>{source.enabled ? 'Disable' : 'Enable'}</ListItemText>
          </MenuItem>
        )}
        {isSourceIndexed(source) && (
          <MenuItem onClick={() => { onChat(); close(); }}>
            <ListItemIcon><ChatIcon fontSize="small" /></ListItemIcon>
            <ListItemText>Chat with source</ListItemText>
          </MenuItem>
        )}
        {showViewInGraph && (
          <MenuItem onClick={() => { onViewInGraph!(); close(); }}>
            <ListItemIcon><AccountTreeIcon fontSize="small" /></ListItemIcon>
            <ListItemText>View in Graph</ListItemText>
          </MenuItem>
        )}
        {showReExtract && (
          <MenuItem onClick={handleReExtractClick}>
            <ListItemIcon>
              <AutorenewIcon
                fontSize="small"
                sx={{ color: source.status === 'indexed' ? 'primary.main' : 'warning.main' }}
              />
            </ListItemIcon>
            <ListItemText>
              {source.status === 'indexed'
                ? 'Extract'
                : source.status === 'committed'
                  ? 'Re-extract (replaces graph data)'
                  : 'Re-extract'}
            </ListItemText>
          </MenuItem>
        )}
        {source.is_paused && onResume && (
          <MenuItem onClick={() => { onResume(); close(); }}>
            <ListItemIcon><PlayArrowIcon fontSize="small" color="success" /></ListItemIcon>
            <ListItemText>Resume Processing</ListItemText>
          </MenuItem>
        )}
        {!source.is_paused && isSourceProcessing(source) && onPause && (
          <MenuItem onClick={() => { onPause(); close(); }}>
            <ListItemIcon><PauseIcon fontSize="small" sx={{ color: 'warning.main' }} /></ListItemIcon>
            <ListItemText>Pause Processing</ListItemText>
          </MenuItem>
        )}
        {isSourceProcessing(source) && !source.is_paused && (
          <MenuItem onClick={() => { onAbort(); close(); }}>
            <ListItemIcon><StopIcon fontSize="small" color="warning" /></ListItemIcon>
            <ListItemText>Abort processing</ListItemText>
          </MenuItem>
        )}
        {source.status === 'error' && onRetry && (
          <MenuItem onClick={() => { onRetry(); close(); }}>
            <ListItemIcon><RestartAltIcon fontSize="small" color="primary" /></ListItemIcon>
            <ListItemText>Retry</ListItemText>
          </MenuItem>
        )}
        {hasConditionalItems && <Divider />}
        <MenuItem onClick={() => { onDelete(); close(); }}>
          <ListItemIcon><DeleteIcon fontSize="small" color="error" /></ListItemIcon>
          <ListItemText sx={{ color: 'error.main' }}>Delete</ListItemText>
        </MenuItem>
      </Menu>

      {/* Re-extract confirmation dialog. Prefers the dedicated re-extract
          endpoint (audit fix #F49) when available — it cleanly clears the
          stale commit_payload (audit fix #F44) and supports more statuses
          than the legacy triggerExtraction(force) path. */}
      <Dialog
        open={reExtractConfirmOpen}
        onClose={() => setReExtractConfirmOpen(false)}
        maxWidth="sm"
        fullWidth
        slotProps={{ paper: { sx: ghostDialogPaperSx } }}
      >
        <DialogTitle>Re-extract source?</DialogTitle>
        <DialogContent>
          <Typography>
            This will discard the current extraction and re-run the LLM. Continue?
          </Typography>
          {source.status === 'committed' && (
            <Typography sx={{ mt: 1, color: 'text.secondary', fontSize: '0.875rem' }}>
              Existing graph nodes, relationships, and templates created from
              this source will be deleted before the new extraction runs.
            </Typography>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setReExtractConfirmOpen(false)} sx={ghostCancelBtnSx}>
            Cancel
          </Button>
          <Button
            variant="outlined"
            onClick={() => {
              setReExtractConfirmOpen(false);
              if (onReextract) {
                onReextract();
              } else if (onReExtract) {
                onReExtract(true);
              }
            }}
            sx={ghostButtonSx(ChaosCypherPalette.warning)}
          >
            Re-extract
          </Button>
        </DialogActions>
      </Dialog>
    </>
  );
}
