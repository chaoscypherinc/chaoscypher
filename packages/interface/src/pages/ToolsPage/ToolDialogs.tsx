// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import {
  Box,
  Typography,
  TextField,
  Button,
  Select,
  MenuItem,
  FormControl,
  InputLabel,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
} from '@mui/material';
import {
  ghostInputSx,
  ghostDialogPaperSx,
  ghostButtonSx,
  ghostCancelBtnSx,
  ghostCodeBlockSx,
} from '../../theme/ghostStyles';
import { ChaosCypherPalette } from '../../theme/palette';
import type { SystemToolSummary } from './SystemToolCard';

const CYAN = ChaosCypherPalette.primary;

/** Detail data for a system tool including schemas. */
export interface SystemToolDetail extends SystemToolSummary {
  input_schema: Record<string, unknown>;
  output_schema: Record<string, unknown>;
}

/** Props for the create/edit tool dialog. */
interface ToolFormDialogProps {
  open: boolean;
  isCreate: boolean;
  toolName: string;
  toolDescription: string;
  selectedSystemTool: string;
  toolConfiguration: string;
  toolTags: string[];
  systemTools: SystemToolSummary[];
  onToolNameChange: (value: string) => void;
  onToolDescriptionChange: (value: string) => void;
  onSelectedSystemToolChange: (value: string) => void;
  onToolConfigurationChange: (value: string) => void;
  onToolTagsChange: (tags: string[]) => void;
  onSubmit: () => void;
  onClose: () => void;
}

/** Dialog for creating or editing a user tool configuration. */
export function ToolFormDialog({
  open,
  isCreate,
  toolName,
  toolDescription,
  selectedSystemTool,
  toolConfiguration,
  toolTags,
  systemTools,
  onToolNameChange,
  onToolDescriptionChange,
  onSelectedSystemToolChange,
  onToolConfigurationChange,
  onToolTagsChange,
  onSubmit,
  onClose,
}: ToolFormDialogProps) {
  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth="md"
      fullWidth
      slotProps={{
        paper: { sx: ghostDialogPaperSx }
      }}
    >
      <DialogTitle sx={{ color: 'text.primary' }}>
        {isCreate ? 'Create User Tool' : 'Edit User Tool'}
      </DialogTitle>
      <DialogContent>
        <TextField label="Tool Name" fullWidth value={toolName} onChange={(e) => onToolNameChange(e.target.value)} margin="normal" sx={ghostInputSx} />
        <TextField label="Description" fullWidth multiline rows={2} value={toolDescription} onChange={(e) => onToolDescriptionChange(e.target.value)} margin="normal" sx={ghostInputSx} />
        <FormControl fullWidth margin="normal" sx={ghostInputSx}>
          <InputLabel>System Tool</InputLabel>
          <Select value={selectedSystemTool} label="System Tool" onChange={(e) => onSelectedSystemToolChange(e.target.value)} disabled={!isCreate}>
            {systemTools.map(tool => (
              <MenuItem key={tool.id} value={tool.id}>{tool.name} ({tool.id})</MenuItem>
            ))}
          </Select>
        </FormControl>
        <TextField label="Configuration (JSON)" fullWidth multiline rows={8} value={toolConfiguration} onChange={(e) => onToolConfigurationChange(e.target.value)} margin="normal" helperText="Enter JSON configuration for the system tool" sx={ghostInputSx} />
        <TextField label="Tags (comma-separated)" fullWidth value={toolTags.join(', ')} onChange={(e) => onToolTagsChange(e.target.value.split(',').map(t => t.trim()).filter(t => t))} margin="normal" sx={ghostInputSx} />
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} sx={ghostCancelBtnSx}>
          Cancel
        </Button>
        <Button
          variant="outlined"
          onClick={onSubmit}
          sx={ghostButtonSx(CYAN)}
        >
          {isCreate ? 'Create' : 'Update'}
        </Button>
      </DialogActions>
    </Dialog>
  );
}

/** Props for the schema viewer dialog. */
interface SchemaDialogProps {
  open: boolean;
  tool: SystemToolDetail | null;
  onClose: () => void;
}

/** Dialog displaying a system tool's input and output JSON schemas. */
export function SchemaDialog({ open, tool, onClose }: SchemaDialogProps) {
  return (
    <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth slotProps={{
      paper: { sx: ghostDialogPaperSx }
    }}>
      <DialogTitle sx={{ color: 'text.primary' }}>{tool?.name} - Schema</DialogTitle>
      <DialogContent>
        <Typography variant="subtitle1" gutterBottom sx={{ color: 'text.secondary' }}>Input Schema:</Typography>
        <Box component="pre" sx={ghostCodeBlockSx}>
          {JSON.stringify(tool?.input_schema ?? null, null, 2)}
        </Box>
        <Typography variant="subtitle1" gutterBottom sx={{ mt: 2, color: 'text.secondary' }}>Output Schema:</Typography>
        <Box component="pre" sx={ghostCodeBlockSx}>
          {JSON.stringify(tool?.output_schema ?? null, null, 2)}
        </Box>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} sx={ghostCancelBtnSx}>
          Close
        </Button>
      </DialogActions>
    </Dialog>
  );
}
