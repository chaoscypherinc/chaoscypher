// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  Typography,
  Box,
  List,
  ListItem,
  ListItemText,
  CircularProgress,
} from '@mui/material';
import WarningIcon from '@mui/icons-material/Warning';
import type { UnifiedSource } from '../../../types';
import { ghostButtonSx, ghostCancelBtnSx, ghostDialogPaperSx } from '../../../theme/ghostStyles';
import { ChaosCypherPalette } from '../../../theme/palette';

interface BulkDeleteDialogProps {
  open: boolean;
  sources: UnifiedSource[];
  onClose: () => void;
  onConfirm: () => void;
  loading: boolean;
}

export function BulkDeleteDialog({
  open,
  sources,
  onClose,
  onConfirm,
  loading,
}: BulkDeleteDialogProps) {
  const activeSources = sources.filter((s) => s.stage === 'active');
  const processingSources = sources.filter((s) => s.stage !== 'active');

  const displaySources = sources.slice(0, 5);
  const remainingCount = sources.length - displaySources.length;

  return (
    <Dialog open={open} onClose={loading ? undefined : onClose} maxWidth="sm" fullWidth slotProps={{
      paper: { sx: ghostDialogPaperSx }
    }}>
      <DialogTitle sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
        <WarningIcon color="error" />
        Delete {sources.length} Source{sources.length !== 1 ? 's' : ''}
      </DialogTitle>
      <DialogContent>
        <Typography gutterBottom>
          Are you sure you want to permanently delete {sources.length} source
          {sources.length !== 1 ? 's' : ''}? This action cannot be undone.
        </Typography>

        {activeSources.length > 0 && (
          <Typography variant="body2" color="error" sx={{ mt: 1 }}>
            {activeSources.length} active source{activeSources.length !== 1 ? 's' : ''} will be
            removed from the knowledge base along with all chunks and citations.
          </Typography>
        )}

        {processingSources.length > 0 && (
          <Typography
            variant="body2"
            sx={{
              color: "text.secondary",
              mt: 1
            }}>
            {processingSources.length} processing source{processingSources.length !== 1 ? 's' : ''}{' '}
            will be deleted.
          </Typography>
        )}

        <Box sx={{ mt: 2, bgcolor: 'action.hover', borderRadius: 1 }}>
          <List dense disablePadding>
            {displaySources.map((source) => (
              <ListItem key={source.id} sx={{ py: 0.5 }}>
                <ListItemText
                  primary={source.title}
                  secondary={source.stage === 'active' ? 'Active' : 'Processing'}
                  slotProps={{
                    primary: { variant: 'body2', noWrap: true },
                    secondary: { variant: 'caption' }
                  }} />
              </ListItem>
            ))}
            {remainingCount > 0 && (
              <ListItem sx={{ py: 0.5 }}>
                <ListItemText
                  primary={`+${remainingCount} more...`}
                  slotProps={{
                    primary: { variant: 'body2', color: 'text.secondary' }
                  }}
                />
              </ListItem>
            )}
          </List>
        </Box>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={loading} sx={ghostCancelBtnSx}>
          Cancel
        </Button>
        <Button
          variant="outlined"
          sx={ghostButtonSx(ChaosCypherPalette.error)}
          onClick={onConfirm}
          disabled={loading}
          startIcon={loading ? <CircularProgress size={16} color="inherit" /> : undefined}
        >
          {loading ? 'Deleting...' : 'Delete'}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
