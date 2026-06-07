// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Create Database Dialog
 *
 * Modal dialog for creating a new database with name validation.
 */

import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  TextField,
  Button,
} from '@mui/material';
import {
  ghostInputSx,
  ghostDialogPaperSx,
  ghostButtonSx,
  ghostCancelBtnSx,
} from '../../theme/ghostStyles';
import { ChaosCypherPalette } from '../../theme/palette';

interface CreateDatabaseDialogProps {
  open: boolean;
  onClose: () => void;
  databaseName: string;
  onDatabaseNameChange: (name: string) => void;
  onCreateDatabase: () => Promise<boolean>;
  creating: boolean;
}

/** Dialog for creating a new database instance. */
export default function CreateDatabaseDialog({
  open,
  onClose,
  databaseName,
  onDatabaseNameChange,
  onCreateDatabase,
  creating,
}: CreateDatabaseDialogProps) {
  const handleCreate = async () => {
    const success = await onCreateDatabase();
    if (success) {
      onClose();
    }
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="xs" fullWidth slotProps={{
      paper: { sx: ghostDialogPaperSx }
    }}>
      <DialogTitle>Create New Database</DialogTitle>
      <DialogContent>
        <TextField
          autoFocus
          label="Database Name"
          value={databaseName}
          onChange={(e) => onDatabaseNameChange(e.target.value)}
          placeholder="e.g., project_alpha"
          fullWidth
          variant="outlined"
          helperText="Alphanumeric, underscores, and hyphens only"
          sx={{ mt: 1, ...ghostInputSx }}
        />
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} sx={ghostCancelBtnSx}>Cancel</Button>
        <Button
          variant="outlined"
          onClick={handleCreate}
          disabled={creating || !databaseName.trim()}
          sx={ghostButtonSx(ChaosCypherPalette.primary)}
        >
          {creating ? 'Creating...' : 'Create'}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
