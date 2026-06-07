// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * WorkflowToolbar: Top toolbar for the workflow builder canvas.
 *
 * Contains navigation, undo/redo controls, layout tools, settings,
 * templates access, and save/test action buttons.
 */

import React from 'react';
import {
  Box,
  IconButton,
  Tooltip,
  Button,
  Typography,
  Divider,
  CircularProgress,
  alpha,
} from '@mui/material';
import ArrowBackIcon from '@mui/icons-material/ArrowBack';
import SaveIcon from '@mui/icons-material/Save';
import PlayArrowIcon from '@mui/icons-material/PlayArrow';
import UndoIcon from '@mui/icons-material/Undo';
import RedoIcon from '@mui/icons-material/Redo';
import SettingsIcon from '@mui/icons-material/Settings';
import AutoFixHighIcon from '@mui/icons-material/AutoFixHigh';
import BookmarksIcon from '@mui/icons-material/Bookmarks';

import type { WorkflowMetadata } from './types';
import { ChaosCypherPalette } from '../../theme/palette';

const CYAN = ChaosCypherPalette.primary;
const MINT = ChaosCypherPalette.success;

interface WorkflowToolbarProps {
  /** Current workflow metadata (null for new workflows). */
  workflow: WorkflowMetadata | null;
  /** Whether the canvas has unsaved changes. */
  isDirty: boolean;
  /** Whether a save operation is in progress. */
  isSaving: boolean;
  /** Whether undo is available. */
  canUndo: boolean;
  /** Whether redo is available. */
  canRedo: boolean;
  /** Navigate back to workflow list. */
  onBack: () => void;
  /** Undo last action. */
  onUndo: () => void;
  /** Redo last undone action. */
  onRedo: () => void;
  /** Auto-layout the canvas nodes. */
  onAutoLayout: () => void;
  /** Open workflow settings modal. */
  onOpenSettings: () => void;
  /** Open step templates panel. */
  onOpenTemplates: () => void;
  /** Start test execution. */
  onTestExecution: () => void;
  /** Save the workflow. */
  onSave: () => void;
}

/**
 * Top action bar for the workflow builder.
 *
 * Renders back navigation, workflow name, undo/redo, layout tools,
 * settings, templates, test, and save buttons in a horizontal strip.
 */
export const WorkflowToolbar: React.FC<WorkflowToolbarProps> = ({
  workflow,
  isDirty,
  isSaving,
  canUndo,
  canRedo,
  onBack,
  onUndo,
  onRedo,
  onAutoLayout,
  onOpenSettings,
  onOpenTemplates,
  onTestExecution,
  onSave,
}) => {
  return (
    <Box
      sx={{
        display: 'flex',
        alignItems: 'center',
        gap: 1,
        px: 2,
        py: 1,
        borderBottom: 1,
        borderColor: 'rgba(255, 255, 255, 0.06)',
        bgcolor: 'rgba(10, 14, 23, 0.95)',
      }}
    >
      <Tooltip title="Back to Workflows">
        <IconButton aria-label="Back to Workflows" onClick={onBack} size="small" sx={{ '&:hover': { bgcolor: 'rgba(255, 255, 255, 0.05)' } }}>
          <ArrowBackIcon />
        </IconButton>
      </Tooltip>

      <Divider orientation="vertical" flexItem sx={{ borderColor: 'rgba(255, 255, 255, 0.08)' }} />

      <Typography variant="h6" sx={{ flexGrow: 1, ml: 1, color: 'text.primary' }}>
        {workflow?.name || 'New Workflow'}
        {isDirty && ' *'}
      </Typography>

      <Tooltip title="Undo (Ctrl+Z)">
        <span>
          <IconButton aria-label="Undo (Ctrl+Z)" onClick={onUndo} disabled={!canUndo} size="small" sx={{ '&:hover': { bgcolor: 'rgba(255, 255, 255, 0.05)' } }}>
            <UndoIcon />
          </IconButton>
        </span>
      </Tooltip>

      <Tooltip title="Redo (Ctrl+Y)">
        <span>
          <IconButton aria-label="Redo (Ctrl+Y)" onClick={onRedo} disabled={!canRedo} size="small" sx={{ '&:hover': { bgcolor: 'rgba(255, 255, 255, 0.05)' } }}>
            <RedoIcon />
          </IconButton>
        </span>
      </Tooltip>

      <Divider orientation="vertical" flexItem sx={{ borderColor: 'rgba(255, 255, 255, 0.08)' }} />

      <Tooltip title="Auto Layout">
        <IconButton aria-label="Auto Layout" onClick={onAutoLayout} size="small" sx={{ '&:hover': { bgcolor: 'rgba(255, 255, 255, 0.05)' } }}>
          <AutoFixHighIcon />
        </IconButton>
      </Tooltip>

      <Tooltip title="Workflow Settings">
        <IconButton aria-label="Workflow Settings" size="small" onClick={onOpenSettings} sx={{ '&:hover': { bgcolor: 'rgba(255, 255, 255, 0.05)' } }}>
          <SettingsIcon />
        </IconButton>
      </Tooltip>

      <Tooltip title="Step Templates">
        <IconButton aria-label="Step Templates" size="small" onClick={onOpenTemplates} sx={{ '&:hover': { bgcolor: 'rgba(255, 255, 255, 0.05)' } }}>
          <BookmarksIcon />
        </IconButton>
      </Tooltip>

      <Divider orientation="vertical" flexItem sx={{ borderColor: 'rgba(255, 255, 255, 0.08)' }} />

      <Button
        variant="outlined"
        startIcon={<PlayArrowIcon />}
        onClick={onTestExecution}
        disabled={!workflow}
        size="small"
        sx={{
          borderColor: alpha(CYAN, 0.3),
          color: CYAN,
          bgcolor: alpha(CYAN, 0.04),
          transition: 'all 0.2s',
          '&:hover': {
            borderColor: alpha(CYAN, 0.6),
            bgcolor: alpha(CYAN, 0.08),
            boxShadow: `0 0 12px ${alpha(CYAN, 0.1)}`,
          },
          '&.Mui-disabled': {
            borderColor: 'rgba(255, 255, 255, 0.06)',
            color: 'rgba(255, 255, 255, 0.2)',
          },
        }}
      >
        Test
      </Button>

      <Button
        variant="outlined"
        startIcon={isSaving ? <CircularProgress size={16} sx={{ color: MINT }} /> : <SaveIcon />}
        onClick={onSave}
        disabled={isSaving || !isDirty}
        size="small"
        sx={{
          borderColor: alpha(MINT, 0.3),
          color: MINT,
          bgcolor: alpha(MINT, 0.04),
          transition: 'all 0.2s',
          '&:hover': {
            borderColor: alpha(MINT, 0.6),
            bgcolor: alpha(MINT, 0.08),
            boxShadow: `0 0 12px ${alpha(MINT, 0.1)}`,
          },
          '&.Mui-disabled': {
            borderColor: 'rgba(255, 255, 255, 0.06)',
            color: 'rgba(255, 255, 255, 0.2)',
          },
        }}
      >
        {workflow ? 'Save' : 'Create'}
      </Button>
    </Box>
  );
};
