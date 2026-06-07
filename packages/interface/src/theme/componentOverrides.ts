// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * MUI theme component overrides for Chaos Cypher.
 *
 * Extracted from App.tsx to keep the root component focused on routing
 * and providers. Returns the `components` object for createTheme().
 */

import { alpha, type ThemeOptions } from '@mui/material';
import { ChaosCypherPalette, ChaosCypherBackground } from './palette';

/**
 * Build MUI component style overrides for the Chaos Cypher theme.
 *
 * @returns The `components` block for MUI createTheme()
 */
export function getComponentOverrides(): NonNullable<ThemeOptions['components']> {
  return {
    MuiAppBar: {
      styleOverrides: {
        root: {
          backgroundImage: 'none',
          backgroundColor: ChaosCypherBackground.dark.default,
          boxShadow: 'none',
          borderBottom: 'none',
        },
      },
    },
    MuiDrawer: {
      styleOverrides: {
        paper: {
          backgroundColor: 'transparent',
          borderRight: 'none',
        },
      },
    },
    MuiPaper: {
      styleOverrides: {
        root: {
          backgroundImage: 'none',
          backgroundColor: 'transparent',
          boxShadow: 'none',
        },
      },
    },
    MuiMenu: {
      styleOverrides: {
        paper: {
          backgroundColor: `${ChaosCypherBackground.dark.paper} !important`,
          border: '1px solid rgba(255, 255, 255, 0.08)',
        },
      },
    },
    MuiPopover: {
      styleOverrides: {
        paper: {
          backgroundColor: `${ChaosCypherBackground.dark.paper} !important`,
          border: '1px solid rgba(255, 255, 255, 0.08)',
        },
      },
    },
    MuiDialog: {
      styleOverrides: {
        paper: {
          backgroundColor: `${ChaosCypherBackground.dark.paper} !important`,
          border: '1px solid rgba(255, 255, 255, 0.08)',
        },
      },
    },
    MuiTooltip: {
      styleOverrides: {
        tooltip: {
          backgroundColor: 'rgba(0, 0, 0, 0.92)',
          border: '1px solid rgba(255, 255, 255, 0.08)',
        },
      },
    },
    MuiAutocomplete: {
      styleOverrides: {
        paper: {
          backgroundColor: `${ChaosCypherBackground.dark.paper} !important`,
          border: '1px solid rgba(255, 255, 255, 0.08)',
        },
      },
    },
    MuiAlert: {
      styleOverrides: {
        root: ({ ownerState }: { ownerState: { variant?: string; color?: string; severity?: string } }) => ({
          backgroundImage: 'none',
          ...(ownerState.variant === 'standard' && (ownerState.color || ownerState.severity) === 'error' && {
            backgroundColor: alpha(ChaosCypherPalette.error, 0.08),
            border: `1px solid ${alpha(ChaosCypherPalette.error, 0.2)}`,
          }),
          ...(ownerState.variant === 'standard' && (ownerState.color || ownerState.severity) === 'warning' && {
            backgroundColor: alpha(ChaosCypherPalette.warning, 0.08),
            border: `1px solid ${alpha(ChaosCypherPalette.warning, 0.2)}`,
          }),
          ...(ownerState.variant === 'standard' && (ownerState.color || ownerState.severity) === 'info' && {
            backgroundColor: alpha(ChaosCypherPalette.info, 0.08),
            border: `1px solid ${alpha(ChaosCypherPalette.info, 0.2)}`,
          }),
          ...(ownerState.variant === 'standard' && (ownerState.color || ownerState.severity) === 'success' && {
            backgroundColor: alpha(ChaosCypherPalette.success, 0.08),
            border: `1px solid ${alpha(ChaosCypherPalette.success, 0.2)}`,
          }),
        }),
      },
    },
    MuiTableContainer: {
      styleOverrides: {
        root: {
          backgroundColor: 'transparent',
        },
      },
    },
    MuiTableRow: {
      styleOverrides: {
        root: {
          borderBottom: `1px solid rgba(255, 255, 255, 0.06)`,
          '&:last-child': {
            borderBottom: 'none',
          },
          '&:hover': {
            backgroundColor: 'rgba(255, 255, 255, 0.03)',
          },
        },
        head: {
          borderBottom: `1px solid rgba(255, 255, 255, 0.10)`,
          '&:hover': {
            backgroundColor: 'transparent',
          },
        },
      },
    },
    MuiTableCell: {
      styleOverrides: {
        root: {
          borderBottom: 'none',
        },
      },
    },
    MuiChip: {
      styleOverrides: {
        filled: {
          backgroundColor: 'transparent',
          border: '1px solid',
        },
        colorSuccess: {
          backgroundColor: 'transparent',
          borderColor: alpha(ChaosCypherPalette.success, 0.5),
          color: ChaosCypherPalette.success,
        },
        colorError: {
          backgroundColor: 'transparent',
          borderColor: alpha(ChaosCypherPalette.error, 0.5),
          color: ChaosCypherPalette.error,
        },
        colorWarning: {
          backgroundColor: 'transparent',
          borderColor: alpha(ChaosCypherPalette.warning, 0.5),
          color: ChaosCypherPalette.warning,
        },
        colorInfo: {
          backgroundColor: 'transparent',
          borderColor: alpha(ChaosCypherPalette.info, 0.5),
          color: ChaosCypherPalette.info,
        },
        colorSecondary: {
          backgroundColor: 'transparent',
          borderColor: alpha(ChaosCypherPalette.secondary, 0.5),
          color: ChaosCypherPalette.secondary,
        },
        colorPrimary: {
          backgroundColor: 'transparent',
          borderColor: alpha(ChaosCypherPalette.primary, 0.5),
          color: ChaosCypherPalette.primary,
        },
        colorDefault: {
          backgroundColor: 'transparent',
          borderColor: 'rgba(255, 255, 255, 0.25)',
          color: 'rgba(255, 255, 255, 0.6)',
        },
      },
    },
    MuiTabs: {
      styleOverrides: {
        indicator: {
          height: 1,
          backgroundColor: ChaosCypherPalette.primary,
          boxShadow: `0 0 8px ${alpha(ChaosCypherPalette.primary, 0.6)}`,
        },
      },
    },
    MuiTab: {
      styleOverrides: {
        root: {
          opacity: 0.45,
          '&.Mui-selected': {
            opacity: 1,
          },
          transition: 'opacity 0.2s ease',
        },
      },
    },
    MuiCheckbox: {
      styleOverrides: {
        root: {
          color: 'rgba(255, 255, 255, 0.15)',
          borderRadius: 1,
          '&:hover': {
            color: alpha(ChaosCypherPalette.primary, 0.6),
            backgroundColor: 'transparent',
          },
          '&.Mui-checked': {
            color: ChaosCypherPalette.primary,
          },
        },
      },
    },
    MuiAccordion: {
      styleOverrides: {
        root: {
          backgroundImage: 'none',
          backgroundColor: 'transparent',
          boxShadow: 'none',
          '&:before': { display: 'none' },
        },
      },
    },
    MuiOutlinedInput: {
      styleOverrides: {
        root: {
          borderRadius: 6,
          backgroundColor: alpha(ChaosCypherPalette.primary, 0.02),
          '& .MuiOutlinedInput-notchedOutline': {
            borderColor: alpha(ChaosCypherPalette.primary, 0.15),
            borderBottomWidth: 2,
            borderBottomColor: alpha(ChaosCypherPalette.primary, 0.4),
          },
          '&:hover .MuiOutlinedInput-notchedOutline': {
            borderColor: alpha(ChaosCypherPalette.primary, 0.25),
            borderBottomColor: alpha(ChaosCypherPalette.primary, 0.6),
          },
          '&.Mui-focused .MuiOutlinedInput-notchedOutline': {
            borderBottomWidth: 2,
            borderBottomColor: ChaosCypherPalette.primary,
          },
        },
      },
    },
    MuiSelect: {
      styleOverrides: {
        select: {
          paddingRight: '40px !important',
        },
        icon: {
          color: 'rgba(255, 255, 255, 0.5)',
          right: 10,
        },
      },
    },
    MuiInputLabel: {
      styleOverrides: {
        root: {
          fontSize: '0.85rem',
        },
      },
    },
  };
}
