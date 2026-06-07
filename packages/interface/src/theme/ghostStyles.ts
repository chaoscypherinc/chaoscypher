// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Ghost UI Styles — shared dark/neon glassmorphic styling constants.
 *
 * Single source of truth for ghost inputs, buttons, dialogs, alerts,
 * tabs, switches, and cancel buttons used across the application.
 *
 * @example
 * ```tsx
 * import { ghostInputSx, ghostDialogPaperSx, ghostButtonSx } from '../theme/ghostStyles';
 *
 * <TextField sx={ghostInputSx} />
 * <Dialog PaperProps={{ sx: ghostDialogPaperSx }}>...</Dialog>
 * <Button variant="outlined" sx={ghostButtonSx('#00E5FF')}>Action</Button>
 * ```
 */
import { alpha } from '@mui/material';
import { ChaosCypherPalette, ChaosCypherNeutrals } from './palette';

const CYAN = ChaosCypherPalette.primary;
const MINT = ChaosCypherPalette.success;
const ERROR = ChaosCypherPalette.error;

// ── Ghost Inputs ──────────────────────────────────────────────────────────

/** Ghost-styled TextField / Select / FormControl. */
export const ghostInputSx = {
  '& .MuiOutlinedInput-root': {
    bgcolor: 'rgba(0, 0, 0, 0.2)',
    borderRadius: '8px',
  },
  '& .MuiOutlinedInput-root .MuiOutlinedInput-notchedOutline': {
    borderColor: 'rgba(255, 255, 255, 0.08)',
  },
  '& .MuiOutlinedInput-root:hover .MuiOutlinedInput-notchedOutline': {
    borderColor: alpha(CYAN, 0.3),
  },
  '& .MuiOutlinedInput-root.Mui-focused .MuiOutlinedInput-notchedOutline': {
    borderColor: `${alpha(CYAN, 0.6)} !important`,
    borderWidth: '1px !important',
  },
  '& .MuiInputLabel-root': {
    color: 'rgba(255, 255, 255, 0.4)',
  },
  '& .MuiInputLabel-root.Mui-focused': {
    color: CYAN,
  },
} as const;

// ── Ghost Dialogs ─────────────────────────────────────────────────────────

/** Glassmorphic Dialog PaperProps sx. */
export const ghostDialogPaperSx = {
  bgcolor: 'rgba(10, 14, 23, 0.95)',
  border: '1px solid rgba(255, 255, 255, 0.06)',
  backdropFilter: 'blur(20px)',
  borderRadius: '12px',
} as const;

// ── Ghost Buttons ─────────────────────────────────────────────────────────

/** Ghost outlined button — pass the neon accent color. */
export const ghostButtonSx = (color: string) => ({
  borderColor: alpha(color, 0.3),
  color,
  bgcolor: alpha(color, 0.04),
  transition: 'all 0.2s',
  '&:hover': {
    borderColor: alpha(color, 0.6),
    bgcolor: alpha(color, 0.08),
    boxShadow: `0 0 12px ${alpha(color, 0.1)}`,
  },
  '&.Mui-disabled': {
    borderColor: 'rgba(255, 255, 255, 0.06)',
    color: 'rgba(255, 255, 255, 0.2)',
    bgcolor: 'transparent',
  },
});

/** Muted dismiss/cancel button styling. */
export const ghostCancelBtnSx = {
  color: 'rgba(255, 255, 255, 0.5)',
  '&:hover': { bgcolor: 'rgba(255, 255, 255, 0.05)' },
} as const;

// ── Ghost Alerts ──────────────────────────────────────────────────────────

/** Neon red error alert. */
export const ghostErrorAlertSx = {
  bgcolor: alpha(ERROR, 0.08),
  border: `1px solid ${alpha(ERROR, 0.2)}`,
  color: '#ff6b8a', // light pink — intentional lighter variant for readable body text
  '& .MuiAlert-icon': { color: ERROR },
} as const;

/** Neon cyan info alert. */
export const ghostInfoAlertSx = {
  bgcolor: alpha(CYAN, 0.06),
  border: `1px solid ${alpha(CYAN, 0.15)}`,
  color: ChaosCypherNeutrals.textSecondary,
  '& .MuiAlert-icon': { color: CYAN },
} as const;

/** Neon mint success alert. */
export const ghostSuccessAlertSx = {
  bgcolor: alpha(MINT, 0.08),
  border: `1px solid ${alpha(MINT, 0.2)}`,
  color: MINT,
  '& .MuiAlert-icon': { color: MINT },
} as const;

// ── Ghost Tabs ────────────────────────────────────────────────────────────

/** Neon cyan tab styling. */
export const ghostTabsSx = {
  '& .MuiTab-root': { color: ChaosCypherNeutrals.textTertiary },
  '& .Mui-selected': { color: CYAN },
  '& .MuiTabs-indicator': { bgcolor: CYAN },
} as const;

// ── Ghost Switch ──────────────────────────────────────────────────────────

/** Neon cyan switch. */
export const ghostSwitchSx = {
  '& .MuiSwitch-switchBase.Mui-checked': { color: CYAN },
  '& .MuiSwitch-switchBase.Mui-checked + .MuiSwitch-track': {
    bgcolor: alpha(CYAN, 0.4),
  },
} as const;

/** Neon mint switch (for enable/disable toggles). */
export const ghostSwitchMintSx = {
  '& .MuiSwitch-switchBase.Mui-checked': { color: MINT },
  '& .MuiSwitch-switchBase.Mui-checked + .MuiSwitch-track': {
    bgcolor: alpha(MINT, 0.4),
  },
} as const;

// ── Ghost Tables ──────────────────────────────────────────────────────────

/** Table header cell styling. */
export const ghostTableHeadCellSx = {
  color: ChaosCypherNeutrals.textMuted,
  borderColor: 'rgba(255, 255, 255, 0.06)',
  fontSize: 12,
  textTransform: 'uppercase' as const,
  letterSpacing: '0.05em',
} as const;

/** Table body row hover + border styling. */
export const ghostTableRowSx = {
  '&:hover': { bgcolor: 'rgba(0, 229, 255, 0.03)' },
  '& td': { borderColor: 'rgba(255, 255, 255, 0.04)' },
} as const;

// ── Ghost Code Blocks ─────────────────────────────────────────────────────

/** Dark code block / pre styling. */
export const ghostCodeBlockSx = {
  bgcolor: 'rgba(0, 0, 0, 0.3)',
  border: '1px solid rgba(255, 255, 255, 0.06)',
  color: ChaosCypherNeutrals.textPrimary,
  p: 2,
  borderRadius: '8px',
  overflow: 'auto',
  fontFamily: 'monospace',
  fontSize: '0.875rem',
} as const;
