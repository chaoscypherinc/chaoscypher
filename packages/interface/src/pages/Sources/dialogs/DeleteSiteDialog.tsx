// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { useState } from 'react';
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  FormControlLabel,
  Radio,
  RadioGroup,
  Typography,
} from '@mui/material';
import type { Site } from '../../../types';
import { ghostButtonSx, ghostCancelBtnSx, ghostDialogPaperSx } from '../../../theme/ghostStyles';
import { ChaosCypherPalette } from '../../../theme/palette';

interface DeleteSiteDialogProps {
  open: boolean;
  site: Site;
  onClose: () => void;
  /** `deletePages` true deletes the pages too; false keeps them as separate sources. */
  onConfirm: (deletePages: boolean) => void;
}

/** Delete an imported site, keeping its pages as separate sources (the default) or not. */
export function DeleteSiteDialog({ open, site, onClose, onConfirm }: DeleteSiteDialogProps) {
  const [deletePages, setDeletePages] = useState(false);
  const pages = site.progress.total + site.progress.gone;
  const count = `${pages} ${pages === 1 ? 'page' : 'pages'}`;

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth="sm"
      fullWidth
      slotProps={{ paper: { sx: ghostDialogPaperSx } }}
    >
      <DialogTitle>Delete {site.title}?</DialogTitle>
      <DialogContent>
        <Typography variant="body2" sx={{ color: 'text.secondary', mb: 1 }}>
          The site stops being checked for updates. Its pages can stay as ordinary sources.
        </Typography>
        <RadioGroup
          value={deletePages ? 'delete' : 'keep'}
          onChange={(e) => setDeletePages(e.target.value === 'delete')}
        >
          <FormControlLabel
            value="keep"
            control={<Radio size="small" />}
            label={`Keep its ${count} as separate sources`}
          />
          <FormControlLabel
            value="delete"
            control={<Radio size="small" />}
            label={`Delete its ${count} too`}
          />
        </RadioGroup>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} sx={ghostCancelBtnSx}>Cancel</Button>
        <Button
          variant="outlined"
          sx={ghostButtonSx(ChaosCypherPalette.error)}
          onClick={() => onConfirm(deletePages)}
        >
          Delete
        </Button>
      </DialogActions>
    </Dialog>
  );
}
