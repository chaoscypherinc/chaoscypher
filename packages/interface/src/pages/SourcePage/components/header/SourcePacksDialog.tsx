// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * @module SourcePacksDialog
 * Source settings: the packs a source extracts with, the same chips as at
 * import (PackSelect). Changing them re-extracts the source, so the dialog
 * says so before the user confirms: a committed source's graph data is
 * deleted first and the model runs again.
 */

import { useState } from 'react';
import {
  Alert,
  Button,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
} from '@mui/material';
import { PackSelect } from '../../../../components/upload/PackSelect';
import { sourcePacks } from '../../../../utils/packs';
import { ghostButtonSx, ghostCancelBtnSx, ghostDialogPaperSx } from '../../../../theme/ghostStyles';
import { ChaosCypherPalette } from '../../../../theme/palette';
import type { ExtractionDomain } from '../../../../services/api/sourceProcessing';
import type { Source } from '../../../../types';

interface SourcePacksDialogProps {
  open: boolean;
  source: Source;
  availableDomains: ExtractionDomain[];
  submitting: boolean;
  onClose: () => void;
  onConfirm: (packs: string[]) => void;
}

function samePacks(a: string[], b: string[]): boolean {
  return a.length === b.length && [...a].sort().every((p, i) => p === [...b].sort()[i]);
}

export function SourcePacksDialog({
  open,
  source,
  availableDomains,
  submitting,
  onClose,
  onConfirm,
}: SourcePacksDialogProps) {
  const current = sourcePacks(source) ?? [];
  const [packs, setPacks] = useState<string[]>(current);
  // Reseed when the dialog opens for a (different) source: React's
  // adjust-state-during-render pattern, keyed on the open source id.
  const openKey = open ? source.id : null;
  const [prevKey, setPrevKey] = useState<string | null>(openKey);
  if (openKey !== prevKey) {
    setPrevKey(openKey);
    if (openKey) setPacks(current);
  }
  const unchanged = samePacks(packs, current);
  const committed = source.status === 'committed';

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth="sm"
      fullWidth
      slotProps={{ paper: { sx: ghostDialogPaperSx } }}
    >
      <DialogTitle sx={{ color: 'text.primary' }}>Packs</DialogTitle>
      <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: '8px !important' }}>
        <PackSelect
          value={packs}
          availableDomains={availableDomains}
          onChange={(next) => setPacks(next ?? [])}
          disabled={submitting}
        />
        <Alert severity="warning" variant="outlined">
          Changing the packs re-extracts this source
          {committed
            ? ': its graph data (nodes, relationships, templates) is deleted first and the model runs again over every chunk.'
            : ': the model runs again over every chunk.'}
        </Alert>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} sx={ghostCancelBtnSx}>
          Cancel
        </Button>
        <Button
          variant="outlined"
          onClick={() => onConfirm(packs)}
          disabled={submitting || unchanged}
          startIcon={submitting ? <CircularProgress size={16} sx={{ color: 'primary.main' }} /> : undefined}
          sx={ghostButtonSx(ChaosCypherPalette.warning)}
        >
          {submitting ? 'Re-extracting...' : 'Re-extract with these packs'}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
